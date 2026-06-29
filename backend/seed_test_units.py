# seed_test_units.py
# LabStockV2 — Fix and expand critical_call_test_units table
#
# Run ONCE from C:\LabStockV2\backend\
#   python seed_test_units.py
#
# What it does:
#   1. Clears the old wrong seed (WBC and Platelets had g/dL — wrong)
#   2. Inserts the correct full test list for Haematology and Biochemistry

import sqlite3

DB_PATH = r"C:\QmsApp\lab_stock.db"

CORRECT_UNITS = [
    # Haematology
    ("Haematology", "Hb",           "g/dL"),
    ("Haematology", "WBC",          "× 10⁹/L"),
    ("Haematology", "Platelets",    "× 10⁹/L"),
    ("Haematology", "PT/INR",       "seconds / ratio"),
    ("Haematology", "APTT",         "seconds"),

    # Biochemistry
    ("Biochemistry", "Urea",              "mmol/L"),
    ("Biochemistry", "Creatinine",        "µmol/L"),
    ("Biochemistry", "Potassium",         "mmol/L"),
    ("Biochemistry", "Sodium",            "mmol/L"),
    ("Biochemistry", "Chloride",          "mmol/L"),
    ("Biochemistry", "Bilirubin Direct",  "mmol/L"),
    ("Biochemistry", "Bilirubin Total",   "mmol/L"),
]

def seed():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # Clear old seed entirely
    cur.execute("DELETE FROM critical_call_test_units")
    print("Cleared old test units.")

    # Insert correct data
    for dept, test, unit in CORRECT_UNITS:
        cur.execute(
            "INSERT INTO critical_call_test_units (working_department, test_name, unit) VALUES (?, ?, ?)",
            (dept, test, unit)
        )
        print(f"  ✓  {dept:<14} {test:<22} → {unit}")

    conn.commit()
    conn.close()
    print(f"\nDone. {len(CORRECT_UNITS)} test units seeded correctly.")

if __name__ == "__main__":
    seed()
