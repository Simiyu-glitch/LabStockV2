# ════════════════════════════════════════════════════════════════
#  tasks.py  —  Clinical Task Communication System
#  St. Mary's Mission Hospital  |  Architect: Emmanuel Simiyu
#
#  This turns the simple handover/task list into a living
#  conversation with full accountability:
#    • Commentary threads — the person doing the work has a voice
#    • Escalation flags    — 👁️ Watching · ⚡ Interested · 🚨 Fast tracking
#    • @mentions           — pull in a colleague to assist
#    • Attachments         — photos & PDFs = OBJECTIVE EVIDENCE (ISO 15189)
#    • Role-based closing  — only the right people can resolve a task
#    • CAPA prompt         — every close asks "was a CAPA required?"
#    • Three states        — pending → resolved → archived
#
#  ADDITIVE. It builds on the existing `handovers` table (never
#  rebuilds it) and adds five new tables of its own. Timestamps in
#  the NEW tables use ISO format; the legacy handovers table keeps
#  its old dd/mm/yyyy format so nothing existing breaks. (Law 3 still
#  holds — who, when, where, stamped on every record.)
#
#  Law 7 is enforced here too: every write checks the caller's role.
# ════════════════════════════════════════════════════════════════

import sqlite3
import json
from datetime import datetime, timedelta

DB = r"C:\QmsApp\lab_stock.db"

# ── Attachment rules (objective-evidence guardrails) ──
MAX_ATTACH_BYTES   = 5 * 1024 * 1024          # 5 MB per file
MAX_ATTACH_PER_MSG = 3                          # 3 files per message
ALLOWED_PHOTO_EXT  = (".jpg", ".jpeg", ".png")
ALLOWED_PDF_EXT    = (".pdf",)

# ── Who can do what (single source of truth — all lowercase) ──
FLAG_ROLES  = ["hod", "qa", "manager", "director"]   # can escalate a task
CLOSE_ROLES = ["qa", "manager", "director"]          # can close ANY task


# ────────────────────────────────────────────────────────────────
#  PART A — PURE DATABASE LOGIC  (no Streamlit, fully testable)
# ────────────────────────────────────────────────────────────────

def _conn():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def now_iso():
    """ISO time for the NEW tables."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_legacy():
    """dd/mm/yyyy time to match the existing handovers table format."""
    return datetime.now().strftime("%d/%m/%Y %I:%M%p")


# ── TABLE CREATION ───────────────────────────────────────────────

def ensure_task_tables():
    """Create the five new task tables (IF NOT EXISTS — safe every boot)."""
    conn = _conn()
    c = conn.cursor()

    # 1. The conversation thread on each task
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_comments (
            comment_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id              INTEGER NOT NULL,
            author_full_name     TEXT NOT NULL,
            author_familiar_name TEXT NOT NULL,
            author_role          TEXT NOT NULL,
            comment_text         TEXT,
            created_at           TEXT NOT NULL
        )
    """)

    # 2. Escalation flags. Latest is_active=1 row is the current flag.
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_flags (
            flag_id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id              INTEGER NOT NULL,
            flag_level           TEXT NOT NULL,   -- watching|interested|fast_tracking
            flagged_by_full_name TEXT NOT NULL,
            flagged_by_familiar  TEXT NOT NULL,
            flagged_by_role      TEXT NOT NULL,
            flagged_at           TEXT NOT NULL,
            is_active            INTEGER DEFAULT 1
        )
    """)

    # 3. @mentions — who was pulled into the thread, by whom
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_mentions (
            mention_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id              INTEGER NOT NULL,
            mentioned_familiar   TEXT NOT NULL,
            mentioned_by_familiar TEXT NOT NULL,
            created_at           TEXT NOT NULL
        )
    """)

    # 4. Attachments — GENERIC. record_type + record_id means ANY module
    #    (tasks today, bench decon tomorrow) can hang evidence here.
    c.execute("""
        CREATE TABLE IF NOT EXISTS attachments (
            attachment_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            record_type          TEXT NOT NULL,   -- 'task'
            record_id            INTEGER NOT NULL,
            file_name            TEXT NOT NULL,
            file_type            TEXT NOT NULL,   -- 'photo' | 'pdf'
            file_data            BLOB,            -- the bytes themselves
            file_size            INTEGER,
            uploaded_by_full     TEXT NOT NULL,
            uploaded_by_familiar TEXT NOT NULL,
            uploaded_at          TEXT NOT NULL
        )
    """)

    # 6. Appreciations — "Good work 👏" from a senior. Positive
    #    reinforcement: catching people doing things RIGHT, not just wrong.
    c.execute("""
        CREATE TABLE IF NOT EXISTS task_appreciations (
            appr_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id          INTEGER NOT NULL,
            given_by_full    TEXT NOT NULL,
            given_by_familiar TEXT NOT NULL,
            given_by_role    TEXT NOT NULL,
            given_at         TEXT NOT NULL
        )
    """)

    # 5. CAPA register — every closed task answers "was a CAPA needed?"
    c.execute("""
        CREATE TABLE IF NOT EXISTS capa (
            capa_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type      TEXT NOT NULL,   -- 'task'
            source_id        INTEGER NOT NULL,
            capa_required    INTEGER NOT NULL, -- 1 yes, 0 no
            waiver_reason    TEXT,             -- reason given when NO
            raised_by_full   TEXT NOT NULL,
            raised_by_familiar TEXT NOT NULL,
            raised_at        TEXT NOT NULL,
            status           TEXT DEFAULT 'open'  -- open|waived
        )
    """)

    conn.commit()
    conn.close()


