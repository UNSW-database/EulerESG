"""
HippoRAG upload hook: warm index immediately after upload.

Place this file at:
  backend/src/esg_encoding/retrieval/hipporag/hooks.py

Purpose
- Call HippoRAG background indexing right after /api/upload-report succeeds
- Non-blocking (threaded)
- Safe fallback if HippoRAG is disabled or not ready
"""

from __future__ import annotations

from typing import Optional
from loguru import logger

from ...models import ReportContent
from ...chat.chatbot import ESGChatbot


def warm_hipporag_after_upload(
        chatbot: ESGChatbot,
        report_content: Optional[ReportContent],
) -> None:
    """
    Schedule HippoRAG indexing immediately after upload.

    This function is SAFE to call even if:
    - HippoRAG is disabled
    - HippoRAG not installed
    - report_content is None
    """

    if report_content is None:
        return

    retriever = (
        getattr(chatbot, "_hipporag_retriever", None)
        or getattr(chatbot, "_hippo_retriever", None)
    )
    settings = (
        getattr(chatbot, "_hipporag_settings", None)
        or getattr(chatbot, "_hippo_settings", None)
    )

    if retriever is None or settings is None:
        # HippoRAG not enabled / not patched
        return

    if not settings.enabled:
        return

    try:
        # Non-blocking: background thread
        scheduled = retriever.schedule_index(
            file_id=report_content.document_id,
            report_content=report_content,
        )
        if scheduled:
            logger.info(
                f"[HippoRAG] upload warm scheduled "
                f"file_id={report_content.document_id}"
            )
    except Exception as e:
        # Never fail upload because of HippoRAG
        logger.warning(f"[HippoRAG] upload warm failed (ignored): {e}")
