# Development

## Running the stack locally

`deploy/scripts/dev.sh` starts Console (:8080) + Worker (:9100) with the
process driver. Worker → Core launching uses the **core** venv
(`LUNEL_CORE_PYTHON`, `LUNEL_CORE_CWD`) so each component's dependencies
stay isolated.

## Test suite

```bash
# core protocol tests (header parse/build, AEAD streams, quota gates)
(cd core && .venv/bin/python -m pytest ../tests -q)

# end-to-end: boot console+worker, deploy an instance, relay through gateway
# (see tests/e2e_local.py — requires local PostgreSQL; used in CI)
```

## Conventions

- **No panel globals.** Relay code receives a `RelayContext`; worker code a
  driver interface. Anything reaching into another component's module state
  is a bug.
- **Real states only.** Deployment transitions happen only after the
  underlying action (worker call, probe, provider poll) succeeded. If a
  feature cannot be implemented in the environment, document the limitation
  instead of simulating success.
- **Secrets never in logs or API responses.** Use the redacting loggers;
  when adding an endpoint ask "would I ship this field to a hostile browser?"
- **Wire compatibility.** Protocol paths and encodings (`/ws/{uuid}`,
  `/trojan-ws`, `/ss-ws`, `/xhttp-siz10`, `/txhttp-siz10`, share-link URLs)
  are frozen — existing RVG-era client configs must keep importing cleanly.

## Layout map

See README "Repository layout" and docs/ARCHITECTURE.md for module-by-module
responsibilities.
