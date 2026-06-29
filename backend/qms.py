# ════════════════════════════════════════════════════════════════
#  qms.py  —  Lab QMS foundation + Compliance modules
#  St. Mary's Mission Hospital  |  Architect: Emmanuel Simiyu
#
#  ADDITIVE only. Never touches the four original tables.
#  lab_app.py imports this and calls:
#       qms.qms_startup()
#       qms.render_session_setup(name, role)
#       qms.render_charts_page(name, role)
#       qms.render_critical_call_page(name, role)
#       qms.render_bench_decon_page(name, role)   # legacy, still works
#
#  LAW 7 — role access enforced at database query level.
# ════════════════════════════════════════════════════════════════

import sqlite3
import json
from datetime import datetime, date

DB = r"C:\QmsApp\lab_stock.db"

BENCH_DECON_CHECKLIST = [
    "Check for cleanliness",
    "Wipe using 25ml Aniosyme diluted in 5 litres of water, allow 5 min contact time, then dry with towel",
    "General cleaning of benchtop, equipment surfaces and waste disposal",
]


MODULE_REGISTRY = {
    "BENCH_DECON": {
        "label": "Bench decontamination",
        "icon": "🧼",
        "frequency": "DAILY",
        "applies_to": "ALL",
    },
    "FRIDGE_MAINT": {
        "label": "Fridge maintenance",
        "icon": "🧊",
        "frequency": "DAILY",
        "applies_to": ["Haematology", "Biochemistry", "MCH"],
        "checklist": [
            "Record refrigerator temperature",
            "Dust/clean surfaces of scope components with mild detergent",
            "Remove containers and materials that are out dated",
            "Keep door seals clean",
        ],
    },
}


def get_module_checklist_items(module_name, location_name):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT m.checklist_items
        FROM qms_modules m
        JOIN locations l ON l.location_id = m.location_id
        WHERE m.module_name = ? AND l.location_name = ?
              AND m.is_active = 1
    """, (module_name, location_name))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    info = MODULE_REGISTRY.get(module_name)
    if info and "checklist" in info:
        return info["checklist"]
    if module_name == "BENCH_DECON":
        return BENCH_DECON_CHECKLIST
    return []


def get_todays_module_entry(module_name, location_name, working_dept,
                            on_date=None, role=None):
    if role and not can_access(role, BENCH_DECON_READ_ROLES):
        return None

    if on_date is None:
        on_date = today_iso()
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT log_id, performed_by_full_name, recorded_at,
               check_cleanliness, aniosyme_wipe, general_cleaning,
               checked_items_json
        FROM bench_decontamination_log
        WHERE entry_date = ? AND location_name = ? AND working_department = ?
              AND module_name = ?
        ORDER BY log_id DESC LIMIT 1
    """, (on_date, location_name, working_dept, module_name))
    row = c.fetchone()
    conn.close()
    if not row:
        return None

    log_id, performed_by, recorded_at, ck, an, gc, items_json = row
    if items_json:
        try:
            checked = json.loads(items_json)
        except Exception:
            checked = [bool(ck), bool(an), bool(gc)]
    else:
        checked = [bool(ck), bool(an), bool(gc)]

    return {
        "log_id": log_id,
        "performed_by": performed_by,
        "recorded_at": recorded_at,
        "checked": checked,
    }


def submit_module_entry(module_name, location_name, working_dept,
                        performed_by_full_name, checked_items,
                        comments=None, session_id=None, role=None):
    if role:
        check_access(role, BENCH_DECON_WRITE_ROLES)

    ck = checked_items[0] if len(checked_items) > 0 else False
    an = checked_items[1] if len(checked_items) > 1 else False
    gc = checked_items[2] if len(checked_items) > 2 else False
    items_json = json.dumps([bool(x) for x in checked_items])

    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO bench_decontamination_log
        (entry_date, session_id, location_name, working_department,
         performed_by_full_name, check_cleanliness, aniosyme_wipe,
         general_cleaning, recorded_at, comments, month_year,
         period_status, module_name, checked_items_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
    """, (today_iso(), session_id, location_name, working_dept,
          performed_by_full_name, 1 if ck else 0, 1 if an else 0,
          1 if gc else 0, now_iso(), comments, this_month_year(),
          module_name, items_json))
    log_id = c.lastrowid
    conn.commit()
    conn.close()
    return log_id


def module_applies_to_zone(module_name, working_dept):
    info = MODULE_REGISTRY.get(module_name)
    if not info:
        return False
    applies = info["applies_to"]
    if applies == "ALL":
        return True
    return working_dept in applies


def get_zone_modules(working_dept):
    return [name for name in MODULE_REGISTRY
            if module_applies_to_zone(name, working_dept)]


def get_module_zone_status(module_name, working_dept, on_date=None, role=None):
    if on_date is None:
        on_date = today_iso()
    status = []
    for location_name, _default_active in get_cluster_locations(working_dept):
        open_today, reason = is_location_open(location_name, on_date)
        entry = get_todays_module_entry(module_name, location_name,
                                        working_dept, on_date, role=role)
        status.append({
            "location": location_name,
            "open":     open_today,
            "reason":   reason,
            "done":     entry is not None,
            "entry":    entry,
        })
    return status


def get_zone_compliance_summary(working_dept, on_date=None, role=None):
    summary = []
    for module_name in get_zone_modules(working_dept):
        info = MODULE_REGISTRY[module_name]
        status = get_module_zone_status(module_name, working_dept,
                                        on_date, role=role)
        open_locs = [s for s in status if s["open"]]
        done = sum(1 for s in open_locs if s["done"])
        total = len(open_locs)
        summary.append({
            "module_name": module_name,
            "label": info["label"],
            "icon": info["icon"],
            "done": done,
            "total": total,
            "fully_done": (total > 0 and done == total),
        })
    return summary


def get_qms_conn():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_iso():
    return date.today().strftime("%Y-%m-%d")


def this_month_year():
    return date.today().strftime("%Y-%m")


def ensure_module_columns():
    conn = get_qms_conn()
    c = conn.cursor()
    for col, decl in [
        ("module_name", "TEXT DEFAULT 'BENCH_DECON'"),
        ("checked_items_json", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE bench_decontamination_log "
                       f"ADD COLUMN {col} {decl}")
            conn.commit()
        except Exception:
            pass
    conn.close()


def ensure_qms_tables():
    conn = get_qms_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            staff_id           INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name          TEXT NOT NULL,
            familiar_name      TEXT NOT NULL,
            nickname           TEXT,
            role               TEXT NOT NULL,
            default_department TEXT,
            pin_hash           TEXT,
            is_active          INTEGER DEFAULT 1,
            created_at         TEXT,
            created_by         TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            location_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            location_name   TEXT NOT NULL,
            is_working_dept INTEGER,
            is_active       INTEGER DEFAULT 1,
            display_order   INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS department_clusters (
            cluster_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            working_dept      TEXT NOT NULL,
            location_id       INTEGER,
            is_default_active INTEGER DEFAULT 1,
            display_order     INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS location_schedules (
            schedule_id        INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id        INTEGER,
            operates_monday    INTEGER DEFAULT 1,
            operates_tuesday   INTEGER DEFAULT 1,
            operates_wednesday INTEGER DEFAULT 1,
            operates_thursday  INTEGER DEFAULT 1,
            operates_friday    INTEGER DEFAULT 1,
            operates_saturday  INTEGER DEFAULT 1,
            operates_sunday    INTEGER DEFAULT 1,
            operates_holidays  INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS public_holidays (
            holiday_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            holiday_date TEXT NOT NULL,
            holiday_name TEXT NOT NULL,
            added_by     TEXT NOT NULL,
            added_at     TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS qms_modules (
            module_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            module_name       TEXT NOT NULL,
            location_id       INTEGER,
            is_active         INTEGER DEFAULT 1,
            checklist_items   TEXT,
            frequency         TEXT,
            review_tier2_role TEXT,
            review_tier3_role TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id           INTEGER,
            staff_full_name    TEXT NOT NULL,
            familiar_name      TEXT NOT NULL,
            role               TEXT NOT NULL,
            working_department TEXT,
            default_department TEXT,
            session_type       TEXT NOT NULL,
            active_locations   TEXT,
            login_time         TEXT NOT NULL,
            logout_time        TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS bench_decontamination_log (
            log_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date             TEXT NOT NULL,
            session_id             INTEGER,
            location_name          TEXT NOT NULL,
            working_department     TEXT NOT NULL,
            performed_by_full_name TEXT NOT NULL,
            check_cleanliness      INTEGER DEFAULT 0,
            aniosyme_wipe          INTEGER DEFAULT 0,
            general_cleaning       INTEGER DEFAULT 0,
            recorded_at            TEXT NOT NULL,
            tier2_reviewed_by      TEXT,
            tier2_reviewed_at      TEXT,
            tier3_reviewed_by      TEXT,
            tier3_reviewed_at      TEXT,
            comments               TEXT,
            month_year             TEXT NOT NULL,
            period_status          TEXT DEFAULT 'ACTIVE'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            notification_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient_staff_id INTEGER,
            title              TEXT NOT NULL,
            message            TEXT NOT NULL,
            record_type        TEXT,
            record_id          INTEGER,
            is_read            INTEGER DEFAULT 0,
            read_at            TEXT,
            created_at         TEXT NOT NULL,
            escalation_level   INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS document_version_archive (
            archive_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id      INTEGER,
            version_number   INTEGER,
            content_snapshot TEXT,
            superseded_by    TEXT NOT NULL,
            superseded_at    TEXT NOT NULL,
            change_reason    TEXT NOT NULL,
            access_level     TEXT DEFAULT 'QA_MANAGER_ONLY'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS completed_period_archive (
            period_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            module_type       TEXT NOT NULL,
            location_name     TEXT NOT NULL,
            period_month_year TEXT NOT NULL,
            signed_off_by     TEXT NOT NULL,
            signed_off_at     TEXT NOT NULL,
            record_count      INTEGER,
            compliance_rate   REAL,
            is_locked         INTEGER DEFAULT 1,
            access_level      TEXT DEFAULT 'HOD_QA_MANAGER'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS communications_log (
            comm_id             INTEGER PRIMARY KEY AUTOINCREMENT,
            module_name         TEXT NOT NULL,
            entry_date          TEXT NOT NULL,
            working_department  TEXT NOT NULL,
            performed_by_full_name TEXT NOT NULL,
            time_reported       TEXT NOT NULL,
            location             TEXT,
            read_back           INTEGER DEFAULT 0,
            mode_of_communication TEXT,
            fields_json         TEXT NOT NULL,
            recorded_at         TEXT NOT NULL,
            session_id          INTEGER,
            month_year          TEXT NOT NULL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS critical_call_test_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            working_department TEXT NOT NULL,
            test_name TEXT NOT NULL,
            unit TEXT NOT NULL,
            UNIQUE(working_department, test_name)
        )
    """)

    conn.commit()
    conn.close()


_SETTINGS_SEED = {
    "hospital_name":   "St. Mary's Mission Hospital",
    "hospital_motto":  "One Team One Direction",
    "hospital_logo":   "",
    "primary_color":   "#d32f2f",
    "footer_text":     "Compassion in Healthcare",
    "document_prefix": "STMMHL",
}

_LOCATIONS_SEED = [
    ("Haematology",     1, 1),
    ("Transfusion",     0, 2),
    ("Biochemistry",    1, 3),
    ("Immunology",      0, 4),
    ("Serology",        0, 5),
    ("Parasitology",    1, 6),
    ("Microbiology",    0, 7),
    ("Reception",       0, 8),
    ("Phlebotomy Room", 0, 9),
    ("Phlebotomy",      1, 10),
    ("MCH",             1, 11),
    ("Office",          0, 12),
]