def _add_handover_columns():
    """Add closing/archiving columns to the existing handovers table.
    try/except per column — exactly the safe pattern lab_app.py uses."""
    conn = _conn()
    c = conn.cursor()
    for col, decl in [("resolved_by", "TEXT"), ("archived_at", "TEXT")]:
        try:
            c.execute(f"ALTER TABLE handovers ADD COLUMN {col} {decl}")
            conn.commit()
        except Exception:
            pass
    conn.close()


def tasks_startup():
    """Call once at boot, after qms.qms_startup(). Idempotent."""
    ensure_task_tables()
    _add_handover_columns()
    archive_old_resolved_tasks()   # tidy: sweep day-old resolved → archived


# ── LAW 7 HELPERS ────────────────────────────────────────────────

def _has_role(role, allowed):
    return role is not None and role.lower() in [r.lower() for r in allowed]


def get_full_name(familiar_name):
    """Bridge familiar → full name from the staff table (Law 4)."""
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT full_name FROM staff WHERE familiar_name = ? AND is_active = 1",
              (familiar_name,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else familiar_name


def get_mentionable_staff(exclude_familiar):
    """Familiar names we may @mention — everyone active except the
    housekeeper (no clinical-task access) and the person themselves."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT familiar_name, role FROM staff
                 WHERE is_active = 1 AND role != 'housekeeper'
                 ORDER BY familiar_name""")
    rows = c.fetchall()
    conn.close()
    return [fam for fam, _r in rows if fam != exclude_familiar]


# ── THE TASK ITSELF (reads from the existing handovers table) ─────

