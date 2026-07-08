"""
auth.py — DB-backed JWT Authentication Module
----------------------------------------------
Handles: DB user lookup/creation, token creation/validation,
         bcrypt passwords, refresh tokens, rate-limit tracking.
"""

import os
import time
import hashlib
import hmac as _hmac
import base64
import json
import uuid
import bcrypt
import logging
from typing import Optional, Tuple
from collections import defaultdict

logger = logging.getLogger(__name__)

# ── Secret keys (loaded from env) ────────────────────────────────────────────
JWT_SECRET         = os.getenv("JWT_SECRET", "CHANGE_ME_IN_PRODUCTION_USE_STRONG_RANDOM_KEY")
JWT_REFRESH_SECRET = os.getenv("JWT_REFRESH_SECRET", "CHANGE_ME_REFRESH_SECRET_DIFFERENT_FROM_ABOVE")
ACCESS_TOKEN_EXP   = int(os.getenv("ACCESS_TOKEN_EXP_MINUTES", "60"))   # minutes
REFRESH_TOKEN_EXP  = int(os.getenv("REFRESH_TOKEN_EXP_DAYS", "7"))      # days

# ── In-memory refresh token store ─────────────────────────────────────────────
_refresh_token_store: dict = {}

# ── Revoked access tokens (JTI blacklist) ─────────────────────────────────────
_revoked_jtis: set = set()

# ── Rate limiter: { ip: [timestamp, ...] } ────────────────────────────────────
_login_attempts: dict = defaultdict(list)
MAX_ATTEMPTS   = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
WINDOW_SECONDS = int(os.getenv("LOGIN_WINDOW_SECONDS", "300"))

# ─────────────────────────────────────────────────────────────────────────────
# Lightweight pure-Python HS256 JWT (no PyJWT dependency)
# ─────────────────────────────────────────────────────────────────────────────

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (padding % 4))

def _sign(h64: str, b64: str, secret: str) -> str:
    msg = f"{h64}.{b64}".encode()
    sig = _hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return _b64url_encode(sig)

def _create_raw_token(payload: dict, secret: str, exp_seconds: int) -> str:
    now = int(time.time())
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body   = _b64url_encode(json.dumps({**payload, "iat": now, "exp": now + exp_seconds}).encode())
    return f"{header}.{body}.{_sign(header, body, secret)}"

def verify_token(token: str, secret: str) -> Optional[dict]:
    try:
        h, b, sig = token.split(".")
        if not _hmac.compare_digest(sig, _sign(h, b, secret)):
            return None
        payload = json.loads(_b64url_decode(b))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Public token helpers  (sub = email, uid = user DB id)
# ─────────────────────────────────────────────────────────────────────────────

def create_access_token(user_id: int, email: str, role: str) -> str:
    jti = str(uuid.uuid4())
    return _create_raw_token(
        {"sub": email, "uid": user_id, "role": role, "jti": jti, "type": "access"},
        JWT_SECRET, ACCESS_TOKEN_EXP * 60,
    )

def create_refresh_token(user_id: int, email: str) -> str:
    jti = str(uuid.uuid4())
    token = _create_raw_token(
        {"sub": email, "uid": user_id, "jti": jti, "type": "refresh"},
        JWT_REFRESH_SECRET, REFRESH_TOKEN_EXP * 86400,
    )
    tok_hash = hashlib.sha256(token.encode()).hexdigest()
    _refresh_token_store[tok_hash] = {
        "uid": user_id, "email": email,
        "expires": time.time() + REFRESH_TOKEN_EXP * 86400,
    }
    return token

def verify_access_token(token: str) -> Optional[dict]:
    payload = verify_token(token, JWT_SECRET)
    if payload and payload.get("type") == "access":
        if payload.get("jti", "") in _revoked_jtis:
            return None
        return payload
    return None

def verify_refresh_token(token: str) -> Optional[dict]:
    payload = verify_token(token, JWT_REFRESH_SECRET)
    if not payload or payload.get("type") != "refresh":
        return None
    tok_hash = hashlib.sha256(token.encode()).hexdigest()
    record = _refresh_token_store.get(tok_hash)
    if not record or record["expires"] < time.time():
        return None
    return payload

def revoke_access_token(token: str):
    payload = verify_token(token, JWT_SECRET)
    if payload:
        _revoked_jtis.add(payload.get("jti", ""))

def revoke_refresh_token(token: str):
    _refresh_token_store.pop(hashlib.sha256(token.encode()).hexdigest(), None)

# ─────────────────────────────────────────────────────────────────────────────
# Password helpers
# ─────────────────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# DB-backed user helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_user_by_email(db, email: str):
    """Returns User ORM object or None."""
    from database import User
    return db.query(User).filter(User.email == email).first()

def get_user_by_id(db, user_id: int):
    from database import User
    return db.query(User).filter(User.id == user_id).first()

def create_user(db, email: str, username: str, password: str, role: str = "user"):
    """Create a new user, raises ValueError if email/username taken."""
    from database import User
    if db.query(User).filter(User.email == email).first():
        raise ValueError("Email already registered.")
    if db.query(User).filter(User.username == username).first():
        raise ValueError("Username already taken.")
    user = User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def authenticate_user(db, email_or_username: str, password: str):
    """Returns User ORM object or None."""
    from database import User
    user = (
        db.query(User)
        .filter((User.email == email_or_username) | (User.username == email_or_username))
        .first()
    )
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user

# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter
# ─────────────────────────────────────────────────────────────────────────────

def is_rate_limited(ip: str) -> Tuple[bool, int]:
    now = time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < WINDOW_SECONDS]
    if len(_login_attempts[ip]) >= MAX_ATTEMPTS:
        oldest = _login_attempts[ip][0]
        return True, max(int(WINDOW_SECONDS - (now - oldest)), 0)
    return False, 0

def record_login_attempt(ip: str):
    _login_attempts[ip].append(time.time())

def clear_login_attempts(ip: str):
    _login_attempts.pop(ip, None)
