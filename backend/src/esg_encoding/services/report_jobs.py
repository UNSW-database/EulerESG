"""In-memory report processing jobs and SSE event state.

This module intentionally keeps the implementation lightweight. It is used by
the FastAPI backend process to track long-running report uploads that continue
after the initial HTTP upload request returns.
"""

from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional


TERMINAL_STATUSES = {"success", "failed", "partial_success", "cancelled"}

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.RLock()
_max_events = int(os.getenv("REPORT_JOB_MAX_EVENTS", "500") or "500")
_executor: Optional[ThreadPoolExecutor] = None


def _now() -> str:
    return datetime.now().isoformat()


def get_executor() -> ThreadPoolExecutor:
    """Return a shared background executor for report processing.

    The default is one worker because the backend uses shared global components
    such as current_report/current_assessment and GPU embedding/reranker caches.
    Increase REPORT_BACKGROUND_WORKERS only after those globals are made per-job.
    """
    global _executor
    if _executor is None:
        max_workers = max(1, int(os.getenv("REPORT_BACKGROUND_WORKERS", "1") or "1"))
        _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="report-job")
    return _executor


def create_report_job(
    *,
    file_id: str,
    filename: str,
    user_id: Optional[int] = None,
    file_ids: Optional[List[str]] = None,
    company_id: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    job_id = f"report_{uuid.uuid4().hex}"
    now = _now()
    job = {
        "job_id": job_id,
        "file_id": file_id,
        "file_ids": list(file_ids or ([file_id] if file_id else [])),
        "filename": filename,
        "company_id": company_id,
        "batch_id": batch_id,
        "user_id": user_id,
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "message": "Report processing is queued.",
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "result": None,
        # `details` and `paddle_progress` store internal progress payloads.
        # They are redacted from public API/SSE responses by default.
        "details": {},
        "paddle_progress": None,
        "seq": 0,
        "events": [],
    }
    with _lock:
        _jobs[job_id] = job
        _append_event_locked(job, event_type="progress")
    return snapshot_report_job(job_id) or job


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


_STAGE_MESSAGES = {
    "queued": "Waiting to start.",
    "started": "Starting document processing.",
    "saving": "Uploading document.",
    "file_saved": "Document uploaded.",
    "pdf_processing": "Reading document content.",
    "ocr_start": "Reading document content.",
    "ocr_queued": "Reading document content.",
    "ocr_batch_processing": "Reading document content.",
    "ocr_merging": "Organizing extracted content.",
    "pdf_processed": "Document content extracted.",
    "summary_ready": "Preparing summary.",
    "artifacts_loading": "Loading saved report evidence.",
    "artifacts_loaded": "Saved report evidence loaded.",
    "assessment_start": "Starting disclosure assessment.",
    "assessment_scope": "Analyzing disclosure information.",
    "assessment_scope_done": "Disclosure assessment updated.",
    "assessment_committing": "Saving disclosure assessment.",
    "completed": "Processing completed.",
    "failed": "Processing failed. Please try again.",
    "partial_success": "Processing completed with warnings.",
}


def _public_message(job: Dict[str, Any]) -> str:
    status = str(job.get("status") or "").strip().lower()
    stage = str(job.get("stage") or "").strip().lower()
    if status == "failed" or stage == "failed":
        return "Processing failed. Please try again."
    if status in {"success", "partial_success"}:
        return "Processing completed." if status == "success" else "Processing completed with warnings."
    return _STAGE_MESSAGES.get(stage) or "Processing document."


def _public_error(job: Dict[str, Any]) -> Optional[str]:
    status = str(job.get("status") or "").strip().lower()
    if status == "failed":
        return "Processing failed. Please try again."
    return None


def _public_snapshot(job: Dict[str, Any]) -> Dict[str, Any]:
    """Return a user-facing job snapshot for API/SSE clients.

    Internal logs, file-system paths, worker IDs, Redis keys, PaddleOCR batch
    details, and raw exception strings are intentionally not exposed to the
    frontend. They remain available only in backend / worker container logs.
    Set REPORT_JOB_EXPOSE_INTERNAL_DETAILS=true for local debugging if needed.
    """
    expose_internal = _env_bool("REPORT_JOB_EXPOSE_INTERNAL_DETAILS", False)
    payload = {
        "job_id": job.get("job_id"),
        "file_id": job.get("file_id"),
        "file_ids": list(job.get("file_ids") or []),
        "filename": job.get("filename"),
        "company_id": job.get("company_id"),
        "batch_id": job.get("batch_id"),
        "status": job.get("status"),
        "stage": job.get("stage"),
        "progress": job.get("progress"),
        "message": _public_message(job),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "error": _public_error(job),
        "result": job.get("result") if job.get("status") in {"success", "partial_success"} else None,
        "seq": job.get("seq", 0),
    }
    if expose_internal:
        payload["internal_message"] = job.get("message")
        payload["internal_error"] = job.get("error")
        payload["details"] = job.get("details") or {}
        payload["paddle_progress"] = job.get("paddle_progress")
    return payload

def _append_event_locked(job: Dict[str, Any], *, event_type: str = "progress") -> Dict[str, Any]:
    job["seq"] = int(job.get("seq", 0)) + 1
    event = _public_snapshot(job)
    event["event"] = event_type
    event["seq"] = job["seq"]
    events: List[Dict[str, Any]] = job.setdefault("events", [])
    events.append(event)
    if len(events) > _max_events:
        del events[:-_max_events]
    return event


def update_report_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    progress: Optional[float] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    result: Optional[dict] = None,
    extra: Optional[dict] = None,
    event_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if status is not None:
            job["status"] = status
            if status == "processing" and not job.get("started_at"):
                job["started_at"] = _now()
            if status in TERMINAL_STATUSES:
                job["completed_at"] = _now()
        if stage is not None:
            job["stage"] = stage
        if progress is not None:
            try:
                job["progress"] = max(0, min(100, round(float(progress), 1)))
            except Exception:
                pass
        if message is not None:
            job["message"] = message
        if error is not None:
            job["error"] = error
        if result is not None:
            job["result"] = result
        if extra:
            # Keep a structured internal copy of extra fields for backend diagnostics.
            safe_extra = {k: v for k, v in extra.items() if k not in {"events", "user_id"}}
            job["details"] = safe_extra
            # If the extractor passes an explicit `paddle_progress` object, keep it internally.
            # Otherwise synthesize one from common OCR page-batch fields for diagnostics.
            if isinstance(safe_extra.get("paddle_progress"), dict):
                job["paddle_progress"] = safe_extra.get("paddle_progress")
            elif any(k in safe_extra for k in {"paddle_job_id", "total_pages", "total_units", "units_done", "running_batches"}):
                job["paddle_progress"] = {
                    k: v
                    for k, v in safe_extra.items()
                    if k in {
                        "paddle_job_id",
                        "total_pages",
                        "pages_done",
                        "pages_success",
                        "pages_failed",
                        "total_units",
                        "units_done",
                        "units_success",
                        "units_failed",
                        "units_running",
                        "units_queued",
                        "page_batch_size",
                        "running_batches",
                        "last_finished_unit",
                    }
                }
            for key, value in safe_extra.items():
                # Preserve backwards-compatible flat fields for code that already
                # reads them directly from the job snapshot.
                job[key] = value
        job["updated_at"] = _now()
        ev_type = event_type or ("done" if job.get("status") in {"success", "partial_success"} else "error" if job.get("status") == "failed" else "progress")
        _append_event_locked(job, event_type=ev_type)
        return _public_snapshot(job)


def snapshot_report_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(_public_snapshot(job)) if job else None


def get_report_job_owner(job_id: str) -> Optional[int]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return job.get("user_id")


def get_report_job_events_since(job_id: str, seq: int) -> List[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return []
        return [dict(ev) for ev in job.get("events", []) if int(ev.get("seq", 0)) > seq]
