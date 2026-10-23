# Rate Master: simple ERP for products, vendor rates and dummy BOQs

Two files do all the work:

| File | What it is |
|---|---|
| `app.py` | Backend: Flask + SQLite. Creates `erp.db` next to itself on first run. |
| `index.html` | Frontend: one plain HTML page (no build step, no framework). |
| `seed.py` | Optional: loads 14 dummy vaporizer components with made-up vendor rates. |

## Run it

```bash
pip install flask
python seed.py        # optional, adds dummy data
python app.py
```

Open http://127.0.0.1:5000

## What you can do

**Products & rates tab**
- Add a product with its name, unit and **any number of vendors**, each with its own cost per unit.
- Edit or delete products. The lowest vendor rate is highlighted.
- Search by product, unit or vendor name.

**BOQ tab**
- Type a product name, enter the quantity, add it to the BOQ.
- Choose the rate to use for the whole BOQ: lowest, average or highest vendor rate.
- Override the vendor on any single line.
- Download the BOQ as CSV (opens in Excel) or print it.
- The BOQ you're building is kept in the browser, so a page refresh doesn't lose it.

## Data model

```
products      (id, name UNIQUE, unit)
vendor_rates  (id, product_id → products.id, vendor, cost)
```

One product → many vendor rates. Deleting a product deletes its rates.

## API (for scripts)

| Method | URL | Body / notes |
|---|---|---|
| GET | `/api/products` | All products with their vendor rates, cheapest first |
| POST | `/api/products` | `{"name": "...", "unit": "Nos", "vendors": [{"vendor": "ABC", "cost": 1200}]}` |
| PUT | `/api/products/<id>` | Same body as POST; replaces the product's vendor list |
| DELETE | `/api/products/<id>` | |
| GET | `/api/vendors` | Distinct vendor names |
| POST | `/api/boq` | See below |

### Generating a BOQ from Python (e.g. from component counts)

Items can be matched by `name` (case-insensitive) or `product_id`. Add `"vendor"` to force a specific vendor on a line.

```python
import requests

counts = {"Ball valve 1in SS316 cryogenic": 4, "Pressure gauge 0-40 bar 100 mm dial": 2}

boq = requests.post("http://127.0.0.1:5000/api/boq", json={
    "basis": "lowest",   # or "average" / "highest"
    "items": [{"name": n, "qty": q} for n, q in counts.items()],
}).json()

for line in boq["lines"]:
    print(line["sno"], line["name"], line["qty"], line["unit"], line["rate"], line["vendor"], line["amount"])
print("Total:", boq["total"])
print("Not found in ERP:", boq["unmatched"])
```

Names that don't match any product come back in `unmatched` instead of failing the whole BOQ, so you can see which components still need rates.
