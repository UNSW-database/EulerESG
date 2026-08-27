"""Deterministic, read-only projection of assessments into disclosure graphs.

This module deliberately does not call an LLM, HippoRAG, or a graph database.
It projects already persisted standards, report metadata, assessments, and
evidence artifacts into a stable API representation suitable for interactive
exploration.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..company_registry import company_registry
from ..file_manager import file_manager
from ..graph_models import (
    DisclosureGraphEdge,
    DisclosureGraphNode,
    DisclosureGraphOwner,
    DisclosureGraphResponse,
    DisclosureGraphStats,
)
from .standards_library_service import StandardsLibraryError, get_standard_metrics


GRAPH_SCHEMA_VERSION = "1.0"


class DisclosureGraphNotFound(LookupError):
    """Raised when an owned graph source, assessment, or node is unavailable."""


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _identity_text(value: Any) -> str:
    text = _text(value).casefold()
    text = (
        text.replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )
    return re.sub(r"[^a-z0-9%]+", " ", text).strip()


def _json_digest(payload: Any, length: int = 24) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:length]


def _stable_id(kind: str, *parts: Any) -> str:
    return f"{kind}:{_json_digest(list(parts))}"


def _public_properties(**values: Any) -> Dict[str, Any]:
    """Drop null/blank values while preserving false, zero, and empty arrays."""
    output: Dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        output[key] = value
    return output


@lru_cache(maxsize=128)
def _read_json_snapshot(path_text: str, mtime_ns: int, size: int) -> Any:
    del mtime_ns, size
    return json.loads(Path(path_text).read_text(encoding="utf-8"))


def _read_json(path: Path) -> Any:
    stat = path.stat()
    return _read_json_snapshot(str(path.resolve()), stat.st_mtime_ns, stat.st_size)


def _direct_child(root: Path, filename: Any) -> Optional[Path]:
    """Resolve a persisted output filename without allowing path traversal."""
    name = str(filename or "").strip()
    if not name or Path(name).name != name:
        return None
    resolved_root = root.resolve()
    candidate = (resolved_root / name).resolve()
    if candidate.parent != resolved_root or not candidate.is_file():
        return None
    return candidate


def _sanitize_filename_part(value: Any) -> str:
    text = _text(value) or "report"
    for character in '<>:"/\\|?*':
        text = text.replace(character, "_")
    return text[:80]


def _report_manifest(root: Path, file_id: str) -> Optional[Dict[str, Any]]:
    path = _direct_child(root, f"{file_id}_compliance_manifest.json")
    if path is None:
        return None
    try:
        payload = _read_json(path)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _scoped_assessment_name(
    file_id: str,
    file_info: Dict[str, Any],
    scope_key: str,
) -> str:
    framework = _text(file_info.get("framework")).upper()
    if framework == "GRI":
        stem = f"GRI_{_text(file_info.get('gri_sector'))}_{scope_key}"
    elif framework in {"CDP", "TCFD"}:
        stem = f"{framework}_{scope_key}"
    else:
        stem = scope_key
    return f"{_sanitize_filename_part(stem)}_{file_id}_compliance.json"


def _find_report_assessment(
    file_id: str,
    file_info: Dict[str, Any],
    scope_key: Optional[str],
) -> Tuple[Optional[Path], Optional[str]]:
    root = Path(file_manager.compliance_outputs)
    requested_scope = _text(scope_key)
    manifest = _report_manifest(root, file_id)
    outputs = list((manifest or {}).get("outputs") or [])

    if requested_scope:
        for output in outputs:
            if _text(output.get("scope_key")) != requested_scope:
                continue
            path = _direct_child(root, output.get("json_filename"))
            if path is not None:
                return path, requested_scope
        direct = _direct_child(
            root,
            _scoped_assessment_name(file_id, file_info, requested_scope),
        )
        if direct is not None:
            return direct, requested_scope
        # A requested scope is strict: never silently return another scope.
        return None, requested_scope

    if outputs:
        default_scope = _text((manifest or {}).get("default_scope_key"))
        selected = next(
            (
                output
                for output in outputs
                if default_scope and _text(output.get("scope_key")) == default_scope
            ),
            outputs[0],
        )
        path = _direct_child(root, selected.get("json_filename"))
        if path is not None:
            return path, _text(selected.get("scope_key")) or default_scope or None

    direct = _direct_child(root, f"{file_id}_compliance.json")
    if direct is not None:
        return direct, None

    # Legacy outputs are only considered without an explicit scope. Exclude
    # company aggregates so they can never become a report-level judgment.
    matches = [
        path
        for path in root.glob("*compliance*.json")
        if (
            path.is_file()
            and file_id in path.name
            and not path.name.startswith("company_")
        )
    ]
    if matches:
        matches.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
        return matches[0], None
    return None, None


def _framework_for(
    assessment: Dict[str, Any],
    file_info: Dict[str, Any],
) -> str:
    return (
        _text(assessment.get("framework"))
        or _text(file_info.get("framework"))
        or "Unknown"
    ).upper()


def _scope_for(
    assessment: Dict[str, Any],
    file_info: Dict[str, Any],
    resolved_scope: Optional[str],
) -> Optional[str]:
    explicit = _text(resolved_scope) or _text(assessment.get("scope_key"))
    if explicit:
        return explicit
    framework = _framework_for(assessment, file_info)
    if framework == "GRI":
        return _text(file_info.get("gri_topic")) or None
    return (
        _text(file_info.get("semi_industry"))
        or _text(file_info.get("industry"))
        or None
    )


def _group_for(framework: str, file_info: Dict[str, Any]) -> Optional[str]:
    if framework == "GRI":
        return _text(file_info.get("gri_sector")) or None
    return None


def _assessment_metrics(assessment: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = assessment.get("metric_analyses")
    if not isinstance(rows, list):
        rows = assessment.get("sasb_metric_rows")
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


def _metric_field(row: Dict[str, Any], lower: str, title: str) -> Any:
    # Current assessment JSON intentionally carries canonical lowercase fields
    # alongside legacy title-case display fields. Key presence, not truthiness,
    # determines precedence so a canonical null/"n/a" cannot be resurrected by
    # a stale legacy duplicate. Older files with only title-case keys still work.
    return row.get(lower) if lower in row else row.get(title)


def _status_value(row: Dict[str, Any]) -> str:
    raw = _metric_field(row, "disclosure_status", "Disclosure Status")
    return _normalize_status_value(raw)


def _normalize_status_value(raw: Any) -> str:
    value = _text(raw).lower().replace(" ", "_").replace("-", "_")
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    aliases = {
        "disclosed": "fully_disclosed",
        "full": "fully_disclosed",
        "partial": "partially_disclosed",
        "partially": "partially_disclosed",
        "not": "not_disclosed",
        "none": "not_disclosed",
    }
    return aliases.get(value, value or "not_disclosed")


def _validate_assessment_consistency(
    assessment: Dict[str, Any],
    *,
    framework: str,
    scope_key: Optional[str],
) -> None:
    """Reject persisted assessment snapshots that cannot form a lossless graph."""
    rows = _assessment_metrics(assessment)
    if not rows:
        raise DisclosureGraphNotFound("Report assessment has no metric results")

    declared_total = assessment.get("total_metrics_analyzed")
    if declared_total is None:
        declared_total = assessment.get("total_metrics")
    if declared_total is not None:
        try:
            expected_total = int(declared_total)
        except (TypeError, ValueError) as exc:
            raise DisclosureGraphNotFound(
                "Report assessment has an invalid total metric count"
            ) from exc
        if expected_total != len(rows):
            raise DisclosureGraphNotFound(
                "Report assessment metric count is inconsistent with its summary"
            )

    identities = [
        _metric_identity(
            framework,
            scope_key,
            _metric_field(row, "metric_code", "Code"),
            _metric_field(row, "metric_name", "Metric"),
        )
        for row in rows
    ]
    if len(set(identities)) != len(identities):
        raise DisclosureGraphNotFound(
            "Report assessment contains duplicate metric identities"
        )

    supported_statuses = {
        "fully_disclosed",
        "partially_disclosed",
        "not_disclosed",
    }
    actual = Counter(_status_value(row) for row in rows)
    if any(status not in supported_statuses for status in actual):
        raise DisclosureGraphNotFound(
            "Report assessment contains an invalid disclosure status"
        )

    raw_summary = assessment.get("disclosure_summary")
    if not isinstance(raw_summary, dict):
        return
    expected: Counter[str] = Counter()
    for raw_status, raw_count in raw_summary.items():
        status = _normalize_status_value(raw_status)
        if status not in supported_statuses:
            continue
        try:
            expected[status] += int(raw_count)
        except (TypeError, ValueError) as exc:
            raise DisclosureGraphNotFound(
                "Report assessment has an invalid disclosure summary"
            ) from exc
    if expected and any(expected[status] != actual[status] for status in supported_statuses):
        raise DisclosureGraphNotFound(
            "Report assessment disclosure totals are inconsistent with its rows"
        )


def _metric_identity(
    framework: str,
    scope_key: Optional[str],
    code: Any,
    name: Any,
) -> Tuple[str, str, str, str]:
    # Metric name is intentionally part of the identity: SASB can assess
    # multiple separately named submetrics under one shared code.
    return (
        _identity_text(framework),
        _identity_text(scope_key),
        _identity_text(code),
        _identity_text(name),
    )


class _GraphBuilder:
    def __init__(self) -> None:
        self.nodes: Dict[str, DisclosureGraphNode] = {}
        self.edges: Dict[str, DisclosureGraphEdge] = {}
        self.truncated = False

    def add_node(
        self,
        node_id: str,
        node_type: str,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
        *,
        group_id: Optional[str] = None,
    ) -> str:
        clean_properties = dict(properties or {})
        existing = self.nodes.get(node_id)
        if existing is None:
            self.nodes[node_id] = DisclosureGraphNode(
                id=node_id,
                kind=node_type,
                label=label or node_type.title(),
                group_id=group_id,
                properties=clean_properties,
            )
        else:
            merged = dict(existing.properties)
            merged.update(clean_properties)
            existing.properties = merged
            if label and (not existing.label or existing.label == existing.type.title()):
                existing.label = label
            if group_id and not existing.group_id:
                existing.group_id = group_id
        return node_id

    def add_edge(
        self,
        edge_type: str,
        source: str,
        target: str,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
        discriminator: Any = None,
    ) -> str:
        edge_id = _stable_id(
            "edge",
            edge_type,
            source,
            target,
            discriminator,
        )
        if edge_id not in self.edges:
            self.edges[edge_id] = DisclosureGraphEdge(
                id=edge_id,
                kind=edge_type,
                source=source,
                target=target,
                label=label,
                properties=dict(properties or {}),
            )
        return edge_id

    def response(
        self,
        *,
        graph_id: str,
        graph_revision: str,
        owner: DisclosureGraphOwner,
        scope_key: Optional[str],
        framework: Optional[str],
    ) -> DisclosureGraphResponse:
        nodes = sorted(self.nodes.values(), key=lambda item: item.id)
        edges = sorted(self.edges.values(), key=lambda item: item.id)
        node_types = Counter(item.type for item in nodes)
        edge_types = Counter(item.type for item in edges)
        disclosure_statuses = Counter(
            str(item.properties.get("status") or "not_disclosed")
            for item in nodes
            if item.type == "disclosure"
        )
        return DisclosureGraphResponse(
            schema_version=GRAPH_SCHEMA_VERSION,
            graph_id=graph_id,
            graph_revision=graph_revision,
            owner=owner,
            scope_key=scope_key,
            framework=framework,
            nodes=nodes,
            edges=edges,
            stats=DisclosureGraphStats(
                node_count=len(nodes),
                edge_count=len(edges),
                node_types=dict(sorted(node_types.items())),
                edge_types=dict(sorted(edge_types.items())),
                disclosure_statuses=dict(sorted(disclosure_statuses.items())),
            ),
            truncated=self.truncated,
        )


def _load_standard_scope(
    framework: str,
    scope_key: Optional[str],
    group_key: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not framework or not scope_key:
        return None
    try:
        return get_standard_metrics(
            framework.lower(),
            scope_key,
            group_id=group_key,
        )
    except StandardsLibraryError:
        return None


def _taxonomy_nodes(
    builder: _GraphBuilder,
    *,
    framework: str,
    scope_key: Optional[str],
    group_key: Optional[str],
    standard_scope: Optional[Dict[str, Any]],
) -> Tuple[Dict[Tuple[str, str, str, str], str], str, str]:
    framework_key = framework.lower() or "unknown"
    framework_data = (standard_scope or {}).get("framework") or {}
    framework_label = _text(framework_data.get("name")) or framework
    framework_id = f"framework:{framework_key}"
    builder.add_node(
        framework_id,
        "framework",
        framework_label,
        _public_properties(
            framework=framework,
            as_of=framework_data.get("as_of"),
            source_url=framework_data.get("source_url"),
        ),
    )

    group_data = (standard_scope or {}).get("group") or {}
    resolved_group_key = _text(group_data.get("id")) or _text(group_key)
    group_id: Optional[str] = None
    parent_id = framework_id
    if resolved_group_key:
        group_id = _stable_id("group", framework_key, resolved_group_key)
        builder.add_node(
            group_id,
            "group",
            _text(group_data.get("label")) or resolved_group_key,
            _public_properties(key=resolved_group_key),
        )
        builder.add_edge("has_group", framework_id, group_id, "has group")
        parent_id = group_id

    scope_data = (standard_scope or {}).get("scope") or {}
    resolved_scope_key = _text(scope_data.get("id")) or _text(scope_key) or "default"
    scope_id = _stable_id(
        "scope",
        framework_key,
        resolved_group_key,
        resolved_scope_key,
    )
    builder.add_node(
        scope_id,
        "scope",
        _text(scope_data.get("label")) or resolved_scope_key,
        _public_properties(key=resolved_scope_key, group_key=resolved_group_key),
    )
    builder.add_edge("has_scope", parent_id, scope_id, "has scope")

    metric_nodes: Dict[Tuple[str, str, str, str], str] = {}
    for metric in (standard_scope or {}).get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        metric_id = _add_metric_taxonomy(
            builder,
            framework=framework,
            scope_key=resolved_scope_key,
            scope_id=scope_id,
            row=metric,
            standard_metric=metric,
        )
        identity = _metric_identity(
            framework,
            resolved_scope_key,
            metric.get("code"),
            metric.get("name"),
        )
        metric_nodes[identity] = metric_id
    return metric_nodes, scope_id, resolved_scope_key


def _add_metric_taxonomy(
    builder: _GraphBuilder,
    *,
    framework: str,
    scope_key: Optional[str],
    scope_id: str,
    row: Dict[str, Any],
    standard_metric: Optional[Dict[str, Any]] = None,
) -> str:
    name = _text(_metric_field(row, "metric_name", "Metric") or row.get("name"))
    code = _text(_metric_field(row, "metric_code", "Code") or row.get("code"))
    topic = _text(_metric_field(row, "topic", "Topic") or row.get("topic")) or "General"
    category = _text(
        _metric_field(row, "category", "Category") or row.get("category")
    )
    metric_type = _text(_metric_field(row, "type", "Type") or row.get("type"))
    unit = _text(_metric_field(row, "unit", "Unit") or row.get("unit"))
    standard_metric = standard_metric or {}
    simple_definition = _text(standard_metric.get("simple_definition"))
    technical_definition = (
        _text(standard_metric.get("definition"))
        or _text(_metric_field(row, "definition", "Definition"))
    )
    definition = simple_definition or technical_definition

    topic_id = _stable_id("topic", framework, scope_key, topic)
    builder.add_node(
        topic_id,
        "topic",
        topic,
        _public_properties(scope_key=scope_key),
    )
    builder.add_edge("has_topic", scope_id, topic_id, "has topic")

    identity = _metric_identity(framework, scope_key, code, name)
    metric_id = _stable_id("metric", *identity)
    builder.add_node(
        metric_id,
        "metric",
        name or code or "Unnamed metric",
        _public_properties(
            code=code,
            name=name,
            topic=topic,
            category=category,
            type=metric_type,
            unit=unit,
            definition=definition,
            simple_definition=simple_definition,
            technical_definition=technical_definition,
            framework=framework,
            scope_key=scope_key,
        ),
        group_id=code or None,
    )
    builder.add_edge("has_metric", topic_id, metric_id, "has metric")
    return metric_id


def _path_within(path_value: Any, root: Path) -> Optional[Path]:
    """Return an existing file only when it stays inside the artifact root."""
    try:
        path = Path(str(path_value or "")).resolve()
        resolved_root = root.resolve()
        if path.parent != resolved_root or not path.is_file():
            return None
        return path
    except Exception:
        return None


def _artifact_index(file_id: str, file_info: Dict[str, Any]) -> Dict[str, Any]:
    """Load only JSON evidence artifacts; never load the embedding matrix."""
    root = Path(file_manager.embeddings_outputs)
    segment_path = _path_within(file_info.get("segments_path"), root)
    if segment_path is None:
        fallback = root / f"{file_id}_segments.json"
        segment_path = fallback if fallback.is_file() else None

    segments: Dict[str, Dict[str, Any]] = {}
    if segment_path is not None:
        try:
            payload = _read_json(segment_path)
            segments = {
                str(item.get("segment_id")): item
                for item in (payload or [])
                if isinstance(item, dict) and item.get("segment_id")
            }
        except Exception:
            segments = {}

    corpus_path = _path_within(file_info.get("metric_retrieval_corpus_path"), root)
    if corpus_path is None:
        manifest_path = _path_within(
            file_info.get("metric_retrieval_manifest_path"),
            root,
        )
        if manifest_path is None:
            fallback_manifest = root / f"{file_id}_metric_retrieval_manifest.json"
            manifest_path = fallback_manifest if fallback_manifest.is_file() else None
        if manifest_path is not None:
            try:
                manifest = _read_json(manifest_path)
                corpus_path = _direct_child(root, manifest.get("corpus_file"))
            except Exception:
                corpus_path = None

    blocks: Dict[str, Dict[str, Any]] = {}
    segment_to_block: Dict[str, str] = {}
    if corpus_path is not None:
        try:
            corpus = _read_json(corpus_path)
            blocks = {
                str(item.get("block_id")): item
                for item in (corpus.get("evidence_blocks") or [])
                if isinstance(item, dict) and item.get("block_id")
            }
            segment_to_block = {
                str(segment_id): str(block_id)
                for segment_id, block_id in (
                    corpus.get("segment_to_block_id") or {}
                ).items()
            }
        except Exception:
            blocks = {}
            segment_to_block = {}

    return {
        "segments": segments,
        "blocks": blocks,
        "segment_to_block": segment_to_block,
    }


def _local_segment_id(segment_id: Any, source_report_id: str) -> str:
    value = str(segment_id or "").strip()
    prefix = f"{source_report_id}::"
    return value[len(prefix) :] if value.startswith(prefix) else value


def _evidence_references(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return ordered, de-duplicated persisted evidence references."""
    references: List[Dict[str, Any]] = []
    explicit_support_ids: set[str] = set()

    for year_value in row.get("year_values") or row.get("Year Values") or []:
        if not isinstance(year_value, dict):
            continue
        segment_id = _text(year_value.get("evidence_segment_id"))
        if segment_id:
            explicit_support_ids.add(segment_id)
            references.append(
                {
                    "segment_id": segment_id,
                    "data_page": year_value.get("page"),
                    "source_report_id": next(
                        (
                            source.get("source_report_id")
                            for source in (year_value.get("sources") or [])
                            if isinstance(source, dict) and source.get("source_report_id")
                        ),
                        None,
                    ),
                    "role": "supporting",
                    "source_type": "report_page",
                    "year": year_value.get("year"),
                }
            )

    status = _status_value(row)
    sources = [
        dict(source)
        for source in (row.get("evidence_sources") or [])
        if isinstance(source, dict)
    ]
    for index, source in enumerate(sources):
        segment_id = _text(source.get("segment_id"))
        role = "candidate"
        if status != "not_disclosed" and (
            segment_id in explicit_support_ids or index == 0
        ):
            role = "supporting"
        references.append({**source, "segment_id": segment_id or None, "role": role})

    # Legacy assessments have only retrieval segment IDs. These are candidates,
    # not automatically supporting evidence, because the list was the complete
    # retrieval window rather than a final citation list.
    for segment_id in row.get("evidence_segments") or []:
        value = _text(segment_id)
        if value:
            references.append(
                {
                    "segment_id": value,
                    "role": (
                        "supporting" if value in explicit_support_ids else "candidate"
                    ),
                    "source_type": "report_page",
                }
            )

    unique: List[Dict[str, Any]] = []
    seen = set()
    for reference in references:
        key = (
            _text(reference.get("source_report_id")),
            _text(reference.get("segment_id")),
            reference.get("data_page"),
            reference.get("target_page"),
            _text(reference.get("role")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(reference)
    return unique


def _evidence_node(
    *,
    reference: Dict[str, Any],
    default_report_id: str,
    report_infos: Dict[str, Dict[str, Any]],
    artifact_indexes: Dict[str, Dict[str, Any]],
) -> Optional[Tuple[str, str, Dict[str, Any], str]]:
    source_report_id = _text(reference.get("source_report_id")) or default_report_id
    if source_report_id not in report_infos:
        return None
    raw_segment_id = _text(reference.get("segment_id"))
    local_segment_id = _local_segment_id(raw_segment_id, source_report_id)
    if source_report_id not in artifact_indexes:
        artifact_indexes[source_report_id] = _artifact_index(
            source_report_id,
            report_infos[source_report_id],
        )
    index = artifact_indexes[source_report_id]
    block_id = index["segment_to_block"].get(local_segment_id)
    block = index["blocks"].get(block_id) if block_id else None
    segment = index["segments"].get(local_segment_id)

    if block is not None:
        evidence_key = f"block:{block_id}"
        block_type = _text(block.get("block_type")) or "evidence"
        page = block.get("page_number") or reference.get("data_page")
        content = str(block.get("full_content") or "").strip()
        source_segment_ids = list(block.get("source_segment_ids") or [])
        properties = _public_properties(
            report_id=source_report_id,
            evidence_kind="complete_block",
            block_id=block_id,
            block_type=block_type,
            page=page,
            content=content,
            section_path=list(block.get("section_path") or []),
            source_table_id=block.get("source_table_id"),
            source_segment_ids=source_segment_ids,
            content_hash=block.get("content_hash"),
        )
    elif segment is not None:
        evidence_key = f"segment:{local_segment_id}"
        block_type = _text(segment.get("segment_type")) or "evidence"
        page = segment.get("page_number") or reference.get("data_page")
        content = str(segment.get("content") or "").strip()
        properties = _public_properties(
            report_id=source_report_id,
            evidence_kind="segment",
            segment_id=local_segment_id,
            block_type=block_type,
            page=page,
            content=content,
            source_table_id=segment.get("source_table_id"),
            row_header=segment.get("row_header"),
            column_header=segment.get("col_header"),
            value_text=segment.get("value_text"),
            unit=segment.get("unit"),
        )
    else:
        page = reference.get("data_page") or reference.get("target_page")
        evidence_key = (
            f"segment:{local_segment_id}"
            if local_segment_id
            else f"page:{page}:{_text(reference.get('source_type'))}"
        )
        block_type = _text(reference.get("evidence_type")) or "evidence"
        fallback_content = (
            _text(reference.get("source_context"))
            or _text(reference.get("caption"))
        )
        properties = _public_properties(
            report_id=source_report_id,
            evidence_kind="reference",
            segment_id=local_segment_id,
            block_type=block_type,
            page=page,
            content=fallback_content,
        )

    # Whitelist public provenance; never leak persisted filesystem paths.
    for key in (
        "source_type",
        "data_page",
        "target_page",
        "link_source_page",
        "anchor_text",
        "asset_id",
        "bbox",
        "caption",
        "confidence",
        "structure_confidence",
        "ocr_confidence",
        "header_path",
        "rowspan",
        "colspan",
        "parse_pass",
        "review_status",
        "conflicts",
        "year",
    ):
        if reference.get(key) not in (None, "", []):
            properties[key] = reference.get(key)

    evidence_id = f"evidence:{source_report_id}:{evidence_key}"
    page_text = f" · p. {page}" if page not in (None, "") else ""
    label = f"{block_type.replace('_', ' ').title()}{page_text}"
    return evidence_id, label, properties, source_report_id


def _standard_lookup(
    standard_scope: Optional[Dict[str, Any]],
) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    exact: Dict[Tuple[str, str], Dict[str, Any]] = {}
    by_code: Dict[str, List[Dict[str, Any]]] = {}
    for metric in (standard_scope or {}).get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        code = _identity_text(metric.get("code"))
        name = _identity_text(metric.get("name"))
        exact[(code, name)] = metric
        if code:
            by_code.setdefault(code, []).append(metric)
    return exact, by_code


def _matching_standard_metric(
    row: Dict[str, Any],
    exact: Dict[Tuple[str, str], Dict[str, Any]],
    by_code: Dict[str, List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    code = _identity_text(_metric_field(row, "metric_code", "Code"))
    name = _identity_text(_metric_field(row, "metric_name", "Metric"))
    direct = exact.get((code, name))
    if direct is not None:
        return direct
    candidates = by_code.get(code) or []
    # A code-only fallback is safe only when that code has exactly one metric.
    return candidates[0] if len(candidates) == 1 else None


def _report_label(file_id: str, file_info: Dict[str, Any]) -> str:
    return (
        _text(file_info.get("original_name"))
        or _text(file_info.get("safe_filename"))
        or file_id
    )


def _report_year(file_info: Dict[str, Any]) -> Optional[int]:
    raw = file_info.get("report_year")
    try:
        year = int(raw)
        if 1900 <= year <= 2099:
            return year
    except (TypeError, ValueError):
        pass
    for value in (file_info.get("original_name"), file_info.get("safe_filename")):
        match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(value or ""))
        if match:
            return int(match.group(1))
    return None


def _add_report_node(
    builder: _GraphBuilder,
    file_id: str,
    file_info: Dict[str, Any],
    *,
    assessment_available: bool,
    assessment: Optional[Dict[str, Any]] = None,
) -> str:
    assessment = assessment or {}
    assessment_rows = _assessment_metrics(assessment)
    disclosure_summary = assessment.get("disclosure_summary")
    if not isinstance(disclosure_summary, dict) and assessment_rows:
        disclosure_summary = dict(
            sorted(Counter(_status_value(row) for row in assessment_rows).items())
        )
    overall_score = assessment.get("overall_compliance_score")
    if overall_score is None:
        overall_score = assessment.get("overall_score")
    total_metrics = assessment.get("total_metrics_analyzed")
    if total_metrics is None:
        total_metrics = assessment.get("total_metrics")
    if total_metrics is None and assessment_rows:
        total_metrics = len(assessment_rows)
    if overall_score is None and total_metrics:
        fully = int((disclosure_summary or {}).get("fully_disclosed") or 0)
        partial = int((disclosure_summary or {}).get("partially_disclosed") or 0)
        overall_score = (fully + 0.5 * partial) / int(total_metrics)
    report_id = f"report:{file_id}"
    builder.add_node(
        report_id,
        "report",
        _report_label(file_id, file_info),
        _public_properties(
            file_id=file_id,
            filename=file_info.get("original_name") or file_info.get("safe_filename"),
            report_year=_report_year(file_info),
            status=file_info.get("status"),
            page_count=file_info.get("page_count"),
            upload_time=file_info.get("upload_time"),
            framework=file_info.get("framework"),
            industry=file_info.get("industry"),
            semi_industry=file_info.get("semi_industry"),
            gri_sector=file_info.get("gri_sector"),
            gri_topic=file_info.get("gri_topic"),
            assessment_available=assessment_available,
            overall_score=overall_score,
            total_metrics_analyzed=total_metrics,
            disclosure_summary=disclosure_summary,
        ),
    )
    return report_id


def _project_assessment_rows(
    builder: _GraphBuilder,
    *,
    report_id: str,
    report_node_id: str,
    assessment: Dict[str, Any],
    framework: str,
    scope_key: Optional[str],
    group_key: Optional[str],
    standard_scope: Optional[Dict[str, Any]],
    metric_nodes: Dict[Tuple[str, str, str, str], str],
    scope_node_id: str,
    resolved_scope_key: str,
    include_evidence: bool,
    evidence_limit: int,
    evidence_disclosure_ids: Optional[set[str]],
    report_infos: Dict[str, Dict[str, Any]],
    artifact_indexes: Dict[str, Dict[str, Any]],
) -> None:
    exact_standard, standards_by_code = _standard_lookup(standard_scope)

    for row_index, row in enumerate(_assessment_metrics(assessment)):
        name = _text(_metric_field(row, "metric_name", "Metric"))
        code = _text(_metric_field(row, "metric_code", "Code"))
        identity = _metric_identity(framework, resolved_scope_key, code, name)
        metric_node_id = metric_nodes.get(identity)
        if metric_node_id is None:
            standard_metric = _matching_standard_metric(
                row,
                exact_standard,
                standards_by_code,
            )
            metric_node_id = _add_metric_taxonomy(
                builder,
                framework=framework,
                scope_key=resolved_scope_key,
                scope_id=scope_node_id,
                row=row,
                standard_metric=standard_metric,
            )
            metric_nodes[identity] = metric_node_id

        status = _status_value(row)
        year_values = _metric_field(row, "year_values", "Year Values") or []
        if not isinstance(year_values, list):
            year_values = []
        references = _evidence_references(row)
        metric_hash = metric_node_id.split(":", 1)[-1]
        disclosure_id = f"disclosure:{report_id}:{metric_hash}"
        value = _metric_field(row, "value", "Value")
        page = _metric_field(row, "page", "Page")
        selected_year = _metric_field(row, "selected_year", "Selected Year")
        value_status = _metric_field(row, "value_status", "Value Status")
        reasoning = _metric_field(row, "reasoning", "LLM Analysis")
        context = _metric_field(row, "context", "Context")
        builder.add_node(
            disclosure_id,
            "disclosure",
            f"{name or code or 'Metric'} · {status.replace('_', ' ').title()}",
            _public_properties(
                report_id=report_id,
                aggregation_scope="report",
                metric_code=code,
                metric_name=name,
                status=status,
                value=value,
                unit=_metric_field(row, "unit", "Unit"),
                value_status=value_status,
                page=page,
                year_values=year_values,
                selected_year=selected_year,
                context=context,
                reasoning=reasoning,
                derived_calculation=row.get("derived_calculation"),
                improvement_suggestions=list(row.get("improvement_suggestions") or []),
                evidence_count=len(references),
                has_evidence=bool(references),
                assessment_row_index=row_index,
            ),
            group_id=code or None,
        )
        builder.add_edge(
            "has_disclosure",
            report_node_id,
            disclosure_id,
            "has disclosure",
        )
        builder.add_edge(
            "assesses",
            disclosure_id,
            metric_node_id,
            "assesses",
        )

        if (
            not include_evidence
            or not references
            or (
                evidence_disclosure_ids is not None
                and disclosure_id not in evidence_disclosure_ids
            )
        ):
            continue
        if len(references) > evidence_limit:
            builder.truncated = True
        for reference_index, reference in enumerate(references[:evidence_limit]):
            evidence = _evidence_node(
                reference=reference,
                default_report_id=report_id,
                report_infos=report_infos,
                artifact_indexes=artifact_indexes,
            )
            if evidence is None:
                continue
            evidence_id, evidence_label, evidence_properties, source_report_id = evidence
            builder.add_node(
                evidence_id,
                "evidence",
                evidence_label,
                evidence_properties,
                group_id=source_report_id,
            )
            role = _text(reference.get("role")) or "candidate"
            relation = "supported_by" if role == "supporting" else "candidate_evidence"
            builder.add_edge(
                relation,
                disclosure_id,
                evidence_id,
                relation.replace("_", " "),
                _public_properties(
                    role=role,
                    page=evidence_properties.get("page"),
                    year=reference.get("year"),
                ),
                discriminator=reference_index,
            )
            source_report_node_id = f"report:{source_report_id}"
            if source_report_node_id in builder.nodes:
                builder.add_edge(
                    "from_report",
                    evidence_id,
                    source_report_node_id,
                    "from report",
                )


def _load_owned_report_context(
    file_id: str,
    user_id: int,
    scope_key: Optional[str],
) -> Dict[str, Any]:
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not isinstance(file_info, dict) or file_info.get("file_type") != "report":
        raise DisclosureGraphNotFound("Report not found or access denied")
    report_status = _text(file_info.get("status")).lower()
    if report_status not in {"processed", "ready", "completed"}:
        raise DisclosureGraphNotFound("Report analysis is not complete")
    if any(
        file_info.get(key) is True
        for key in ("stale", "analysis_stale", "assessment_stale", "expired")
    ):
        raise DisclosureGraphNotFound("Report assessment is stale")
    canonical_id = _text(file_info.get("file_id")) or _text(file_id)
    path, resolved_scope = _find_report_assessment(
        canonical_id,
        file_info,
        scope_key,
    )
    if path is None:
        detail = (
            f"No assessment found for report {canonical_id} and scope {_text(scope_key)}"
            if _text(scope_key)
            else f"No assessment found for report {canonical_id}"
        )
        raise DisclosureGraphNotFound(detail)
    try:
        assessment = _read_json(path)
    except Exception as exc:
        raise DisclosureGraphNotFound("Report assessment is unavailable") from exc
    if not isinstance(assessment, dict) or not _assessment_metrics(assessment):
        raise DisclosureGraphNotFound("Report assessment has no metric results")
    framework = _framework_for(assessment, file_info)
    scope = _scope_for(assessment, file_info, resolved_scope)
    _validate_assessment_consistency(
        assessment,
        framework=framework,
        scope_key=scope,
    )
    return {
        "file_id": canonical_id,
        "file_info": file_info,
        "assessment": assessment,
        "assessment_path": path,
        "framework": framework,
        "scope_key": scope,
        "group_key": _group_for(framework, file_info),
    }


def build_report_disclosure_graph(
    *,
    file_id: str,
    user_id: int,
    scope_key: Optional[str] = None,
    include_evidence: bool = False,
    evidence_limit: int = 8,
    _evidence_disclosure_ids: Optional[set[str]] = None,
) -> DisclosureGraphResponse:
    context = _load_owned_report_context(file_id, user_id, scope_key)
    canonical_id = context["file_id"]
    file_info = context["file_info"]
    assessment = context["assessment"]
    framework = context["framework"]
    scope = context["scope_key"]
    group = context["group_key"]
    standard_scope = _load_standard_scope(framework, scope, group)

    builder = _GraphBuilder()
    report_node_id = _add_report_node(
        builder,
        canonical_id,
        file_info,
        assessment_available=True,
        assessment=assessment,
    )
    metric_nodes, scope_node_id, resolved_scope = _taxonomy_nodes(
        builder,
        framework=framework,
        scope_key=scope,
        group_key=group,
        standard_scope=standard_scope,
    )
    report_infos = {canonical_id: file_info}
    _project_assessment_rows(
        builder,
        report_id=canonical_id,
        report_node_id=report_node_id,
        assessment=assessment,
        framework=framework,
        scope_key=scope,
        group_key=group,
        standard_scope=standard_scope,
        metric_nodes=metric_nodes,
        scope_node_id=scope_node_id,
        resolved_scope_key=resolved_scope,
        include_evidence=include_evidence,
        evidence_limit=max(1, int(evidence_limit)),
        evidence_disclosure_ids=_evidence_disclosure_ids,
        report_infos=report_infos,
        artifact_indexes={},
    )

    graph_id = _stable_id("graph", "report", canonical_id, resolved_scope)
    graph_revision = _json_digest(
        {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "owner_type": "report",
            "owner_id": canonical_id,
            "scope_key": resolved_scope,
            "assessment": assessment,
        },
        length=32,
    )
    return builder.response(
        graph_id=graph_id,
        graph_revision=graph_revision,
        owner=DisclosureGraphOwner(
            type="report",
            id=canonical_id,
            label=_report_label(canonical_id, file_info),
        ),
        scope_key=resolved_scope,
        framework=framework,
    )


def _selected_company_report_ids(
    company: Dict[str, Any],
    selected_report_ids: Optional[Sequence[str]],
) -> List[str]:
    owned_ids = [_text(value) for value in company.get("report_ids") or [] if _text(value)]
    if not owned_ids:
        raise DisclosureGraphNotFound("Company has no reports")
    requested = list(
        dict.fromkeys(
            _text(value)
            for value in (selected_report_ids or [])
            if _text(value)
        )
    )
    if not requested:
        return owned_ids
    invalid = [value for value in requested if value not in set(owned_ids)]
    if invalid:
        raise DisclosureGraphNotFound(
            "One or more requested reports do not belong to this company"
        )
    return requested


def build_company_disclosure_graph(
    *,
    company_id: str,
    user_id: int,
    scope_key: Optional[str] = None,
    include_evidence: bool = False,
    evidence_limit: int = 8,
    selected_report_ids: Optional[Sequence[str]] = None,
    _evidence_disclosure_ids: Optional[set[str]] = None,
) -> DisclosureGraphResponse:
    company = company_registry.get_company(company_id, user_id)
    if not isinstance(company, dict):
        raise DisclosureGraphNotFound("Company not found or access denied")
    report_ids = _selected_company_report_ids(company, selected_report_ids)

    report_infos: Dict[str, Dict[str, Any]] = {}
    contexts: Dict[str, Dict[str, Any]] = {}
    for report_id in report_ids:
        file_info = file_manager.get_file_info(report_id, user_id=user_id)
        if not isinstance(file_info, dict) or file_info.get("file_type") != "report":
            continue
        canonical_id = _text(file_info.get("file_id")) or report_id
        report_infos[canonical_id] = file_info
        try:
            contexts[canonical_id] = _load_owned_report_context(
                canonical_id,
                user_id,
                scope_key,
            )
        except DisclosureGraphNotFound:
            # Keep the report visible as unavailable, but never substitute the
            # company aggregate assessment for its missing judgment set.
            continue

    if not report_infos:
        raise DisclosureGraphNotFound("Company has no available report metadata")
    if not contexts:
        detail = (
            f"No report assessments found for company and scope {_text(scope_key)}"
            if _text(scope_key)
            else "No report assessments found for company"
        )
        raise DisclosureGraphNotFound(detail)

    builder = _GraphBuilder()
    company_node_id = f"company:{company_id}"
    company_label = _text(company.get("company_name")) or company_id
    builder.add_node(
        company_node_id,
        "company",
        company_label,
        _public_properties(
            company_id=company_id,
            company_name=company_label,
            status=company.get("status"),
            analysis_version=company.get("analysis_version"),
            stale=bool(company.get("stale")),
            report_count=len(report_infos),
            assessed_report_count=len(contexts),
        ),
    )

    report_node_ids: Dict[str, str] = {}
    for report_id, file_info in report_infos.items():
        report_node_id = _add_report_node(
            builder,
            report_id,
            file_info,
            assessment_available=report_id in contexts,
            assessment=(contexts.get(report_id) or {}).get("assessment"),
        )
        report_node_ids[report_id] = report_node_id
        builder.add_edge(
            "has_report",
            company_node_id,
            report_node_id,
            "has report",
        )

    taxonomy_cache: Dict[
        Tuple[str, str, str],
        Tuple[
            Optional[Dict[str, Any]],
            Dict[Tuple[str, str, str, str], str],
            str,
            str,
        ],
    ] = {}
    artifact_indexes: Dict[str, Dict[str, Any]] = {}
    frameworks = set()
    scopes = set()
    revision_reports: List[Dict[str, Any]] = []
    for report_id in report_infos:
        context = contexts.get(report_id)
        if context is None:
            revision_reports.append({"report_id": report_id, "assessment": None})
            continue
        framework = context["framework"]
        scope = context["scope_key"]
        group = context["group_key"]
        frameworks.add(framework)
        if scope:
            scopes.add(scope)
        cache_key = (framework, _text(group), _text(scope))
        cached_taxonomy = taxonomy_cache.get(cache_key)
        if cached_taxonomy is None:
            standard_scope = _load_standard_scope(framework, scope, group)
            metric_nodes, scope_node_id, resolved_scope = _taxonomy_nodes(
                builder,
                framework=framework,
                scope_key=scope,
                group_key=group,
                standard_scope=standard_scope,
            )
            cached_taxonomy = (
                standard_scope,
                metric_nodes,
                scope_node_id,
                resolved_scope,
            )
            taxonomy_cache[cache_key] = cached_taxonomy
        standard_scope, metric_nodes, scope_node_id, resolved_scope = cached_taxonomy
        _project_assessment_rows(
            builder,
            report_id=report_id,
            report_node_id=report_node_ids[report_id],
            assessment=context["assessment"],
            framework=framework,
            scope_key=scope,
            group_key=group,
            standard_scope=standard_scope,
            metric_nodes=metric_nodes,
            scope_node_id=scope_node_id,
            resolved_scope_key=resolved_scope,
            include_evidence=include_evidence,
            evidence_limit=max(1, int(evidence_limit)),
            evidence_disclosure_ids=_evidence_disclosure_ids,
            report_infos=report_infos,
            artifact_indexes=artifact_indexes,
        )
        revision_reports.append(
            {
                "report_id": report_id,
                "scope_key": resolved_scope,
                "assessment": context["assessment"],
            }
        )

    response_framework = next(iter(frameworks)) if len(frameworks) == 1 else "MULTIPLE"
    response_scope = next(iter(scopes)) if len(scopes) == 1 else (
        _text(scope_key) or "multiple"
    )
    selected_identity = sorted(report_infos)
    graph_id = _stable_id(
        "graph",
        "company",
        company_id,
        response_scope,
        selected_identity,
    )
    graph_revision = _json_digest(
        {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "owner_type": "company",
            "owner_id": company_id,
            "scope_key": response_scope,
            "report_ids": selected_identity,
            "reports": revision_reports,
        },
        length=32,
    )
    return builder.response(
        graph_id=graph_id,
        graph_revision=graph_revision,
        owner=DisclosureGraphOwner(
            type="company",
            id=company_id,
            label=company_label,
        ),
        scope_key=response_scope or None,
        framework=response_framework,
    )


def _graph_neighbors(
    graph: DisclosureGraphResponse,
    *,
    node_id: str,
    depth: int,
) -> DisclosureGraphResponse:
    nodes_by_id = {node.id: node for node in graph.nodes}
    if node_id not in nodes_by_id:
        raise DisclosureGraphNotFound(f"Graph node not found: {node_id}")

    adjacency: Dict[str, set[str]] = {value: set() for value in nodes_by_id}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)

    reached = {node_id}
    queue = deque([(node_id, 0)])
    maximum_depth = max(1, int(depth))
    while queue:
        current, current_depth = queue.popleft()
        if current_depth >= maximum_depth:
            continue
        for neighbor in sorted(adjacency.get(current, set())):
            if neighbor in reached:
                continue
            reached.add(neighbor)
            queue.append((neighbor, current_depth + 1))

    nodes = sorted(
        (nodes_by_id[value] for value in reached),
        key=lambda item: item.id,
    )
    edges = sorted(
        (
            edge
            for edge in graph.edges
            if edge.source in reached and edge.target in reached
        ),
        key=lambda item: item.id,
    )
    node_types = Counter(item.type for item in nodes)
    edge_types = Counter(item.type for item in edges)
    disclosure_statuses = Counter(
        str(item.properties.get("status") or "not_disclosed")
        for item in nodes
        if item.type == "disclosure"
    )
    return DisclosureGraphResponse(
        schema_version=graph.schema_version,
        graph_id=graph.graph_id,
        graph_revision=graph.graph_revision,
        owner=graph.owner,
        scope_key=graph.scope_key,
        framework=graph.framework,
        nodes=nodes,
        edges=edges,
        stats=DisclosureGraphStats(
            node_count=len(nodes),
            edge_count=len(edges),
            node_types=dict(sorted(node_types.items())),
            edge_types=dict(sorted(edge_types.items())),
            disclosure_statuses=dict(sorted(disclosure_statuses.items())),
        ),
        truncated=graph.truncated,
    )


def _evidence_disclosures_for_neighborhood(
    graph: DisclosureGraphResponse,
    *,
    node_id: str,
    depth: int,
) -> set[str]:
    """Return only disclosures whose evidence can appear within the requested depth."""
    nodes_by_id = {node.id: node for node in graph.nodes}
    if node_id not in nodes_by_id:
        raise DisclosureGraphNotFound(f"Graph node not found: {node_id}")
    adjacency: Dict[str, set[str]] = {value: set() for value in nodes_by_id}
    for edge in graph.edges:
        adjacency.setdefault(edge.source, set()).add(edge.target)
        adjacency.setdefault(edge.target, set()).add(edge.source)

    maximum_disclosure_distance = max(0, int(depth) - 1)
    reached = {node_id}
    queue = deque([(node_id, 0)])
    disclosure_ids: set[str] = set()
    while queue:
        current, current_depth = queue.popleft()
        node = nodes_by_id[current]
        if node.type == "disclosure":
            disclosure_ids.add(current)
        if current_depth >= maximum_disclosure_distance:
            continue
        for neighbor in adjacency.get(current, set()):
            if neighbor in reached:
                continue
            reached.add(neighbor)
            queue.append((neighbor, current_depth + 1))
    return disclosure_ids


def build_report_graph_neighbors(
    *,
    file_id: str,
    user_id: int,
    node_id: str,
    scope_key: Optional[str] = None,
    depth: int = 2,
    evidence_limit: int = 8,
) -> DisclosureGraphResponse:
    base_graph = build_report_disclosure_graph(
        file_id=file_id,
        user_id=user_id,
        scope_key=scope_key,
    )
    disclosure_ids = _evidence_disclosures_for_neighborhood(
        base_graph,
        node_id=node_id,
        depth=depth,
    )
    graph = base_graph
    if disclosure_ids:
        graph = build_report_disclosure_graph(
            file_id=file_id,
            user_id=user_id,
            scope_key=scope_key,
            include_evidence=True,
            evidence_limit=evidence_limit,
            _evidence_disclosure_ids=disclosure_ids,
        )
    return _graph_neighbors(graph, node_id=node_id, depth=depth)


def build_company_graph_neighbors(
    *,
    company_id: str,
    user_id: int,
    node_id: str,
    scope_key: Optional[str] = None,
    depth: int = 2,
    evidence_limit: int = 8,
    selected_report_ids: Optional[Sequence[str]] = None,
) -> DisclosureGraphResponse:
    base_graph = build_company_disclosure_graph(
        company_id=company_id,
        user_id=user_id,
        scope_key=scope_key,
        selected_report_ids=selected_report_ids,
    )
    disclosure_ids = _evidence_disclosures_for_neighborhood(
        base_graph,
        node_id=node_id,
        depth=depth,
    )
    graph = base_graph
    if disclosure_ids:
        graph = build_company_disclosure_graph(
            company_id=company_id,
            user_id=user_id,
            scope_key=scope_key,
            include_evidence=True,
            evidence_limit=evidence_limit,
            selected_report_ids=selected_report_ids,
            _evidence_disclosure_ids=disclosure_ids,
        )
    return _graph_neighbors(graph, node_id=node_id, depth=depth)


__all__ = [
    "DisclosureGraphNotFound",
    "build_company_disclosure_graph",
    "build_company_graph_neighbors",
    "build_report_disclosure_graph",
    "build_report_graph_neighbors",
]
