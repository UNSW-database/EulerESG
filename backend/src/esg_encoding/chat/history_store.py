"""Chat history serialization helpers."""

from __future__ import annotations

from typing import Any, Dict, List

from ..models import ChatMessage


def messages_to_dict(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    """Convert ChatMessage objects to JSON-serializable dictionaries."""
    return [
        {
            "role": msg.role,
            "content": msg.content,
            "timestamp": msg.timestamp.isoformat(),
        }
        for msg in messages
    ]
