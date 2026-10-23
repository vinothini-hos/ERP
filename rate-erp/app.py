"""
Simple ERP for products and their vendor rates.
Backend: Flask + SQLite (a single file, erp.db, is created next to this script).

Run:
    pip install flask
    python app.py
Then open http://127.0.0.1:5000

How a vendor's rate for a product is decided on a given day
-----------------------------------------------------------
Every price is stored as a rate entry that applies from a start date up to and
including its "valid till" date. After the valid-till date the vendor has NO rate
for that product until a new rate is added.
A new rate starts by itself: today if the vendor's rates have all expired, or the
day after the vendor's latest rate ends. (It can also be started today, replacing
the current rate.) Old rates are never overwritten, so the full history is kept.
"""
import math
import os
import sqlite3
from datetime import date, timedelta

from flask import Flask, g, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "erp.db")
DB_VERSION = 4

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------- database
SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    unit TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vendor_rates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    vendor         TEXT NOT NULL,
    cost           REAL NOT NULL,
    effective_from TEXT NOT NULL,   -- YYYY-MM-DD, first day the rate applies (set automatically)
    valid_until    TEXT             -- YYYY-MM-DD, last day the rate applies
);
"""


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    """Create the tables, or upgrade a database made by an earlier version of this app."""
    con = connect()
    try:
        existed = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vendor_rates'").fetchone()
        con.executescript(SCHEMA)
        if existed and con.execute("PRAGMA user_version").fetchone()[0] < DB_VERSION:
            cols = {r["name"] for r in con.execute("PRAGMA table_info(vendor_rates)")}
            if "effective_from" not in cols:
                # 1st version: prices had no dates
                con.execute("ALTER TABLE vendor_rates ADD COLUMN effective_from TEXT")
                con.execute("ALTER TABLE vendor_rates ADD COLUMN valid_until TEXT")
                con.execute("UPDATE vendor_rates SET effective_from = ?", (today(),))
            elif "is_offer" not in cols:
                # 2nd version: an entry with an end date was an offer. Offers are no longer used.
                con.execute("DELETE FROM vendor_rates WHERE valid_until IS NOT NULL")
            else:
                # 3rd version: offers were marked with is_offer. Offers are no longer used.
                con.execute("DELETE FROM vendor_rates WHERE is_offer = 1")
                try:
                    con.execute("ALTER TABLE vendor_rates DROP COLUMN is_offer")
                except sqlite3.OperationalError:
                    pass  # very old SQLite: the unused column just stays
        con.execute(f"PRAGMA user_version = {DB_VERSION}")
        con.commit()
    finally:
        con.close()


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


# ---------------------------------------------------------------- helpers
class BadRequest(Exception):
    pass


@app.errorhandler(BadRequest)
def bad_request(err):
    return jsonify(error=str(err)), 400


def today():
    return date.today().isoformat()


def add_days(iso, n):
    return (date.fromisoformat(iso) + timedelta(days=n)).isoformat()


def parse_day(value, what):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise BadRequest(f"Enter a valid {what} date.")


def day_param():
    """The ?date=YYYY-MM-DD query parameter, or today."""
    value = request.args.get("date")
    return parse_day(value, "as-of") if value else today()


def parse_cost(value, vendor):
    try:
        cost = float(value)
    except (TypeError, ValueError):
        raise BadRequest(f"Enter a valid price for {vendor}.")
    if not math.isfinite(cost) or cost < 0:
        raise BadRequest(f"Price for {vendor} can't be negative.")
    return round(cost, 2)


def nice_day(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def canonical_vendor(con, name):
    """Reuse the saved spelling if this vendor already exists in any case (abc → ABC)."""
    row = con.execute("SELECT vendor FROM vendor_rates WHERE vendor = ? COLLATE NOCASE LIMIT 1", (name,)).fetchone()
    return row["vendor"] if row else name


def entries_for(con, product_id=None, vendor=None):
    sql, args = "SELECT * FROM vendor_rates WHERE 1 = 1", []
    if product_id is not None:
        sql += " AND product_id = ?"
        args.append(product_id)
    if vendor is not None:
        sql += " AND vendor = ?"
        args.append(vendor)
    return [dict(r) for r in con.execute(sql, args)]


def group_by_vendor(entries):
    out = {}
    for e in entries:
        out.setdefault(e["vendor"], []).append(e)
    return out


# ---------------------------------------------------------------- rate rules
def spans(entries):
    """
    One vendor's rates for one product, oldest first, each paired with the last day it
    really applies: its valid-till date, or the day before the next rate starts if that
    comes first (the rate was replaced). None = no end date (data from older versions only).
    """
    rates = sorted(entries, key=lambda e: (e["effective_from"], e["id"]))
    out = []
    for i, e in enumerate(rates):
        end = e["valid_until"]
        if i + 1 < len(rates):
            cut = add_days(rates[i + 1]["effective_from"], -1)
            if end is None or cut < end:
                end = cut
        out.append((e, end))
    return out


def rate_on(entries, day):
    """The vendor's rate entry that applies on `day`, with its last day, or (None, None)."""
    for e, end in spans(entries):
        if e["effective_from"] <= day and (end is None or day <= end):
            return e, end
    return None, None


