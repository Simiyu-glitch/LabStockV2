# charts.py
# LabStockV2 — Charts Module (Bench Decon + Fridge Maintenance)
#
# This file is a THIN WRAPPER only.
# All business logic lives in qms.py — unchanged from Streamlit.
# We just expose it as JSON endpoints for React to consume.
#
# Endpoints:
#   GET  /charts/status?department=Haematology   → locations + module status
#   GET  /charts/checklist?module=BENCH_DECON&location=Haematology+Bench
#   POST /charts/submit                          → save a completed chart

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

import qms

router = APIRouter(prefix="/charts", tags=["charts"])


# ── REQUEST / RESPONSE SHAPES ─────────────────────────────────

class SubmitChartRequest(BaseModel):
    module_name: str          # "BENCH_DECON" or "FRIDGE_MAINT"
    location_name: str        # e.g. "Haematology Bench"
    working_dept: str         # e.g. "Haematology"
    full_name: str            # performed_by_full_name (Law 3)
    checked_items: List[bool] # [True, True, True] — one bool per checklist item
    comments: str = ""        # optional


# ── ENDPOINTS ─────────────────────────────────────────────────

@router.get("/status")
def get_charts_status(department: str):
    """
    Returns all locations in the department cluster (from DB, not hardcoded)
    and whether each module is done today for each location.

    React uses this to build the location pills and module cards.
    """
    try:
        # Get all modules that apply to this department
        modules = qms.get_zone_modules(department)

        # Get all locations in the cluster from DB
        locations = qms.get_cluster_locations(department)

        result = []
        for location_name, is_default_active in locations:
            open_today, reason = qms.is_location_open(location_name)

            location_data = {
                "location_name": location_name,
                "is_open": open_today,
                "reason": reason,
                "modules": []
            }

            for module_name in modules:
                info = qms.MODULE_REGISTRY.get(module_name, {})
                entry = qms.get_todays_module_entry(
                    module_name, location_name, department
                )
                is_done = entry is not None

                # Get who submitted it if done
                # get_todays_module_entry returns a dict with key "performed_by"
                submitted_by = None
                if is_done and entry:
                    submitted_by = entry.get("performed_by", "a team member")

                location_data["modules"].append({
                    "module_name": module_name,
                    "label": info.get("label", module_name),
                    "icon": info.get("icon", "📋"),
                    "is_done": is_done,
                    "submitted_by": submitted_by,
                })

            result.append(location_data)

        return {"department": department, "locations": result}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/checklist")
def get_checklist(module_name: str, location_name: str):
    """
    Returns the checklist items for a given module + location.
    React renders these as checkboxes.
    """
    try:
        items = qms.get_module_checklist_items(module_name, location_name)
        return {"module_name": module_name, "items": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit")
def submit_chart(body: SubmitChartRequest):
    """
    Save a completed chart entry.
    Calls qms.submit_module_entry() — the same function Streamlit uses.
    Returns the new log_id on success.
    """
    try:
        # Guard: don't allow double submission
        existing = qms.get_todays_module_entry(
            body.module_name, body.location_name, body.working_dept
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail="This chart has already been submitted today."
            )

        log_id = qms.submit_module_entry(
            module_name=body.module_name,
            location_name=body.location_name,
            working_dept=body.working_dept,
            performed_by_full_name=body.full_name,
            checked_items=body.checked_items,
            comments=body.comments or None,
        )

        return {
            "success": True,
            "log_id": log_id,
            "message": f"Chart saved successfully."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