_CLUSTERS_SEED = [
    ("Haematology",  "Haematology",     1, 1),
    ("Haematology",  "Transfusion",     1, 2),
    ("Biochemistry", "Biochemistry",    1, 1),
    ("Biochemistry", "Immunology",      1, 2),
    ("Biochemistry", "Serology",        1, 3),
    ("Parasitology", "Parasitology",    1, 1),
    ("Parasitology", "Microbiology",    1, 2),
    ("Phlebotomy",   "Reception",       1, 1),
    ("Phlebotomy",   "Phlebotomy Room", 1, 2),
    ("MCH",          "MCH",             1, 1),
    ("Office",       "Office",          1, 1),
]

_STAFF_SEED = [
    ("Dr Esther Opuba",  "Dr Opuba",  "director",     "Administration"),
    ("Milka Muriithi",    "Milka",     "manager",      "Administration"),
    ("Enock Kimutai",     "Enock",     "qa",           "Administration"),
    ("Emmanuel Simiyu",   "Emmanuel",  "hod",          "Haematology"),
    ("Mercy Okumu",       "Mercy",     "hod",          "Parasitology"),
    ("Hybine Chebet",     "Chebet",    "hod",          "Biochemistry"),
    ("Anthony Wambua",    "Anthony",   "mlt",          None),
    ("Rebecca Mideva",    "Rebecca",   "mlt",          None),
    ("Nancy Koigi",       "Nancy",     "mlt",          None),
    ("Erick Wamae",       "Erick",     "mlt",          None),
    ("Wairia Erick",      "Wairia",    "mlt",          None),
    ("Pauline Ndambuki",  "Pauline",   "mlt",          None),
    ("Francis Mubasu",    "Francis",   "mlt",          None),
    ("Juma Andanje",      "Juma",      "mlt",          None),
    ("Paul Wesonga",      "Paul",      "mlt",          None),
    ("Mourine Chebet",    "Mourine",   "phlebotomist", "Phlebotomy"),
    ("Grace Simatwa",     "Grace",     "phlebotomist", "Phlebotomy"),
    ("Stanley Kipkirui",  "Stanley",   "phlebotomist", "Phlebotomy"),
    ("Nicholas Aomo",   "Nicholas",  "housekeeper",  "Office"),
]


def _location_id(cursor, location_name):
    cursor.execute("SELECT location_id FROM locations WHERE location_name = ?",
                   (location_name,))
    row = cursor.fetchone()
    return row[0] if row else None


def seed_settings():
    conn = get_qms_conn()
    c = conn.cursor()
    for key, value in _SETTINGS_SEED.items():
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                  (key, value))
    conn.commit()
    conn.close()


def seed_locations():
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM locations")
    if c.fetchone()[0] == 0:
        for name, is_wd, order in _LOCATIONS_SEED:
            c.execute("""INSERT INTO locations
                         (location_name, is_working_dept, is_active, display_order)
                         VALUES (?, ?, 1, ?)""", (name, is_wd, order))
        conn.commit()
    conn.close()


def seed_clusters():
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM department_clusters")
    if c.fetchone()[0] == 0:
        for working_dept, loc_name, active, order in _CLUSTERS_SEED:
            loc_id = _location_id(c, loc_name)
            c.execute("""INSERT INTO department_clusters
                         (working_dept, location_id, is_default_active, display_order)
                         VALUES (?, ?, ?, ?)""",
                      (working_dept, loc_id, active, order))
        conn.commit()
    conn.close()


def seed_schedules():
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM location_schedules")
    if c.fetchone()[0] == 0:
        c.execute("SELECT location_id, location_name FROM locations")
        for loc_id, loc_name in c.fetchall():
            if loc_name == "MCH":
                c.execute("""INSERT INTO location_schedules
                    (location_id, operates_saturday, operates_sunday,
                     operates_holidays) VALUES (?, 0, 0, 0)""", (loc_id,))
            else:
                c.execute("INSERT INTO location_schedules (location_id) VALUES (?)",
                          (loc_id,))
        conn.commit()
    conn.close()


def seed_staff():
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM staff")
    if c.fetchone()[0] == 0:
        for full, familiar, role, dept in _STAFF_SEED:
            c.execute("""INSERT INTO staff
                         (full_name, familiar_name, role, default_department,
                          pin_hash, is_active, created_at, created_by)
                         VALUES (?, ?, ?, ?, NULL, 1, ?, 'system_seed')""",
                      (full, familiar, role, dept, now_iso()))
        conn.commit()
    conn.close()


_CRITICAL_TEST_UNITS_SEED = [
    ("Haematology", "WBC", "g/dL"),
    ("Haematology", "Platelets", "g/dL"),
    ("Haematology", "Hb", "g/dL"),
]


def seed_critical_call_test_units():
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM critical_call_test_units")
    if c.fetchone()[0] == 0:
        for dept, test, unit in _CRITICAL_TEST_UNITS_SEED:
            c.execute("""INSERT INTO critical_call_test_units
                (working_department, test_name, unit) VALUES (?, ?, ?)""",
                (dept, test, unit))
        conn.commit()
    conn.close()


def seed_qms_modules():
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM qms_modules WHERE module_name = 'BENCH_DECON'")
    if c.fetchone()[0] == 0:
        checklist_json = json.dumps(BENCH_DECON_CHECKLIST)
        c.execute("SELECT location_id FROM locations WHERE is_active = 1")
        for (loc_id,) in c.fetchall():
            c.execute("""INSERT INTO qms_modules
                (module_name, location_id, is_active, checklist_items,
                 frequency, review_tier2_role, review_tier3_role)
                VALUES ('BENCH_DECON', ?, 1, ?, 'DAILY', 'hod', 'manager')""",
                (loc_id, checklist_json))
        conn.commit()
    conn.close()


def qms_startup():
    ensure_qms_tables()
    ensure_module_columns()
    _migrate_staff_roles()
    seed_settings()
    seed_locations()
    seed_clusters()
    seed_schedules()
    seed_staff()
    seed_qms_modules()
    seed_critical_call_test_units()


def _migrate_staff_roles():
    conn = get_qms_conn()
    c = conn.cursor()
    role_map = {
        "Manager":      "manager",
        "QA":           "qa",
        "HOD":          "hod",
        "MLT":          "mlt",
        "Phlebotomist": "phlebotomist",
        "Director":     "director",
        "Housekeeper":  "housekeeper",
    }
    for old, new in role_map.items():
        c.execute("UPDATE staff SET role = ? WHERE role = ?", (new, old))
    conn.commit()
    conn.close()


BENCH_DECON_WRITE_ROLES = ["mlt", "hod", "qa", "manager", "phlebotomist"]
BENCH_DECON_READ_ROLES  = ["mlt", "hod", "qa", "manager", "phlebotomist",
                            "director"]
ARCHIVE_READ_ROLES      = ["hod", "qa", "manager", "director"]
ARCHIVE_SIGNOFF_ROLES   = ["hod", "manager"]
DOC_ARCHIVE_READ_ROLES  = ["qa", "manager"]
LOT_APPROVAL_ROLES      = ["hod", "manager"]
STOCK_RECEIVE_ROLES     = ["manager"]
LAB_OVERVIEW_ROLES      = ["manager", "qa", "director"]
HOUSEKEEPING_WRITE_ROLES = ["housekeeper", "manager"]
COMMUNICATIONS_WRITE_ROLES = ["mlt", "hod", "qa", "manager", "phlebotomist"]
COMMUNICATIONS_READ_ROLES  = ["mlt", "hod", "qa", "manager", "phlebotomist",
                               "director"]


def check_access(role, required_roles):
    if role is None:
        raise PermissionError("No role supplied. Cannot verify access.")
    if role.lower() in [r.lower() for r in required_roles]:
        return True
    raise PermissionError(
        f"Access denied. Required: {', '.join(required_roles)}. "
        f"Your role: {role}."
    )


def can_access(role, required_roles):
    if role is None:
        return False
    return role.lower() in [r.lower() for r in required_roles]


def get_setting(key, default=""):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else default


