# leave_api.py — Leave Request API Endpoints
# LabStockV2 · FastAPI backend
#
# Endpoints:
#   GET  /leave/public-holidays          — list PHs for a given month
#   GET  /leave/my-requests              — staff's own leave history
#   GET  /leave/balance/{staff_id}       — current leave balances
#   POST /leave/calculate-days           — count working days in a range
#   POST /leave/submit                   — staff submits a leave request
#   GET  /leave/pending                  — Milka's approval queue
#   POST /leave/approve/{request_id}     — Milka approves
#   POST /leave/reject/{request_id}      — Milka rejects

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3
from datetime import date, datetime, timedelta

from database import get_db

router = APIRouter(prefix="/leave", tags=["leave"])

# Leave types exactly as stored in DB
LEAVE_TYPES = ["Annual Leave", "PH off day", "Sick", "Special Off"]

# Which types need Director approval after Milka
NEEDS_DIRECTOR = ["Annual Leave"]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pk(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    return "staff_id" if "staff_id" in cols else "id"


def _name_col(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    if "familiar_name" in cols:
        return "familiar_name"
    if "full_name" in cols:
        return "full_name"
    return "name"


def _ensure_leave_table(conn):
    """Create leave_requests table if it doesn't exist yet."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leave_requests (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id          INTEGER NOT NULL,
            start_date        TEXT NOT NULL,
            end_date          TEXT NOT NULL,
            type              TEXT NOT NULL,
            working_days      INTEGER DEFAULT 0,
            status            TEXT DEFAULT 'Pending Manager',
            note              TEXT,
            requested_by      TEXT,
            milka_approved_at TEXT,
            opuba_approved_at TEXT,
            rejected_by       TEXT,
            rejected_reason   TEXT,
            created_at        TEXT,
            FOREIGN KEY (staff_id) REFERENCES staff(id)
        )
    """)
    conn.commit()


def _get_ph_dates_for_range(conn, start: date, end: date) -> set:
    """Return set of date strings that are public holidays in range."""
    rows = conn.execute(
        """
        SELECT holiday_date FROM public_holidays
        WHERE holiday_date BETWEEN ? AND ?
        """,
        (start.isoformat(), end.isoformat())
    ).fetchall()
    return {r[0] for r in rows}


def _count_working_days(start: date, end: date, ph_dates: set) -> int:
    """
    Count working days between start and end inclusive.
    Rules:
      - Sundays never count
      - Public holidays never count
      - Saturdays DO count (lab works Saturdays)
    """
    count = 0
    current = start
    while current <= end:
        # 6 = Sunday in Python's weekday() where Monday=0
        if current.weekday() != 6 and current.isoformat() not in ph_dates:
            count += 1
        current += timedelta(days=1)
    return count


# ==================================================================
# SCHEMAS
# ==================================================================

class CalculateDaysRequest(BaseModel):
    start_date: str   # "YYYY-MM-DD"
    end_date:   str   # "YYYY-MM-DD"


class SubmitLeaveRequest(BaseModel):
    staff_id:   int
    start_date: str
    end_date:   str
    leave_type: str
    note:       Optional[str] = None
    requested_by: str         # name of person submitting


class RejectRequest(BaseModel):
    rejected_by:     str
    rejected_reason: str


# ==================================================================
# GET /leave/public-holidays?year=2026&month=6
# Returns PHs for a given month so the calendar can grey them out
# ==================================================================

@router.get("/public-holidays")
def get_public_holidays(
    year: int,
    month: int,
    conn: sqlite3.Connection = Depends(get_db)
):
    import calendar
    _, last_day = calendar.monthrange(year, month)
    month_start = f"{year}-{month:02d}-01"
    month_end   = f"{year}-{month:02d}-{last_day:02d}"

    rows = conn.execute(
        """
        SELECT holiday_date, holiday_name
        FROM public_holidays
        WHERE holiday_date BETWEEN ? AND ?
        ORDER BY holiday_date
        """,
        (month_start, month_end)
    ).fetchall()

    return {"holidays": [dict(r) for r in rows]}


# ==================================================================
# GET /leave/balance/{staff_id}
# Returns current annual leave and PH balances for a staff member
# ==================================================================

@router.get("/balance/{staff_id}")
def get_balance(
    staff_id: int,
    conn: sqlite3.Connection = Depends(get_db)
):
    pk = _pk(conn)
    nm = _name_col(conn)

    row = conn.execute(
        f"SELECT * FROM staff WHERE {pk} = ?", (staff_id,)
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Staff not found")

    d = dict(row)
    annual_total     = int(d.get("annual_leave_total", 24) or 24)
    annual_taken     = int(d.get("annual_leave_taken", 0)  or 0)
    annual_remaining = annual_total - annual_taken

    ph_earned    = int(d.get("ph_earned", 0) or 0)
    ph_taken     = int(d.get("ph_taken",  0) or 0)
    ph_remaining = ph_earned - ph_taken

    return {
        "staff_id":          staff_id,
        "name":              d.get(nm) or d.get("name", ""),
        "annual_total":      annual_total,
        "annual_taken":      annual_taken,
        "annual_remaining":  annual_remaining,
        "ph_earned":         ph_earned,
        "ph_taken":          ph_taken,
        "ph_remaining":      ph_remaining,
        "sick_days_taken":   int(d.get("sick_days_taken", 0) or 0),
    }


# ==================================================================
# POST /leave/calculate-days
# Given start and end date, return working days count + PH list
# Used by frontend to update counter in real time as user picks
# ==================================================================

@router.post("/calculate-days")
def calculate_days(
    body: CalculateDaysRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    try:
        start = date.fromisoformat(body.start_date)
        end   = date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if end < start:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    ph_dates    = _get_ph_dates_for_range(conn, start, end)
    working_days = _count_working_days(start, end, ph_dates)

    # Count Sundays in range for display
    total_days = (end - start).days + 1
    sundays    = sum(
        1 for i in range(total_days)
        if (start + timedelta(days=i)).weekday() == 6
    )

    return {
        "start_date":    body.start_date,
        "end_date":      body.end_date,
        "working_days":  working_days,
        "total_days":    total_days,
        "sundays":       sundays,
        "ph_count":      len(ph_dates),
        "ph_dates":      sorted(list(ph_dates)),
    }


# ==================================================================
# GET /leave/my-requests?staff_id=3
# Returns all leave requests for a specific staff member
# ==================================================================

@router.get("/my-requests")
def get_my_requests(
    staff_id: int,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)

    rows = conn.execute(
        """
        SELECT * FROM leave_requests
        WHERE staff_id = ?
        ORDER BY created_at DESC
        """,
        (staff_id,)
    ).fetchall()

    return {"requests": [dict(r) for r in rows]}


# ==================================================================
# POST /leave/submit
# Staff member submits a leave request
# ==================================================================

@router.post("/submit")
def submit_leave(
    body: SubmitLeaveRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)

    # Validate dates
    try:
        start = date.fromisoformat(body.start_date)
        end   = date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if end < start:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    # Validate leave type
    if body.leave_type not in LEAVE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Leave type must be one of: {LEAVE_TYPES}"
        )

    # Check for overlapping approved/pending requests for this staff
    overlap = conn.execute(
        """
        SELECT id FROM leave_requests
        WHERE staff_id = ?
          AND status NOT IN ('Rejected')
          AND NOT (end_date < ? OR start_date > ?)
        """,
        (body.staff_id, body.start_date, body.end_date)
    ).fetchone()

    if overlap:
        raise HTTPException(
            status_code=409,
            detail="You already have a leave request overlapping these dates."
        )

    # Count working days
    ph_dates     = _get_ph_dates_for_range(conn, start, end)
    working_days = _count_working_days(start, end, ph_dates)

    # Determine initial status
    # Annual Leave → Pending Manager (Milka approves first, then Dr Opuba)
    # All others   → Pending Manager (Milka approves, done)
    initial_status = "Pending Manager"

    conn.execute(
        """
        INSERT INTO leave_requests
            (staff_id, start_date, end_date, type, working_days,
             status, note, requested_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            body.staff_id,
            body.start_date,
            body.end_date,
            body.leave_type,
            working_days,
            initial_status,
            body.note or "",
            body.requested_by,
            _now(),
        )
    )
    conn.commit()

    request_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {
        "status":       "submitted",
        "request_id":   request_id,
        "working_days": working_days,
        "needs_director": body.leave_type in NEEDS_DIRECTOR,
    }


# ==================================================================
# GET /leave/pending
# Milka's approval queue — all pending requests across all staff
# ==================================================================

@router.get("/pending")
def get_pending(
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)

    rows = conn.execute(
        f"""
        SELECT
            lr.*,
            s.{nm} AS staff_name,
            s.role  AS staff_role
        FROM leave_requests lr
        LEFT JOIN staff s ON s.{pk} = lr.staff_id
        WHERE lr.status = 'Pending Manager'
        ORDER BY lr.created_at ASC
        """,
    ).fetchall()

    return {"pending": [dict(r) for r in rows]}


# ==================================================================
# POST /leave/approve/{request_id}
# Milka approves a leave request
# Annual Leave → moves to "Pending Director"
# All others   → moves to "Approved", balance deducted immediately
# ==================================================================

@router.post("/approve/{request_id}")
def approve_leave(
    request_id: int,
    approved_by: str,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)
    pk = _pk(conn)

    row = conn.execute(
        "SELECT * FROM leave_requests WHERE id = ?", (request_id,)
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    req = dict(row)

    if req["status"] != "Pending Manager":
        raise HTTPException(
            status_code=400,
            detail=f"Request is already {req['status']}"
        )

    leave_type   = req["type"]
    working_days = req.get("working_days", 0) or 0

    if leave_type in NEEDS_DIRECTOR:
        # Annual Leave — forward to Dr Opuba
        conn.execute(
            """
            UPDATE leave_requests
            SET status = 'Pending Director', milka_approved_at = ?
            WHERE id = ?
            """,
            (_now(), request_id)
        )
        new_status = "Pending Director"
    else:
        # PH Off / Sick / Special — approve immediately and deduct balance
        conn.execute(
            """
            UPDATE leave_requests
            SET status = 'Approved', milka_approved_at = ?
            WHERE id = ?
            """,
            (_now(), request_id)
        )

        # Deduct from the right balance
        staff_id = req["staff_id"]
        if leave_type == "PH off day":
            conn.execute(
                f"UPDATE staff SET ph_taken = ph_taken + ? WHERE {pk} = ?",
                (working_days, staff_id)
            )
        elif leave_type == "Sick":
            conn.execute(
                f"UPDATE staff SET sick_days_taken = sick_days_taken + ? WHERE {pk} = ?",
                (working_days, staff_id)
            )
        # Special Off → no balance impact

        new_status = "Approved"

    conn.commit()

    return {
        "status":     "done",
        "new_status": new_status,
        "forwarded_to_director": leave_type in NEEDS_DIRECTOR,
    }


# ==================================================================
# POST /leave/reject/{request_id}
# Milka rejects a leave request with a reason
# ==================================================================

# ==================================================================
# GET /leave/my-blocked-dates?staff_id=3
# Returns date ranges already taken by THIS staff member
# (Approved or Pending) — calendar will block these out
# ==================================================================

@router.get("/my-blocked-dates")
def get_my_blocked_dates(
    staff_id: int,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)

    rows = conn.execute(
        """
        SELECT start_date, end_date, type, status
        FROM leave_requests
        WHERE staff_id = ?
          AND status NOT IN ('Rejected')
        ORDER BY start_date
        """,
        (staff_id,)
    ).fetchall()

    # Expand each range into individual blocked dates
    blocked = []
    for r in rows:
        d = date.fromisoformat(r["start_date"])
        end = date.fromisoformat(r["end_date"])
        while d <= end:
            blocked.append(d.isoformat())
            d += timedelta(days=1)

    return {"blocked_dates": list(set(blocked))}


# ==================================================================
# POST /leave/check-overlap
# Given a date range, check who else is already on leave
# Used to show overlap warning BEFORE submission
# Sick leave shows as count only — never reveals name to peers
# ==================================================================

class OverlapCheckRequest(BaseModel):
    staff_id:   int     # the person requesting — exclude from results
    start_date: str
    end_date:   str

@router.post("/check-overlap")
def check_overlap(
    body: OverlapCheckRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)

    # Find other staff with approved/pending leave overlapping this range
    rows = conn.execute(
        f"""
        SELECT
            lr.start_date,
            lr.end_date,
            lr.type,
            lr.status,
            s.{nm} AS staff_name
        FROM leave_requests lr
        LEFT JOIN staff s ON s.{pk} = lr.staff_id
        WHERE lr.staff_id != ?
          AND lr.status NOT IN ('Rejected')
          AND NOT (lr.end_date < ? OR lr.start_date > ?)
        ORDER BY lr.start_date
        """,
        (body.staff_id, body.start_date, body.end_date)
    ).fetchall()

    conflicts = []
    for r in rows:
        d = dict(r)
        is_sick = d["type"] in ("Sick", "Sick Leave", "SICK")
        conflicts.append({
            "start_date":  d["start_date"],
            "end_date":    d["end_date"],
            "type":        d["type"],
            "status":      d["status"],
            # Sick leave: never reveal name to peers
            "staff_name":  None if is_sick else d["staff_name"],
            "is_private":  is_sick,
        })

    return {
        "has_overlap":     len(conflicts) > 0,
        "conflict_count":  len(conflicts),
        "conflicts":       conflicts,
    }


# ==================================================================
# POST /leave/reject/{request_id}
# Milka rejects a leave request with a reason
# ==================================================================

@router.post("/reject/{request_id}")
def reject_leave(
    request_id: int,
    body: RejectRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)

    row = conn.execute(
        "SELECT id, status FROM leave_requests WHERE id = ?", (request_id,)
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    if dict(row)["status"] not in ("Pending Manager", "Pending Director"):
        raise HTTPException(status_code=400, detail="Request cannot be rejected in its current state")

    conn.execute(
        """
        UPDATE leave_requests
        SET status = 'Rejected',
            rejected_by = ?,
            rejected_reason = ?
        WHERE id = ?
        """,
        (body.rejected_by, body.rejected_reason, request_id)
    )
    conn.commit()

    return {"status": "rejected", "request_id": request_id}


# ==================================================================
# GET /leave/team-calendar?year=2026&month=6
# Returns all staff leave for a given month
# Used by the calendar to show names on dates
# Sick leave: shows as "Sick" with no name — privacy rule
# ==================================================================

@router.get("/team-calendar")
def get_team_calendar(
    year: int,
    month: int,
    conn: sqlite3.Connection = Depends(get_db)
):
    import calendar as cal_mod
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)

    _, last_day = cal_mod.monthrange(year, month)
    month_start = f"{year}-{month:02d}-01"
    month_end   = f"{year}-{month:02d}-{last_day:02d}"

    rows = conn.execute(
        f"""
        SELECT
            lr.id,
            lr.staff_id,
            lr.start_date,
            lr.end_date,
            lr.type,
            lr.status,
            lr.requested_by,
            s.{nm}           AS familiar_name
        FROM leave_requests lr
        LEFT JOIN staff s ON s.{pk} = lr.staff_id
        WHERE lr.status NOT IN ('Rejected')
          AND NOT (lr.end_date < ? OR lr.start_date > ?)
        ORDER BY lr.start_date
        """,
        (month_start, month_end)
    ).fetchall()

    # Build a dict: date_str -> list of entries
    day_map = {}

    for r in rows:
        d = dict(r)
        is_sick    = d["type"] in ("Sick", "Sick Leave", "SICK", "sick", "sick off", "Sick off", "Sick Off")
        start      = date.fromisoformat(d["start_date"])
        end        = date.fromisoformat(d["end_date"])

        # Clamp to this month
        clamp_start = max(start, date(year, month, 1))
        clamp_end   = min(end,   date(year, month, last_day))

        current = clamp_start
        while current <= clamp_end:
            iso = current.isoformat()
            if iso not in day_map:
                day_map[iso] = []
            day_map[iso].append({
                "staff_id":     d["staff_id"],
                "familiar_name": None if is_sick else (d["familiar_name"] or d.get("requested_by") or "Staff"),
                "type":         d["type"],
                "status":       d["status"],
                "is_private":   is_sick,
            })
            current += timedelta(days=1)

    return {"days": day_map}
