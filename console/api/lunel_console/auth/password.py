"""Local (non-GitHub) authentication: first-run setup + password login.

Enabled automatically when GitHub OAuth is not configured, so a fresh
deployment needs zero environment variables: the first visitor creates the
admin account, everyone after that signs in with a password.

Password hashing: scrypt with per-user random salt (stdlib, no extra deps).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import HTTPException


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _scheme, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def validate_new_account(password: str, name: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="password must be at least 8 characters")
    if len(password) > 200:
        raise HTTPException(status_code=400, detail="password too long")
    if not (1 <= len(name.strip()) <= 60):
        raise HTTPException(status_code=400, detail="name must be 1-60 characters")
