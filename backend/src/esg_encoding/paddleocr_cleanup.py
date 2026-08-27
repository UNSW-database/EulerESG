"""Safe cleanup for stale, report-independent PaddleOCR working files."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from loguru import logger


def _remove_child(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def cleanup_stale_paddleocr_artifacts() -> dict[str, int]:
    """Remove only expired children of the two configured OCR work roots."""
    try:
        ttl_hours = max(0.0, float(os.getenv("PADDLEOCR_STALE_ARTIFACT_TTL_HOURS", "24") or "24"))
    except (TypeError, ValueError):
        ttl_hours = 24.0
    result = {"removed": 0, "failed": 0}
    if ttl_hours <= 0:
        return result

    cutoff = time.time() - ttl_hours * 3600
    roots = (
        Path(os.getenv("PADDLEOCR_OUTPUT_DIR", "/workspace/uploads/paddleocr_vl_output")),
        Path(os.getenv("PADDLEOCR_JOB_WORK_DIR", "/workspace/uploads/paddleocr_vl_jobs")),
    )
    for configured_root in roots:
        try:
            root = configured_root.resolve()
            # Never clean a filesystem root or another accidentally broad path.
            if len(root.parts) < 3 or not root.is_dir():
                continue
            for child in root.iterdir():
                try:
                    if child.stat(follow_symlinks=False).st_mtime >= cutoff:
                        continue
                    _remove_child(child)
                    result["removed"] += 1
                except FileNotFoundError:
                    continue
                except Exception as exc:
                    result["failed"] += 1
                    logger.warning("Failed to remove stale OCR artifact path={} error={}", child, exc)
        except Exception as exc:
            result["failed"] += 1
            logger.warning("Failed to inspect OCR work root path={} error={}", configured_root, exc)
    if result["removed"]:
        logger.info("Removed stale PaddleOCR working artifacts count={}", result["removed"])
    return result
