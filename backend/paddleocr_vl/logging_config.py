"""Logging configuration shared by PaddleOCR worker entrypoints."""

from __future__ import annotations

import logging
import os
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        logger.opt(exception=record.exc_info, depth=6).log(level, record.getMessage())


def configure_logging(service: str) -> None:
    level = str(os.getenv("APP_LOG_LEVEL", "INFO") or "INFO").strip().upper()
    if level not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
        level = "INFO"
    logger.remove()
    logger.configure(extra={"service": service, "request_id": "-"})
    logger.add(
        sys.stderr,
        level=level,
        serialize=str(os.getenv("APP_LOG_FORMAT", "text")).lower() == "json",
        backtrace=False,
        diagnose=False,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{extra[service]} | {name}:{function}:{line} - {message}"
        ),
    )
    logging.basicConfig(handlers=[_InterceptHandler()], level=logging.WARNING, force=True)
    for name in ("httpx", "httpcore", "urllib3", "paddlex", "transformers"):
        logging.getLogger(name).setLevel(logging.WARNING)
