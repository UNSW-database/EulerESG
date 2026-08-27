"""Central logging policy for the API and backend jobs."""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from loguru import logger


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(exception=record.exc_info, depth=6).log(level, record.getMessage())


def _level() -> str:
    value = str(os.getenv("APP_LOG_LEVEL", "INFO") or "INFO").strip().upper()
    return value if value in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"} else "INFO"


def configure_logging(service: str = "backend") -> None:
    """Configure Loguru once per process without logging sensitive payloads."""
    json_logs = str(os.getenv("APP_LOG_FORMAT", "text")).strip().lower() == "json"
    logger.remove()
    logger.configure(extra={"service": service, "request_id": "-"})
    logger.add(
        sys.stderr,
        level=_level(),
        serialize=json_logs,
        backtrace=False,
        diagnose=False,
        enqueue=False,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{extra[service]} | request_id={extra[request_id]} | {name}:{function}:{line} - {message}"
        ),
    )

    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.WARNING, force=True)
    for name in ("httpx", "httpcore", "urllib3", "multipart", "watchfiles", "transformers"):
        logging.getLogger(name).setLevel(logging.WARNING)


def env_float(name: str, default: float, minimum: Optional[float] = None) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = float(default)
    return max(float(minimum), value) if minimum is not None else value
