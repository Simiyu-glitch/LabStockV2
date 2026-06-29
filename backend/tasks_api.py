# tasks_api.py
# LabStockV2 — Tasks Module FastAPI Wrapper

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

# ── CRITICAL: Set DB path BEFORE any tasks functions are called ──
import tasks as t
t.DB = r"C:\QmsApp\lab_stock.db"

# Call startup to ensure all task tables exist
try:
    t.tasks_startup()
except Exception:
    pass  # tables may already exist

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _task_to_dict(row):
    if not row:
        return None
    if isinstance(row, dict):
        return row
    return {
        "id":         row[0],
        "from_staff": row[1],
        "to_staff":   row[2],
        "message":    row[3],
        "created_at": row[4],
        "status":     row[5],
    }


def _enrich_task(task_dict):
    if not task_dict:
        return task_dict
    tid = task_dict["id"]
    try:
        task_dict["comment_count"] = t.count_comments(tid)
    except Exception:
        task_dict["comment_count"] = 0
    try:
        flag = t.get_active_flag(tid)
        task_dict["flag"] = {
            "level":      flag["level"],
            "flagged_by": flag.get("by_full") or flag.get("by_familiar"),
            "flagged_at": flag.get("at"),
        } if flag else None
    except Exception:
        task_dict["flag"] = None
    return task_dict


class AssignTaskRequest(BaseModel):
    to_staff: str
    message: str
    from_familiar: str
    working_dept: str

class CommentRequest(BaseModel):
    task_id: int
    author_familiar: str
    author_role: str
    text: str

class FlagRequest(BaseModel):
    task_id: int
    level: str
    by_familiar: str
    by_role: str

class ResolveRequest(BaseModel):
    task_id: int
    closer_familiar: str
    closer_role: str
    capa_required: bool
    waiver_reason: Optional[str] = None


@router.get("/list")
def list_tasks(familiar_name: str, role: str):
    try:
        rows = t.get_tasks_for_user(familiar_name, role)
        tasks = [_enrich_task(_task_to_dict(r)) for r in rows]
        return {"tasks": tasks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail/{task_id}")
def get_task_detail(task_id: int):
    try:
        task = t.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        thread        = t.get_thread(task_id)
        flag          = t.get_active_flag(task_id)
        appreciations = t.get_appreciations(task_id)
        mentions      = t.get_mentions(task_id)
        capa          = t.get_capa_for_task(task_id)

        return {
            "task": task,
            "thread": [
                {
                    "comment_id":      c[0],
                    "author_full":     c[1],
                    "author_familiar": c[2],
                    "author_role":     c[3],
                    "text":            c[4],
                    "created_at":      c[5],
                }
                for c in thread
            ],
            "flag": {
                "level":      flag["level"],
                "flagged_by": flag.get("by_full") or flag.get("by_familiar"),
                "flagged_at": flag.get("at"),
            } if flag else None,
            "appreciations": [
                {"by_familiar": a[0], "by_full": a[1], "given_at": a[3]}
                for a in appreciations
            ],
            "mentions": [
                {"mentioned": m[0], "by": m[1], "at": m[2]}
                for m in mentions
            ],
            "capa": {
                "required":      bool(capa[0]),
                "waiver_reason": capa[1],
                "raised_by":     capa[2],
                "raised_at":     capa[3],
                "status":        capa[4],
            } if capa else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/staff")
def get_staff():
    try:
        conn = t._conn()
        c = conn.cursor()
        c.execute("""SELECT familiar_name, full_name, role
                     FROM staff WHERE is_active = 1 AND role != 'housekeeper'
                     ORDER BY familiar_name""")
        rows = c.fetchall()
        conn.close()
        return {"staff": [{"familiar": r[0], "full": r[1], "role": r[2]} for r in rows]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/assign")
def assign_task(body: AssignTaskRequest):
    try:
        conn = t._conn()
        c = conn.cursor()
        c.execute("""INSERT INTO handovers
            (from_staff, to_staff, message, created_at, status)
            VALUES (?, ?, ?, ?, 'pending')""",
            (body.from_familiar, body.to_staff,
             body.message, t.now_legacy()))
        task_id = c.lastrowid
        conn.commit()
        conn.close()
        return {"success": True, "task_id": task_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/comment")
def add_comment(body: CommentRequest):
    try:
        t.add_comment(body.task_id, body.author_familiar,
                      body.author_role, body.text)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/flag")
def set_flag(body: FlagRequest):
    try:
        t.set_flag(body.task_id, body.level,
                   body.by_familiar, body.by_role)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resolve")
def resolve_task(body: ResolveRequest):
    try:
        # Allow assignee to resolve their own task regardless of role
        task = t.get_task(body.task_id)
        is_assignee = task and task.get("to_staff") == body.closer_familiar
        if is_assignee:
            # Bypass role check — resolve directly
            import json
            from datetime import datetime
            conn = t._conn()
            c = conn.cursor()
            c.execute("UPDATE handovers SET status = ?, done_at = ?, resolved_by = ? WHERE id = ?",
                      ("done", t.now_legacy(), body.closer_familiar, body.task_id))
            c.execute("""INSERT INTO capa
                (source_type, source_id, capa_required, waiver_reason,
                 raised_by_full, raised_by_familiar, raised_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                ("task", body.task_id,
                 1 if body.capa_required else 0,
                 None if body.capa_required else body.waiver_reason,
                 t.get_full_name(body.closer_familiar),
                 body.closer_familiar, t.now_iso(),
                 "open" if body.capa_required else "waived"))
            conn.commit()
            conn.close()
        else:
            t.resolve_task(body.task_id, body.closer_familiar,
                           body.closer_role, body.capa_required,
                           body.waiver_reason)
        return {"success": True}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/appreciate/{task_id}")
def appreciate(task_id: int, by_familiar: str, by_role: str):
    try:
        t.add_appreciation(task_id, by_familiar, by_role)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MentionRequest(BaseModel):
    task_id: int
    mentioned_familiar: str
    by_familiar: str

@router.post("/mention")
def add_mention(body: MentionRequest):
    """Tag someone into a task thread."""
    try:
        t.add_mention(body.task_id, body.mentioned_familiar, body.by_familiar)
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MultiAssignRequest(BaseModel):
    to_staff_list: list
    message: str
    from_familiar: str
    working_dept: str

@router.post("/assign-multi")
def assign_multi(body: MultiAssignRequest):
    """Assign same task to multiple staff members at once."""
    try:
        conn = t._conn()
        c = conn.cursor()
        task_ids = []
        for to_staff in body.to_staff_list:
            c.execute(
                "INSERT INTO handovers (from_staff, to_staff, message, created_at, status) VALUES (?, ?, ?, ?, ?)",
                (body.from_familiar, to_staff, body.message, t.now_legacy(), "pending")
            )
            task_ids.append(c.lastrowid)
        conn.commit()
        conn.close()
        return {"success": True, "task_ids": task_ids, "count": len(task_ids)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