def start_info(entries, day):
    """When would a new rate for this vendor start if added on `day`?"""
    sp = spans(entries)
    info = {"start": day, "latest_end": None, "open_ended": False}
    if not sp:
        return info
    if sp[-1][1] is None:              # latest rate has no end date (older data): replace it from today
        info["open_ended"] = True
        return info
    latest_end = max(end for _, end in sp if end is not None)
    if latest_end >= day:              # a rate is still running (or coming up): start after it
        info.update(start=add_days(latest_end, 1), latest_end=latest_end)
    return info


def vendor_summary(vendor, entries, day):
    current, end = rate_on(entries, day)
    sp = spans(entries)
    upcoming = next(((e, e_end) for e, e_end in sp
                     if e["effective_from"] > day and (e_end is None or e_end >= e["effective_from"])), None)
    ended = [e_end for e, e_end in sp
             if e_end is not None and e["effective_from"] <= e_end < day]
    status = "active" if current else ("expired" if ended else "not_started")
    return {
        "vendor": vendor,
        "cost": current["cost"] if current else None,   # price that applies on `day`; None = no valid rate
        "status": status,                                # active | expired | not_started
        "valid_till": end if current else None,
        "expired_on": max(ended) if status == "expired" else None,   # last day the previous rate was valid
        "next_rate": {"cost": upcoming[0]["cost"], "from": upcoming[0]["effective_from"],
                      "till": upcoming[1]} if upcoming else None,
    }


def products_on(con, day):
    """All products with each vendor's rate on `day`: vendors with a valid rate cheapest first, then the rest."""
    products = {r["id"]: {"id": r["id"], "name": r["name"], "unit": r["unit"], "vendors": []}
                for r in con.execute("SELECT * FROM products ORDER BY name COLLATE NOCASE")}
    grouped = {}
    for e in entries_for(con):
        grouped.setdefault((e["product_id"], e["vendor"]), []).append(e)
    for (pid, vendor), entries in grouped.items():
        if pid in products:
            products[pid]["vendors"].append(vendor_summary(vendor, entries, day))
    for p in products.values():
        p["vendors"].sort(key=lambda v: (v["cost"] is None, v["cost"] or 0, v["vendor"].lower()))
    return list(products.values())


def history_of(con, pid, day):
    """Every rate entry of a product with its status on `day`, plus when a new rate would start."""
    rows, vendors = [], {}
    for vendor, entries in group_by_vendor(entries_for(con, product_id=pid)).items():
        current, _ = rate_on(entries, day)
        end_of = {e["id"]: end for e, end in spans(entries)}
        for e in entries:
            if e["effective_from"] > day:
                status = ("upcoming", "Upcoming")
            elif current and current["id"] == e["id"]:
                status = ("current", "In effect")
            elif end_of[e["id"]] != e["valid_until"]:
                status = ("replaced", "Replaced")
            else:
                status = ("expired", "Expired")
            rows.append({
                "id": e["id"], "vendor": vendor, "cost": e["cost"],
                "effective_from": e["effective_from"], "valid_until": e["valid_until"],
                "status": status[0], "status_label": status[1],
            })
        vendors[vendor] = start_info(entries, today())
    rows.sort(key=lambda r: (r["effective_from"], r["id"]), reverse=True)
    rows.sort(key=lambda r: r["vendor"].lower())
    return {"entries": rows, "vendors": vendors}


def add_rate(con, pid, vendor, cost, till, replace=False, start=None):
    """
    Add one rate entry and return (action, start).
    The start date is worked out automatically unless `start` is given.
    The same vendor and dates again counts as a correction: the price is updated.
    """
    t = today()
    if not till:
        raise BadRequest(f"Enter a valid-till date for {vendor}.")
    if start is None:
        start = t if replace else start_info(entries_for(con, pid, vendor), t)["start"]
    if till < start:
        if start > t:
            raise BadRequest(f"This rate would start on {nice_day(start)}, the day after the latest rate for "
                             f"{vendor} ends, so its valid-till date must be {nice_day(start)} or later. "
                             f"To replace the current rate from today, choose to start it today instead.")
        raise BadRequest(f"The valid-till date for {vendor} has already passed.")

    row = con.execute("SELECT id FROM vendor_rates WHERE product_id = ? AND vendor = ? "
                      "AND effective_from = ? AND valid_until = ?", (pid, vendor, start, till)).fetchone()
    if row:
        con.execute("UPDATE vendor_rates SET cost = ? WHERE id = ?", (cost, row["id"]))
        return "updated", start
    con.execute("INSERT INTO vendor_rates (product_id, vendor, cost, effective_from, valid_until) "
                "VALUES (?, ?, ?, ?, ?)", (pid, vendor, cost, start, till))
    return "added", start


