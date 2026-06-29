# communications.py
# LabStockV2 — Communications & Post Analysis Module
#
# Thin wrapper around existing qms.py functions.
# No business logic lives here — only JSON endpoints.
#
# Endpoints:
#   GET  /communications/tests?department=Haematology  → test list + units
#   GET  /communications/log?module=CRITICAL_CALL&department=Haematology → recent entries
#   POST /communications/submit                         → save any comms entry

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import qms

router = APIRouter(prefix="/communications", tags=["communications"])


# ── REQUEST SHAPES ────────────────────────────────────────────

class CriticalCallRequest(BaseModel):
    # Patient
    patient_name: str
    uhid: str
    location: str
    # Test
    test_name: str
    unit: str
    first_value: str
    repeat_value: Optional[str] = None   # N/A for free-form tests
    pbf_done: Optional[bool] = None      # N/A for non-haem tests
    # Communication
    time_identified: str    # "HH:MM"
    time_reported: str      # "HH:MM"
    physician_name: str
    physician_cadre: str
    mode_of_communication: str
    read_back: bool
    comments: Optional[str] = ""
    # Session
    working_dept: str
    full_name: str          # performed_by_full_name (Law 3)


# ── ENDPOINTS ─────────────────────────────────────────────────

@router.get("/tests")
def get_tests(department: str):
    """
    Returns all tests configured for this department from DB.
    React uses this to populate the test dropdown and auto-fill units.
    """
    try:
        # get_test_unit_options() returns all tests lab-wide (no dept filter)
        # We then filter by department from the DB directly
        conn = qms.get_qms_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT test_name, unit FROM critical_call_test_units WHERE working_department = ? ORDER BY test_name",
            (department,)
        )
        rows = cur.fetchall()
        conn.close()
        tests = [{"test_name": row[0], "unit": row[1]} for row in rows]
        return {"department": department, "tests": tests}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/log")
def get_log(module: str, department: str, limit: int = 100, all_departments: bool = False):
    """
    Returns recent entries for a given module.
    If all_departments=True, returns across all departments (elevated roles only).
    """
    try:
        if all_departments:
            # Query across all departments
            conn = qms.get_qms_conn()
            cur = conn.cursor()
            cur.execute("""
                SELECT comm_id, entry_date, performed_by_full_name, time_reported,
                       location, read_back, mode_of_communication, fields_json,
                       recorded_at, working_department
                FROM communications_log
                WHERE module_name = ?
                ORDER BY comm_id DESC LIMIT ?
            """, (module, limit))
            rows = cur.fetchall()
            conn.close()
            import json
            results = []
            for row in rows:
                (comm_id, entry_date, performed_by, time_reported, location,
                 read_back, mode, fields_json, recorded_at, working_dept) = row
                try:
                    extra = json.loads(fields_json)
                except Exception:
                    extra = {}
                results.append({
                    "comm_id": comm_id,
                    "entry_date": entry_date,
                    "performed_by": performed_by,
                    "time_reported": time_reported,
                    "location": location,
                    "read_back": bool(read_back),
                    "mode_of_communication": mode,
                    "recorded_at": recorded_at,
                    "working_department": working_dept,
                    **extra,
                })
            return {"module": module, "department": "ALL", "entries": results}
        else:
            rows = qms.get_communications_for_zone(module, department, limit=limit)
            # Add working_department to each entry
            for r in rows:
                r["working_department"] = department
            return {"module": module, "department": department, "entries": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/critical-call")
def submit_critical_call(body: CriticalCallRequest):
    """
    Save a Critical Call entry.
    Calls qms.submit_communication() — same function Streamlit used.
    """
    try:
        extra_fields = {
            "patient_name":       body.patient_name,
            "uhid":               body.uhid,
            "location":           body.location,
            "test_name":          body.test_name,
            "unit":               body.unit,
            "first_value":        body.first_value,
            "repeat_value":       body.repeat_value or "N/A",
            "pbf_done":           "Yes" if body.pbf_done else ("No" if body.pbf_done is False else "N/A"),
            "time_identified":    body.time_identified,
            "time_reported":      body.time_reported,
            "physician_name":     body.physician_name,
            "physician_cadre":    body.physician_cadre,
            "mode_of_communication": body.mode_of_communication,
        }

        comm_id = qms.submit_communication(
            module_name="CRITICAL_CALL",
            working_dept=body.working_dept,
            performed_by_full_name=body.full_name,
            location=body.location,
            read_back=body.read_back,
            mode_of_communication=body.mode_of_communication,
            extra_fields=extra_fields,
        )

        return {"success": True, "comm_id": comm_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
