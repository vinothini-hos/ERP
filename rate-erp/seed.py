"""
Optional: fill the ERP with dummy vaporizer components and vendor rates.
All names and prices below are made up, for testing BOQ generation only.

Run once:  python seed.py
Products that already exist (same name) are skipped, so it's safe to re-run.
"""
from app import connect, init_db

DUMMY_PRODUCTS = [
    # (product name, unit, [(vendor, cost per unit in INR), ...])
    ("Ambient air vaporizer finned tube 6 m", "Nos", [("Vel Steel Traders", 48500), ("Chennai Industrial Supplies", 51200), ("Kovai Fabricators", 46900)]),
    ("Ball valve 1in SS316 cryogenic", "Nos", [("Sri Balaji Valves", 7800), ("Coimbatore Flow Controls", 8250)]),
    ("Globe valve 2in SS304 cryogenic", "Nos", [("Sri Balaji Valves", 14200), ("Coimbatore Flow Controls", 13650), ("Chennai Industrial Supplies", 15100)]),
    ("Check valve 1in SS316", "Nos", [("Coimbatore Flow Controls", 4600), ("Sri Balaji Valves", 4950)]),
    ("Pressure safety valve 1/2in", "Nos", [("Precision Instruments Co.", 6900), ("Sri Balaji Valves", 7250)]),
    ("Pressure gauge 0-40 bar 100 mm dial", "Nos", [("Precision Instruments Co.", 1850), ("Chennai Industrial Supplies", 2100)]),
    ("Temperature transmitter PT100", "Nos", [("Precision Instruments Co.", 9400), ("Kovai Automation", 8800)]),
    ("SS304 pipe 2in Sch 10", "m", [("Vel Steel Traders", 1320), ("Chennai Industrial Supplies", 1410)]),
    ("SS304 elbow 2in 90 deg", "Nos", [("Vel Steel Traders", 520), ("Chennai Industrial Supplies", 495)]),
    ("Flange 2in #300 WNRF SS304", "Nos", [("Vel Steel Traders", 2650), ("Kovai Fabricators", 2780)]),
    ("Spiral wound gasket 2in #300", "Nos", [("Chennai Industrial Supplies", 340), ("Coimbatore Flow Controls", 365)]),
    ("Stud bolt set 5/8in x 100 mm", "Set", [("Chennai Industrial Supplies", 210), ("Vel Steel Traders", 195)]),
    ("MS structural frame ISMC 100", "kg", [("Kovai Fabricators", 92), ("Vel Steel Traders", 88)]),
    ("Epoxy painting", "m²", [("Kovai Fabricators", 260)]),
]


def main():
    init_db()
    added = 0
    with connect() as con:
        for name, unit, vendors in DUMMY_PRODUCTS:
            if con.execute("SELECT 1 FROM products WHERE name = ?", (name,)).fetchone():
                continue
            pid = con.execute("INSERT INTO products (name, unit) VALUES (?, ?)", (name, unit)).lastrowid
            con.executemany("INSERT INTO vendor_rates (product_id, vendor, cost) VALUES (?, ?, ?)",
                            [(pid, v, c) for v, c in vendors])
            added += 1
    print(f"Added {added} dummy products ({len(DUMMY_PRODUCTS) - added} already existed).")


if __name__ == "__main__":
    main()
