import logging
import os
import sys
import time

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class _SkipHealthChecks(logging.Filter):
    # Render and the Docker HEALTHCHECK poll /health every ~30 s and would bury everything else.
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) > 2 and str(args[2]).startswith("/health"))


def configure_logging() -> None:
    requested = os.environ.get("LOG_LEVEL", "").strip().upper()
    level = requested if requested in LEVELS else "INFO"

    formatter = logging.Formatter(FORMAT, DATE_FORMAT)
    formatter.converter = time.gmtime
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[handler], force=True)

    # uvicorn installs its own handlers before it imports the app; route them through ours for one format.
    for name in UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)
    logging.getLogger("uvicorn.access").addFilter(_SkipHealthChecks())

    # httpx logs every request URL at INFO; the tarball redirect URL carries a temporary download token.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    if requested and requested != level:
        logging.getLogger(__name__).warning("Ignoring invalid LOG_LEVEL=%r; using INFO", requested)
