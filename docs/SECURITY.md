# Lunel Security

## Model

Three trust boundaries, each with its own credential:

```
Browser ──(session cookie + CSRF header)──▶ Console API
Console  ──(LUNEL_WORKER_TOKEN)───────────▶ Worker
Worker   ──(per-instance LUNEL_CORE_API_TOKEN)──▶ Lunel Core
Client   ──(protocol credential: VLESS uuid / trojan password / SS key)──▶ Core transports
```

Public proxy traffic is additionally gated by the **endpoint token** in
`/i/<token>/...` — a per-instance, rotatable secret that must be known even
to reach the protocol layer.

## Authentication & sessions

- GitHub OAuth (authorization-code) with single-use server-side `state`.
- Opaque 256-bit session tokens; **only SHA-256 hashes are stored**.
- Cookies: `HttpOnly`, `SameSite=Lax`, `Secure` in production
  (`LUNEL_COOKIE_SECURE=1`), 7-day TTL, max 20 sessions per user.
- **CSRF**: every mutating request must repeat the session token in
  `X-Lunel-CSRF` (synchronizer-token; the cookie is unreadable to other
  origins by construction).
- Accounts can be disabled by admins, which immediately destroys sessions.

## Authorization

- Every Console query is scoped by `user_id` — instance IDs are checked
  against the session owner before any data is returned (404 otherwise).
- Admin routes require the `is_admin` flag and are written to
  `activity_events` with the acting admin's login.
- Worker routes require the shared worker token; Core management requires
  the per-instance token. Neither is ever sent to browsers.

## Input validation & rate limiting

- All request bodies validated server-side (protocol allow-lists, resource
  bounds 0.1–8 CPU / 128–8192 MB, name lengths, slug charset, seq bounds).
- Sliding-window rate limits per IP and per user: auth 10/min, writes 60/min,
  reads 300/min; OAuth endpoints rate-limited independently.
- Instance cap per user (25) to bound scheduler load.

## Secrets

- Link credentials (VLESS UUIDs, Trojan passwords, SS keys) live **only** in
  the Core instance state; the Console stores none of them.
- `core_api_token` never appears in any Console API response.
- Log redaction on all three components strips UUID-shaped and long
  base64/hex strings before emission; Core never logs link credentials.
- GitHub client secret is used in-memory once per login and never persisted.

## Isolation

Docker driver (production): `--cap-drop ALL`, `no-new-privileges`, read-only
rootfs + `tmpfs /tmp(noexec,nosuid)`, non-root UID 65532, `--pids-limit`,
`--cpus`, `--memory`, private bridge network, port published to loopback
only. The Docker socket is mounted **only** into the Worker, never into
Core containers.

Process driver (dev only, documented as weaker): own session
(`start_new_session`), `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE`, per-instance
data dir, 0o077 umask, own venv.

## Known trade-offs (documented, not hidden)

- **X-Forwarded-For trust**: client IP logging trusts proxy headers; on
  Lucity/Cloudflare-class platforms the edge sets them. Direct unproxied
  exposure would allow log spoofing (logging only — no auth decisions use it).
- **Worker token is shared**: suitable for one operator's nodes; multi-tenant
  deployments should issue per-node tokens (the worker stores a hash-ready
  interface; see `TODO(per-node-tokens)` in `worker/lunel_worker/main.py`).
- **Path endpoints** depend on the endpoint token's secrecy; rotate from the
  Networking tab if leaked.

## Reporting

See repository SECURITY policy — please report vulnerabilities privately
rather than opening public issues.
