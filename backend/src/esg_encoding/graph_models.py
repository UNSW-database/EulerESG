"""Public response models for deterministic disclosure graph projections."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DisclosureGraphOwner(BaseModel):
    """The report or company whose assessment is projected into the graph."""

    type: str
    id: str
    label: str


class DisclosureGraphNode(BaseModel):
    """One typed graph node with JSON-safe display properties."""

    id: str
    kind: str
    label: str
    group_id: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)

    @property
    def type(self) -> str:
        """Internal compatibility accessor; the public JSON contract uses kind."""
        return self.kind


class DisclosureGraphEdge(BaseModel):
    """One directed semantic relationship between graph nodes."""

    id: str
    kind: str
    source: str
    target: str
    label: str
    properties: Dict[str, Any] = Field(default_factory=dict)

    @property
    def type(self) -> str:
        """Internal compatibility accessor; the public JSON contract uses kind."""
        return self.kind


class DisclosureGraphStats(BaseModel):
    """Small counts used by the graph UI without re-scanning the payload."""

    node_count: int
    edge_count: int
    node_types: Dict[str, int] = Field(default_factory=dict)
    edge_types: Dict[str, int] = Field(default_factory=dict)
    disclosure_statuses: Dict[str, int] = Field(default_factory=dict)


class DisclosureGraphResponse(BaseModel):
    """Versioned graph payload shared by report, company and neighbor routes."""

    schema_version: str = "1.0"
    graph_id: str
    graph_revision: str
    owner: DisclosureGraphOwner
    scope_key: Optional[str] = None
    framework: Optional[str] = None
    nodes: List[DisclosureGraphNode] = Field(default_factory=list)
    edges: List[DisclosureGraphEdge] = Field(default_factory=list)
    stats: DisclosureGraphStats
    truncated: bool = False


__all__ = [
    "DisclosureGraphEdge",
    "DisclosureGraphNode",
    "DisclosureGraphOwner",
    "DisclosureGraphResponse",
    "DisclosureGraphStats",
]
