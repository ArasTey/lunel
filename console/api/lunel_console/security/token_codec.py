"""Obfuscated endpoint-token codec.

Endpoint paths (/i/<token>) are the only public surface of an instance.
To make them non-enumerable and tamper-proof — and to stop them looking
like proxy-panel paths in edge logs — the token is an AES-256-GCM
ciphertext of {instance_id, issued_at} under a key derived from the panel
secret. The gateway decodes it; forged or truncated tokens simply fail.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEX = b"lunel-endpoint-token-v1"


def _key(secret_key: str) -> bytes:
    return hashlib.sha256(_KEX + secret_key.encode()).digest()


def encode_token(instance_id: str, secret_key: str, ttl_days: int = 0) -> str:
    """AES-256-GCM encrypt the instance id into a URL-safe opaque token."""
    key = _key(secret_key)
    nonce = secrets.token_bytes(12)
    payload = {"i": instance_id, "t": datetime.now(timezone.utc).isoformat()}
    import json

    if ttl_days:
        payload["e"] = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")


def decode_token(token: str, secret_key: str) -> str | None:
    """Decrypt an endpoint token back to the instance id, or None."""
    try:
        key = _key(secret_key)
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
        nonce, ct = raw[:12], raw[12:]
        import json

        payload = json.loads(AESGCM(key).decrypt(nonce, ct, None))
        exp = payload.get("e")
        if exp and datetime.fromisoformat(exp) < datetime.now(timezone.utc):
            return None
        return payload.get("i")
    except Exception:
        return None
