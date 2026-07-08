"""
crypto_utils.py — AES-256-GCM Credential Encryption
------------------------------------------------------
Provides encrypt/decrypt for trading credentials stored in DB.
• AES-256-GCM: authenticated encryption (prevents tampering)
• Random 12-byte nonce per encryption call
• Stored as base64(nonce + tag + ciphertext)
• Key loaded from CREDENTIAL_ENCRYPTION_KEY env var (32 raw bytes as 64 hex chars)
"""

import os
import base64
import secrets
import logging

logger = logging.getLogger(__name__)

# ── Load/derive the 32-byte AES-256 key ───────────────────────────────────────
_RAW_KEY_HEX = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")

def _derive_key() -> bytes:
    """Load key from env. Falls back to a deterministic dev key (NOT for production)."""
    global _RAW_KEY_HEX
    if _RAW_KEY_HEX and len(_RAW_KEY_HEX) == 64:
        try:
            return bytes.fromhex(_RAW_KEY_HEX)
        except ValueError:
            pass
    # Dev fallback — warn loudly
    logger.warning(
        "CREDENTIAL_ENCRYPTION_KEY not set or invalid. Using insecure dev key. "
        "Run: python generate_enc_key.py to create a proper key."
    )
    import hashlib
    return hashlib.sha256(b"INSECURE_DEV_KEY_CHANGE_ME_NOW").digest()

_KEY: bytes = _derive_key()


def encrypt(plaintext: str) -> str:
    """
    Encrypt a string value with AES-256-GCM.
    Returns base64(nonce[12] + tag[16] + ciphertext).
    """
    if not plaintext:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(12)
        aesgcm = AESGCM(_KEY)
        ct_with_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        # ct_with_tag = ciphertext + 16-byte GCM tag (cryptography lib appends tag)
        raw = nonce + ct_with_tag
        return base64.urlsafe_b64encode(raw).decode("ascii")
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise


def decrypt(ciphertext_b64: str) -> str:
    """
    Decrypt a value encrypted by encrypt().
    Returns the original plaintext string.
    Raises on tampering or wrong key.
    """
    if not ciphertext_b64:
        return ""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        raw = base64.urlsafe_b64decode(ciphertext_b64)
        nonce = raw[:12]
        ct_with_tag = raw[12:]
        aesgcm = AESGCM(_KEY)
        plaintext = aesgcm.decrypt(nonce, ct_with_tag, None)
        return plaintext.decode("utf-8")
    except Exception as e:
        logger.error(f"Decryption failed (bad key or tampered data): {e}")
        raise


def generate_key_hex() -> str:
    """Generate a new random 32-byte key as 64 hex chars."""
    return secrets.token_bytes(32).hex()
