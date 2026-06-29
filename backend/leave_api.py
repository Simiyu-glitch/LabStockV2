# leave_api.py — Leave Request API Endpoints
# LabStockV2 · FastAPI backend
#
# BALANCE RULE (updated):
#   Annual Leave → deducted immediately on SUBMIT (not at Opuba approval)
#   Milka rejects → days credited back
#   Opuba rejects → days credited back
#   PH off        → deducted at Milka approval (unchanged)
#   Sick          → no deduction (unchanged)
#   Special Off   → no deduction (unchanged)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import sqlite3
from datetime import date, datetime, timedelta

from database import get_db

router = APIRouter(prefix="/leave", tags=["leave"])

LEAVE_TYPES    = ["Annual Leave", "PH off day", "Sick", "Special Off"]
NEEDS_DIRECTOR = ["Annual Leave"]


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _pk(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    return "staff_id" if "staff_id" in cols else "id"


def _name_col(conn):
    cols = [r[1] for r in conn.execute("PRAGMA table_info(staff)").fetchall()]
    if "familiar_name" in cols: return "familiar_name"
    if "full_name"     in cols: return "full_name"
    return "name"


def _ensure_leave_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS public_holidays (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            holiday_date TEXT NOT NULL UNIQUE,
            holiday_name TEXT NOT NULL
        )
    """)
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
    try:
        rows = conn.execute(
            "SELECT holiday_date FROM public_holidays WHERE holiday_date BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat())
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _count_working_days(start: date, end: date, ph_dates: set) -> int:
    """Mon–Sat inclusive, excluding Sundays and PHs."""
    count = 0
    current = start
    while current <= end:
        if current.weekday() != 6 and current.isoformat() not in ph_dates:
            count += 1
        current += timedelta(days=1)
    return count


# ==================================================================
# SCHEMAS
# ==================================================================

class CalculateDaysRequest(BaseModel):
    start_date: str
    end_date:   str

class SubmitLeaveRequest(BaseModel):
    staff_id:     int
    start_date:   str
    end_date:     str
    leave_type:   str
    note:         Optional[str] = None
    requested_by: str

class RejectRequest(BaseModel):
    rejected_by:     str
    rejected_reason: str

class OverlapCheckRequest(BaseModel):
    staff_id:   int
    start_date: str
    end_date:   str


# ==================================================================
# GET /leave/public-holidays
# ==================================================================

@router.get("/public-holidays")
def get_public_holidays(
    year:  int,
    month: int,
    conn:  sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)
    import calendar as cal_mod
    _, last_day = cal_mod.monthrange(year, month)
    month_start = f"{year}-{month:02d}-01"
    month_end   = f"{year}-{month:02d}-{last_day:02d}"
    try:
        rows = conn.execute(
            """SELECT holiday_date, holiday_name FROM public_holidays
               WHERE holiday_date BETWEEN ? AND ? ORDER BY holiday_date""",
            (month_start, month_end)
        ).fetchall()
        return {"holidays": [dict(r) for r in rows]}
    except Exception:
        return {"holidays": []}


# ==================================================================
# GET /leave/balance/{staff_id}
# ==================================================================

@router.get("/balance/{staff_id}")
def get_balance(
    staff_id: int,
    conn: sqlite3.Connection = Depends(get_db)
):
    pk = _pk(conn)
    nm = _name_col(conn)

    row = conn.execute(f"SELECT * FROM staff WHERE {pk} = ?", (staff_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Staff not found")

    d = dict(row)
    annual_total     = int(d.get("annual_leave_total", 24) or 24)
    annual_taken     = int(d.get("annual_leave_taken", 0)  or 0)
    annual_remaining = annual_total - annual_taken
    ph_earned        = int(d.get("ph_earned", 0) or 0)
    ph_taken         = int(d.get("ph_taken",  0) or 0)
    ph_remaining     = ph_earned - ph_taken

    return {
        "staff_id":         staff_id,
        "name":             d.get(nm) or d.get("name", ""),
        "annual_total":     annual_total,
        "annual_taken":     annual_taken,
        "annual_remaining": annual_remaining,
        "ph_earned":        ph_earned,
        "ph_taken":         ph_taken,
        "ph_remaining":     ph_remaining,
        "sick_days_taken":  int(d.get("sick_days_taken", 0) or 0),
    }


# ==================================================================
# POST /leave/calculate-days
# ==================================================================

@router.post("/calculate-days")
def calculate_days(
    body: CalculateDaysRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)
    try:
        start = date.fromisoformat(body.start_date)
        end   = date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if end < start:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    ph_dates     = _get_ph_dates_for_range(conn, start, end)
    working_days = _count_working_days(start, end, ph_dates)
    total_days   = (end - start).days + 1
    sundays      = sum(
        1 for i in range(total_days)
        if (start + timedelta(days=i)).weekday() == 6
    )
    return {
        "start_date":   body.start_date,
        "end_date":     body.end_date,
        "working_days": working_days,
        "total_days":   total_days,
        "sundays":      sundays,
        "ph_count":     len(ph_dates),
        "ph_dates":     sorted(list(ph_dates)),
    }


# ==================================================================
# GET /leave/my-requests
# ==================================================================

@router.get("/my-requests")
def get_my_requests(staff_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _ensure_leave_table(conn)
    rows = conn.execute(
        "SELECT * FROM leave_requests WHERE staff_id = ? ORDER BY created_at DESC",
        (staff_id,)
    ).fetchall()
    return {"requests": [dict(r) for r in rows]}


# ==================================================================
# POST /leave/submit
#
# NEW BALANCE RULE:
#   Annual Leave → deduct annual_leave_taken immediately on submit
#   PH off       → deduct ph_taken immediately on submit
#   Sick/Special → no deduction ever (ghost hours only)
#
# If Milka or Opuba rejects → reject endpoint credits days back
# ==================================================================

@router.post("/submit")
def submit_leave(
    body: SubmitLeaveRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)
    pk = _pk(conn)

    try:
        start = date.fromisoformat(body.start_date)
        end   = date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if end < start:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    if body.leave_type not in LEAVE_TYPES:
        raise HTTPException(status_code=400, detail=f"Leave type must be one of: {LEAVE_TYPES}")

    # Block if staff already has overlapping leave
    overlap = conn.execute(
        """SELECT id FROM leave_requests
           WHERE staff_id = ? AND status NOT IN ('Rejected')
             AND NOT (end_date < ? OR start_date > ?)""",
        (body.staff_id, body.start_date, body.end_date)
    ).fetchone()
    if overlap:
        raise HTTPException(
            status_code=409,
            detail="You already have a leave request overlapping these dates."
        )

    ph_dates     = _get_ph_dates_for_range(conn, start, end)
    working_days = _count_working_days(start, end, ph_dates)

    # ── Deduct balance immediately on submit ──────────────────
    # Annual Leave: check balance first, then deduct
    if body.leave_type == "Annual Leave":
        staff_row = conn.execute(
            f"SELECT annual_leave_taken, annual_leave_total FROM staff WHERE {pk} = ?",
            (body.staff_id,)
        ).fetchone()
        if staff_row:
            taken   = int(staff_row["annual_leave_taken"] or 0)
            total   = int(staff_row["annual_leave_total"] or 24)
            remaining = total - taken
            if working_days > remaining:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient balance. You have {remaining} days remaining but requested {working_days}."
                )
        conn.execute(
            f"UPDATE staff SET annual_leave_taken = annual_leave_taken + ? WHERE {pk} = ?",
            (working_days, body.staff_id)
        )

    elif body.leave_type == "PH off day":
        staff_row = conn.execute(
            f"SELECT ph_earned, ph_taken FROM staff WHERE {pk} = ?",
            (body.staff_id,)
        ).fetchone()
        if staff_row:
            ph_remaining = int(staff_row["ph_earned"] or 0) - int(staff_row["ph_taken"] or 0)
            if working_days > ph_remaining:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient PH balance. You have {ph_remaining} PH days remaining."
                )
        conn.execute(
            f"UPDATE staff SET ph_taken = ph_taken + ? WHERE {pk} = ?",
            (working_days, body.staff_id)
        )

    # Sick and Special Off: no balance deduction

    conn.execute(
        """INSERT INTO leave_requests
               (staff_id, start_date, end_date, type, working_days,
                status, note, requested_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            body.staff_id, body.start_date, body.end_date,
            body.leave_type, working_days, "Pending Manager",
            body.note or "", body.requested_by, _now(),
        )
    )
    conn.commit()
    request_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    return {
        "status":         "submitted",
        "request_id":     request_id,
        "working_days":   working_days,
        "needs_director": body.leave_type in NEEDS_DIRECTOR,
    }