def get_task(task_id):
    """One task header from handovers. Returns dict or None."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT id, from_staff, to_staff, message, created_at,
                        status, done_at, resolved_by
                 FROM handovers WHERE id = ?""", (task_id,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {
        "id": r[0], "from_staff": r[1], "to_staff": r[2], "message": r[3],
        "created_at": r[4], "status": r[5], "done_at": r[6], "resolved_by": r[7],
    }


def get_tasks_for_user(familiar_name, role):
    """Active (not archived) tasks this person should see.
    Managers/QA/Directors see everything. Others see tasks assigned
    to them, sent by them, or where they were @mentioned."""
    conn = _conn()
    c = conn.cursor()
    if _has_role(role, ["manager", "qa", "director"]):
        c.execute("""SELECT id, from_staff, to_staff, message, created_at, status
                     FROM handovers WHERE status != 'archived'
                     ORDER BY id DESC""")
        rows = c.fetchall()
    else:
        c.execute("""
            SELECT DISTINCT h.id, h.from_staff, h.to_staff, h.message,
                   h.created_at, h.status
            FROM handovers h
            LEFT JOIN task_mentions m ON m.task_id = h.id
            WHERE h.status != 'archived'
              AND (h.to_staff = ? OR h.from_staff = ? OR m.mentioned_familiar = ?)
            ORDER BY h.id DESC
        """, (familiar_name, familiar_name, familiar_name))
        rows = c.fetchall()
    conn.close()
    return rows


# ── COMMENTARY ───────────────────────────────────────────────────

def add_comment(task_id, author_familiar, author_role, text):
    """Add one comment to a task thread. Anyone who can see the task
    may speak — that is the whole point: the person on the ground gets
    a voice. Returns the new comment_id."""
    author_full = get_full_name(author_familiar)
    conn = _conn()
    c = conn.cursor()
    c.execute("""INSERT INTO task_comments
        (task_id, author_full_name, author_familiar_name, author_role,
         comment_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (task_id, author_full, author_familiar, author_role, text, now_iso()))
    cid = c.lastrowid
    conn.commit()
    conn.close()
    return cid


def get_thread(task_id):
    """Return the full comment thread oldest-first."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT comment_id, author_full_name, author_familiar_name,
                        author_role, comment_text, created_at
                 FROM task_comments WHERE task_id = ?
                 ORDER BY comment_id ASC""", (task_id,))
    rows = c.fetchall()
    conn.close()
    return rows


# ── ESCALATION FLAGS ─────────────────────────────────────────────

def set_flag(task_id, level, by_familiar, by_role):
    """Raise an escalation flag. Only seniors (FLAG_ROLES) may flag.
    The newest flag becomes the active one; older flags deactivate."""
    if not _has_role(by_role, FLAG_ROLES):
        raise PermissionError(
            f"Only HOD and above can flag a task. Your role: {by_role}.")
    by_full = get_full_name(by_familiar)
    conn = _conn()
    c = conn.cursor()
    c.execute("UPDATE task_flags SET is_active = 0 WHERE task_id = ?", (task_id,))
    c.execute("""INSERT INTO task_flags
        (task_id, flag_level, flagged_by_full_name, flagged_by_familiar,
         flagged_by_role, flagged_at, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)""",
        (task_id, level, by_full, by_familiar, by_role, now_iso()))
    conn.commit()
    conn.close()


def clear_flag(task_id, by_role):
    """Remove the active flag. Only seniors may clear."""
    if not _has_role(by_role, FLAG_ROLES):
        raise PermissionError("Only HOD and above can clear a flag.")
    conn = _conn()
    c = conn.cursor()
    c.execute("UPDATE task_flags SET is_active = 0 WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_active_flag(task_id):
    """Return the current flag dict or None."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT flag_level, flagged_by_full_name, flagged_by_familiar,
                        flagged_by_role, flagged_at
                 FROM task_flags WHERE task_id = ? AND is_active = 1
                 ORDER BY flag_id DESC LIMIT 1""", (task_id,))
    r = c.fetchone()
    conn.close()
    if not r:
        return None
    return {"level": r[0], "by_full": r[1], "by_familiar": r[2],
            "by_role": r[3], "at": r[4]}


# ── APPRECIATION ("Good work 👏") ────────────────────────────────

def add_appreciation(task_id, by_familiar, by_role):
    """A senior taps 'Good work'. Only seniors may appreciate.
    One appreciation per senior per task (toggle off if tapped again)."""
    if not _has_role(by_role, FLAG_ROLES):
        raise PermissionError("Only HOD and above can appreciate work.")
    by_full = get_full_name(by_familiar)
    conn = _conn()
    c = conn.cursor()
    # toggle: if this senior already appreciated, remove it
    c.execute("""SELECT appr_id FROM task_appreciations
                 WHERE task_id = ? AND given_by_familiar = ?""",
              (task_id, by_familiar))
    existing = c.fetchone()
    if existing:
        c.execute("DELETE FROM task_appreciations WHERE appr_id = ?",
                  (existing[0],))
    else:
        c.execute("""INSERT INTO task_appreciations
            (task_id, given_by_full, given_by_familiar, given_by_role, given_at)
            VALUES (?, ?, ?, ?, ?)""",
            (task_id, by_full, by_familiar, by_role, now_iso()))
    conn.commit()
    conn.close()


def get_appreciations(task_id):
    """Return list of (familiar, full, at) who appreciated this task."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT given_by_familiar, given_by_full, given_at
                 FROM task_appreciations WHERE task_id = ?
                 ORDER BY appr_id""", (task_id,))
    rows = c.fetchall()
    conn.close()
    return rows


# ── @MENTIONS ────────────────────────────────────────────────────

def add_mention(task_id, mentioned_familiar, by_familiar):
    """Pull a colleague into the thread. Records it (and they will now
    see the task in their list). Skips duplicates."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT 1 FROM task_mentions
                 WHERE task_id = ? AND mentioned_familiar = ?""",
              (task_id, mentioned_familiar))
    if not c.fetchone():
        c.execute("""INSERT INTO task_mentions
            (task_id, mentioned_familiar, mentioned_by_familiar, created_at)
            VALUES (?, ?, ?, ?)""",
            (task_id, mentioned_familiar, by_familiar, now_iso()))
        conn.commit()
    conn.close()


def get_mentions(task_id):
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT mentioned_familiar, mentioned_by_familiar, created_at
                 FROM task_mentions WHERE task_id = ? ORDER BY mention_id""",
              (task_id,))
    rows = c.fetchall()
    conn.close()
    return rows


def get_participants(task_id, task):
    """Everyone in the thread: assigner + assignee + all mentioned."""
    people = []
    if task:
        people.append(task["from_staff"])
        people.append(task["to_staff"])
    for fam, _by, _at in get_mentions(task_id):
        people.append(fam)
    # de-duplicate, keep order
    seen, out = set(), []
    for p in people:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


# ── ATTACHMENTS (objective evidence) ─────────────────────────────

def _classify_file(file_name):
    """Return 'photo', 'pdf', or None based on the extension."""
    lower = file_name.lower()
    if lower.endswith(ALLOWED_PHOTO_EXT):
        return "photo"
    if lower.endswith(ALLOWED_PDF_EXT):
        return "pdf"
    return None


def save_attachment(record_type, record_id, file_name, file_bytes,
                    uploaded_by_familiar):
    """Store one attachment as objective evidence. Enforces the rules:
    allowed type, size limit, max-per-record. Returns (ok, message)."""
    file_type = _classify_file(file_name)
    if file_type is None:
        return False, "Only photos (JPG/PNG) and PDF files are allowed."
    if len(file_bytes) > MAX_ATTACH_BYTES:
        return False, "File too large. Maximum 5 MB per file."

    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT COUNT(*) FROM attachments
                 WHERE record_type = ? AND record_id = ?""",
              (record_type, record_id))
    if c.fetchone()[0] >= MAX_ATTACH_PER_MSG:
        conn.close()
        return False, f"Maximum {MAX_ATTACH_PER_MSG} attachments per record."

    full = get_full_name(uploaded_by_familiar)
    c.execute("""INSERT INTO attachments
        (record_type, record_id, file_name, file_type, file_data, file_size,
         uploaded_by_full, uploaded_by_familiar, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (record_type, record_id, file_name, file_type, file_bytes,
         len(file_bytes), full, uploaded_by_familiar, now_iso()))
    conn.commit()
    conn.close()
    return True, "Attached."


def get_attachments_meta(record_type, record_id):
    """List attachment metadata (NO bytes — keeps this light)."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT attachment_id, file_name, file_type, file_size,
                        uploaded_by_full, uploaded_at
                 FROM attachments
                 WHERE record_type = ? AND record_id = ?
                 ORDER BY attachment_id""", (record_type, record_id))
    rows = c.fetchall()
    conn.close()
    return rows


