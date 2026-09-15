"""Production entrypoint: ``python -m app.server``."""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from app.config import ConfigurationError, get_settings
from app.logging_config import configure_logging
from app.main import create_app


def main() -> None:
    configure_logging(os.environ.get("TRACEHOLLOW_LOG_LEVEL", "INFO").upper())
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        logging.getLogger("tracehollow").critical(
            "configuration_invalid", extra={"error": str(exc)}
        )
        sys.exit(2)
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host=os.environ.get("TRACEHOLLOW_LISTEN_HOST", "0.0.0.0"),  # noqa: S104 - container port
        port=int(os.environ.get("TRACEHOLLOW_LISTEN_PORT", "8000")),
        log_config=None,
        access_log=False,
        server_header=False,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
