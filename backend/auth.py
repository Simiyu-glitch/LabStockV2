# auth.py
# LabStockV2 — Authentication Module
#
# Exposes one endpoint:
#   POST /auth/login  →  receives name + password, returns JWT token
#
# The JWT token is like a hospital wristband:
#   - It proves who you are
#   - It carries your role
#   - It expires after 8 hours (one shift)
#   - FastAPI checks it on every protected request

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from jose import jwt

from database import get_db

# ── JWT CONFIG ───────────────────────────────────────────────
# SECRET_KEY: the seal on the wristband envelope.
# In production, move this to an environment variable.
# Never commit a real secret to GitHub.
SECRET_KEY = "labstock-dev-secret-change-before-live"
ALGORITHM  = "HS256"
TOKEN_EXPIRE_HOURS = 8   # one full shift

# ── ROUTER ───────────────────────────────────────────────────
# An APIRouter is like a department within FastAPI.
# main.py will attach this router under the /auth prefix.
router = APIRouter(prefix="/auth", tags=["auth"])

# ── REQUEST BODY SHAPE ───────────────────────────────────────
# Pydantic model: defines exactly what React must send.
# If React sends something different, FastAPI rejects it automatically.
class LoginRequest(BaseModel):
    name: str       # familiar_name, e.g. "Erick"
    password: str   # plain password, e.g. "4142"

# ── RESPONSE BODY SHAPE ──────────────────────────────────────
# What FastAPI sends back to React on successful login.
class LoginResponse(BaseModel):
    access_token: str    # the JWT wristband
    token_type: str      # always "bearer"
    familiar_name: str   # "Erick" — shown on screen (Law 4)
    full_name: str       # "Erick Wamae" — for audit trail (Law 4)
    role: str            # "mlt", "hod", "manager", "director", etc.

# ── HELPER: HASH A PASSWORD ──────────────────────────────────
def hash_pin(plain: str) -> str:
    """SHA-256 hash — same function used in seed_pins.py."""
    return hashlib.sha256(plain.encode()).hexdigest()

# ── HELPER: CREATE A JWT TOKEN ───────────────────────────────
def create_token(data: dict) -> str:
    """
    Seal the envelope. Put staff info inside and sign it.
    The token carries: familiar_name, full_name, role, and expiry time.
    """
    payload = data.copy()
    expire  = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    payload.update({"exp": expire})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# ── LOGIN ENDPOINT ───────────────────────────────────────────
@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest):
    """
    POST /auth/login
    React sends: { "name": "Erick", "password": "4142" }
    FastAPI returns: { "access_token": "...", "role": "mlt", ... }
    Or: HTTP 401 if name not found or password wrong.
    """
    conn = get_db()
    try:
        # Step 1 — Find the staff member by familiar_name
        # We search familiar_name first, then full_name (covers "Dr Opuba" etc.)
        row = conn.execute(
            """
            SELECT staff_id, familiar_name, full_name, role, pin_hash, is_active
            FROM staff
            WHERE familiar_name = ? OR full_name = ?
            """,
            (body.name, body.name)
        ).fetchone()

        # Step 2 — Name not found at all
        if not row:
            raise HTTPException(status_code=401, detail="Invalid name or password")

        # Step 3 — Account is deactivated (soft-deleted staff)
        if not row["is_active"]:
            raise HTTPException(status_code=401, detail="Account is deactivated")

        # Step 4 — No hash stored yet (should not happen after seed_pins.py)
        if not row["pin_hash"]:
            raise HTTPException(status_code=401, detail="Account not configured. Contact admin.")

        # Step 5 — Compare the hashed password
        if hash_pin(body.password) != row["pin_hash"]:
            raise HTTPException(status_code=401, detail="Invalid name or password")

        # Step 6 — All checks passed. Issue the JWT wristband.
        token = create_token({
            "sub":            row["familiar_name"],   # "subject" = who this token is for
            "full_name":      row["full_name"],
            "role":           row["role"],
            "staff_id":       row["staff_id"],
        })

        return LoginResponse(
            access_token  = token,
            token_type    = "bearer",
            familiar_name = row["familiar_name"],
            full_name     = row["full_name"],
            role          = row["role"],
        )

    finally:
        conn.close()   # Always close the connection, even if an error occurred
