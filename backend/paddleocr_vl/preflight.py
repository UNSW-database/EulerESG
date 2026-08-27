"""PaddleOCR-VL v1.6 model preflight.

This script is normally run automatically by docker-compose before backend and workers start.

Manual retry command, when the model cache may be empty or broken:

    docker compose run --rm paddleocr-model-init

It uses the same shared .paddlex volume as workers. With a remote VLM backend it
validates the layout pipeline after the VLM service is healthy; with local VLM
inference it also downloads and validates the VLM model files.
"""

from __future__ import annotations

import os
import sys

from loguru import logger
from logging_config import configure_logging

configure_logging("paddleocr-preflight")

# 预检服务是唯一默认允许在线下载模型的进程。
# 普通 worker 默认只使用已经通过预检的本地缓存，避免解析任务期间写坏 .paddlex 缓存。
os.environ.setdefault("PADDLEOCR_ALLOW_MODEL_DOWNLOAD", "true")
os.environ.setdefault("PADDLEOCR_REQUIRE_PREFLIGHT_MARKER", "false")

from parse_core import get_pipeline, release_pipeline


def main() -> int:
    logger.info("PaddleOCR-VL model preflight started")
    logger.info("pipeline_version={}", os.getenv("PADDLEOCR_PIPELINE_VERSION", "v1.6"))
    logger.info("PADDLE_PDX_MODEL_SOURCE={}", os.getenv("PADDLE_PDX_MODEL_SOURCE", "huggingface"))
    logger.info("HF_ENDPOINT={}", os.getenv("HF_ENDPOINT", ""))
    logger.info("PADDLEOCR_VL_REC_BACKEND={}", os.getenv("PADDLEOCR_VL_REC_BACKEND", "local"))
    logger.info("PADDLEOCR_VL_REC_SERVER_URL={}", os.getenv("PADDLEOCR_VL_REC_SERVER_URL", ""))

    pipe = get_pipeline()
    logger.info("PaddleOCR-VL model preflight OK: {}", type(pipe).__name__)

    if os.getenv("PADDLEOCR_PREFLIGHT_RELEASE_AFTER", "true").strip().lower() in {"1", "true", "yes", "on"}:
        release_pipeline("preflight complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logger.exception("PaddleOCR-VL model preflight failed")
        raise SystemExit(2)
