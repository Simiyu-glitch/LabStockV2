# seed_pins.py
# LabStockV2 — One-Time PIN Seeding Script
#
# Run this ONCE from C:\LabStockV2\backend\
# It hashes every staff password and writes it into lab_stock.db
#
# After running this, the STAFF dict in lab_app.py is no longer
# the source of login truth. The database owns it.
#
# Command to run:
#   cd C:\LabStockV2\backend
#   python seed_pins.py

import sqlite3
import hashlib

# ── CONFIG ───────────────────────────────────────────────────
DB_PATH = r"C:\QmsApp\lab_stock.db"

# ── STAFF PASSWORDS (from lab_app.py STAFF dict) ─────────────
# familiar_name → plain password
# These are the CURRENT passwords. After seeding, change them
# through the app — never edit this file again.
STAFF_PASSWORDS = {
    "Dr Opuba":  "1111",
    "Nicholas":  "8888",
    "Milka":     "2423",
    "Enock":     "1819",
    "Emmanuel":  "5657",
    "Mercy":     "1902",
    "Chebet":    "1919",
    "Anthony":   "4050",
    "Rebecca":   "8081",
    "Nancy":     "2020",
    "Erick":     "4142",
    "Wairia":    "8901",
    "Pauline":   "2010",
    "Francis":   "2024",
    "Juma":      "1516",
    "Paul":      "0202",
    "Mourine":   "2221",
    "Grace":     "6789",
    "Stanley":   "3331",
}

# ── HASHING FUNCTION ─────────────────────────────────────────
def hash_pin(plain_password: str) -> str:
    """
    Hash a plain password using SHA-256.
    Like a blender — you can't unblend it.
    Input:  "4142"
    Output: "a665a45920422f9d417e4867efdc4fb8a04a1f3fff1fa07e998e86f7f7a27ae3"
    (that long string is stored, never the original "4142")
    """
    return hashlib.sha256(plain_password.encode()).hexdigest()

# ── MAIN SEEDING LOGIC ───────────────────────────────────────
def seed():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # Detect which column is the primary key (staff_id or id)
    cols = [r[1] for r in cur.execute("PRAGMA table_info(staff)").fetchall()]
    pk_col = "staff_id" if "staff_id" in cols else "id"

    print(f"Connected to: {DB_PATH}")
    print(f"Primary key column: {pk_col}")
    print("-" * 50)

    seeded = 0
    not_found = []

    for familiar_name, plain_password in STAFF_PASSWORDS.items():
        hashed = hash_pin(plain_password)

        # Find by familiar_name first, then fall back to full_name
        row = cur.execute(
            f"SELECT {pk_col} FROM staff WHERE familiar_name = ? OR full_name = ?",
            (familiar_name, familiar_name)
        ).fetchone()

        if row:
            staff_id = row[0]
            cur.execute(
                f"UPDATE staff SET pin_hash = ? WHERE {pk_col} = ?",
                (hashed, staff_id)
            )
            print(f"  ✓  {familiar_name:<12}  →  hash written")
            seeded += 1
        else:
            not_found.append(familiar_name)
            print(f"  ✗  {familiar_name:<12}  →  NOT FOUND in staff table")

    conn.commit()
    conn.close()

    print("-" * 50)
    print(f"Done. {seeded} staff hashed successfully.")
    if not_found:
        print(f"WARNING — these names were not found in the DB: {not_found}")
        print("They may need to be added to the staff table first.")

if __name__ == "__main__":
    seed()