def get_full_name(familiar_name):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT full_name FROM staff WHERE familiar_name = ? AND is_active = 1",
              (familiar_name,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else familiar_name


def get_staff_role_from_db(familiar_name):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT role FROM staff WHERE familiar_name = ? AND is_active = 1",
              (familiar_name,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def get_working_departments():
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""SELECT DISTINCT working_dept FROM department_clusters
                 ORDER BY working_dept""")
    depts = [r[0] for r in c.fetchall()]
    conn.close()
    return depts


def get_cluster_locations(working_dept):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT l.location_name, dc.is_default_active
        FROM department_clusters dc
        JOIN locations l ON l.location_id = dc.location_id
        WHERE dc.working_dept = ?
        ORDER BY dc.display_order
    """, (working_dept,))
    rows = c.fetchall()
    conn.close()
    return rows


def is_holiday(on_date):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT holiday_name FROM public_holidays WHERE holiday_date = ?",
              (on_date,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


_WEEKDAY_COLUMNS = [
    "operates_monday", "operates_tuesday", "operates_wednesday",
    "operates_thursday", "operates_friday", "operates_saturday",
    "operates_sunday",
]


def is_location_open(location_name, on_date=None):
    if on_date is None:
        on_date = today_iso()
    dt = datetime.strptime(on_date, "%Y-%m-%d").date()
    weekday_col = _WEEKDAY_COLUMNS[dt.weekday()]

    conn = get_qms_conn()
    c = conn.cursor()
    c.execute(f"""
        SELECT s.{weekday_col}, s.operates_holidays
        FROM location_schedules s
        JOIN locations l ON l.location_id = s.location_id
        WHERE l.location_name = ?
    """, (location_name,))
    row = c.fetchone()
    conn.close()

    if not row:
        return True, "No schedule set — assumed open"
    operates_today, operates_holidays = row
    holiday_name = is_holiday(on_date)
    if holiday_name and not operates_holidays:
        return False, f"Closed — {holiday_name}"
    if not operates_today:
        return False, "Closed — not a working day here"
    return True, "Open"


def get_checklist_items(location_name):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT m.checklist_items
        FROM qms_modules m
        JOIN locations l ON l.location_id = m.location_id
        WHERE m.module_name = 'BENCH_DECON' AND l.location_name = ?
              AND m.is_active = 1
    """, (location_name,))
    row = c.fetchone()
    conn.close()
    if row and row[0]:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    return BENCH_DECON_CHECKLIST


def get_todays_entry(location_name, working_dept, on_date=None, role=None):
    if role and not can_access(role, BENCH_DECON_READ_ROLES):
        return None

    if on_date is None:
        on_date = today_iso()
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT log_id, performed_by_full_name, recorded_at,
               check_cleanliness, aniosyme_wipe, general_cleaning
        FROM bench_decontamination_log
        WHERE entry_date = ? AND location_name = ? AND working_department = ?
        ORDER BY log_id DESC LIMIT 1
    """, (on_date, location_name, working_dept))
    row = c.fetchone()
    conn.close()
    return row


def submit_bench_decon(location_name, working_dept, performed_by_full_name,
                       check_cleanliness, aniosyme_wipe, general_cleaning,
                       comments=None, session_id=None, role=None):
    if role:
        check_access(role, BENCH_DECON_WRITE_ROLES)

    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO bench_decontamination_log
        (entry_date, session_id, location_name, working_department,
         performed_by_full_name, check_cleanliness, aniosyme_wipe,
         general_cleaning, recorded_at, comments, month_year, period_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
    """, (today_iso(), session_id, location_name, working_dept,
          performed_by_full_name,
          1 if check_cleanliness else 0,
          1 if aniosyme_wipe else 0,
          1 if general_cleaning else 0,
          now_iso(), comments, this_month_year()))
    log_id = c.lastrowid
    conn.commit()
    conn.close()
    return log_id


def get_cluster_today_status(working_dept, on_date=None, role=None):
    if on_date is None:
        on_date = today_iso()
    status = []
    for location_name, _default_active in get_cluster_locations(working_dept):
        open_today, reason = is_location_open(location_name, on_date)
        entry = get_todays_entry(location_name, working_dept, on_date, role=role)
        status.append({
            "location": location_name,
            "open":     open_today,
            "reason":   reason,
            "done":     entry is not None,
            "entry":    entry,
        })
    return status


def get_completed_period_archive(role):
    check_access(role, ARCHIVE_READ_ROLES)
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT period_id, module_type, location_name, period_month_year,
               signed_off_by, signed_off_at, record_count,
               compliance_rate, is_locked
        FROM completed_period_archive
        ORDER BY signed_off_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def get_document_version_archive(role):
    check_access(role, DOC_ARCHIVE_READ_ROLES)
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT archive_id, document_id, version_number,
               superseded_by, superseded_at, change_reason
        FROM document_version_archive
        ORDER BY superseded_at DESC
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def get_test_unit_options(working_dept=None):
    """All known test→unit pairs, lab-wide — NOT scoped by zone.
    Any MLT in any department can see and pick any test (e.g. TB can
    be flagged critical from Haematology, Biochemistry, or Micro).
    working_dept is accepted but ignored on read; kept so existing
    call sites don't need to change their argument list."""
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""SELECT DISTINCT test_name, unit FROM critical_call_test_units
                 ORDER BY test_name""")
    rows = c.fetchall()
    conn.close()
    return rows


def get_unit_for_test(working_dept, test_name):
    """Lab-wide lookup — not zone-scoped. Matching is case-insensitive
    and trims whitespace. Returns None if the test isn't configured
    anywhere yet (the form falls back to free-text entry)."""
    if not test_name:
        return None
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""SELECT unit FROM critical_call_test_units
                 WHERE LOWER(TRIM(test_name)) = LOWER(TRIM(?))
                 LIMIT 1""", (test_name,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def add_or_update_test_unit(working_dept, test_name, unit, role):
    """HOD/manager only — Law 1, settings in the database. Still
    stamps working_dept (whoever added it), purely for provenance —
    it does NOT restrict who can see or use the test afterward."""
    check_access(role, ["hod", "manager"])
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""INSERT OR REPLACE INTO critical_call_test_units
        (working_department, test_name, unit) VALUES (?, ?, ?)""",
        (working_dept, test_name.strip(), unit.strip()))
    conn.commit()
    conn.close()


def submit_communication(module_name, working_dept, performed_by_full_name,
                         location, read_back, mode_of_communication,
                         extra_fields, session_id=None, role=None):
    if role:
        check_access(role, COMMUNICATIONS_WRITE_ROLES)

    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO communications_log
        (module_name, entry_date, working_department, performed_by_full_name,
         time_reported, location, read_back, mode_of_communication,
         fields_json, recorded_at, session_id, month_year)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (module_name, today_iso(), working_dept, performed_by_full_name,
          now_iso(), location, 1 if read_back else 0, mode_of_communication,
          json.dumps(extra_fields), now_iso(), session_id, this_month_year()))
    comm_id = c.lastrowid
    conn.commit()
    conn.close()
    return comm_id


def get_communications_for_zone(module_name, working_dept, role=None,
                                limit=50):
    if role and not can_access(role, COMMUNICATIONS_READ_ROLES):
        return []

    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT comm_id, entry_date, performed_by_full_name, time_reported,
               location, read_back, mode_of_communication, fields_json,
               recorded_at
        FROM communications_log
        WHERE module_name = ? AND working_department = ?
        ORDER BY comm_id DESC LIMIT ?
    """, (module_name, working_dept, limit))
    rows = c.fetchall()
    conn.close()

    results = []
    for row in rows:
        (comm_id, entry_date, performed_by, time_reported, location,
         read_back, mode, fields_json, recorded_at) = row
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
            **extra,
        })
    return results


def get_today_communications_count(working_dept):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("""
        SELECT module_name, COUNT(*) FROM communications_log
        WHERE working_department = ? AND entry_date = ?
        GROUP BY module_name
    """, (working_dept, today_iso()))
    rows = c.fetchall()
    conn.close()
    return {module_name: count for module_name, count in rows}


QMS_CSS = """
<style>
#MainMenu {visibility: hidden;}
header {visibility: hidden;}
footer {visibility: hidden;}

.qms-topbar {
    background: #d32f2f;
    padding: 10px 20px;
    border-radius: 8px 8px 0 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0;
}
.qms-topbar-left { color: #fff; font-size: 16px; font-weight: 600; letter-spacing: 0.3px; }
.qms-topbar-right { display: flex; align-items: center; gap: 14px; color: rgba(255,255,255,0.88); font-size: 13px; }
.qms-bell { position: relative; font-size: 18px; cursor: pointer; }
.qms-bell-badge { position: absolute; top: -5px; right: -7px; background: #ffd600; color: #333; font-size: 9px; font-weight: 700; border-radius: 10px; padding: 1px 5px; min-width: 16px; text-align: center; }

.qms-subbar { background: #29abe2; padding: 5px 20px; border-radius: 0; display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.qms-subbar-left { color: #fff; font-size: 13px; }
.qms-dept-pill { background: rgba(255,255,255,0.22); border-radius: 20px; padding: 2px 12px; color: #fff; font-size: 12px; font-weight: 500; }

.qms-metrics { display: grid; gap: 10px; margin-bottom: 16px; }
.qms-metrics-3 { grid-template-columns: repeat(3, 1fr); }
.qms-metrics-4 { grid-template-columns: repeat(4, 1fr); }
.qms-metrics-2 { grid-template-columns: repeat(2, 1fr); }
.qms-metric { background: #fff; border: 0.5px solid #e0e0e0; border-radius: 10px; padding: 12px 10px; text-align: center; }
.qms-metric-red   { border-top: 3px solid #d32f2f; }
.qms-metric-amber { border-top: 3px solid #ba7517; }
.qms-metric-green { border-top: 3px solid #3b6d11; }
.qms-metric-blue  { border-top: 3px solid #29abe2; }
.qms-metric-num { font-size: 28px; font-weight: 700; line-height: 1; margin-bottom: 4px; }
.qms-metric-num-red   { color: #d32f2f; }
.qms-metric-num-amber { color: #ba7517; }
.qms-metric-num-green { color: #3b6d11; }
.qms-metric-num-blue  { color: #29abe2; }
.qms-metric-label { font-size: 11px; color: #777; }

.qms-sec-hd { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; margin-top: 4px; }
.qms-sec-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.qms-sec-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; }
.qms-sec-count { font-size: 11px; color: #999; margin-left: auto; }

.qms-row-list { background: #fff; border: 0.5px solid #e0e0e0; border-radius: 10px; overflow: hidden; margin-bottom: 10px; }
.qms-row { display: flex; align-items: center; gap: 10px; padding: 9px 14px; border-bottom: 0.5px solid #f0f0f0; }
.qms-row:last-child { border-bottom: none; }
.qms-row-bar { width: 3px; height: 30px; border-radius: 2px; flex-shrink: 0; }
.qms-bar-red   { background: #d32f2f; }
.qms-bar-amber { background: #ba7517; }
.qms-bar-green { background: #3b6d11; }
.qms-row-info { flex: 1; min-width: 0; }
.qms-row-name { font-size: 13px; font-weight: 500; color: #222; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.qms-row-sub  { font-size: 10px; color: #999; margin-top: 2px; }
.qms-row-val  { font-size: 15px; font-weight: 700; flex-shrink: 0; }
.qms-progress { height: 3px; border-radius: 2px; background: #f0f0f0; overflow: hidden; margin-top: 4px; }
.qms-progress-fill { height: 100%; border-radius: 2px; }

.qms-card { background: #fff; border: 0.5px solid #e0e0e0; border-radius: 10px; padding: 14px; margin-bottom: 10px; }
.qms-card-red-accent   { border-left: 3px solid #d32f2f; }
.qms-card-amber-accent { border-left: 3px solid #ba7517; }
.qms-card-green-accent { border-left: 3px solid #3b6d11; }
.qms-card-blue-accent  { border-left: 3px solid #29abe2; }

.qms-badge { font-size: 10px; font-weight: 600; padding: 2px 9px; border-radius: 10px; display: inline-block; }
.qms-badge-red   { background: #fff5f5; color: #d32f2f; }
.qms-badge-green { background: #eaf3de; color: #3b6d11; }
.qms-badge-amber { background: #faeeda; color: #ba7517; }
.qms-badge-blue  { background: #e6f1fb; color: #185fa5; }
.qms-badge-purple { background: #eeedfe; color: #3c3489; }

.qms-confirm { background: #e6f1fb; border: 0.5px solid #b3d9f5; border-radius: 8px; padding: 10px 14px; font-size: 13px; color: #185fa5; margin: 10px 0; }

.qms-equip-done { background: #fff; border: 0.5px solid #e0e0e0; border-top: 2px solid #3b6d11; border-radius: 10px; padding: 12px 14px; margin-bottom: 8px; }
.qms-equip-pending { background: #fff; border: 0.5px solid #e0e0e0; border-top: 2px solid #d32f2f; border-radius: 10px; padding: 12px 14px; margin-bottom: 8px; }
.qms-equip-closed { background: #fafafa; border: 0.5px solid #e0e0e0; border-radius: 10px; padding: 12px 14px; margin-bottom: 8px; opacity: 0.6; }
.qms-equip-name { font-size: 14px; font-weight: 600; color: #222; margin-bottom: 2px; }
.qms-equip-sub  { font-size: 11px; color: #999; }
.qms-done-stamp { font-size: 11px; color: #3b6d11; font-weight: 500; margin-top: 6px; }

.qms-door-greeting { text-align: center; padding: 32px 10px 10px; }
.qms-door-name { font-size: 42px; font-weight: 800; color: #1a1a1a; letter-spacing: -1px; line-height: 1.1; }
.qms-door-motto { font-size: 21px; font-weight: 700; letter-spacing: 0.04em; margin-top: 12px; }
.qms-door-divider { width: 60px; height: 2px; margin: 18px auto; border-radius: 2px; background: linear-gradient(90deg, #29abe2, #d32f2f); }
.qms-door-prompt { text-align: center; font-size: 16px; color: #666; margin-bottom: 20px; }
.qms-door-footer { text-align: center; font-size: 11px; color: #bbb; margin-top: 20px; }

.qms-director-banner { background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); border-radius: 10px; padding: 16px 20px; margin-bottom: 16px; display: flex; align-items: center; gap: 14px; }
.qms-director-label { color: rgba(255,255,255,0.6); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; }
.qms-director-name { color: #fff; font-size: 18px; font-weight: 700; margin-top: 2px; }
.qms-director-tag { background: rgba(255,255,255,0.12); border-radius: 20px; padding: 2px 12px; color: rgba(255,255,255,0.8); font-size: 11px; }

.stButton > button { border-radius: 8px !important; font-weight: 600 !important; font-size: 13px !important; }
.qms-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.qms-table th { background: #d32f2f; color: #fff; padding: 7px 10px; text-align: left; font-weight: 500; }
.qms-table td { padding: 7px 10px; border-bottom: 0.5px solid #f0f0f0; color: #333; }
.qms-table tr:nth-child(even) td { background: #fafafa; }
</style>
"""


def inject_css():
    import streamlit as st
    st.markdown(QMS_CSS, unsafe_allow_html=True)


def _greeting():
    h = datetime.now().hour
    if 5 <= h < 12:  return "🌅 Good morning"
    if 12 <= h < 17: return "☀️ Good afternoon"
    if 17 <= h < 21: return "🌆 Good evening"
    return "🌙 Good night"