def clean_name_unit(data):
    name = str(data.get("name") or "").strip()
    unit = str(data.get("unit") or "").strip()
    if not name:
        raise BadRequest("Enter a product name.")
    if not unit:
        raise BadRequest("Enter a unit, for example Nos, m or kg.")
    return name, unit


def product_exists(con, pid):
    return con.execute("SELECT 1 FROM products WHERE id = ?", (pid,)).fetchone() is not None


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/api/products")
def list_products():
    """?date=YYYY-MM-DD (optional, default today): rates as they apply on that day."""
    return jsonify(products_on(db(), day_param()))


@app.post("/api/products")
def create_product():
    """Body: {"name", "unit", "vendors": [{"vendor", "cost", "valid_until"}]}. Rates start today."""
    data = request.get_json(silent=True) or {}
    name, unit = clean_name_unit(data)
    t = today()

    rows, seen = [], set()
    for row in data.get("vendors") or []:
        vendor = str(row.get("vendor") or "").strip()
        cost, till = row.get("cost"), row.get("valid_until")
        if not vendor and cost in ("", None) and not till:
            continue  # ignore completely empty rows
        if not vendor:
            raise BadRequest("Pick or type a vendor for each price you enter.")
        cost = parse_cost(cost, vendor)
        if not till:
            raise BadRequest(f"Enter a valid-till date for {vendor}.")
        till = parse_day(till, f"valid-till for {vendor}")
        if till < t:
            raise BadRequest(f"The valid-till date for {vendor} has already passed.")
        if vendor.lower() in seen:
            raise BadRequest(f"{vendor} is listed twice for this product.")
        seen.add(vendor.lower())
        rows.append((vendor, cost, till))
    if not rows:
        raise BadRequest("Add at least one vendor with a price and valid-till date.")

    con = db()
    try:
        pid = con.execute("INSERT INTO products (name, unit) VALUES (?, ?)", (name, unit)).lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error=f"A product named “{name}” already exists."), 409
    for vendor, cost, till in rows:
        add_rate(con, pid, canonical_vendor(con, vendor), cost, till, start=t)
    con.commit()
    return jsonify(id=pid), 201


@app.put("/api/products/<int:pid>")
def update_product(pid):
    """Body: {"name", "unit"}. Prices are changed by adding rate entries, not here."""
    name, unit = clean_name_unit(request.get_json(silent=True) or {})
    con = db()
    try:
        cur = con.execute("UPDATE products SET name = ?, unit = ? WHERE id = ?", (name, unit, pid))
    except sqlite3.IntegrityError:
        return jsonify(error=f"A product named “{name}” already exists."), 409
    if cur.rowcount == 0:
        return jsonify(error="Product not found."), 404
    con.commit()
    return jsonify(id=pid)


@app.delete("/api/products/<int:pid>")
def delete_product(pid):
    con = db()
    con.execute("DELETE FROM products WHERE id = ?", (pid,))
    con.commit()
    return jsonify(ok=True)


@app.get("/api/products/<int:pid>/rates")
def product_rates(pid):
    """Full rate history of one product. ?date= sets the day each entry's status is worked out for."""
    con = db()
    if not product_exists(con, pid):
        return jsonify(error="Product not found."), 404
    return jsonify(history_of(con, pid, day_param()))


@app.post("/api/products/<int:pid>/rates")
def create_rate(pid):
    """
    Body: {"vendor", "cost", "valid_until",
           "replace_current": false,   (true = start today, replacing the vendor's current rate)
           "effective_from": optional  (set the start date yourself instead)}
    """
    con = db()
    if not product_exists(con, pid):
        return jsonify(error="Product not found."), 404
    data = request.get_json(silent=True) or {}
    vendor = str(data.get("vendor") or "").strip()
    if not vendor:
        raise BadRequest("Select or type a vendor.")
    vendor = canonical_vendor(con, vendor)
    cost = parse_cost(data.get("cost"), vendor)
    till = parse_day(data["valid_until"], "valid-till") if data.get("valid_until") else None
    start = parse_day(data["effective_from"], "start") if data.get("effective_from") else None
    action, start = add_rate(con, pid, vendor, cost, till,
                             replace=bool(data.get("replace_current")), start=start)
    con.commit()
    return jsonify(action=action, effective_from=start, valid_until=till), 201 if action == "added" else 200


@app.delete("/api/rates/<int:rate_id>")
def delete_rate(rate_id):
    con = db()
    con.execute("DELETE FROM vendor_rates WHERE id = ?", (rate_id,))
    con.commit()
    return jsonify(ok=True)


@app.get("/api/vendors")
def list_vendors():
    rows = db().execute("SELECT DISTINCT vendor FROM vendor_rates ORDER BY vendor COLLATE NOCASE")
    return jsonify([r["vendor"] for r in rows])


init_db()

if __name__ == "__main__":
    app.run(debug=True)