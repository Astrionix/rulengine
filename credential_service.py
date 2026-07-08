"""
credential_service.py — Write-only credential management
----------------------------------------------------------
Rules enforced here:
• Credentials are AES-256-GCM encrypted before DB storage.
• Encrypted values are NEVER returned to callers.
• Partial updates: only non-empty fields overwrite existing values.
• All DB writes use transactions; partial writes are prevented.
"""

import logging
from typing import Optional
from crypto_utils import encrypt, decrypt

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_encrypt(value: Optional[str]) -> Optional[str]:
    """Encrypt only if value is non-empty, else return None."""
    if value and value.strip():
        return encrypt(value.strip())
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def upsert_credentials(
    db,
    user_id: int,
    *,
    upstox_client_id:     Optional[str] = None,
    upstox_client_secret: Optional[str] = None,
    upstox_access_token:  Optional[str] = None,
    analytics_token:      Optional[str] = None,
) -> bool:
    """
    Create or update credentials for a user.
    • Only fields with non-empty values are written.
    • Existing values are preserved when field is blank/None.
    • Uses a single DB transaction — atomic write.

    Returns True on success, raises on error.
    """
    from database import UserCredential

    try:
        cred = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()

        if cred is None:
            # First-time creation
            cred = UserCredential(user_id=user_id)
            db.add(cred)

        # Partial update logic: only overwrite if new value provided
        if upstox_client_id and upstox_client_id.strip():
            cred.upstox_client_id = _safe_encrypt(upstox_client_id)

        if upstox_client_secret and upstox_client_secret.strip():
            cred.upstox_client_secret = _safe_encrypt(upstox_client_secret)

        if upstox_access_token and upstox_access_token.strip():
            cred.upstox_access_token = _safe_encrypt(upstox_access_token)

        if analytics_token and analytics_token.strip():
            cred.analytics_token = _safe_encrypt(analytics_token)

        db.commit()
        db.refresh(cred)
        logger.info(f"[CRED] Credentials upserted for user_id={user_id}")
        return True

    except Exception as e:
        db.rollback()
        logger.error(f"[CRED] Failed to upsert credentials for user_id={user_id}: {e}")
        raise


def get_decrypted_credentials(db, user_id: int) -> dict:
    """
    Returns decrypted credentials dict for RUNTIME use ONLY (e.g. API calls).
    This function MUST NOT be called from any API response path.
    Missing/unset fields return empty string.
    """
    from database import UserCredential

    cred = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not cred:
        return {
            "upstox_client_id":     "",
            "upstox_client_secret": "",
            "upstox_access_token":  "",
            "analytics_token":      "",
        }

    def _safe_decrypt(val):
        if not val:
            return ""
        try:
            return decrypt(val)
        except Exception:
            logger.warning(f"Decryption failed for user_id={user_id}, returning empty")
            return ""

    return {
        "upstox_client_id":     _safe_decrypt(cred.upstox_client_id),
        "upstox_client_secret": _safe_decrypt(cred.upstox_client_secret),
        "upstox_access_token":  _safe_decrypt(cred.upstox_access_token),
        "analytics_token":      _safe_decrypt(cred.analytics_token),
        "updated_at":           cred.updated_at.isoformat() if cred.updated_at else None,
    }


def has_credentials(db, user_id: int) -> bool:
    """Returns True if the user has saved at least an access token."""
    from database import UserCredential
    cred = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not cred:
        return False
    return bool(cred.upstox_access_token)


def credential_field_status(db, user_id: int) -> dict:
    """
    Returns a dict indicating WHICH fields are set (True/False).
    NEVER returns actual values — safe to send to frontend.
    """
    from database import UserCredential
    cred = db.query(UserCredential).filter(UserCredential.user_id == user_id).first()
    if not cred:
        return {
            "upstox_client_id":     False,
            "upstox_client_secret": False,
            "upstox_access_token":  False,
            "analytics_token":      False,
        }
    return {
        "upstox_client_id":     bool(cred.upstox_client_id),
        "upstox_client_secret": bool(cred.upstox_client_secret),
        "upstox_access_token":  bool(cred.upstox_access_token),
        "analytics_token":      bool(cred.analytics_token),
    }