def _initials(name):
    parts = name.replace("Dr ", "").strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return name[:2].upper() if name else "?"


_ZONE_PILL_COLORS = {
    "Haematology":  ("#fcebeb", "#a32d2d"),
    "Biochemistry": ("#e6f1fb", "#185fa5"),
    "Parasitology": ("#eaf3de", "#3b6d11"),
    "Phlebotomy":   ("#faeeda", "#854f0b"),
    "MCH":          ("#eeedfe", "#3c3489"),
    "Office":       ("#f0f0f0", "#555"),
}


def render_topbar(name, role, notif_count=0):
    """Modern header: white card, two brand dots, avatar chip.
    Replaces the old flat red bar everywhere — this is now the only
    topbar style used across the app (see standing design rule)."""
    import streamlit as st
    initials = _initials(name)
    bell_badge = (f'<span style="position:absolute;top:-3px;right:-3px;'
                 f'background:#d32f2f;color:#fff;font-size:9px;'
                 f'font-weight:600;border-radius:10px;min-width:16px;'
                 f'height:16px;display:flex;align-items:center;'
                 f'justify-content:center;padding:0 3px;">{notif_count}'
                 f'</span>' if notif_count > 0 else '')
    st.markdown(f"""
        <div style="background:#fff;border-radius:12px 12px 0 0;
             padding:12px 18px;display:flex;justify-content:space-between;
             align-items:center;border-bottom:0.5px solid #e8e8e8;">
            <div style="display:flex;align-items:center;gap:10px;">
                <div style="display:flex;gap:4px;">
                    <span style="width:8px;height:8px;border-radius:50%;
                         background:#d32f2f;display:inline-block;"></span>
                    <span style="width:8px;height:8px;border-radius:50%;
                         background:#29abe2;display:inline-block;"></span>
                </div>
                <span style="font-size:14px;font-weight:600;color:#1a1a1a;">
                    St Mary's Mission Hospital</span>
            </div>
            <div style="display:flex;align-items:center;gap:12px;">
                <div style="position:relative;width:32px;height:32px;
                     border-radius:50%;border:0.5px solid #e0e0e0;
                     display:flex;align-items:center;justify-content:center;">
                    🔔{bell_badge}
                </div>
                <div style="display:flex;align-items:center;gap:7px;">
                    <div style="width:28px;height:28px;border-radius:50%;
                         background:#d32f2f;color:#fff;font-size:10px;
                         font-weight:600;display:flex;align-items:center;
                         justify-content:center;">{initials}</div>
                    <div>
                        <div style="font-size:12px;font-weight:600;
                             color:#1a1a1a;">{name}</div>
                        <div style="font-size:9px;color:#999;
                             text-transform:uppercase;">{role}</div>
                    </div>
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)


def render_subbar(page_title, dept_label):
    """Modern slim subbar: page title left, soft pill badge right —
    replaces the old flat blue bar."""
    import streamlit as st
    bg, fg = "#fcebeb", "#a32d2d"
    for zone, (zbg, zfg) in _ZONE_PILL_COLORS.items():
        if zone in dept_label:
            bg, fg = zbg, zfg
            break
    st.markdown(f"""
        <div style="background:#fff;padding:8px 18px;display:flex;
             justify-content:space-between;align-items:center;
             border-bottom:0.5px solid #e8e8e8;margin-bottom:16px;
             border-radius:0 0 12px 12px;">
            <span style="font-size:13px;color:#555;">{page_title}</span>
            <span style="background:{bg};color:{fg};border-radius:16px;
                 padding:3px 12px;font-size:11px;font-weight:500;">
                 {dept_label}</span>
        </div>
    """, unsafe_allow_html=True)


def _dept_pill_label(name, role, working_dept=None):
    dept_icons = {
        "Haematology": "🩸", "Biochemistry": "🧪", "Parasitology": "🔬",
        "Phlebotomy": "💉", "MCH": "🤱", "Office": "🗂️",
        "Administration": "🏥",
    }
    if role == "director":
        return "🔭 Lab Director"
    if role in ("manager", "qa"):
        return "🏥 Management"
    if working_dept:
        icon = dept_icons.get(working_dept, "📍")
        return f"{icon} {working_dept}"
    return "📍 Lab"


def create_session(familiar_name, full_name, role, working_dept,
                   session_type, active_locations):
    conn = get_qms_conn()
    c = conn.cursor()
    c.execute("SELECT staff_id, role, default_department FROM staff "
              "WHERE familiar_name = ?", (familiar_name,))
    row       = c.fetchone()
    staff_id  = row[0] if row else None
    staff_role = row[1] if row else role
    default_d  = row[2] if row else None
    c.execute("""
        INSERT INTO sessions
        (staff_id, staff_full_name, familiar_name, role, working_department,
         default_department, session_type, active_locations, login_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (staff_id, full_name, familiar_name, staff_role, working_dept,
          default_d, session_type, json.dumps(active_locations), now_iso()))
    sid = c.lastrowid
    conn.commit()
    conn.close()
    return sid


def _begin_session(name, full_name, role, working_dept, session_type):
    import streamlit as st
    active = []
    if session_type == "FULL_SHIFT" and working_dept not in ("Administration",):
        active = [loc for loc, _ in get_cluster_locations(working_dept)]
    sid = create_session(name, full_name, role, working_dept, session_type, active)
    st.session_state.working_department = working_dept
    st.session_state.session_type       = session_type
    st.session_state.active_locations   = active
    st.session_state.qms_session_id     = sid
    st.session_state.session_ready      = True


def render_session_setup(name, role):
    import streamlit as st

    full_name = get_full_name(name)
    raw_motto = get_setting("hospital_motto", "One Team One Direction")
    parts       = raw_motto.split("One Direction")
    motto_left  = parts[0]
    motto_right = "One Direction" + (parts[1] if len(parts) > 1 else "")

    st.markdown("""
        <div style="display:flex;align-items:center;justify-content:center;
                    gap:6px;margin-top:20px;margin-bottom:0;">
            <span style="width:8px;height:8px;border-radius:50%;
                         background:#d32f2f;display:inline-block;"></span>
            <span style="font-size:11px;color:#aaa;letter-spacing:0.08em;">
                ST MARY'S MISSION HOSPITAL</span>
            <span style="width:8px;height:8px;border-radius:50%;
                         background:#29abe2;display:inline-block;"></span>
        </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
        <div class="qms-door-greeting">
            <div class="qms-door-name">{_greeting()}, {name}!</div>
            <div class="qms-door-motto">
                <span style="color:#29abe2;">{motto_left}</span>
                <span style="color:#d32f2f;">{motto_right}</span>
            </div>
        </div>
        <div class="qms-door-divider"></div>
        <p class="qms-door-prompt">Which department are you working in today?</p>
    """, unsafe_allow_html=True)

    dept_icons = {
        "Haematology": "🩸", "Biochemistry": "🧪", "Parasitology": "🔬",
        "Phlebotomy": "💉", "MCH": "🤱", "Office": "🗂️",
    }

    all_depts = get_working_departments()
    if "Phlebotomy" in all_depts:
        all_depts.remove("Phlebotomy")
        all_depts.insert(0, "Phlebotomy")

    st.markdown("""
        <style>
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button {
            background: #ffffff !important;
            border: 1.5px solid #e0e0e0 !important;
            border-radius: 20px !important;
            padding: 22px 10px 18px !important;
            width: 100% !important;
            height: 150px !important;
            white-space: pre-line !important;
            transition: border-color 0.15s, box-shadow 0.15s, transform 0.12s !important;
            box-shadow: 0 1px 5px rgba(0,0,0,0.07) !important;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button p {
            font-size: 15px !important;
            font-weight: 900 !important;
            color: #1a1a1a !important;
            white-space: pre-line !important;
            line-height: 1.5 !important;
            letter-spacing: 0.2px !important;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button p::first-line {
            font-size: 38px !important;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button:hover {
            border-color: #29abe2 !important;
            box-shadow: 0 6px 18px rgba(41,171,226,0.18) !important;
            transform: translateY(-3px) !important;
            background: #f8fbff !important;
        }
        div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] > button:active {
            transform: translateY(0) !important;
            box-shadow: 0 1px 5px rgba(0,0,0,0.07) !important;
        }
        button[data-testid="baseButton-secondary"][aria-label="door_Phlebotomy"],
        div[data-testid="stButton"]:has(button[aria-label*="Phlebotomy"]) button {
            border: 2.5px solid #29abe2 !important;
            box-shadow: 0 2px 14px rgba(41,171,226,0.22) !important;
        }
        .main .block-container { background: #fafaf8 !important; }
        </style>
    """, unsafe_allow_html=True)

    cols_per_row = 3
    for row_start in range(0, len(all_depts), cols_per_row):
        row_depts = all_depts[row_start: row_start + cols_per_row]
        cols = st.columns(len(row_depts))
        for col, dept in zip(cols, row_depts):
            icon = dept_icons.get(dept, "🏥")
            with col:
                if st.button(f"{icon}\n{dept}", key=f"door_{dept}",
                             use_container_width=True, type="secondary"):
                    _begin_session(name, full_name, role, dept, "FULL_SHIFT")
                    st.session_state.page = ("lab_overview"
                                             if role == "director" else "home_dashboard")
                    st.rerun()

    st.markdown(
        '<p class="qms-door-footer">Tap once — your session opens immediately.'
        ' Your choice is locked for this session.</p>',
        unsafe_allow_html=True)


FORM_FIELD_CSS = """
<style>
.qms-form-section-red  { background: #fcebeb; color: #a32d2d; }
.qms-form-section-blue { background: #e6f1fb; color: #185fa5; }
.qms-form-section {
    font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.06em; padding: 8px 14px; border-radius: 8px 8px 0 0;
    margin-bottom: 8px;
}

div[data-testid="stTextInput"] input,
div[data-testid="stNumberInput"] input {
    border: 0.5px solid #e0e0e0 !important;
    border-radius: 8px !important;
    background: #ffffff !important;
    padding: 8px 10px !important;
    font-size: 13px !important;
    color: #1a1a1a !important;
}
div[data-testid="stTextInput"] input:focus,
div[data-testid="stNumberInput"] input:focus {
    border-color: #29abe2 !important;
    box-shadow: 0 0 0 1px #29abe2 !important;
}
div[data-testid="stTextInput"] label,
div[data-testid="stNumberInput"] label,
div[data-testid="stRadio"] label[data-baseweb] {
    font-size: 12px !important;
    color: #555 !important;
    font-weight: 500 !important;
}

div[data-testid="stRadio"] > div[role="radiogroup"] {
    flex-direction: row !important;
    gap: 6px !important;
    flex-wrap: wrap;
}
div[data-testid="stRadio"] > div[role="radiogroup"] label {
    background: #f5f5f3 !important;
    border-radius: 16px !important;
    padding: 6px 14px !important;
    font-size: 12px !important;
    border: 0.5px solid #e0e0e0 !important;
    cursor: pointer !important;
    margin: 0 !important;
}

div[data-testid="stSelectbox"] > div {
    border: 0.5px solid #e0e0e0 !important;
    border-radius: 8px !important;
}

.qms-units-auto input {
    border: 0.5px solid #29abe2 !important;
    background: #e6f1fb !important;
    color: #185fa5 !important;
    font-weight: 600 !important;
}
</style>
"""


def inject_form_css():
    import streamlit as st
    st.markdown(FORM_FIELD_CSS, unsafe_allow_html=True)


ISO_RATIONALE_BENCH_DECON = {
    0: "Visual inspection of all work surfaces · ISO 15189:2022 cl. 6.3.2",
    1: "Validated decontamination procedure · 25ml Aniosyme in 5L water · 5min contact · dry with towel · cl. 6.3.2",
    2: "Benchtop, equipment surfaces and waste disposal · cl. 6.3.2, 8.9.1",
}

ISO_RATIONALE_FRIDGE_MAINT = {
    0: "Daily temperature record required · STMMHL-LOG-041 cl. 6.3.2",
    1: "Mild detergent only · avoid solvents that may affect seals",
    2: "Prevents contamination and false stock · cl. 5.4",
    3: "Maintains cold-chain integrity · door seal failure risks temperature excursion",
}

MODULE_ISO_RATIONALE = {
    "BENCH_DECON": ISO_RATIONALE_BENCH_DECON,
    "FRIDGE_MAINT": ISO_RATIONALE_FRIDGE_MAINT,
}

MODULE_ICONS_EMOJI = {
    "BENCH_DECON": "🧼",
    "FRIDGE_MAINT": "🧊",
}


# ============================================================================
#  qms.py — CHARTS PAGE  v3  (navigation fixed)
# ----------------------------------------------------------------------------
#  INSTALL — replace exactly TWO functions:
#    1. In qms.py, find the line:   def render_charts_page(name, role):
#    2. Delete everything from that line DOWN TO (but NOT including) the line:
#                                    def _compact_row(label, key, ...
#       That deletes the old render_charts_page AND the old
#       _render_module_checklist_body — they sit back-to-back.
#    3. Paste everything below in their place.
#    4. Save. Stop Streamlit (Ctrl+C) and start it again.
#
#  WHAT WAS WRONG (root cause):
#    A per-render counter (charts_render_n) was baked into every widget key.
#    It increments on every rerun, so the key of a button on the rerun that
#    PROCESSES a click no longer matches the key it had when CLICKED.
#    Streamlit therefore never detects the click → no page ever opens.
#    The counter was an unnecessary "fix" for a duplicate-key error that was
#    really caused by the router (already fixed in lab_app.py).
#
#  THE FIX:
#    • Counter removed. Stable, unique keys used everywhere.
#    • tick_emoji is now defined in the done-state list (was undefined → crash).
# ============================================================================


def render_charts_page(name, role):
    import streamlit as st

    if not can_access(role, BENCH_DECON_READ_ROLES):
        st.error("You do not have access to charts.")
        return

    full_name    = get_full_name(name)
    working_dept = st.session_state.get("working_department")
    pill_label   = _dept_pill_label(name, role, working_dept)
    is_read_only = (role == "director")

    # navigation state
    if "charts_location" not in st.session_state:
        st.session_state["charts_location"] = None
    if "charts_module" not in st.session_state:
        st.session_state["charts_module"] = None

    render_topbar(name, role)

    if is_read_only:
        st.markdown(
            '<div style="background:#f0f0f0;border:0.5px solid #ddd;'
            'border-radius:12px;padding:12px 16px;display:flex;'
            'align-items:center;gap:12px;margin-bottom:12px;">'
            '<span style="font-size:22px;">🔭</span>'
            '<div><div style="font-size:13px;font-weight:700;color:#333;">'
            'Director view</div>'
            '<div style="font-size:12px;color:#777;">Read-only · cannot submit</div>'
            '</div></div>',
            unsafe_allow_html=True)

    if not working_dept or working_dept == "Administration":
        depts = [d for d in get_working_departments() if d != "Office"]
        working_dept = st.selectbox("Working department", depts)

    sel_location = st.session_state["charts_location"]
    sel_module   = st.session_state["charts_module"]

    pill_bg, pill_fg = "#fcebeb", "#791F1F"
    for zone, (zbg, zfg) in _ZONE_PILL_COLORS.items():
        if zone in pill_label:
            pill_bg, pill_fg = zbg, zfg
            break

    # =====================================================================
    #  LEVEL 1 — zone landing: location cards
    # =====================================================================
    if sel_location is None:

        st.markdown(
            '<div style="background:#f4f3ec;border:0.5px solid #e6e4d8;'
            'border-radius:12px;padding:10px 16px;display:flex;'
            'align-items:center;gap:10px;margin-bottom:16px;">'
            '<span style="font-size:15px;">📋</span>'
            '<span style="font-size:14px;font-weight:700;color:#1a1a1a;">Charts</span>'
            f'<span style="margin-left:auto;background:{pill_bg};color:{pill_fg};'
            f'border-radius:999px;padding:3px 13px;font-size:12px;'
            f'font-weight:700;">{pill_label}</span></div>',
            unsafe_allow_html=True)

        summary = get_zone_compliance_summary(working_dept, role=role)
        daily   = [s for s in summary
                   if MODULE_REGISTRY.get(s["module_name"], {}).get("frequency") == "DAILY"]
        total_done    = sum(s["done"] for s in daily)
        total_pending = sum(max(0, s["total"] - s["done"]) for s in daily)

        mc1, mc2 = st.columns(2)
        with mc1:
            st.markdown(
                '<div style="background:#EAF3DE;border-radius:12px;'
                'padding:15px 16px;margin-bottom:14px;">'
                '<div style="font-size:11px;font-weight:700;color:#27500A;'
                'text-transform:uppercase;letter-spacing:0.07em;">Done today</div>'
                f'<div style="font-size:30px;font-weight:700;color:#085041;'
                f'line-height:1.15;margin-top:4px;">{total_done}</div></div>',
                unsafe_allow_html=True)
        with mc2:
            st.markdown(
                '<div style="background:#FCEBEB;border-radius:12px;'
                'padding:15px 16px;margin-bottom:14px;">'
                '<div style="font-size:11px;font-weight:700;color:#791F1F;'
                'text-transform:uppercase;letter-spacing:0.07em;">Pending</div>'
                f'<div style="font-size:30px;font-weight:700;color:#A32D2D;'
                f'line-height:1.15;margin-top:4px;">{total_pending}</div></div>',
                unsafe_allow_html=True)

        st.markdown(
            '<div style="font-size:11px;font-weight:700;color:#999;'
            'text-transform:uppercase;letter-spacing:0.09em;'
            'margin-bottom:10px;">Locations in your zone</div>',
            unsafe_allow_html=True)

        locations = get_cluster_locations(working_dept)
        if not locations:
            st.info("No locations configured for this zone yet.")
            return

        _LOC_TILE = {
            "Haematology":     ("#FCEBEB", "#A32D2D", "🩸"),
            "Transfusion":     ("#E6F1FB", "#185FA5", "🩸"),
            "Biochemistry":    ("#FCEBEB", "#A32D2D", "🧪"),
            "Immunology":      ("#EEEDFE", "#3C3489", "🛡️"),
            "Serology":        ("#FAEEDA", "#854F0B", "🧫"),
            "Parasitology":    ("#EAF3DE", "#27500A", "🔬"),
            "Microbiology":    ("#E1F5EE", "#085041", "🦠"),
            "Phlebotomy":      ("#FAEEDA", "#854F0B", "💉"),
            "Phlebotomy Room": ("#FAEEDA", "#854F0B", "💉"),
            "MCH":             ("#EEEDFE", "#3C3489", "👶"),
            "Reception":       ("#F1EFE8", "#444441", "📋"),
            "Office":          ("#F1EFE8", "#444441", "🏢"),
        }

        def _loc_pending_count(loc_name):
            count = 0
            for mod in daily:
                if not get_module_checklist_items(mod["module_name"], loc_name):
                    continue
                if not get_todays_module_entry(mod["module_name"], loc_name,
                                               working_dept, role=role):
                    count += 1
            return count

        cols = st.columns(2)
        for idx, (loc_name, _) in enumerate(locations):
            open_today, _ = is_location_open(loc_name)
            tile_bg, tile_fg, tile_icon = _LOC_TILE.get(
                loc_name, ("#F1EFE8", "#444441", "📋"))

            applicable = [mod["label"].lower() for mod in daily
                          if get_module_checklist_items(mod["module_name"], loc_name)]
            subtitle = " · ".join(applicable) if applicable else "no modules configured"

            loc_pending = _loc_pending_count(loc_name) if open_today else 0

            if not open_today:
                chip = ('<span style="background:#F1EFE8;color:#5F5E5A;'
                        'font-size:11px;font-weight:700;padding:3px 10px;'
                        'border-radius:999px;">Closed</span>')
            elif loc_pending == 0:
                chip = ('<span style="background:#EAF3DE;color:#27500A;'
                        'font-size:11px;font-weight:700;padding:3px 10px;'
                        'border-radius:999px;">All done</span>')
            else:
                chip = (f'<span style="background:#FAEEDA;color:#633806;'
                        f'font-size:11px;font-weight:700;padding:3px 10px;'
                        f'border-radius:999px;">{loc_pending} pending</span>')

            with cols[idx % 2]:
                st.markdown(
                    '<div style="background:#fff;border:0.5px solid #e0e0e0;'
                    'border-radius:14px;padding:16px;margin-bottom:4px;">'
                    '<div style="display:flex;align-items:center;'
                    'justify-content:space-between;margin-bottom:12px;">'
                    f'<div style="width:42px;height:42px;border-radius:11px;'
                    f'background:{tile_bg};display:flex;align-items:center;'
                    f'justify-content:center;">'
                    f'<span style="font-size:21px;">{tile_icon}</span></div>'
                    f'{chip}</div>'
                    f'<div style="font-size:15px;font-weight:700;color:#1a1a1a;">'
                    f'{loc_name}</div>'
                    f'<div style="font-size:12px;color:#888;margin-top:3px;'
                    f'margin-bottom:4px;">{subtitle}</div></div>',
                    unsafe_allow_html=True)

                # STABLE KEY — no counter. This is what makes the click register.
                if open_today:
                    if st.button(f"Open  {loc_name} ›",
                                 key=f"loc_open_{loc_name}",
                                 use_container_width=True):
                        st.session_state["charts_location"] = loc_name
                        st.session_state["charts_module"]   = None
                        st.rerun()
                else:
                    st.button(f"Closed — {loc_name}",
                              key=f"loc_closed_{loc_name}",
                              use_container_width=True,
                              disabled=True)

                st.write("")

        return   # end Level 1

    # =====================================================================
    #  LEVELS 2 & 3 — inside a location
    # =====================================================================

    back_col, title_col = st.columns([3, 7])
    with back_col:
        if st.button("← Charts", key="charts_back_btn"):
            st.session_state["charts_location"] = None
            st.session_state["charts_module"]   = None
            st.rerun()
    with title_col:
        st.markdown(
            f'<div style="padding-top:6px;font-size:14px;font-weight:700;'
            f'color:#1a1a1a;">{sel_location}</div>',
            unsafe_allow_html=True)

    st.markdown(
        '<div style="height:0.5px;background:#e6e4d8;margin:8px 0 14px;"></div>',
        unsafe_allow_html=True)

    open_today, closed_reason = is_location_open(sel_location)
    if not open_today:
        st.markdown(
            f'<div style="background:#F1EFE8;border:0.5px solid #D3D1C7;'
            f'border-radius:12px;padding:14px 16px;">'
            f'<div style="font-size:14px;font-weight:700;color:#2C2C2A;">'
            f'{sel_location} is closed today</div>'
            f'<div style="font-size:12px;color:#5F5E5A;margin-top:4px;">'
            f'{closed_reason}</div></div>',
            unsafe_allow_html=True)
        return

    summary = get_zone_compliance_summary(working_dept, role=role)
    daily   = [s for s in summary
               if MODULE_REGISTRY.get(s["module_name"], {}).get("frequency") == "DAILY"]

    applicable_modules = [
        mod for mod in daily
        if get_module_checklist_items(mod["module_name"], sel_location)
    ]

    if not applicable_modules:
        st.info(f"No daily chart modules configured for {sel_location} yet.")
        return

    loc_pend = sum(
        1 for mod in applicable_modules
        if not get_todays_module_entry(mod["module_name"], sel_location,
                                       working_dept, role=role)
    )
    if loc_pend == 0:
        loc_chip = ('<span style="background:#EAF3DE;color:#27500A;font-size:11px;'
                    'font-weight:700;padding:3px 10px;border-radius:999px;">All done</span>')
    else:
        loc_chip = (f'<span style="background:#FAEEDA;color:#633806;font-size:11px;'
                    f'font-weight:700;padding:3px 10px;border-radius:999px;">'
                    f'{loc_pend} pending</span>')

    st.markdown(
        f'<div style="display:flex;align-items:center;justify-content:space-between;'
        f'margin-bottom:14px;">'
        f'<span style="font-size:13px;color:#888;">Charts in this location</span>'
        f'{loc_chip}</div>',
        unsafe_allow_html=True)

    _MODULE_TILE = {
        "BENCH_DECON":  ("#FAEEDA", "#854F0B", "🧼"),
        "FRIDGE_MAINT": ("#E6F1FB", "#185FA5", "🧊"),
    }

    for mod in applicable_modules:
        module_name = mod["module_name"]
        tile_bg, tile_fg, tile_icon = _MODULE_TILE.get(
            module_name, ("#F1EFE8", "#444441", "📋"))
        existing = get_todays_module_entry(
            module_name, sel_location, working_dept, role=role)
        is_done  = bool(existing)
        is_open  = (sel_module == module_name)

        if is_done:
            status_chip = ('<span style="background:#EAF3DE;color:#27500A;'
                           'font-size:11px;font-weight:700;padding:3px 10px;'
                           'border-radius:999px;">✓ Done</span>')
        else:
            status_chip = ('<span style="background:#FAEEDA;color:#633806;'
                           'font-size:11px;font-weight:700;padding:3px 10px;'
                           'border-radius:999px;">Pending</span>')

        st.markdown(
            '<div style="background:#f8f8f6;border:0.5px solid #e6e4d8;'
            'border-radius:12px 12px 0 0;padding:13px 16px;'
            'display:flex;align-items:center;gap:12px;margin-top:4px;">'
            f'<div style="width:38px;height:38px;border-radius:10px;'
            f'background:{tile_bg};display:flex;align-items:center;'
            f'justify-content:center;flex:none;">'
            f'<span style="font-size:19px;">{tile_icon}</span></div>'
            f'<div style="flex:1;">'
            f'<div style="font-size:14px;font-weight:700;color:#1a1a1a;">'
            f'{mod["label"]}</div>'
            f'<div style="font-size:12px;color:#888;margin-top:2px;">'
            f'Daily · ISO 15189:2022</div></div>'
            f'{status_chip}</div>',
            unsafe_allow_html=True)

        # STABLE KEY — toggles open/closed reliably now.
        btn_label = "Close ▲" if is_open else ("View ▼" if is_done else "Open ▼")
        if st.button(btn_label,
                     key=f"mod_toggle_{module_name}_{sel_location}",
                     use_container_width=True):
            st.session_state["charts_module"] = None if is_open else module_name
            st.rerun()

        if is_open:
            _render_module_checklist_body(
                module_name, sel_location, working_dept,
                full_name, role, is_read_only)

        st.markdown(
            '<div style="height:0.5px;background:#ececec;margin:2px 0 10px;"></div>',
            unsafe_allow_html=True)


def _render_module_checklist_body(module_name, location_name, working_dept,
                                   full_name, role, is_read_only):
    """Checklist items + submit. Stable widget keys (router calls charts once,
    so keys never collide and clicks register correctly)."""
    import streamlit as st

    iso_rationale = MODULE_ISO_RATIONALE.get(module_name, {})
    existing      = get_todays_module_entry(
        module_name, location_name, working_dept, role=role)
    items         = get_module_checklist_items(module_name, location_name)

    # already done → read-only ticked list
    if existing:
        checked = existing.get("checked", [])
        st.markdown(
            f'<div style="background:#fff;border:0.5px solid #e6e4d8;'
            f'border-radius:0 0 12px 12px;padding:12px 16px 4px;">'
            f'<div style="font-size:12px;color:#27500A;font-weight:700;'
            f'margin-bottom:8px;">Logged by {existing["performed_by"]} '
            f'· {existing["recorded_at"]}</div></div>',
            unsafe_allow_html=True)
        for i, label in enumerate(items):
            ticked     = i < len(checked) and checked[i]
            tick_emoji = "✅" if ticked else "⬜"     # ← was undefined before
            hint       = iso_rationale.get(i, "")
            hint_html  = (f'<div style="font-size:11px;color:#888;margin-top:2px;">'
                          f'{hint}</div>' if hint else "")
            st.markdown(
                f'<div style="background:#fff;border-left:0.5px solid #e6e4d8;'
                f'border-right:0.5px solid #e6e4d8;padding:10px 16px;'
                f'display:flex;align-items:flex-start;gap:11px;'
                f'border-bottom:0.5px solid #f4f4f4;">'
                f'<span style="font-size:16px;flex:none;margin-top:1px;">{tick_emoji}</span>'
                f'<div><div style="font-size:13px;font-weight:700;color:#1a1a1a;">'
                f'{label}</div>{hint_html}</div></div>',
                unsafe_allow_html=True)
        st.write("")
        return

    # pending + read-only (director)
    if is_read_only:
        st.markdown(
            '<div style="background:#fff;border:0.5px solid #e6e4d8;'
            'border-radius:0 0 12px 12px;padding:12px 16px;">'
            '<div style="font-size:12px;color:#888;">Entry not yet submitted '
            'by lab staff.</div></div>',
            unsafe_allow_html=True)
        return

    # pending: interactive checklist
    st.markdown(
        '<div style="background:#fff;border:0.5px solid #e6e4d8;'
        'border-radius:0 0 12px 12px;padding:4px 0 0;">',
        unsafe_allow_html=True)

    vals = []
    for i, label in enumerate(items):
        hint = iso_rationale.get(i, "")
        hint_html = (f'<div style="font-size:11px;color:#888;margin-top:2px;">'
                     f'{hint}</div>' if hint else "")
        st.markdown(
            f'<div style="padding:4px 16px 0;border-bottom:0.5px solid #f4f4f4;">',
            unsafe_allow_html=True)
        cb_col, text_col = st.columns([1, 10])
        with cb_col:
            val = st.checkbox(
                label,
                key=f"cb_{module_name}_{location_name}_{i}",
                label_visibility="collapsed")
            vals.append(val)
        with text_col:
            st.markdown(
                f'<div style="padding-top:5px;">'
                f'<div style="font-size:13px;font-weight:700;color:#1a1a1a;">'
                f'{label}</div>{hint_html}</div>',
                unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('</div>', unsafe_allow_html=True)

    st.write("")
    if st.button(
            f"✅  Submit — {location_name}",
            key=f"submit_{module_name}_{location_name}",
            use_container_width=True,
            type="primary"):
        if not any(vals):
            st.error("Tick at least one item before submitting.")
        else:
            try:
                submit_module_entry(
                    module_name, location_name, working_dept,
                    full_name, vals, role=role)
                st.session_state.flash = (
                    f"{MODULE_REGISTRY[module_name]['label']} logged "
                    f"for {location_name}.")
                st.session_state["charts_module"] = None
                st.rerun()
            except PermissionError as e:
                st.error(f"🔒 {e}")
    st.write("")

def _compact_row(label, key, placeholder="", value=None, disabled=False,
                  label_width=2, input_width=5):
    """One compact row: label fixed-width on the left, input filling
    the right. Streamlit always puts a widget's own label above it —
    that can't be overridden with CSS alone — so this hides the
    native label and draws our own beside the field using columns,
    which is what actually produces the label-left/input-right look
    from the approved mockup."""
    import streamlit as st
    lc, ic = st.columns([label_width, input_width])
    with lc:
        st.markdown(f'<div style="padding-top:8px;font-size:12px;'
                    f'color:#555;font-weight:500;">{label}</div>',
                    unsafe_allow_html=True)
    with ic:
        kwargs = {"label_visibility": "collapsed", "key": key,
                  "placeholder": placeholder}
        if value is not None:
            kwargs["value"] = value
        if disabled:
            kwargs["disabled"] = True
        return st.text_input(label, **kwargs)


def render_critical_call_page(name, role):
    """Critical Call — STMMHL-LOG-085. Test is picked from a lab-wide
    list (not zone-scoped — TB can be flagged from any department).
    Picking a known test locks Units to its configured value. If the
    test isn't in the list yet, a free-text fallback is offered so a
    real critical value is never blocked by incomplete config.
    Settings (add/edit tests) only visible to HOD/manager."""
    import streamlit as st

    if not can_access(role, COMMUNICATIONS_READ_ROLES):
        st.error("You do not have access to Critical Call.")
        return

    inject_form_css()

    full_name    = get_full_name(name)
    working_dept = st.session_state.get("working_department")
    pill_label   = _dept_pill_label(name, role, working_dept)
    is_read_only = (role == "director")
    can_edit_tests = can_access(role, ["hod", "manager"])

    render_topbar(name, role)

    # Settings gear — only rendered at all for hod/manager. Invisible
    # to mlt/phlebotomist, exactly as agreed (not their concern).
    if can_edit_tests:
        sub_cols = st.columns([9, 1])
        with sub_cols[0]:
            render_subbar("🚨 Critical Call", pill_label)
        with sub_cols[1]:
            if st.button("⚙️", key="cc_settings_btn", help="Edit tests & units"):
                st.session_state.cc_show_settings = not st.session_state.get(
                    "cc_show_settings", False)
                st.rerun()
    else:
        render_subbar("🚨 Critical Call", pill_label)

    st.caption("STMMHL-LOG-085 · Version 3 · ISO 15189:2022 cl. 5.7")
    st.write("")

    # Settings panel — hard-blocked by role even if somehow reached
    if st.session_state.get("cc_show_settings"):
        _render_test_unit_settings(role)
        st.divider()

    if is_read_only:
        st.info("Director view — read-only. See the log below.")
    else:
        k = st.session_state.get("cc_form_key", 0)

        col_left, col_right = st.columns(2)

        with col_left:
            st.markdown('<div class="qms-form-section qms-form-section-red">'
                        'PATIENT &amp; RESULT</div>', unsafe_allow_html=True)
            patient_name = _compact_row("Patient", f"cc_pname_{k}",
                                        placeholder="Type name")
            uhid = _compact_row("UHID", f"cc_uhid_{k}",
                                placeholder="Type UHID")

            all_tests = [t for t, u in get_test_unit_options()]

            tlc, tic, uic = st.columns([2, 3, 2])
            with tlc:
                st.markdown('<div style="padding-top:8px;font-size:12px;'
                           'color:#555;font-weight:500;">Test</div>',
                           unsafe_allow_html=True)
            with tic:
                if all_tests:
                    pick_options = ["— Type instead —"] + all_tests
                    picked = st.selectbox(
                        "Test", options=pick_options, key=f"cc_test_pick_{k}",
                        label_visibility="collapsed")
                else:
                    picked = "— Type instead —"
                    st.caption("No tests configured yet")
            with uic:
                if picked != "— Type instead —":
                    auto_unit = get_unit_for_test(working_dept, picked)
                else:
                    auto_unit = None
                if auto_unit:
                    st.markdown('<div class="qms-units-auto">',
                               unsafe_allow_html=True)
                    units = st.text_input("Units ✓", value=auto_unit,
                                          key=f"cc_units_locked_{k}",
                                          disabled=True,
                                          label_visibility="collapsed")
                    st.markdown('</div>', unsafe_allow_html=True)
                elif picked == "— Type instead —":
                    units = st.text_input("Units", value="",
                                          key=f"cc_units_free_{k}",
                                          placeholder="e.g. g/dL, NA",
                                          label_visibility="collapsed")
                else:
                    units = st.text_input("Units", value="",
                                          key=f"cc_units_free2_{k}",
                                          placeholder="—",
                                          label_visibility="collapsed")

            if picked == "— Type instead —":
                flc, fic = st.columns([2, 5])
                with flc:
                    st.markdown('<div style="padding-top:8px;font-size:11px;'
                               'color:#888;">Test name</div>',
                               unsafe_allow_html=True)
                with fic:
                    test_name = st.text_input(
                        "Test name", key=f"cc_test_free_{k}",
                        placeholder="Type test name",
                        label_visibility="collapsed")
            else:
                test_name = picked

            vlc, v1c, v2c = st.columns([2, 2.5, 2.5])
            with vlc:
                st.markdown('<div style="padding-top:8px;font-size:12px;'
                           'color:#555;font-weight:500;">1st / Repeat</div>',
                           unsafe_allow_html=True)
            with v1c:
                first_value = st.text_input("1st Value", key=f"cc_v1_{k}",
                                            placeholder="e.g. 1.2",
                                            label_visibility="collapsed")
            with v2c:
                repeat_value = st.text_input(
                    "Repeat", key=f"cc_v2_{k}",
                    placeholder="e.g. 1.1 / NA",
                    label_visibility="collapsed")

        with col_right:
            st.markdown('<div class="qms-form-section qms-form-section-blue">'
                        'NOTIFICATION &amp; SIGN-OFF</div>', unsafe_allow_html=True)
            time_identified = _compact_row("Identified", f"cc_tid_{k}",
                                           placeholder="e.g. 08:05")

            mlc, mic = st.columns([2, 5])
            with mlc:
                st.markdown('<div style="padding-top:8px;font-size:12px;'
                           'color:#555;font-weight:500;">Mode</div>',
                           unsafe_allow_html=True)
            with mic:
                mode = st.radio("Mode", ["Smartphone", "Extension", "In person"],
                                key=f"cc_mode_{k}", horizontal=True,
                                label_visibility="collapsed")

            notified = _compact_row("Notified", f"cc_notified_{k}",
                                    placeholder="Name & cadre")
            location = _compact_row("Location", f"cc_loc_{k}",
                                    placeholder="e.g. Room 7")

            rlc, ric = st.columns([2, 5])
            with rlc:
                st.markdown('<div style="padding-top:8px;font-size:12px;'
                           'color:#555;font-weight:500;">Read back?</div>',
                           unsafe_allow_html=True)
            with ric:
                read_back = st.radio("Read back", ["Yes", "No"],
                                     key=f"cc_rb_{k}", horizontal=True,
                                     label_visibility="collapsed")

        st.markdown(f"""
            <div class="qms-confirm">
            {full_name} · {datetime.now().strftime('%d %B %Y')} ·
            time stamped on submit
            </div>
        """, unsafe_allow_html=True)

        if st.button("🚨 Submit Critical Call", use_container_width=True,
                     type="primary"):
            if not patient_name.strip() or not uhid.strip():
                st.error("Patient Name and UHID are required.")
            elif not test_name.strip() or not first_value.strip():
                st.error("Test and 1st Value are required.")
            else:
                extra = {
                    "patient_name": patient_name.strip(),
                    "uhid": uhid.strip(),
                    "test": test_name.strip(),
                    "units": units.strip(),
                    "first_value": first_value.strip(),
                    "repeat_value": repeat_value.strip(),
                    "time_identified": time_identified.strip(),
                    "individual_notified": notified.strip(),
                }
                try:
                    submit_communication(
                        "CRITICAL_CALL", working_dept, full_name,
                        location=location.strip(), read_back=(read_back == "Yes"),
                        mode_of_communication=mode, extra_fields=extra,
                        role=role)
                    st.session_state.flash = (
                        f"Critical call logged for {patient_name.strip()}.")
                    st.session_state.cc_form_key = k + 1
                    st.rerun()
                except PermissionError as e:
                    st.error(f"🔒 {e}")

    st.divider()
    st.markdown("**Today's critical calls — this zone**")
    records = get_communications_for_zone("CRITICAL_CALL", working_dept, role=role)
    if not records:
        st.info("No critical calls logged yet today.")
    else:
        for r in records:
            rb_badge = "✅ Read back" if r["read_back"] else "⚠️ No read back"
            st.markdown(f"""
                <div class="qms-card qms-card-red-accent">
                    <div style="display:flex;justify-content:space-between;">
                        <strong>{r.get('patient_name','')}</strong>
                        <span style="font-size:11px;color:#888;">{r['time_reported']}</span>
                    </div>
                    <div style="font-size:12px;color:#555;margin-top:4px;">
                        {r.get('test','')} {r.get('first_value','')}{r.get('units','')}
                        → {r.get('individual_notified','')} · {r['location']}
                    </div>
                    <div style="font-size:11px;color:#888;margin-top:4px;">
                        {rb_badge} · Logged by {r['performed_by']}
                    </div>
                </div>
            """, unsafe_allow_html=True)


def _render_test_unit_settings(role):
    """Critical Call test/unit editor. HOD/manager only — hard-blocked
    by check_access even if somehow reached without the gear icon
    (e.g. a stale session_state flag). Lab-wide list, not zone-scoped."""
    import streamlit as st

    try:
        check_access(role, ["hod", "manager"])
    except PermissionError:
        st.error("🔒 Not allowed — Test & Unit settings are HOD/Manager only.")
        return

    st.markdown("**⚙️ Critical Call — Test & Unit Settings**")
    st.caption("Lab-wide list — visible to every department. "
              "Changes apply immediately.")

    existing = get_test_unit_options()
    if existing:
        for test_name, unit in existing:
            ec1, ec2, ec3 = st.columns([2, 2, 1])
            with ec1:
                new_name = st.text_input("Test", value=test_name,
                                         key=f"edit_test_{test_name}",
                                         label_visibility="collapsed")
            with ec2:
                new_unit = st.text_input("Unit", value=unit,
                                         key=f"edit_unit_{test_name}",
                                         label_visibility="collapsed")
            with ec3:
                if st.button("Save", key=f"save_test_{test_name}",
                            use_container_width=True):
                    working_dept = st.session_state.get(
                        "working_department", "Administration")
                    try:
                        add_or_update_test_unit(working_dept, new_name,
                                                new_unit, role)
                        st.session_state.flash = f"{new_name} updated."
                        st.rerun()
                    except PermissionError as e:
                        st.error(f"🔒 {e}")
    else:
        st.info("No tests configured yet. Add the first one below.")

    st.write("")
    st.markdown("**Add a new test**")
    nc1, nc2, nc3 = st.columns([2, 2, 1])
    with nc1:
        add_name = st.text_input("New test name", key="add_test_name",
                                 placeholder="e.g. RBC",
                                 label_visibility="collapsed")
    with nc2:
        add_unit = st.text_input("Unit", key="add_test_unit",
                                 placeholder="e.g. x10^12/L or NA",
                                 label_visibility="collapsed")
    with nc3:
        if st.button("Add", key="add_test_btn", use_container_width=True,
                     type="primary"):
            if not add_name.strip() or not add_unit.strip():
                st.error("Both test name and unit are required.")
            else:
                working_dept = st.session_state.get(
                    "working_department", "Administration")
                try:
                    add_or_update_test_unit(working_dept, add_name,
                                            add_unit, role)
                    st.session_state.flash = f"{add_name.strip()} added."
                    st.rerun()
                except PermissionError as e:
                    st.error(f"🔒 {e}")


def render_bench_decon_page(name, role):
    import streamlit as st

    if not can_access(role, BENCH_DECON_READ_ROLES):
        st.error("You do not have access to bench decontamination records.")
        return

    full_name    = get_full_name(name)
    working_dept = st.session_state.get("working_department")
    pill_label   = _dept_pill_label(name, role, working_dept)
    is_read_only = (role == "director")

    render_topbar(name, role)
    render_subbar("🧼 Bench Decontamination", pill_label)

    if is_read_only:
        st.markdown("""
            <div class="qms-director-banner">
                <div style="font-size:28px;">🔭</div>
                <div>
                    <div class="qms-director-label">Director view</div>
                    <div class="qms-director-name">Read-only visibility</div>
                </div>
                <div style="margin-left:auto;">
                    <span class="qms-director-tag">Cannot submit entries</span>
                </div>
            </div>
        """, unsafe_allow_html=True)

    st.caption("STMMHL-LOG-180 V4 · ISO 15189:2022 — 6.3.2, 8.4, 8.9.1")
    st.write("")

    if not working_dept or working_dept == "Administration":
        depts = [d for d in get_working_departments() if d != "Office"]
        working_dept = st.selectbox("Working department", depts)

    status_list   = get_cluster_today_status(working_dept, role=role)
    done_count    = sum(1 for s in status_list if s["open"] and s["done"])
    pending_count = sum(1 for s in status_list if s["open"] and not s["done"])
    closed_count  = sum(1 for s in status_list if not s["open"])

    st.markdown(f"""
        <div class="qms-metrics qms-metrics-3">
            <div class="qms-metric qms-metric-green">
                <div class="qms-metric-num qms-metric-num-green">{done_count}</div>
                <div class="qms-metric-label">Done</div>
            </div>
            <div class="qms-metric qms-metric-red">
                <div class="qms-metric-num qms-metric-num-red">{pending_count}</div>
                <div class="qms-metric-label">Pending</div>
            </div>
            <div class="qms-metric">
                <div class="qms-metric-num" style="color:#aaa;">{closed_count}</div>
                <div class="qms-metric-label">Closed</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    locations = get_cluster_locations(working_dept)
    if not locations:
        st.info("No locations configured for this department yet.")
        return

    for location_name, _default_active in locations:
        open_today, reason = is_location_open(location_name)
        existing           = get_todays_entry(location_name, working_dept, role=role)

        if not open_today:
            st.markdown(f"""
                <div class="qms-equip-closed">
                    <div class="qms-equip-name">⚪ {location_name}</div>
                    <div class="qms-equip-sub">CLOSED — NOT REQUIRED · {reason}</div>
                </div>
            """, unsafe_allow_html=True)
            continue

        if existing:
            (log_id, done_by, recorded_at, ck, an, gc) = existing
            items     = get_checklist_items(location_name)
            ticks     = [("✅ " if v else "⬜ ") + l
                         for l, v in zip(items, (ck, an, gc))]
            ticks_html = " &nbsp;·&nbsp; ".join(ticks)
            st.markdown(f"""
                <div class="qms-equip-done">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <div class="qms-equip-name">🟢 {location_name}</div>
                        <span class="qms-badge qms-badge-green">✓ Done</span>
                    </div>
                    <div class="qms-done-stamp">Logged by {done_by} · {recorded_at}</div>
                    <div style="font-size:11px;color:#888;margin-top:4px;">{ticks_html}</div>
                </div>
            """, unsafe_allow_html=True)
            continue

        st.markdown(f"""
            <div class="qms-equip-pending">
                <div style="display:flex;justify-content:space-between;align-items:center;">
                    <div class="qms-equip-name">🔴 {location_name}</div>
                    <span class="qms-badge qms-badge-red">Pending</span>
                </div>
            </div>
        """, unsafe_allow_html=True)

        if is_read_only:
            st.caption("Director view — entry not yet submitted by lab staff.")
            continue

        items = get_checklist_items(location_name)
        vals  = []
        for i, label in enumerate(items):
            checked = st.checkbox(label,
                                  key=f"bd_{working_dept}_{location_name}_{i}",
                                  help=ISO_RATIONALE_BENCH_DECON.get(i, ""))
            if ISO_RATIONALE_BENCH_DECON.get(i):
                st.caption(ISO_RATIONALE_BENCH_DECON[i])
            vals.append(checked)

        if st.button(f"✅ Submit — {location_name}",
                     key=f"bd_submit_{working_dept}_{location_name}",
                     use_container_width=True, type="primary"):
            ck = vals[0] if len(vals) > 0 else False
            an = vals[1] if len(vals) > 1 else False
            gc = vals[2] if len(vals) > 2 else False
            if not (ck or an or gc):
                st.error("Tick at least one item before submitting.")
            else:
                try:
                    submit_bench_decon(location_name, working_dept, full_name,
                                       ck, an, gc, role=role)
                    st.session_state.flash = (
                        f"Bench decontamination logged for {location_name}.")
                    st.rerun()
                except PermissionError as e:
                    st.error(f"🔒 {e}")
        st.write("")


# ── HOME DASHBOARD — Staff landing screen ────────────────────────
# Replaces the old "pending_work" landing page for all clinical roles
# except director (who lands on dashboard.py's Lab Overview) and
# phlebotomist (future variation). Popup alarm fires on entry if
# pending tasks > 0. Four supercards: Charts, Communications, Quality,
# Stock. Sidebar buttons navigate to the same proven pages.


def _get_pending_tasks_for_staff(familiar_name):
    """Pending tasks assigned to this person, read straight from the
    real `handovers` table. Tasks are stored with FAMILIAR names in
    to_staff / from_staff (that is what create_handover writes), so we
    match on the familiar name, not the full name.

    A task counts as 'new' if it arrived in the last 3 hours."""
    from datetime import datetime, timedelta

    conn = get_qms_conn()
    c = conn.cursor()
    try:
        c.execute("""
            SELECT id, message, from_staff, created_at
            FROM handovers
            WHERE to_staff = ? AND status = 'pending'
            ORDER BY id DESC
            LIMIT 20
        """, (familiar_name,))
        rows = c.fetchall()
    except Exception:
        rows = []
    conn.close()

    results = []
    for row in rows:
        created = row[3]
        is_new = False
        try:
            dt = datetime.strptime(created, "%d/%m/%Y %I:%M%p")
            is_new = (datetime.now() - dt) <= timedelta(hours=3)
        except Exception:
            is_new = False
        results.append({
            "id":          row[0],
            "title":       row[1],
            "assigned_by": row[2],
            "created_at":  created,
            "is_new":      is_new,
        })
    return results


def _get_charts_pending_count(working_dept, role):
    """How many daily chart modules still pending today for this zone."""
    try:
        summary = get_zone_compliance_summary(working_dept, role=role)
        return sum(
            max(0, s["total"] - s["done"]) for s in summary
            if MODULE_REGISTRY.get(s["module_name"], {}).get("frequency") == "DAILY"
        )
    except Exception:
        return 0


def _get_comms_today_count(working_dept):
    """Total communications logged today in this zone (both modules)."""
    try:
        counts = get_today_communications_count(working_dept)
        return sum(counts.values())
    except Exception:
        return 0


def render_home_dashboard(name, role):
    """Staff Home Dashboard — the landing screen after a department is
    chosen. Built to match the approved mockup:

        topbar  ·  greeting + dept pill  ·  pending-tasks card
        YOUR TOOLS  ·  Charts | Communications / Quality | Stock  ·  footer

    A one-time popup (st.dialog) fires on entry when there are pending
    tasks. RENDERING RULE: every HTML block below is built as ONE
    concatenated string with NO leading spaces inside it. Streamlit's
    markdown reader treats lines starting with 4+ spaces as a code
    block, which is what was breaking the cards before. Same safe
    pattern dashboard.py already uses."""
    import streamlit as st
    from collections import Counter

    if not can_access(role, BENCH_DECON_READ_ROLES):
        st.error("Access denied.")
        return

    working_dept = st.session_state.get("working_department", "")
    pill_label   = _dept_pill_label(name, role, working_dept)
    pending      = _get_pending_tasks_for_staff(name)   # familiar name
    n_pending    = len(pending)
    charts_pend  = _get_charts_pending_count(working_dept, role)
    comms_today  = _get_comms_today_count(working_dept)

    # ── 1. Topbar (reused, proven) ───────────────────────────────
    render_topbar(name, role, notif_count=n_pending)

    # ── 2. Greeting strip + department pill ──────────────────────
    bg, fg = "#fcebeb", "#a32d2d"
    for zone, (zbg, zfg) in _ZONE_PILL_COLORS.items():
        if zone in pill_label:
            bg, fg = zbg, zfg
            break
    greeting_word = _greeting().split(" ", 1)[1]   # drop the emoji
    st.markdown(
        '<div style="background:#fff;padding:10px 18px;display:flex;'
        'justify-content:space-between;align-items:center;'
        'border-bottom:0.5px solid #e8e8e8;margin-bottom:18px;">'
        f'<span style="font-size:15px;color:#1a1a1a;font-weight:500;">'
        f'{greeting_word}, {name}</span>'
        f'<span style="background:{bg};color:{fg};border-radius:16px;'
        f'padding:4px 14px;font-size:12px;font-weight:600;">{pill_label}'
        '</span></div>',
        unsafe_allow_html=True)

    # ── 3. Pending-tasks card (always shown) ─────────────────────
    if n_pending > 0:
        by_assigner = Counter(t["assigned_by"] for t in pending)
        parts = [f"{cnt} from {who}" for who, cnt in by_assigner.items()]
        new_count = sum(1 for t in pending if t.get("is_new"))
        if new_count:
            parts.append(f"{new_count} new")
        sub = " · ".join(parts)

        rows_html = ""
        for t in pending:
            is_new = t.get("is_new", False)
            row_bg = "background:#fcebeb;" if is_new else ""
            if is_new:
                right = ('<span style="font-size:11px;font-weight:700;'
                         'color:#d32f2f;background:#fff;border:0.5px solid '
                         '#f3c5c5;border-radius:10px;padding:2px 10px;">New</span>')
                t_color, t_weight = "#d32f2f", "600"
            else:
                right = ('<span style="font-size:12px;color:#999;'
                         'white-space:nowrap;margin-left:12px;">'
                         f'{t["assigned_by"]}</span>')
                t_color, t_weight = "#1a1a1a", "400"
            rows_html += (
                '<div style="display:flex;justify-content:space-between;'
                'align-items:center;padding:11px 16px;'
                f'border-top:0.5px solid #f0f0f0;{row_bg}">'
                f'<span style="font-size:13px;color:{t_color};'
                f'font-weight:{t_weight};">{t["title"]}</span>'
                f'{right}</div>'
            )

        st.markdown(
            '<div style="background:#fff;border:1.5px solid #d32f2f;'
            'border-radius:12px;margin-bottom:20px;overflow:hidden;">'
            '<div style="padding:14px 16px;display:flex;align-items:center;'
            'gap:12px;">'
            '<div style="width:34px;height:34px;border-radius:50%;'
            'background:#fcebeb;display:flex;align-items:center;'
            'justify-content:center;flex-shrink:0;">'
            '<span style="color:#d32f2f;font-size:16px;">🔔</span></div>'
            '<div style="flex:1;">'
            f'<div style="font-size:15px;font-weight:700;color:#1a1a1a;">'
            f'You have {n_pending} pending task'
            f'{"s" if n_pending != 1 else ""}</div>'
            f'<div style="font-size:12px;color:#888;margin-top:2px;">{sub}</div>'
            '</div></div>'
            f'{rows_html}'
            '</div>',
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="background:#eaf3de;border:0.5px solid #c0dd97;'
            'border-radius:12px;padding:14px 18px;margin-bottom:20px;'
            'display:flex;align-items:center;gap:12px;">'
            '<span style="font-size:18px;">✅</span>'
            '<span style="font-size:13px;color:#3b6d11;font-weight:500;">'
            f'Good {greeting_word.lower()}, {name}. '
            'You have no pending tasks today.</span></div>',
            unsafe_allow_html=True)

    # ── 4. YOUR TOOLS — four cards ───────────────────────────────
    charts_badge = ""
    if charts_pend > 0:
        charts_badge = ('<span style="background:#faeeda;color:#854f0b;'
                        'font-size:10px;font-weight:600;padding:3px 10px;'
                        f'border-radius:10px;">{charts_pend} pending</span>')
    comms_badge = ""
    if comms_today > 0:
        comms_badge = ('<span style="background:#e6f1fb;color:#185fa5;'
                       'font-size:10px;font-weight:600;padding:3px 10px;'
                       f'border-radius:10px;">{comms_today} today</span>')
    quality_badge = ('<span style="background:#eaf3de;color:#3b6d11;'
                     'font-size:10px;font-weight:600;padding:3px 10px;'
                     'border-radius:10px;">In control</span>')

    def tool_card(col, icon, title, subtitle, badge_html, page_key, btn_key):
        with col:
            st.markdown(
                '<div style="background:#fff;border:0.5px solid #e0e0e0;'
                'border-radius:12px;padding:18px;margin-bottom:6px;'
                'min-height:92px;">'
                '<div style="display:flex;justify-content:space-between;'
                'align-items:flex-start;margin-bottom:12px;">'
                f'<span style="font-size:22px;">{icon}</span>{badge_html}</div>'
                f'<div style="font-size:15px;font-weight:600;color:#1a1a1a;">'
                f'{title}</div>'
                f'<div style="font-size:11px;color:#888;margin-top:3px;">'
                f'{subtitle}</div></div>',
                unsafe_allow_html=True)
            if st.button("Open ›", key=btn_key, use_container_width=True):
                st.session_state.page = page_key
                st.rerun()

    st.markdown(
        '<p style="font-size:10px;font-weight:600;color:#999;'
        'text-transform:uppercase;letter-spacing:0.08em;'
        'margin:4px 0 10px;">Your tools</p>',
        unsafe_allow_html=True)

    r1c1, r1c2 = st.columns(2)
    tool_card(r1c1, "📋", "Charts", "Bench decon · fridge",
              charts_badge, "charts", "home_charts")
    tool_card(r1c2, "📞", "Communications", "Critical call · comm log",
              comms_badge, "critical_call", "home_comms")

    r2c1, r2c2 = st.columns(2)
    tool_card(r2c1, "🧪", "Quality", "Lot-to-lot · QC · EQA",
              quality_badge, "lot_to_lot", "home_quality")
    tool_card(r2c2, "📦", "Stock", "Receive · take · order",
              "", "receive_stock", "home_stock")

    st.markdown(
        '<p style="text-align:center;font-size:11px;color:#bbb;'
        'margin-top:8px;">4 of 6 tools · room for 2 more</p>',
        unsafe_allow_html=True)

    # ── 5. Pop-up alarm (real modal, fires once per task-count) ──
    popup_key = f"popup_shown_{name}_{n_pending}"
    if n_pending > 0 and not st.session_state.get(popup_key, False):
        if hasattr(st, "dialog"):
            st.session_state[popup_key] = True
            title = (f"{name}, you have {n_pending} pending task"
                     + ("s" if n_pending != 1 else ""))

            @st.dialog(title)
            def _pending_dialog():
                for t in pending:
                    is_new = t.get("is_new", False)
                    row_bg = "background:#fcebeb;border-radius:8px;" if is_new else ""
                    if is_new:
                        right = ('<span style="font-size:11px;font-weight:700;'
                                 'color:#d32f2f;">New</span>')
                    else:
                        right = ('<span style="font-size:12px;color:#999;">'
                                 f'{t["assigned_by"]}</span>')
                    st.markdown(
                        '<div style="display:flex;justify-content:space-between;'
                        'align-items:center;padding:10px 8px;'
                        f'border-bottom:0.5px solid #f0f0f0;{row_bg}">'
                        f'<span style="font-size:13px;color:#1a1a1a;">'
                        f'{t["title"]}</span>{right}</div>',
                        unsafe_allow_html=True)
                st.write("")
                if st.button("Open tasks", type="primary",
                             use_container_width=True, key="dialog_open_tasks"):
                    st.session_state.page = "tasks_new"
                    st.rerun()

            _pending_dialog()