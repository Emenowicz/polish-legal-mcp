"""Entry point: ``polish-legal-mcp`` (or ``python -m polish_legal_mcp.main``)."""

from __future__ import annotations

import logging
import sys

import uvicorn
from pydantic import ValidationError

from polish_legal_mcp.config import Settings
from polish_legal_mcp.logging_setup import configure_logging
from polish_legal_mcp.server import build_app

logger = logging.getLogger("polish_legal_mcp")


def main() -> None:
    try:
        settings = Settings()
    except ValidationError as exc:
        configure_logging("INFO")
        # Fail closed: misconfigured auth must never start an open server.
        errors = [f"{'.'.join(map(str, e['loc'])) or 'settings'}: {e['msg']}" for e in exc.errors()]
        logger.error("invalid configuration", extra={"fields": {"errors": errors}})
        sys.exit(2)
    configure_logging(settings.log_level)
    if settings.auth_mode == "none" and not settings.is_loopback:
        logger.warning(
            "authentication disabled on a non-loopback interface; development use only",
            extra={"fields": {"host": settings.host}},
        )
    app = build_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        # Client IPs are not used (rate limits are per user; OAuth URLs come from base_url),
        # so forwarded headers are not trusted at all.
        proxy_headers=False,
        # Access logs would record OAuth callback query strings (authorization codes).
        access_log=False,
        timeout_graceful_shutdown=10,
        log_config=None,
        server_header=False,
    )


if __name__ == "__main__":
    main()
