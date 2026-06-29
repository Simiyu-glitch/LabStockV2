# ==================================================================
# ADD THIS BLOCK to leave_api.py — append before the final line
# GET /leave/recent
# Returns last 30 days of resolved (Approved / Rejected / Pending Director)
# requests for Milka's "Recently resolved" tab
# ==================================================================

@router.get("/recent")
def get_recent(
    conn: sqlite3.Connection = Depends(get_db)
):
    _ensure_leave_table(conn)
    pk = _pk(conn)
    nm = _name_col(conn)

    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()

    rows = conn.execute(
        f"""
        SELECT
            lr.*,
            s.{nm} AS staff_name,
            s.role  AS staff_role
        FROM leave_requests lr
        LEFT JOIN staff s ON s.{pk} = lr.staff_id
        WHERE lr.status NOT IN ('Pending Manager')
          AND lr.created_at >= ?
        ORDER BY lr.created_at DESC
        LIMIT 50
        """,
        (thirty_days_ago,)
    ).fetchall()

    return {"recent": [dict(r) for r in rows]}
