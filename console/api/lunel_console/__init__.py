"""Lunel Console API — control plane for Lunel Core instances.

Stack: FastAPI + PostgreSQL (asyncpg) + GitHub OAuth sessions.

Every route is authenticated and authorized per user; admin routes require
the admin flag. Secrets are never returned in API responses (link credentials
are stored server-side and only proxied to the owning instance).
"""
