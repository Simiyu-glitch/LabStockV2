# rota_api.py — Rota Module API Endpoints
# LabStockV2 · FastAPI backend
#
# Endpoints in this file:
#   GET  /rota/staff-pool          — all schedulable staff + capabilities + balances
#   PUT  /rota/staff-pool          — save pool, capabilities, balances for all staff
#   GET  /rota/departures          — list of departed staff
#   POST /rota/departures          — record a new departure
#   POST /rota/migrate/weekend-col — one-time migration: add can_work_weekend column

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import sqlite3
from datetime import datetime

from database import get_db

router = APIRouter(prefix="/rota", tags=["rota"])

EXCLUDED_FROM_ROTA = [
    "manager", "qa", "director", "housekeeper", "phlebotomist"
]

HOD_ROLES = ["hod"]   # HODs can never work MCH — enforced here too


# ==================================================================
# HELPERS
# ==================================================================

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pk(conn: sqlite3.Connection) -> str:
    """Detect whether the staff table uses 'id' or 'staff_id' as PK."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    return "staff_id" if "staff_id" in cols else "id"


def _name_col(conn: sqlite3.Connection) -> str:
    """Detect which column holds the display name."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    if "familiar_name" in cols:
        return "familiar_name"
    if "full_name" in cols:
        return "full_name"
    return "name"


