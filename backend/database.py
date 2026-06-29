# database.py
# LabStockV2 — Shared Database Connection
# This is the ONE tap that all modules use to reach lab_stock.db
# Never import sqlite3 directly in other modules — always use get_db() from here.

import sqlite3

# ── WHERE IS THE DATABASE? ────────────────────────────────────
# Pointing to the dev sandbox. Switch to C:\LabStock\lab_stock.db for live.
DB_PATH = r"C:\QmsApp\lab_stock.db"

# ── THE TAP ──────────────────────────────────────────────────
def get_db():
    """
    Open and return a SQLite connection with WAL mode enabled.
    WAL = Write-Ahead Logging. Think of it like a queue at the lab reception:
    multiple people can read at the same time, and writes wait their turn
    instead of locking everyone out.

    check_same_thread=False is required from Python 3.12+ because FastAPI
    runs each request in its own worker thread. SQLite's default strict
    thread-checking rejects connections created in a different thread.
    FastAPI manages concurrency safely so this flag is correct here.
    """
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["full_name"]
    return conn
