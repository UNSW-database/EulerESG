"""Read-only catalog for the standards metric files bundled with the backend."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple


_DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"

_FRAMEWORK_METADATA: Tuple[Dict[str, Any], ...] = (
    {
        "id": "sasb",
        "name": "SASB",
        "as_of": "Jan 2026",
        "source_url": "https://www.ifrs.org/issued-standards/sasb-standards/",
        "group_label": "Industry",
        "scope_label": "Sub-industry",
    },
    {
        "id": "gri",
        "name": "GRI",
        "as_of": "Jun 2026",
        "source_url": "https://www.globalreporting.org/standards/gri-standards-download-center/",
        "group_label": "Sector",
        "scope_label": "Topic",
    },
    {
        "id": "cdp",
        "name": "CDP",
        "as_of": "Apr 2026",
        "source_url": "https://www.cdp.net/en/disclosure-2026",
        "group_label": "Topic groups",
        "scope_label": "Topic",
    },
    {
        "id": "aasb",
        "name": "AASB",
        "as_of": "Nov 2025",
        "source_url": "https://standards.aasb.gov.au/sustainability-reporting-standards",
        "group_label": "Standards",
        "scope_label": "Standard",
    },
)

_LABEL_OVERRIDES = {
    "all": "All disclosures",
    "agriculture_aquaculture_and_fishing_sectors": "Agriculture, Aquaculture & Fishing Sectors",
    "oil_and_gas_sector": "Oil & Gas Sector",
    "risk_and_impact": "Risk & Impact",
    "metrics_and_targets": "Metrics & Targets",
}

_CATALOG_CACHE_LOCK = Lock()


class StandardsLibraryError(RuntimeError):
    """Base error for a malformed or unavailable local standards catalog."""


class StandardsScopeNotFound(StandardsLibraryError):
    """Raised when a framework, group, or scope is not in the server catalog."""


class StandardsDataError(StandardsLibraryError):
    """Raised when a bundled standards file is malformed or inconsistent."""


def _root(data_root: Optional[Path] = None) -> Path:
    return Path(data_root or _DEFAULT_DATA_ROOT).resolve()


def _humanize(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    override = _LABEL_OVERRIDES.get(normalized.lower())
    if override:
        return override
    label = normalized.replace("_", " ").strip().title()
    return label.replace(" And ", " & ")


def _path_signature(path: Path) -> Tuple[bool, int, int, int]:
    """Return a cheap cache key that changes when a file or directory changes."""
    try:
        stat = path.stat()
    except OSError:
        return False, 0, 0, 0
    return True, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


@lru_cache(maxsize=512)
def _read_json_cached(
    resolved_path: str,
    signature: Tuple[bool, int, int, int],
) -> Any:
    del signature
    path = Path(resolved_path)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise StandardsDataError(f"Unable to read standards data: {path.name}") from exc


def _read_json(path: Path) -> Any:
    resolved = path.resolve()
    return _read_json_cached(str(resolved), _path_signature(resolved))


def _safe_json_file(directory: Path, filename: str) -> Path:
    name = str(filename or "").strip()
    relative = Path(name)
    if not name or relative.name != name or relative.suffix.lower() != ".json":
        raise StandardsDataError("Standards manifest contains an unsafe file reference")
    resolved_directory = directory.resolve()
    candidate = (resolved_directory / relative).resolve()
    if candidate.parent != resolved_directory:
        raise StandardsDataError("Standards file resolves outside its data directory")
    if not candidate.is_file():
        raise StandardsDataError(f"Standards file is missing: {relative.name}")
    return candidate


def _sasb_groups(data_root: Path) -> List[Dict[str, Any]]:
    directory = data_root / "sasb_metrics"
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        return []
    manifest = _read_json(manifest_path)
    mapping = manifest.get("semi_industry_to_file") if isinstance(manifest, dict) else None
    if not isinstance(mapping, dict):
        raise StandardsDataError("SASB manifest must contain semi_industry_to_file")

    scopes_by_id: Dict[str, Dict[str, str]] = {}
    for label, filename in mapping.items():
        normalized_label = str(label or "").strip()
        if not normalized_label or normalized_label != label:
            raise StandardsDataError("SASB manifest contains an invalid sub-industry label")
        if normalized_label in scopes_by_id:
            raise StandardsDataError("SASB manifest contains a duplicate sub-industry")
        _safe_json_file(directory, str(filename or ""))
        scopes_by_id[normalized_label] = {
            "id": normalized_label,
            "label": normalized_label,
        }

    # Older or test manifests predate the taxonomy. Keep their single-group
    # behavior while treating a present-but-invalid taxonomy as corrupted data.
    if "industry_groups" not in manifest:
        scopes = sorted(
            scopes_by_id.values(),
            key=lambda item: item["label"].casefold(),
        )
        return [{"id": "industries", "label": "Industries", "scopes": scopes}]

    taxonomy = manifest.get("industry_groups")
    if not isinstance(taxonomy, list) or not taxonomy:
        raise StandardsDataError("SASB industry_groups must be a non-empty array")

    groups: List[Dict[str, Any]] = []
    group_ids = set()
    assigned_scopes = set()
    for raw_group in taxonomy:
        if not isinstance(raw_group, dict):
            raise StandardsDataError("SASB industry_groups contains a non-object group")
        group_id = str(raw_group.get("id") or "").strip()
        group_label = str(raw_group.get("label") or "").strip()
        members = raw_group.get("sub_industries")
        if not re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", group_id):
            raise StandardsDataError("SASB industry group contains an invalid id")
        if not group_label:
            raise StandardsDataError("SASB industry group contains an empty label")
        if group_id in group_ids:
            raise StandardsDataError(f"Duplicate SASB industry group id: {group_id}")
        if not isinstance(members, list) or not members:
            raise StandardsDataError(
                f"SASB industry group must contain sub_industries: {group_id}"
            )
        group_ids.add(group_id)

        group_scopes: List[Dict[str, str]] = []
        for member in members:
            scope_id = str(member or "").strip()
            if not scope_id or scope_id != member or scope_id not in scopes_by_id:
                raise StandardsDataError(
                    f"Unknown SASB sub-industry in group {group_id}: {member}"
                )
            if scope_id in assigned_scopes:
                raise StandardsDataError(
                    f"Duplicate SASB sub-industry assignment: {scope_id}"
                )
            assigned_scopes.add(scope_id)
            group_scopes.append(scopes_by_id[scope_id])
        group_scopes.sort(key=lambda item: item["label"].casefold())
        groups.append({"id": group_id, "label": group_label, "scopes": group_scopes})

    orphan_scopes = [
        scope
        for scope_id, scope in scopes_by_id.items()
        if scope_id not in assigned_scopes
    ]
    if orphan_scopes:
        if "other" in group_ids:
            raise StandardsDataError(
                "SASB taxonomy cannot add orphan sub-industries to an existing other group"
            )
        orphan_scopes.sort(key=lambda item: item["label"].casefold())
        groups.append({"id": "other", "label": "Other", "scopes": orphan_scopes})
    return groups


def _split_gri_scope(stem: str) -> Optional[Tuple[str, str]]:
    for marker in ("_sectors_", "_sector_"):
        if marker not in stem:
            continue
        marker_end = stem.index(marker) + len(marker)
        sector = stem[:marker_end].rstrip("_")
        topic = stem[marker_end:].lstrip("_")
        if sector and topic:
            return sector, topic
    return None


def _gri_groups(data_root: Path) -> List[Dict[str, Any]]:
    directory = data_root / "gri_metrics"
    if not directory.is_dir():
        return []
    topics_by_sector: Dict[str, set[str]] = {}
    for path in directory.glob("*.json"):
        parsed = _split_gri_scope(path.stem)
        if parsed is None:
            continue
        sector, topic = parsed
        topics_by_sector.setdefault(sector, set()).add(topic)

    groups: List[Dict[str, Any]] = []
    for sector in sorted(topics_by_sector, key=lambda value: _humanize(value).casefold()):
        topics = sorted(
            topics_by_sector[sector],
            key=lambda value: (value.lower() != "all", _humanize(value).casefold()),
        )
        groups.append(
            {
                "id": sector,
                "label": _humanize(sector),
                "scopes": [{"id": topic, "label": _humanize(topic)} for topic in topics],
            }
        )
    return groups


def _topic_groups(data_root: Path, framework_id: str) -> List[Dict[str, Any]]:
    directory = data_root / f"{framework_id}_metrics"
    if not directory.is_dir():
        return []
    scopes = [
        {"id": path.stem, "label": _humanize(path.stem)}
        for path in directory.glob("*.json")
        if path.is_file()
    ]
    scopes.sort(key=lambda item: item["label"].casefold())
    return [{"id": "topics", "label": "Topics", "scopes": scopes}] if scopes else []


def _build_standards_catalog(root: Path) -> Dict[str, Any]:
    frameworks: List[Dict[str, Any]] = []
    for metadata in _FRAMEWORK_METADATA:
        framework_id = metadata["id"]
        if framework_id == "sasb":
            groups = _sasb_groups(root)
        elif framework_id == "gri":
            groups = _gri_groups(root)
        elif framework_id == "cdp":
            groups = _topic_groups(root, framework_id)
        else:
            groups = []
        scope_count = sum(len(group["scopes"]) for group in groups)
        frameworks.append(
            {
                **metadata,
                "available": scope_count > 0,
                "scope_count": scope_count,
                "groups": groups,
            }
        )
    return {"frameworks": frameworks}


def _catalog_signature(root: Path) -> Tuple[Tuple[bool, int, int, int], ...]:
    """Track only paths that can change catalog membership or SASB taxonomy."""
    return tuple(
        _path_signature(path)
        for path in (
            root / "sasb_metrics",
            root / "sasb_metrics" / "manifest.json",
            root / "gri_metrics",
            root / "cdp_metrics",
        )
    )


@lru_cache(maxsize=16)
def _get_standards_catalog_cached(
    resolved_root: str,
    signature: Tuple[Tuple[bool, int, int, int], ...],
) -> Dict[str, Any]:
    del signature
    return _build_standards_catalog(Path(resolved_root))


def _standards_catalog_snapshot(data_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _root(data_root)
    signature = _catalog_signature(root)
    # functools.lru_cache is thread-safe, but concurrent cold misses may still
    # execute the wrapped function more than once. Serializing the tiny lookup
    # prevents duplicate 77-file catalog scans during startup traffic.
    with _CATALOG_CACHE_LOCK:
        return _get_standards_catalog_cached(str(root), signature)


def get_standards_catalog(data_root: Optional[Path] = None) -> Dict[str, Any]:
    """Return framework metadata without exposing the shared cached snapshot."""
    return deepcopy(_standards_catalog_snapshot(data_root))


def _framework_from_catalog(catalog: Dict[str, Any], framework_id: str) -> Dict[str, Any]:
    normalized = str(framework_id or "").strip().lower()
    for framework in catalog.get("frameworks", []):
        if framework.get("id") == normalized:
            return framework
    raise StandardsScopeNotFound(f"Unknown standards framework: {framework_id}")


def _scope_from_framework(
    framework: Dict[str, Any],
    group_id: Optional[str],
    scope_id: str,
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    groups = framework.get("groups") or []
    if not framework.get("available") or not groups:
        raise StandardsScopeNotFound(
            f"Local metric data is not available for {framework.get('name', 'this framework')}"
        )
    normalized_group = str(group_id or "").strip()
    normalized_scope = str(scope_id or "").strip()
    if not normalized_group:
        if len(groups) == 1:
            normalized_group = str(groups[0].get("id") or "")
        elif framework.get("id") == "sasb":
            matching_groups = [
                item
                for item in groups
                if any(
                    scope.get("id") == normalized_scope
                    for scope in item.get("scopes", [])
                )
            ]
            if len(matching_groups) == 1:
                normalized_group = str(matching_groups[0].get("id") or "")
            elif len(matching_groups) > 1:
                raise StandardsDataError(
                    f"SASB sub-industry belongs to multiple groups: {scope_id}"
                )
            else:
                raise StandardsScopeNotFound(
                    f"Unknown SASB sub-industry: {scope_id}"
                )
    group = next((item for item in groups if item.get("id") == normalized_group), None)
    if group is None:
        raise StandardsScopeNotFound(f"Unknown standards group: {group_id}")
    scope = next(
        (item for item in group.get("scopes", []) if item.get("id") == normalized_scope),
        None,
    )
    if scope is None:
        raise StandardsScopeNotFound(f"Unknown standards scope: {scope_id}")
    return group, scope


def _metric_file(
    data_root: Path,
    framework_id: str,
    group_id: str,
    scope_id: str,
) -> Path:
    if framework_id == "sasb":
        directory = data_root / "sasb_metrics"
        manifest = _read_json(directory / "manifest.json")
        mapping = manifest.get("semi_industry_to_file") if isinstance(manifest, dict) else None
        if not isinstance(mapping, dict) or scope_id not in mapping:
            raise StandardsScopeNotFound(f"Unknown SASB sub-industry: {scope_id}")
        return _safe_json_file(directory, str(mapping[scope_id]))
    if framework_id == "gri":
        return _safe_json_file(data_root / "gri_metrics", f"{group_id}_{scope_id}.json")
    if framework_id == "cdp":
        return _safe_json_file(data_root / f"{framework_id}_metrics", f"{scope_id}.json")
    raise StandardsScopeNotFound(f"Local metric data is not available for {framework_id}")


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_metric(
    item: Dict[str, Any],
    framework_id: str,
    group_id: str,
    scope_id: str,
    index: int,
) -> Dict[str, Any]:
    name = _text(item.get("Metric") if "Metric" in item else item.get("metric"))
    if not name:
        name = f"Metric {index + 1}"
    return {
        "id": f"{framework_id}:{group_id}:{scope_id}:{index + 1}",
        "code": _text(item.get("Code") if "Code" in item else item.get("code")),
        "name": name,
        "topic": _text(item.get("Topic") if "Topic" in item else item.get("topic")),
        "category": _text(
            item.get("Category") if "Category" in item else item.get("category")
        ),
        "type": _text(item.get("Type") if "Type" in item else item.get("type")),
        "unit": _text(item.get("Unit") if "Unit" in item else item.get("unit")),
        "standard": _text(
            item.get("Standard") if "Standard" in item else item.get("standard")
        ),
        "definition": _text(
            item.get("definition") if "definition" in item else item.get("Definition")
        ),
        "simple_definition": _text(
            item.get("simple_definition")
            if "simple_definition" in item
            else item.get("Simple Definition")
        ),
    }


def get_standard_metrics(
    framework_id: str,
    scope_id: str,
    group_id: Optional[str] = None,
    data_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Return normalized metrics for a scope selected from the server catalog."""
    root = _root(data_root)
    catalog = _standards_catalog_snapshot(root)
    framework = _framework_from_catalog(catalog, framework_id)
    group, scope = _scope_from_framework(framework, group_id, scope_id)
    path = _metric_file(root, framework["id"], group["id"], scope["id"])
    raw_metrics = _read_json(path)
    if not isinstance(raw_metrics, list):
        raise StandardsDataError(f"Standards file must contain a JSON array: {path.name}")

    metrics: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_metrics):
        if not isinstance(item, dict):
            raise StandardsDataError(f"Standards file contains a non-object row: {path.name}")
        metrics.append(
            _normalize_metric(item, framework["id"], group["id"], scope["id"], index)
        )
    return {
        "framework": {
            key: framework[key]
            for key in ("id", "name", "as_of", "source_url", "group_label", "scope_label")
        },
        "group": {"id": group["id"], "label": group["label"]},
        "scope": {"id": scope["id"], "label": scope["label"]},
        "total_metrics": len(metrics),
        "metrics": metrics,
    }
