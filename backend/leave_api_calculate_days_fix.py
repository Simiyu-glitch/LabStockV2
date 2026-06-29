# ==================================================================
# PASTE THIS into leave_api.py — REPLACE the existing calculate_days
# endpoint (lines 203-235 approximately).
#
# Changes:
#  1. _get_ph_dates_for_range now catches OperationalError (missing table)
#     so a missing public_holidays table returns empty set instead of 500
#  2. calculate_days calls _ensure_leave_table first
#  3. Adds a debug log so we can see what Python is computing
# ==================================================================

def _get_ph_dates_for_range(conn, start, end):
    """Return set of date strings that are public holidays in range.
    Handles missing public_holidays table gracefully."""
    try:
        rows = conn.execute(
            """
            SELECT holiday_date FROM public_holidays
            WHERE holiday_date BETWEEN ? AND ?
            """,
            (start.isoformat(), end.isoformat())
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()   # no PH table yet — treat as no public holidays


@router.post("/calculate-days")
def calculate_days(
    body: CalculateDaysRequest,
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)   # safety — creates table if missing

    try:
        start = date.fromisoformat(body.start_date)
        end   = date.fromisoformat(body.end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format")

    if end < start:
        raise HTTPException(status_code=400, detail="End date must be after start date")

    ph_dates     = _get_ph_dates_for_range(conn, start, end)
    working_days = _count_working_days(start, end, ph_dates)

    total_days = (end - start).days + 1
    sundays    = sum(
        1 for i in range(total_days)
        if (start + timedelta(days=i)).weekday() == 6
    )

    import logging
    logging.info(f"[calculate-days] {body.start_date}→{body.end_date} "
                 f"total={total_days} sundays={sundays} "
                 f"ph={len(ph_dates)} working={working_days}")

    return {
        "start_date":    body.start_date,
        "end_date":      body.end_date,
        "working_days":  working_days,
        "total_days":    total_days,
        "sundays":       sundays,
        "ph_count":      len(ph_dates),
        "ph_dates":      sorted(list(ph_dates)),
    }