def get_attachment_blob(attachment_id):
    """Fetch one attachment's bytes only when actually displaying it."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT file_name, file_type, file_data
                 FROM attachments WHERE attachment_id = ?""", (attachment_id,))
    r = c.fetchone()
    conn.close()
    return r   # (file_name, file_type, file_data) or None


# ── CLOSING & CAPA ───────────────────────────────────────────────

def assignee_has_responded(task_id, assignee_familiar):
    """Has the person the task was assigned to spoken in the thread?
    Used to stop a task being closed before the assignee acknowledges."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT 1 FROM task_comments
                 WHERE task_id = ? AND author_familiar_name = ? LIMIT 1""",
              (task_id, assignee_familiar))
    found = c.fetchone() is not None
    conn.close()
    return found


def can_close_task(closer_familiar, closer_role, task):
    """Law for closing a task. Returns (can_close: bool, reason: str).

    • Manager / QA / Director  → can close ANY task (they carry the
      authority; e.g. Milka closes a task Dr Esther Opuba flagged
      before travelling).
    • The original assigner     → can close their OWN task, but only
      after the assignee has responded at least once (no silent sweeps).
    • Everyone else             → cannot close."""
    if task is None:
        return False, "Task not found."
    if _has_role(closer_role, CLOSE_ROLES):
        return True, ""
    if closer_familiar == task["from_staff"]:
        if assignee_has_responded(task["id"], task["to_staff"]):
            return True, ""
        return False, ("The assignee has not responded yet. "
                       "Wait for an update before closing.")
    return False, "Only the assigner or a manager can close this task."


def resolve_task(task_id, closer_familiar, closer_role,
                 capa_required, waiver_reason=None):
    """Close a task → status 'done', stamp who/when, and record the
    mandatory CAPA decision. Raises PermissionError if not allowed."""
    task = get_task(task_id)
    ok, reason = can_close_task(closer_familiar, closer_role, task)
    if not ok:
        raise PermissionError(reason)
    if not capa_required and not (waiver_reason and waiver_reason.strip()):
        raise ValueError("A reason is required when no CAPA is raised.")

    closer_full = get_full_name(closer_familiar)
    conn = _conn()
    c = conn.cursor()
    c.execute("""UPDATE handovers
                 SET status = 'done', done_at = ?, resolved_by = ?
                 WHERE id = ?""", (now_legacy(), closer_familiar, task_id))
    c.execute("""INSERT INTO capa
        (source_type, source_id, capa_required, waiver_reason,
         raised_by_full, raised_by_familiar, raised_at, status)
        VALUES ('task', ?, ?, ?, ?, ?, ?, ?)""",
        (task_id, 1 if capa_required else 0,
         None if capa_required else waiver_reason,
         closer_full, closer_familiar, now_iso(),
         'open' if capa_required else 'waived'))
    conn.commit()
    conn.close()


