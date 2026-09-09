"""Entrypoint: ``python -m lunel_core [--config file] [--port N] ...``"""
from __future__ import annotations

import contextlib
import sys

import uvicorn

from .app import Core
from .config import build_config
from .logging import get, setup_logging


def main(argv: list[str] | None = None) -> int:
    try:
        cfg, _ns = build_config(argv)
    except (ValueError, FileNotFoundError) as exc:
        print(f"lunel-core: configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(cfg.log_level, cfg.log_json)
    log = get("runtime", "lunel.core")

    core = Core(cfg)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        await core.store.load(core.links, core.stats)
        from .version import info

        log.info(
            "Lunel Core %s (build=%s commit=%s) listening on %s:%d",
            info()["version"], info()["build"], info()["commit"], cfg.host, cfg.port,
        )
        yield
        await core.store.save(core.links, core.stats)
        log.info("Lunel Core shut down cleanly")

    core.app.router.lifespan_context = lifespan

    uvicorn.run(
        core.app,
        host=cfg.host,
        port=cfg.port,
        log_level=cfg.log_level,
        workers=1,
        loop="auto",
        ws="auto",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