def _ensure_weekend_column(conn: sqlite3.Connection):
    """
    Add can_work_weekend column if it does not exist yet.
    Safe to call on every request — checks first, only adds if missing.
    Defaults to 1 (everyone eligible) so no existing staff loses capability.
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    if "can_work_weekend" not in cols:
        conn.execute(
            "ALTER TABLE staff ADD COLUMN can_work_weekend INTEGER DEFAULT 1"
        )
        conn.commit()


def _ensure_departures_table(conn: sqlite3.Connection):
    """
    Create staff_departures table if it does not exist yet.
    Safe to call on every request — IF NOT EXISTS means no harm done.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff_departures (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id        INTEGER NOT NULL,
            last_working_day TEXT NOT NULL,
            reason          TEXT NOT NULL,
            notes           TEXT,
            recorded_by     TEXT NOT NULL,
            recorded_at     TEXT NOT NULL
        )
    """)
    conn.commit()


# ==================================================================
# SCHEMAS
# ==================================================================

class StaffPoolEntry(BaseModel):
    id: int
    in_pool: bool
    can_work_7am:     bool
    can_work_8am:     bool
    can_work_10am:    bool
    can_work_mch:     bool
    can_work_4pm:     bool
    can_work_night:   bool
    can_work_weekend: bool
    annual_leave_total:  int
    annual_leave_remaining: int
    ph_earned:    int
    ph_remaining: int


class SavePoolRequest(BaseModel):
    staff: List[StaffPoolEntry]


class DepartureCreate(BaseModel):
    staff_id: int
    last_working_day: str       # "YYYY-MM-DD"
    reason: str                 # "Resigned" | "Transferred" | "Contract ended" | "Other"
    notes: Optional[str] = None
    recorded_by: str            # name of manager saving this


# ==================================================================
# GET /rota/staff-pool
# Returns all staff eligible to appear in rota setup
# (excludes manager, qa, director, housekeeper, phlebotomist)
# Also returns departed staff IDs so frontend can grey them out
# ==================================================================

@router.get("/staff-pool")
def get_staff_pool(conn: sqlite3.Connection = Depends(get_db)):
    _ensure_weekend_column(conn)
    _ensure_departures_table(conn)

    pk   = _pk(conn)
    nm   = _name_col(conn)

    # Fetch all active staff excluding non-schedulable roles
    placeholders = ",".join("?" * len(EXCLUDED_FROM_ROTA))
    rows = conn.execute(
        f"""
        SELECT * FROM staff
        WHERE is_active = 1
          AND role NOT IN ({placeholders})
        ORDER BY {nm}
        """,
        EXCLUDED_FROM_ROTA
    ).fetchall()

    # Also fetch departed staff IDs for this call
    departed_ids = {
        r[0] for r in conn.execute(
            "SELECT staff_id FROM staff_departures"
        ).fetchall()
    }

    staff_list = []
    for r in rows:
        d = dict(r)
        sid = d.get(pk) or d.get("id")
        name = d.get(nm) or d.get("name") or ""
        role = d.get("role", "")

        # HODs can NEVER work MCH — enforce at API level regardless of DB value
        mch_eligible = False if role.lower() == "hod" else bool(d.get("can_work_mch", 1))

        # annual_leave_taken is what has been used; remaining = total - taken
        annual_total     = int(d.get("annual_leave_total", 24) or 24)
        annual_taken     = int(d.get("annual_leave_taken", 0) or 0)
        annual_remaining = annual_total - annual_taken

        ph_earned    = int(d.get("ph_earned", 0) or 0)
        ph_taken     = int(d.get("ph_taken",  0) or 0)
        ph_remaining = ph_earned - ph_taken

        staff_list.append({
            "id":            sid,
            "name":          name,
            "role":          role,
            "is_departed":   sid in departed_ids,
            "in_pool":       bool(d.get("in_rota_pool", 0)),
            "can_work_7am":     bool(d.get("can_work_7am", 1)),
            "can_work_8am":     bool(d.get("can_work_8am", 1)),
            "can_work_10am":    bool(d.get("can_work_10am", 1)),
            "can_work_mch":     mch_eligible,
            "can_work_4pm":     bool(d.get("can_work_4pm", 1)),
            "can_work_night":   bool(d.get("can_work_night", 1)),
            "can_work_weekend": bool(d.get("can_work_weekend", 1)),
            # balances
            "annual_leave_total":     annual_total,
            "annual_leave_remaining": annual_remaining,
            "ph_earned":    ph_earned,
            "ph_remaining": ph_remaining,
        })

    return {"staff": staff_list}


# ==================================================================
# PUT /rota/staff-pool
# Save pool toggles + capabilities + balances for all staff in one go
# Milka clicks "Save setup" — one call saves everything
# ==================================================================

@router.put("/staff-pool")
def save_staff_pool(
    body: SavePoolRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_weekend_column(conn)

    pk = _pk(conn)

    for entry in body.staff:
        sid = entry.id

        # Fetch role to enforce HOD/MCH rule server-side
        row = conn.execute(
            f"SELECT role FROM staff WHERE {pk} = ?", (sid,)
        ).fetchone()
        if not row:
            continue

        role = dict(row).get("role", "")
        # HODs can never work MCH — ignore whatever the frontend sent
        mch_value = 0 if role.lower() == "hod" else int(entry.can_work_mch)

        # annual_leave_taken = total - remaining  (we store taken, not remaining)
        annual_taken = entry.annual_leave_total - entry.annual_leave_remaining
        annual_taken = max(0, annual_taken)   # clamp — never negative

        ph_taken = entry.ph_earned - entry.ph_remaining
        ph_taken = max(0, ph_taken)

        conn.execute(
            f"""
            UPDATE staff SET
                in_rota_pool      = ?,
                can_work_7am      = ?,
                can_work_8am      = ?,
                can_work_10am     = ?,
                can_work_mch      = ?,
                can_work_4pm      = ?,
                can_work_night    = ?,
                can_work_weekend  = ?,
                annual_leave_total = ?,
                annual_leave_taken = ?,
                ph_earned         = ?,
                ph_taken          = ?
            WHERE {pk} = ?
            """,
            (
                int(entry.in_pool),
                int(entry.can_work_7am),
                int(entry.can_work_8am),
                int(entry.can_work_10am),
                mch_value,
                int(entry.can_work_4pm),
                int(entry.can_work_night),
                int(entry.can_work_weekend),
                entry.annual_leave_total,
                annual_taken,
                entry.ph_earned,
                ph_taken,
                sid,
            )
        )

    conn.commit()
    return {"status": "saved", "count": len(body.staff)}


# ==================================================================
# GET /rota/departures
# Returns all recorded departures with staff name included
# ==================================================================

@router.get("/departures")
def get_departures(conn: sqlite3.Connection = Depends(get_db)):
    _ensure_departures_table(conn)

    pk = _pk(conn)
    nm = _name_col(conn)

    rows = conn.execute(
        f"""
        SELECT
            d.id,
            d.staff_id,
            s.{nm}          AS staff_name,
            s.role          AS staff_role,
            d.last_working_day,
            d.reason,
            d.notes,
            d.recorded_by,
            d.recorded_at
        FROM staff_departures d
        LEFT JOIN staff s ON s.{pk} = d.staff_id
        ORDER BY d.last_working_day DESC
        """
    ).fetchall()

    return {"departures": [dict(r) for r in rows]}


# ==================================================================
# POST /rota/departures
# Record a new staff departure
# Marks the staff member as inactive in the staff table
# ==================================================================

@router.post("/departures")
def record_departure(
    body: DepartureCreate,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_departures_table(conn)

    pk = _pk(conn)

    # Check staff exists
    row = conn.execute(
        f"SELECT {pk} FROM staff WHERE {pk} = ?", (body.staff_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Staff member not found")

    # Check not already departed
    existing = conn.execute(
        "SELECT id FROM staff_departures WHERE staff_id = ?", (body.staff_id,)
    ).fetchone()
    if existing:
        raise HTTPException(
            status_code=400,
            detail="This staff member already has a departure record"
        )

    # Validate reason
    valid_reasons = ["Resigned", "Transferred", "Contract ended", "Other"]
    if body.reason not in valid_reasons:
        raise HTTPException(status_code=400, detail=f"Reason must be one of: {valid_reasons}")

    # Insert departure record
    conn.execute(
        """
        INSERT INTO staff_departures
            (staff_id, last_working_day, reason, notes, recorded_by, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            body.staff_id,
            body.last_working_day,
            body.reason,
            body.notes or "",
            body.recorded_by,
            _now(),
        )
    )

    # Mark staff as inactive so they disappear from all active queries
    conn.execute(
        f"UPDATE staff SET is_active = 0, in_rota_pool = 0 WHERE {pk} = ?",
        (body.staff_id,)
    )

    conn.commit()
    return {"status": "recorded", "staff_id": body.staff_id}
