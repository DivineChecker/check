"""
backup.py — Encrypted backup and restore for all saved sites.

How it works:
- Export: reads all sites from DB → serializes to JSON → encrypts with
  Fernet (AES-128-CBC + HMAC) using a key derived from BACKUP_PASSWORD
  → sends as a .bak file to Telegram.
- Import: user sends the .bak file → bot decrypts → restores all sites.

The BACKUP_PASSWORD is set as an env var (or falls back to a default).
Without the correct password the file is unreadable — safe to store anywhere.
"""

import base64
import hashlib
import json
import os
import logging
from typing import Optional

log = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
    CRYPTO_OK = True
except ImportError:
    CRYPTO_OK = False
    log.warning("cryptography not installed — backups will be base64-only (no encryption)")


def _get_key() -> bytes:
    """Derive a 32-byte Fernet key from BACKUP_PASSWORD env var."""
    password = os.environ.get("BACKUP_PASSWORD", "checkin-bot-default-key-change-me")
    # SHA-256 → 32 bytes → base64url → valid Fernet key
    digest = hashlib.sha256(password.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(data: bytes) -> bytes:
    """Encrypt bytes. Returns encrypted bytes."""
    if not CRYPTO_OK:
        return base64.b64encode(data)
    f = Fernet(_get_key())
    return f.encrypt(data)


def decrypt(data: bytes) -> Optional[bytes]:
    """Decrypt bytes. Returns None on failure."""
    if not CRYPTO_OK:
        try:
            return base64.b64decode(data)
        except Exception:
            return None
    try:
        f = Fernet(_get_key())
        return f.decrypt(data)
    except InvalidToken:
        return None
    except Exception as e:
        log.error(f"Decrypt error: {e}")
        return None


def export_sites(sites: list[dict]) -> bytes:
    """
    Serialize and encrypt a list of site dicts.
    Returns encrypted bytes to be saved as a .bak file.
    """
    # Strip runtime-only fields, keep everything needed to restore
    clean = []
    for s in sites:
        clean.append({
            "name":           s.get("name"),
            "url":            s.get("url"),
            "auth_type":      s.get("auth_type"),
            "session_cookie": s.get("session_cookie"),
            "api_user":       s.get("api_user"),
            "username":       s.get("username"),
            "password":       s.get("password"),
        })
    payload = json.dumps({"version": 1, "sites": clean}, ensure_ascii=False).encode()
    return encrypt(payload)


def import_sites(data: bytes) -> tuple[bool, list[dict], str]:
    """
    Decrypt and deserialize a backup file.
    Returns (success, sites_list, error_message)
    """
    raw = decrypt(data)
    if raw is None:
        return False, [], "Decryption failed — wrong BACKUP_PASSWORD or corrupted file."
    try:
        payload = json.loads(raw.decode())
        sites   = payload.get("sites", [])
        if not isinstance(sites, list):
            return False, [], "Invalid backup format."
        return True, sites, ""
    except Exception as e:
        return False, [], f"Parse error: {e}"
