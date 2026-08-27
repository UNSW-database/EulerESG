"""Persistent company and multi-report batch metadata.

The file metadata store predates company-level analysis. Keeping company state
in a separate atomic JSON document avoids changing the legacy file layout while
providing stable ownership, report grouping, and aggregate-result versioning.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .file_manager import file_manager


class CompanyRegistryError(ValueError):
    """Raised when a company or batch invariant is violated."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_name(value: str) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def build_scope_config(
    *,
    framework: Optional[str],
    industry: Optional[str],
    semi_industry: Optional[str],
    gri_sector: Optional[str],
    gri_topic: Optional[str],
    scope_slugs: Iterable[str],
) -> Dict[str, Any]:
    """Return the canonical company-level framework/scope identity."""
    scopes = sorted({str(item).strip() for item in scope_slugs if str(item).strip()})
    return {
        "framework": str(framework or "").strip().upper(),
        "industry": str(industry or "").strip(),
        "semi_industry": str(semi_industry or "").strip(),
        "gri_sector": str(gri_sector or "").strip(),
        "gri_topic": str(gri_topic or "").strip(),
        "scope_slugs": scopes,
    }


def scope_signature(scope_config: Dict[str, Any]) -> str:
    return json.dumps(scope_config or {}, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class CompanyRegistry:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or (Path(file_manager.base_dir) / "company_metadata.json"))
        self._lock = threading.RLock()
        self._data = self._load()

    def _empty(self) -> Dict[str, Any]:
        return {"schema_version": 1, "companies": {}, "batches": {}}

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return self._empty()
            payload.setdefault("schema_version", 1)
            payload.setdefault("companies", {})
            payload.setdefault("batches", {})
            return payload
        except Exception:
            return self._empty()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        encoded = json.dumps(self._data, ensure_ascii=False, indent=2).encode("utf-8")
        with open(temp_path, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, self.path)

    @staticmethod
    def _owned(record: Dict[str, Any], user_id: int) -> bool:
        try:
            return int(record.get("user_id")) == int(user_id)
        except (TypeError, ValueError):
            return False

    def list_companies(self, user_id: int) -> List[Dict[str, Any]]:
        with self._lock:
            companies = [
                dict(company)
                for company in self._data["companies"].values()
                if self._owned(company, user_id)
            ]
        companies.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return companies

    def get_company(self, company_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict):
                return None
            if user_id is not None and not self._owned(company, user_id):
                return None
            return dict(company)

    def create_company(
        self,
        *,
        user_id: int,
        company_name: str,
        scope_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        display_name = " ".join(str(company_name or "").strip().split())
        if not display_name:
            raise CompanyRegistryError("Company name is required")
        normalized = _normalized_name(display_name)
        now = _now()
        with self._lock:
            for existing in self._data["companies"].values():
                if self._owned(existing, user_id) and existing.get("normalized_name") == normalized:
                    raise CompanyRegistryError(
                        "A company with this name already exists; select the existing company"
                    )
            company_id = f"company_{uuid.uuid4().hex}"
            company = {
                "company_id": company_id,
                "user_id": int(user_id),
                "company_name": display_name,
                "normalized_name": normalized,
                "scope_config": dict(scope_config),
                "scope_signature": scope_signature(scope_config),
                "report_ids": [],
                "batch_ids": [],
                "status": "empty",
                "analysis_version": 0,
                "active_analysis_id": None,
                "assessment_outputs": [],
                "stale": False,
                "created_at": now,
                "updated_at": now,
            }
            self._data["companies"][company_id] = company
            self._save_locked()
            return dict(company)

    def validate_upload(
        self,
        *,
        company_id: str,
        user_id: int,
        scope_config: Dict[str, Any],
        file_hashes: Iterable[str],
        max_reports: int = 8,
    ) -> Dict[str, Any]:
        hashes = [str(value or "").strip().lower() for value in file_hashes]
        if len(hashes) != len(set(hashes)):
            raise CompanyRegistryError("The upload contains duplicate PDF files")
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict) or not self._owned(company, user_id):
                raise CompanyRegistryError("Company not found")
            if company.get("scope_signature") != scope_signature(scope_config):
                raise CompanyRegistryError(
                    "All reports for one company must use the same framework and metric scope"
                )
            report_ids = list(company.get("report_ids") or [])
            if len(report_ids) + len(hashes) > max(1, int(max_reports)):
                raise CompanyRegistryError(
                    f"A company can contain at most {max_reports} reports"
                )
            existing_hashes = {
                str((file_manager.metadata.get("files", {}).get(file_id) or {}).get("file_hash") or "")
                .strip()
                .lower()
                for file_id in report_ids
            }
            duplicate = sorted(set(hashes).intersection(existing_hashes))
            if duplicate:
                raise CompanyRegistryError("This company already contains one of the uploaded PDF files")
            return dict(company)

    def create_batch(
        self,
        *,
        company_id: str,
        user_id: int,
        upload_mode: str,
        file_ids: List[str],
        report_years: Dict[str, Optional[int]],
        max_reports: int = 8,
    ) -> Dict[str, Any]:
        now = _now()
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict) or not self._owned(company, user_id):
                raise CompanyRegistryError("Company not found")
            incoming_ids = list(file_ids)
            if len(incoming_ids) != len(set(incoming_ids)):
                raise CompanyRegistryError("The batch contains duplicate report IDs")
            existing_ids = list(company.get("report_ids") or [])
            if set(incoming_ids).intersection(existing_ids):
                raise CompanyRegistryError("This company already contains one of the reports")
            if len(existing_ids) + len(incoming_ids) > max(1, int(max_reports)):
                raise CompanyRegistryError(
                    f"A company can contain at most {max_reports} reports"
                )
            existing_hashes = {
                str((file_manager.metadata.get("files", {}).get(file_id) or {}).get("file_hash") or "")
                .strip()
                .lower()
                for file_id in existing_ids
            }
            existing_hashes.discard("")
            incoming_hashes = [
                str((file_manager.metadata.get("files", {}).get(file_id) or {}).get("file_hash") or "")
                .strip()
                .lower()
                for file_id in incoming_ids
            ]
            nonempty_incoming_hashes = [value for value in incoming_hashes if value]
            if len(nonempty_incoming_hashes) != len(set(nonempty_incoming_hashes)):
                raise CompanyRegistryError("The batch contains duplicate PDF files")
            if existing_hashes.intersection(nonempty_incoming_hashes):
                raise CompanyRegistryError(
                    "This company already contains one of the uploaded PDF files"
                )
            batch_id = f"batch_{uuid.uuid4().hex}"
            batch = {
                "batch_id": batch_id,
                "company_id": company_id,
                "user_id": int(user_id),
                "upload_mode": upload_mode,
                "file_ids": list(file_ids),
                "report_years": dict(report_years),
                "processed_file_ids": [],
                "failed_file_ids": [],
                "job_id": None,
                "status": "queued",
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            self._data["batches"][batch_id] = batch
            company["report_ids"] = list(dict.fromkeys([*(company.get("report_ids") or []), *file_ids]))
            company["batch_ids"] = list(dict.fromkeys([*(company.get("batch_ids") or []), batch_id]))
            company["status"] = "processing"
            company["stale"] = bool(company.get("active_analysis_id"))
            company["updated_at"] = now
            self._save_locked()
            return dict(batch)

    def remove_empty_company(self, company_id: str, user_id: int) -> bool:
        """Remove a company created by an upload that failed before batching."""
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict) or not self._owned(company, user_id):
                return False
            if company.get("report_ids") or company.get("batch_ids"):
                return False
            del self._data["companies"][str(company_id)]
            self._save_locked()
            return True

    def get_batch(self, batch_id: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            batch = self._data["batches"].get(str(batch_id))
            if not isinstance(batch, dict):
                return None
            if user_id is not None and not self._owned(batch, user_id):
                return None
            return dict(batch)

    def update_batch(self, batch_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        with self._lock:
            batch = self._data["batches"].get(str(batch_id))
            if not isinstance(batch, dict):
                return None
            batch.update({key: value for key, value in updates.items() if value is not None})
            batch["updated_at"] = _now()
            self._save_locked()
            return dict(batch)

    def mark_analysis_started(self, company_id: str) -> None:
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict):
                return
            company["status"] = "analyzing"
            company["stale"] = bool(company.get("active_analysis_id"))
            company["updated_at"] = _now()
            self._save_locked()

    def mark_analysis_complete(
        self,
        company_id: str,
        *,
        assessment_outputs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict):
                raise CompanyRegistryError("Company not found")
            version = int(company.get("analysis_version") or 0) + 1
            company["analysis_version"] = version
            company["active_analysis_id"] = f"{company_id}:v{version}"
            company["assessment_outputs"] = list(assessment_outputs)
            company["status"] = "ready"
            company["stale"] = False
            company["updated_at"] = _now()
            self._save_locked()
            return dict(company)

    def mark_analysis_failed(self, company_id: str, error: Optional[str] = None) -> None:
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict):
                return
            company["status"] = "failed"
            company["stale"] = bool(company.get("active_analysis_id"))
            company["last_error"] = str(error or "")[:1000] or None
            company["updated_at"] = _now()
            self._save_locked()

    def remove_report(self, company_id: str, file_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            company = self._data["companies"].get(str(company_id))
            if not isinstance(company, dict) or not self._owned(company, user_id):
                return None
            company["report_ids"] = [
                value for value in (company.get("report_ids") or []) if value != file_id
            ]
            company["stale"] = bool(company.get("active_analysis_id"))
            company["status"] = "stale" if company["report_ids"] else "empty"
            company["updated_at"] = _now()
            self._save_locked()
            return dict(company)


company_registry = CompanyRegistry()


__all__ = [
    "CompanyRegistry",
    "CompanyRegistryError",
    "build_scope_config",
    "company_registry",
    "scope_signature",
]