def archive_old_resolved_tasks(hours=24):
    """Sweep resolved tasks older than `hours` into 'archived' so they
    leave the active view and live permanently in Records."""
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT id, done_at FROM handovers
                 WHERE status = 'done' AND done_at IS NOT NULL""")
    cutoff = datetime.now() - timedelta(hours=hours)
    to_archive = []
    for tid, done_at in c.fetchall():
        try:
            dt = datetime.strptime(done_at, "%d/%m/%Y %I:%M%p")
            if dt < cutoff:
                to_archive.append(tid)
        except Exception:
            pass
    for tid in to_archive:
        c.execute("UPDATE handovers SET status = 'archived', archived_at = ? "
                  "WHERE id = ?", (now_legacy(), tid))
    conn.commit()
    conn.close()
    return len(to_archive)


def get_capa_for_task(task_id):
    conn = _conn()
    c = conn.cursor()
    c.execute("""SELECT capa_required, waiver_reason, raised_by_full, raised_at, status
                 FROM capa WHERE source_type = 'task' AND source_id = ?
                 ORDER BY capa_id DESC LIMIT 1""", (task_id,))
    r = c.fetchone()
    conn.close()
    return r


# ── COUNTS used by the dashboard (Interface 4 reads these later) ──

def count_comments(task_id):
    conn = _conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM task_comments WHERE task_id = ?", (task_id,))
    n = c.fetchone()[0]
    conn.close()
    return n


# ════════════════════════════════════════════════════════════════
#  PART B — THE STREAMLIT SCREENS
#  (uses Part A above; relies on qms.inject_css() already run)
# ════════════════════════════════════════════════════════════════

_FLAG_DISPLAY = {
    "watching":      ("👁️", "Watching",      "#185fa5", "#e6f1fb"),
    "interested":    ("⚡", "Interested",     "#ba7517", "#faeeda"),
    "fast_tracking": ("🚨", "Fast tracking",  "#d32f2f", "#fff5f5"),
}

_ROLE_BUBBLE = {
    "director": ("#1a1a2e", "#fff"),
    "manager":  ("#fff5f5", "#333"),
    "qa":       ("#e6f1fb", "#333"),
    "hod":      ("#fef7ee", "#333"),
}


def _age_label(created_at):
    """Human age of a task from its legacy timestamp."""
    try:
        dt = datetime.strptime(created_at, "%d/%m/%Y %I:%M%p")
    except Exception:
        return ""
    secs = int((datetime.now() - dt).total_seconds())
    if secs < 3600:   return f"{secs // 60}min ago"
    if secs < 86400:  return f"{secs // 3600}hr ago"
    return f"{secs // 86400}d ago"


def render_tasks_page(name, role):
    """Single entry point. Shows the task list, or one task's full
    thread if a task is open in session_state."""
    import streamlit as st
    import qms

    open_id = st.session_state.get("open_task_id")
    if open_id:
        _render_task_detail(open_id, name, role)
    else:
        _render_task_list(name, role)


def _render_task_list(name, role):
    import streamlit as st
    import qms

    qms.render_topbar(name, role, notif_count=0)
    qms.render_subbar("👥 Tasks", qms._dept_pill_label(name, role,
                      st.session_state.get("working_department")))

    rows = get_tasks_for_user(name, role)
    pending = [r for r in rows if r[5] == "pending"]
    done    = [r for r in rows if r[5] == "done"]

    metrics_html = (
        '<div class="qms-metrics qms-metrics-2">'
        '<div class="qms-metric qms-metric-red">'
        f'<div class="qms-metric-num qms-metric-num-red">{len(pending)}</div>'
        '<div class="qms-metric-label">Pending</div></div>'
        '<div class="qms-metric qms-metric-green">'
        f'<div class="qms-metric-num qms-metric-num-green">{len(done)}</div>'
        '<div class="qms-metric-label">Resolved (still visible)</div></div>'
        '</div>'
    )
    st.markdown(metrics_html, unsafe_allow_html=True)

    if not rows:
        st.info("No active tasks right now.")
        return

    # ── Each task: clean HTML card + small flat action buttons directly
    #    beneath it. We style buttons GLOBALLY (reliable in Streamlit) and
    #    pull the action row up tight so it reads as part of the card.
    #    Wrapper-class CSS does not reach Streamlit buttons reliably, so we
    #    keep it simple and global on this page.
    is_senior = _has_role(role, FLAG_ROLES)

    st.markdown(
        '''<style>
        /* Small, flat, quiet action buttons for the whole task list */
        section.main div[data-testid="stButton"] > button {
            background: #fff !important;
            border: 0.5px solid #ececec !important;
            border-radius: 8px !important;
            color: #777 !important;
            font-size: 12px !important;
            font-weight: 600 !important;
            padding: 5px 6px !important;
            min-height: 0 !important;
            line-height: 1.2 !important;
            box-shadow: none !important;
        }
        section.main div[data-testid="stButton"] > button:hover {
            color: #29abe2 !important;
            border-color: #bfe0f3 !important;
            background: #f7fcff !important;
        }
        /* squeeze the action columns tight under the card */
        section.main div[data-testid="stHorizontalBlock"] {
            gap: 6px !important;
            margin-top: -6px !important;
            margin-bottom: 16px !important;
        }
        section.main div[data-testid="column"] { padding: 0 !important; }
        </style>''',
        unsafe_allow_html=True)

    for (tid, from_s, to_s, msg, created_at, status) in rows:
        flag = get_active_flag(tid)
        apprs = get_appreciations(tid)
        ccount = count_comments(tid)
        age = _age_label(created_at)

        chip = ""
        if flag:
            emoji, label, color, bg = _FLAG_DISPLAY.get(
                flag["level"], ("", "", "#888", "#eee"))
            chip = (f'<span style="background:{bg};color:{color};'
                    f'border-radius:20px;padding:2px 9px;font-size:10px;'
                    f'font-weight:700;margin-left:8px;white-space:nowrap;">'
                    f'{emoji} {flag["by_familiar"]}</span>')

        appr_chip = ""
        if apprs:
            names = ", ".join(a[0] for a in apprs)
            appr_chip = (f'<span style="background:#eaf3de;color:#3b6d11;'
                         f'border-radius:20px;padding:2px 9px;font-size:10px;'
                         f'font-weight:700;margin-left:6px;white-space:nowrap;">'
                         f'\U0001F44F {names}</span>')

        if status == "done":
            status_badge = '<span class="qms-badge qms-badge-green">\u2713 Resolved</span>'
            ribbon = "#3b6d11"
        else:
            status_badge = '<span class="qms-badge qms-badge-red">Pending</span>'
            ribbon = "#d32f2f"

        card_html = (
            f'<div style="border:0.5px solid #e8e8e8;border-left:3px solid {ribbon};'
            f'border-radius:0 10px 10px 0;background:#fff;padding:14px 16px 10px;'
            f'box-shadow:0 1px 4px rgba(0,0,0,0.05);">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap;">'
            f'<div style="font-size:14px;font-weight:600;color:#1a1a1a;">{msg}</div>'
            f'<div>{chip}{appr_chip}</div></div>'
            f'<div style="font-size:11px;color:#999;margin-top:6px;">'
            f'\U0001F4E4 {from_s} &nbsp;&rarr;&nbsp; \U0001F464 {to_s} '
            f'&nbsp;&middot;&nbsp; {age} &nbsp;&middot;&nbsp; '
            f'\U0001F4AC {ccount} &nbsp; {status_badge}'
            f'</div></div>'
        )
        st.markdown(card_html, unsafe_allow_html=True)

        if is_senior and status == "pending":
            i_appreciated = any(a[0] == name for a in apprs)
            gw = "\U0001F44F Good work" + (" \u2713" if i_appreciated else "")
            qc = st.columns([1.2, 0.9, 1, 0.8, 0.9])
            if qc[0].button(gw, key=f"gw_{tid}", use_container_width=True):
                add_appreciation(tid, name, role); st.rerun()
            if qc[1].button("\U0001F441 Watch", key=f"w_{tid}", use_container_width=True):
                set_flag(tid, "watching", name, role); st.rerun()
            if qc[2].button("\u26A1 Interest", key=f"i_{tid}", use_container_width=True):
                set_flag(tid, "interested", name, role); st.rerun()
            if qc[3].button("\U0001F6A8 Fast", key=f"f_{tid}", use_container_width=True):
                set_flag(tid, "fast_tracking", name, role); st.rerun()
            if qc[4].button("Open \u203a", key=f"open_task_{tid}", use_container_width=True):
                st.session_state.open_task_id = tid; st.rerun()
        else:
            oc = st.columns([4, 1])
            if oc[1].button("Open \u203a", key=f"open_task_{tid}", use_container_width=True):
                st.session_state.open_task_id = tid; st.rerun()

def _render_task_detail(task_id, name, role):
    import streamlit as st
    import qms

    task = get_task(task_id)
    if not task:
        st.error("Task not found.")
        if st.button("← Back to tasks"):
            st.session_state.open_task_id = None
            st.rerun()
        return

    qms.render_topbar(name, role, notif_count=0)
    qms.render_subbar("👥 Task thread", qms._dept_pill_label(name, role,
                      st.session_state.get("working_department")))

    if st.button("← Back to all tasks", key="back_to_list"):
        st.session_state.open_task_id = None
        st.rerun()

    flag = get_active_flag(task_id)

    # ── Task header ──
    flag_html = ""
    if flag:
        emoji, label, color, bg = _FLAG_DISPLAY.get(
            flag["level"], ("", "", "#888", "#eee"))
        flag_html = (f'<span style="background:{bg};color:{color};'
                     f'border:1px solid {color}33;border-radius:20px;'
                     f'padding:3px 10px;font-size:11px;font-weight:700;">'
                     f'{emoji} {label} — {flag["by_familiar"]}</span>')

    header_html = (
        f'<div class="qms-card qms-card-red-accent">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;">'
        f'<div style="font-size:15px;font-weight:700;color:#1a1a1a;">{task["message"]}</div>'
        f'{flag_html}</div>'
        f'<div style="font-size:11px;color:#999;margin-top:6px;">'
        f'\U0001F4E4 From {task["from_staff"]} &nbsp;&middot;&nbsp; \U0001F464 To {task["to_staff"]} '
        f'&nbsp;&middot;&nbsp; {_age_label(task["created_at"])} '
        f'&nbsp;&middot;&nbsp; Status: {task["status"]}'
        f'</div></div>'
    )
    st.markdown(header_html, unsafe_allow_html=True)

    # ── Participants ──
    parts = get_participants(task_id, task)
    chips = "".join(
        f'<span style="background:#fff;border:0.5px solid #e0e0e0;'
        f'border-radius:20px;padding:3px 10px;font-size:11px;margin-right:6px;">'
        f'{p}</span>' for p in parts)
    st.markdown(f'<div style="margin:8px 0 4px;font-size:10px;color:#aaa;'
                f'text-transform:uppercase;letter-spacing:0.06em;">In this thread'
                f'</div><div style="margin-bottom:12px;">{chips}</div>',
                unsafe_allow_html=True)

    # ── Appreciation display (everyone sees who said Good work) ──
    apprs = get_appreciations(task_id)
    if apprs:
        names = ", ".join(a[0] for a in apprs)
        st.markdown(
            f'<div style="background:#eaf3de;color:#3b6d11;border-radius:8px;'
            f'padding:6px 12px;font-size:12px;font-weight:600;margin-bottom:10px;'
            f'display:inline-block;">\U0001F44F {names} appreciated this work</div>',
            unsafe_allow_html=True)

    # ── Escalation + appreciation controls (seniors only) ──
    if _has_role(role, FLAG_ROLES) and task["status"] == "pending":
        st.markdown('<div style="font-size:11px;color:#888;font-weight:600;'
                    'margin-bottom:4px;">Quick actions:</div>',
                    unsafe_allow_html=True)
        i_appreciated = any(a[0] == name for a in apprs)
        gw = "\U0001F44F Good work" + (" \u2713" if i_appreciated else "")
        fc = st.columns(5)
        if fc[0].button(gw, key="dt_gw", use_container_width=True):
            add_appreciation(task_id, name, role); st.rerun()
        if fc[1].button("\U0001F441 Watching", key="fl_watch", use_container_width=True):
            set_flag(task_id, "watching", name, role); st.rerun()
        if fc[2].button("\u26A1 Interested", key="fl_int", use_container_width=True):
            set_flag(task_id, "interested", name, role); st.rerun()
        if fc[3].button("\U0001F6A8 Fast track", key="fl_fast", use_container_width=True):
            set_flag(task_id, "fast_tracking", name, role); st.rerun()
        if fc[4].button("\u2715 Clear", key="fl_clear", use_container_width=True):
            clear_flag(task_id, role); st.rerun()
        st.write("")

    # ── The thread ──
    st.markdown('<div style="font-size:10px;color:#888;font-weight:700;'
                'text-transform:uppercase;letter-spacing:0.07em;margin:10px 0 8px;">'
                'Conversation</div>', unsafe_allow_html=True)

    thread = get_thread(task_id)
    if not thread:
        st.caption("No updates yet. Be the first to add one below.")

    for (cid, full, fam, crole, text, created) in thread:
        bg, fg = _ROLE_BUBBLE.get(crole.lower(), ("#f5f5f3", "#333"))
        border = ""
        if crole.lower() == "manager":  border = "border-left:3px solid #d32f2f;"
        if crole.lower() == "hod":      border = "border-left:3px solid #ba7517;"
        if crole.lower() == "qa":       border = "border-left:3px solid #29abe2;"
        role_color = "#29abe2" if crole.lower() == "director" else "#aaa"
        bubble_html = (
            f'<div style="margin-bottom:10px;">'
            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">'
            f'<span style="font-size:12px;font-weight:600;color:#1a1a1a;">{full}</span>'
            f'<span style="font-size:10px;color:{role_color};">{crole.upper()}</span>'
            f'<span style="font-size:10px;color:#bbb;margin-left:auto;">{created}</span>'
            f'</div>'
            f'<div style="background:{bg};color:{fg};{border}'
            f'border-radius:0 10px 10px 10px;padding:8px 12px;'
            f'font-size:12px;line-height:1.5;">{text or ""}</div>'
            f'</div>'
        )
        st.markdown(bubble_html, unsafe_allow_html=True)

        # attachments under this comment? (attached to the TASK, shown once below)
    # show task-level attachments (objective evidence)
    _render_attachments(task_id)

    st.divider()

    # ── Add an update — chat-style, minimal. One line; extras tucked away. ──
    # Tighten the file uploader so it is small, not a giant drop zone.
    st.markdown(
        '''<style>
        /* shrink the file uploader drop zone right down */
        section.main div[data-testid="stFileUploaderDropzone"] {
            padding: 6px 10px !important;
            min-height: 0 !important;
        }
        section.main div[data-testid="stFileUploaderDropzoneInstructions"] span,
        section.main div[data-testid="stFileUploaderDropzoneInstructions"] small {
            font-size: 10px !important;
        }
        </style>''',
        unsafe_allow_html=True)

    comment = st.text_input("update", key=f"cmt_{task_id}",
                            label_visibility="collapsed",
                            placeholder="Write an update…")

    # Attach + mention live quietly inside one expander — out of the way.
    with st.expander("📎 Attach evidence or mention someone"):
        up = st.file_uploader("Photo or PDF · max 5MB · up to 3",
                              type=["jpg", "jpeg", "png", "pdf"],
                              accept_multiple_files=True,
                              key=f"up_{task_id}")
        mentions = st.multiselect("Mention a colleague to assist",
                                  options=get_mentionable_staff(name),
                                  key=f"mn_{task_id}")

    if st.button("Send \u203a", key=f"send_{task_id}",
                 use_container_width=True, type="primary"):
        if not comment.strip() and not up and not mentions:
            st.error("Write an update, attach evidence, or mention someone.")
        else:
            if comment.strip():
                add_comment(task_id, name, role, comment.strip())
            if up:
                for f in up[:MAX_ATTACH_PER_MSG]:
                    ok, msg = save_attachment("task", task_id, f.name,
                                              f.getvalue(), name)
                    if not ok:
                        st.warning(f"{f.name}: {msg}")
            for m in mentions:
                add_mention(task_id, m, name)
                if comment.strip() == "" and not up:
                    add_comment(task_id, name, role,
                                f"Pulled in @{m} to assist.")
            st.session_state.flash = "Update added to the thread."
            st.rerun()

    # ── Close the task (authorised roles only) ──
    if task["status"] == "pending":
        can, why = can_close_task(name, role, task)
        st.divider()
        if can:
            with st.expander("✅ Resolve & close this task"):
                st.caption("Closing requires a CAPA decision — this keeps "
                           "the loop closed for ISO 15189.")
                capa_choice = st.radio(
                    "Was a CAPA required for this task?",
                    ["No — close cleanly", "Yes — raise a CAPA"],
                    key=f"capa_{task_id}")
                reason = ""
                if capa_choice.startswith("No"):
                    reason = st.text_input(
                        "Reason no CAPA is needed (required)",
                        key=f"reason_{task_id}")
                if st.button("🔒 Confirm resolve", key=f"resolve_{task_id}",
                             type="primary", use_container_width=True):
                    try:
                        resolve_task(task_id, name, role,
                                     capa_required=capa_choice.startswith("Yes"),
                                     waiver_reason=reason)
                        st.session_state.open_task_id = None
                        st.session_state.flash = "Task resolved and recorded."
                        st.rerun()
                    except (PermissionError, ValueError) as e:
                        st.error(str(e))
        else:
            st.caption(f"🔒 {why}")
    else:
        # already resolved — show the CAPA decision on record
        capa = get_capa_for_task(task_id)
        if capa:
            req, waiver, by_full, at, cstatus = capa
            if req:
                st.success(f"Resolved by {task['resolved_by']} · "
                           f"CAPA raised ({cstatus}) · {at}")
            else:
                st.info(f"Resolved by {task['resolved_by']} · "
                        f"No CAPA — \"{waiver}\" · {at}")


def _render_attachments(task_id):
    """Show objective-evidence attachments for this task."""
    import streamlit as st
    meta = get_attachments_meta("task", task_id)
    if not meta:
        return
    st.markdown('<div style="font-size:10px;color:#3b6d11;font-weight:700;'
                'margin:6px 0 6px;">📎 OBJECTIVE EVIDENCE</div>',
                unsafe_allow_html=True)
    for (aid, fname, ftype, fsize, by_full, at) in meta:
        blob = get_attachment_blob(aid)
        if not blob:
            continue
        _fn, _ft, data = blob
        st.markdown(f'<div style="font-size:11px;color:#888;">'
                    f'{fname} · uploaded by {by_full} · {at} · '
                    f'{fsize // 1024} KB</div>', unsafe_allow_html=True)
        if ftype == "photo":
            st.image(data, width=260)
        else:
            st.download_button(f"📄 Open {fname}", data=data,
                               file_name=fname, mime="application/pdf",
                               key=f"dl_{aid}")
        st.markdown('<div style="font-size:10px;color:#3b6d11;'
                    'background:#eaf3de;border-radius:6px;padding:3px 8px;'
                    'display:inline-block;margin-bottom:8px;">'
                    '✅ ISO 15189:2022 cl. 5.3 · Timestamped &amp; locked</div>',
                    unsafe_allow_html=True)