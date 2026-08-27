"""Explicit O(1) content revisions for report-local caches."""

from __future__ import annotations

from typing import Any, Tuple

def document_content_revision(report_or_document: Any) -> Tuple[str, int, int]:
    """Return ``(logical id, instance id, explicit revision)`` in O(1).

    The instance component prevents a copied private cache from being accepted
    by a newly loaded object that happens to have the same document ID and
    revision. The explicit revision invalidates caches after in-place edits.
    """
    document = getattr(report_or_document, "document_content", report_or_document)
    try:
        revision = max(1, int(getattr(document, "content_revision", 1) or 1))
    except (TypeError, ValueError):
        revision = 1
    return (
        str(getattr(document, "document_id", "") or ""),
        id(document),
        revision,
    )


def bump_document_content_revision(report_or_document: Any) -> int:
    """Mark a completed in-place corpus mutation and return its new revision."""
    document = getattr(report_or_document, "document_content", report_or_document)
    _document_id, _document_instance, current_revision = document_content_revision(document)
    revision = current_revision + 1
    try:
        document.content_revision = revision
    except Exception:
        object.__setattr__(document, "content_revision", revision)
    return revision


__all__ = ["bump_document_content_revision", "document_content_revision"]
