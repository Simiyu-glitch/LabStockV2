# clean_test_leave_requests.py
# Run this ONCE from C:\LabStockV2\backend\ to wipe all test leave requests
# Usage: python clean_test_leave_requests.py
#
# This deletes ALL records from leave_requests — safe to do during dev
# Do NOT run this on the live production database

import sqlite3

DB_PATH = r"C:\QmsApp\lab_stock.db"   # dev sandbox

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Show what we are about to delete
rows = conn.execute("SELECT COUNT(*) as cnt FROM leave_requests").fetchone()
print(f"Found {rows['cnt']} leave request(s) to delete.")

details = conn.execute(
    "SELECT staff_id, type, start_date, end_date, status FROM leave_requests ORDER BY created_at"
).fetchall()
for r in details:
    print(f"  [{r['status']}] staff_id={r['staff_id']} {r['type']} {r['start_date']} → {r['end_date']}")

confirm = input("\nType YES to delete all of these: ")
if confirm.strip().upper() == "YES":
    conn.execute("DELETE FROM leave_requests")
    conn.commit()
    print("Done — all test leave requests cleared.")
else:
    print("Cancelled — nothing deleted.")

conn.close()