# ==================================================================
# GET /leave/pending
# ==================================================================

@router.get("/pending")
def get_pending(conn: sqlite3.Connection = Depends(get_db)):
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)
    rows = conn.execute(
        f"""SELECT lr.*, s.{nm} AS staff_name, s.role AS staff_role
            FROM leave_requests lr
            LEFT JOIN staff s ON s.{pk} = lr.staff_id
            WHERE lr.status = 'Pending Manager'
            ORDER BY lr.created_at ASC"""
    ).fetchall()
    return {"pending": [dict(r) for r in rows]}


# ==================================================================
# POST /leave/approve/{request_id}
#
# Balance already deducted at submit — nothing to deduct here.
# Annual Leave → forward to Opuba
# PH/Sick/Special → mark Approved, done
# ==================================================================

@router.post("/approve/{request_id}")
def approve_leave(
    request_id:  int,
    approved_by: str,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)

    row = conn.execute(
        "SELECT * FROM leave_requests WHERE id = ?", (request_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    req = dict(row)
    if req["status"] != "Pending Manager":
        raise HTTPException(status_code=400, detail=f"Request is already {req['status']}")

    leave_type = req["type"]

    if leave_type in NEEDS_DIRECTOR:
        conn.execute(
            "UPDATE leave_requests SET status='Pending Director', milka_approved_at=? WHERE id=?",
            (_now(), request_id)
        )
        new_status = "Pending Director"
    else:
        conn.execute(
            "UPDATE leave_requests SET status='Approved', milka_approved_at=? WHERE id=?",
            (_now(), request_id)
        )
        new_status = "Approved"
        # No balance change here — already deducted at submit

    conn.commit()
    return {
        "status":                "done",
        "new_status":            new_status,
        "forwarded_to_director": leave_type in NEEDS_DIRECTOR,
    }


# ==================================================================
# POST /leave/reject/{request_id}
#
# Credits days BACK to balance since they were deducted at submit.
# Annual Leave → credit annual_leave_taken back
# PH off       → credit ph_taken back
# Sick/Special → no credit needed (nothing was deducted)
# ==================================================================

@router.post("/reject/{request_id}")
def reject_leave(
    request_id: int,
    body: RejectRequest,
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
    if req["status"] not in ("Pending Manager", "Pending Director"):
        raise HTTPException(status_code=400, detail="Request cannot be rejected in its current state")

    # ── Credit days back ──────────────────────────────────────
    leave_type   = req["type"]
    working_days = req.get("working_days", 0) or 0
    staff_id     = req["staff_id"]

    if leave_type == "Annual Leave" and working_days > 0:
        conn.execute(
            f"UPDATE staff SET annual_leave_taken = MAX(0, annual_leave_taken - ?) WHERE {pk} = ?",
            (working_days, staff_id)
        )
    elif leave_type == "PH off day" and working_days > 0:
        conn.execute(
            f"UPDATE staff SET ph_taken = MAX(0, ph_taken - ?) WHERE {pk} = ?",
            (working_days, staff_id)
        )
    # Sick / Special: nothing to credit back

    conn.execute(
        """UPDATE leave_requests
           SET status='Rejected', rejected_by=?, rejected_reason=?
           WHERE id=?""",
        (body.rejected_by, body.rejected_reason, request_id)
    )
    conn.commit()
    return {"status": "rejected", "request_id": request_id}


# ==================================================================
# GET /leave/my-blocked-dates
# ==================================================================

@router.get("/my-blocked-dates")
def get_my_blocked_dates(staff_id: int, conn: sqlite3.Connection = Depends(get_db)):
    _ensure_leave_table(conn)
    rows = conn.execute(
        """SELECT start_date, end_date FROM leave_requests
           WHERE staff_id = ? AND status NOT IN ('Rejected')
           ORDER BY start_date""",
        (staff_id,)
    ).fetchall()
    blocked = []
    for r in rows:
        d   = date.fromisoformat(r["start_date"])
        end = date.fromisoformat(r["end_date"])
        while d <= end:
            blocked.append(d.isoformat())
            d += timedelta(days=1)
    return {"blocked_dates": list(set(blocked))}


# ==================================================================
# POST /leave/check-overlap
# ==================================================================

@router.post("/check-overlap")
def check_overlap(body: OverlapCheckRequest, conn: sqlite3.Connection = Depends(get_db)):
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)
    rows = conn.execute(
        f"""SELECT lr.start_date, lr.end_date, lr.type, lr.status, s.{nm} AS staff_name
            FROM leave_requests lr
            LEFT JOIN staff s ON s.{pk} = lr.staff_id
            WHERE lr.staff_id != ?
              AND lr.status NOT IN ('Rejected')
              AND NOT (lr.end_date < ? OR lr.start_date > ?)
            ORDER BY lr.start_date""",
        (body.staff_id, body.start_date, body.end_date)
    ).fetchall()
    conflicts = []
    for r in rows:
        d = dict(r)
        is_sick = d["type"] in ("Sick", "Sick Leave", "SICK")
        conflicts.append({
            "start_date": d["start_date"],
            "end_date":   d["end_date"],
            "type":       d["type"],
            "status":     d["status"],
            "staff_name": None if is_sick else d["staff_name"],
            "is_private": is_sick,
        })
    return {
        "has_overlap":    len(conflicts) > 0,
        "conflict_count": len(conflicts),
        "conflicts":      conflicts,
    }


# ==================================================================
# GET /leave/team-calendar
# ==================================================================

@router.get("/team-calendar")
def get_team_calendar(year: int, month: int, conn: sqlite3.Connection = Depends(get_db)):
    import calendar as cal_mod
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)

    _, last_day = cal_mod.monthrange(year, month)
    month_start = f"{year}-{month:02d}-01"
    month_end   = f"{year}-{month:02d}-{last_day:02d}"

    rows = conn.execute(
        f"""SELECT lr.id, lr.staff_id, lr.start_date, lr.end_date,
                   lr.type, lr.status, lr.requested_by, s.{nm} AS familiar_name
            FROM leave_requests lr
            LEFT JOIN staff s ON s.{pk} = lr.staff_id
            WHERE lr.status NOT IN ('Rejected')
              AND NOT (lr.end_date < ? OR lr.start_date > ?)
            ORDER BY lr.start_date""",
        (month_start, month_end)
    ).fetchall()

    day_map = {}
    for r in rows:
        d       = dict(r)
        is_sick = d["type"] in ("Sick", "Sick Leave", "SICK", "sick", "sick off", "Sick off", "Sick Off")
        start   = date.fromisoformat(d["start_date"])
        end     = date.fromisoformat(d["end_date"])
        clamp_start = max(start, date(year, month, 1))
        clamp_end   = min(end,   date(year, month, last_day))
        current = clamp_start
        while current <= clamp_end:
            iso = current.isoformat()
            if iso not in day_map:
                day_map[iso] = []
            day_map[iso].append({
                "staff_id":      d["staff_id"],
                "familiar_name": None if is_sick else (d["familiar_name"] or d.get("requested_by") or "Staff"),
                "type":          d["type"],
                "status":        d["status"],
                "is_private":    is_sick,
            })
            current += timedelta(days=1)

    return {"days": day_map}


# ==================================================================
# GET /leave/recent
# ==================================================================

@router.get("/recent")
def get_recent(conn: sqlite3.Connection = Depends(get_db)):
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)
    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
    rows = conn.execute(
        f"""SELECT lr.*, s.{nm} AS staff_name, s.role AS staff_role
            FROM leave_requests lr
            LEFT JOIN staff s ON s.{pk} = lr.staff_id
            WHERE lr.status NOT IN ('Pending Manager')
              AND lr.created_at >= ?
            ORDER BY lr.created_at DESC
            LIMIT 50""",
        (thirty_days_ago,)
    ).fetchall()
    return {"recent": [dict(r) for r in rows]}
