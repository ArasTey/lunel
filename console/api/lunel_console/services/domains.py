"""Domain management: collision-free generated endpoints.

Format: ``<slug>-<short-random>.<domain_root>`` — the random suffix prevents
collisions and avoids leaking instance identity; custom domains are allowed
later (``is_custom=True`` rows).
"""
from __future__ import annotations

import re
import secrets

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,38}[a-z0-9])?$")
SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        slug = "instance"
    return slug[:40].strip("-")


def validate_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug))


def generate_domain(slug: str, domain_root: str) -> str:
    suffix = "".join(secrets.choice(SUFFIX_ALPHABET) for _ in range(6))
    return f"{slug}-{suffix}.{domain_root}"
