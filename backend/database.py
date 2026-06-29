# database.py
# LabStockV2 — Shared Database Connection
# This is the ONE tap that all modules use to reach lab_stock.db
# Never import sqlite3 directly in other modules — always use get_db() from here.

import sqlite3
from pathlib import Path

# ── WHERE IS THE DATABASE? ────────────────────────────────────
# Pointing to the dev sandbox. Switch to C:\LabStack\lab_stock.db for live.
DB_PATH = r"C:\QmsApp\lab_stock.db"

# ── THE TAP ──────────────────────────────────────────────────
def get_db():
    """
    Open and return a SQLite connection with WAL mode enabled.
    WAL = Write-Ahead Logging. Think of it like a queue at the lab reception:
    multiple people can read at the same time, and writes wait their turn
    instead of locking everyone out. This is what makes the database safe
    for concurrent access.

    Call this at the start of any function that needs the database.
    Close it when done — or use Python's 'with' statement.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["full_name"]
    return conn
