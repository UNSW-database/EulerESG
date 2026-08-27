"""Chat context helpers.

This module is intentionally small after the first routing/package split.
The main prompt-building behavior still lives in ESGChatbot to avoid changing
runtime logic. New chat context code should be added here instead of api/core.py.
"""

from __future__ import annotations

from typing import Iterable


def join_relevant_segments(segments: Iterable[str], *, limit: int = 5) -> list[str]:
    """Return a bounded list of non-empty retrieved segment texts."""
    result: list[str] = []
    for item in segments:
        text = str(item or "").strip()
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result
