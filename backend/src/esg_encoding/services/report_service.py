"""Report upload and retrieval service functions."""

from .common import *  # noqa: F401,F403
from fastapi import Header, Query
from fastapi.responses import StreamingResponse
import shutil
import tempfile
import time

import numpy as np

from ..auth.service import get_user_id_from_authorization
from ..gpu_model_lifecycle import with_backend_model_task
from ..retrieval.hipporag.hooks import warm_hipporag_after_upload
from .report_jobs import (
    TERMINAL_STATUSES,
    create_report_job,
    get_executor as get_report_job_executor,
    get_report_job_events_since,
    get_report_job_owner,
    snapshot_report_job,
    update_report_job,
)


_report_reanalysis_lock = threading.RLock()
_progress_metadata_flush_lock = threading.RLock()
_progress_metadata_last_flush: Dict[str, float] = {}


def _runtime_resource_snapshot() -> dict:
    snapshot: dict = {}
    try:
        import resource
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        snapshot["peak_rss_mb"] = round(rss / (1024 if os.name != "darwin" else 1024 * 1024), 2)
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            snapshot["gpu_allocated_mb"] = round(torch.cuda.memory_allocated() / 1048576, 2)
            snapshot["gpu_peak_allocated_mb"] = round(torch.cuda.max_memory_allocated() / 1048576, 2)
    except Exception:
        pass
    return snapshot



def _emit_upload_progress(progress_cb, stage: str, message: str, progress: Optional[float] = None, **extra) -> None:
    """Send upload/report processing progress to the active SSE job."""
    if not progress_cb:
        return
    try:
        progress_cb(stage=stage, message=message, progress=progress, extra=extra or None)
    except Exception as exc:
        logger.debug(f"Upload progress callback skipped: {exc}")


def _file_metadata_job_matches(
    file_id: str,
    expected_job_id: Optional[str],
    *,
    job_id_field: str = "processing_job_id",
) -> bool:
    """Read the current ownership token at a durable-output boundary."""
    if expected_job_id is None:
        return True
    try:
        with file_manager._metadata_lock:
            finfo = file_manager.metadata.get("files", {}).get(file_id)
            return bool(
                isinstance(finfo, dict)
                and str(finfo.get(job_id_field) or "") == str(expected_job_id)
            )
    except Exception as exc:
        logger.debug(f"File metadata job check skipped for {file_id}: {exc}")
        return False


def _patch_file_metadata(
    file_id: str,
    *,
    expected_job_id: Optional[str] = None,
    job_id_field: str = "processing_job_id",
    **updates,
) -> bool:
    """Atomically patch metadata only when the owning job token is current."""
    persisted = False
    try:
        # Keep the same lock order as the debounced progress helper so durable
        # metadata and its debounce bookkeeping form one transaction.
        with _progress_metadata_flush_lock:
            with file_manager._metadata_lock:
                finfo = file_manager.metadata.get("files", {}).get(file_id)
                if not isinstance(finfo, dict):
                    return False
                if (
                    expected_job_id is not None
                    and str(finfo.get(job_id_field) or "") != str(expected_job_id)
                ):
                    return False
                # ``None`` is meaningful for terminal transitions: it clears a
                # stale job id or error left by an earlier/retried run.
                finfo.update(updates)
                file_manager._save_metadata()
                persisted = True
            if persisted:
                _progress_metadata_last_flush.pop(file_id, None)
    except Exception as exc:
        logger.debug(f"File metadata patch skipped for {file_id}: {exc}")
    return persisted


def _finalize_report_file_metadata(
    file_id: str,
    *,
    destination_status: str,
    expected_job_id: Optional[str] = None,
    job_id_field: str = "processing_job_id",
    **updates,
) -> bool:
    """Move and finalize a report without a stale job crossing the token check."""
    finalized = False
    try:
        with _progress_metadata_flush_lock:
            with file_manager._metadata_lock:
                finfo = file_manager.metadata.get("files", {}).get(file_id)
                if not isinstance(finfo, dict):
                    return False
                if (
                    expected_job_id is not None
                    and str(finfo.get(job_id_field) or "") != str(expected_job_id)
                ):
                    return False
                # FileManager uses an RLock, so keeping this outer lock fences
                # both its path/status mutation and our terminal fields.
                if not file_manager.move_report_file(file_id, destination_status):
                    return False
                finfo = file_manager.metadata.get("files", {}).get(file_id)
                if not isinstance(finfo, dict):
                    return False
                finfo.update(updates)
                file_manager._save_metadata()
                finalized = True
            _progress_metadata_last_flush.pop(file_id, None)
    except Exception as exc:
        logger.debug(f"Report finalization skipped for {file_id}: {exc}")
    return finalized


def _progress_metadata_flush_seconds() -> float:
    """Return the durable progress-write interval (bounded for operability)."""
    try:
        return min(
            60.0,
            max(
                1.0,
                float(os.getenv("REPORT_PROGRESS_METADATA_FLUSH_SECONDS", "12") or "12"),
            ),
        )
    except (TypeError, ValueError):
        return 12.0


def _patch_progress_file_metadata(
    file_id: str,
    *,
    force: bool = False,
    expected_job_id: Optional[str] = None,
    job_id_field: str = "processing_job_id",
    **updates,
) -> None:
    """Update progress in memory and debounce expensive full metadata writes.

    Dashboard reads in this process still observe every progress event. The
    complete JSON document is persisted at most once per configured interval;
    callers force terminal transitions so completion/failure is never delayed.
    """
    if not file_id:
        return
    now = time.monotonic()
    try:
        with _progress_metadata_flush_lock:
            last_flush = _progress_metadata_last_flush.get(file_id)
            should_flush = (
                force
                or last_flush is None
                or now - last_flush >= _progress_metadata_flush_seconds()
            )
            with file_manager._metadata_lock:
                finfo = file_manager.metadata.get("files", {}).get(file_id)
                if not isinstance(finfo, dict):
                    return
                # A previous job may still emit a delayed callback after a retry
                # has installed a new job token (or after a terminal transition
                # cleared it). Never let that stale event overwrite current state.
                if (
                    expected_job_id is not None
                    and str(finfo.get(job_id_field) or "") != str(expected_job_id)
                ):
                    return
                finfo.update(updates)
                if should_flush:
                    file_manager._save_metadata()
                    _progress_metadata_last_flush[file_id] = now
            if force:
                _progress_metadata_last_flush.pop(file_id, None)
    except Exception as exc:
        logger.debug(f"Progress metadata patch skipped for {file_id}: {exc}")


def _ocr_progress_metadata_updates(extra: Optional[dict]) -> dict:
    """Extract internal OCR progress fields for backend-side diagnostics only."""
    if not isinstance(extra, dict):
        return {}

    progress = extra.get("paddle_progress")
    if not isinstance(progress, dict):
        progress = extra

    field_map = {
        "total_pages": "processing_total_pages",
        "pages_done": "processing_pages_done",
        "pages_success": "processing_pages_success",
        "pages_failed": "processing_pages_failed",
        "total_units": "processing_total_units",
        "units_done": "processing_units_done",
        "units_success": "processing_units_success",
        "units_failed": "processing_units_failed",
        "units_running": "processing_units_running",
        "units_queued": "processing_units_queued",
        "page_batch_size": "processing_page_batch_size",
        "running_batches": "processing_running_batches",
    }
    return {
        target_key: progress.get(source_key)
        for source_key, target_key in field_map.items()
        if progress.get(source_key) is not None
    }


def _report_progress_metadata_updates(
    *,
    stage: str,
    message: str,
    progress: Optional[float],
    job_id: str,
    extra: Optional[dict],
) -> dict:
    """Map a progress event to durable dashboard state.

    The in-memory job remains ``processing`` until the final event containing
    the result is emitted.  File metadata, however, must not be changed back to
    ``processing`` after the processing body has reached a terminal stage.
    """
    normalized_stage = str(stage or "").strip().lower()
    terminal_status = {
        "completed": "processed",
        "partial_success": "processed",
        "failed": "failed",
    }.get(normalized_stage)
    is_terminal = terminal_status is not None
    updates = {
        "status": terminal_status or "processing",
        "processing_job_id": None if is_terminal else job_id,
        "processing_stage": normalized_stage or stage,
    }
    if progress is not None or is_terminal:
        updates["processing_progress"] = 100 if progress is None else progress
    if is_terminal:
        raw_error = extra.get("error") if isinstance(extra, dict) else None
        updates["processing_error"] = (
            None
            if normalized_stage == "completed"
            else str(raw_error or message or "Processing completed with warnings.")[:1000]
        )
    updates.update(_ocr_progress_metadata_updates(extra))
    return updates


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _report_artifact_conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=409, detail=detail)


def _load_validated_report_artifacts(file_id: str) -> dict:
    """Load the immutable report retrieval corpus and validate its row mapping.

    Assessment-only reanalysis must never reconstruct missing embeddings.  A
    missing/corrupt artifact, or any ambiguity between segment rows and the
    persisted NumPy matrix, is therefore a conflict that requires a full
    reprocess instead of an implicit OCR/embedding fallback.
    """
    artifacts = file_manager.load_report_artifacts(
        file_id,
        include_metric_corpus=True,
    )
    if not artifacts:
        raise _report_artifact_conflict(
            "Persisted report segments and embeddings are unavailable; reprocess the report first."
        )

    segments = list(artifacts.get("segments") or [])
    matrix = artifacts.get("embedding_matrix")
    embedding_segment_ids = [
        str(value) for value in (artifacts.get("embedding_segment_ids") or [])
    ]
    segment_ids = [str(getattr(segment, "segment_id", "") or "") for segment in segments]

    valid_matrix = (
        isinstance(matrix, np.ndarray)
        and matrix.ndim == 2
        and matrix.shape[0] > 0
        and matrix.shape[1] > 0
    )
    row_mapping_matches = (
        bool(segments)
        and valid_matrix
        and matrix.shape[0] == len(segments)
        and len(embedding_segment_ids) == len(segments)
        and embedding_segment_ids == segment_ids
        and all(segment_ids)
        and len(set(segment_ids)) == len(segment_ids)
    )
    if not row_mapping_matches:
        raise _report_artifact_conflict(
            "Persisted report artifacts are inconsistent; segment and embedding rows must match exactly."
        )
    if not np.issubdtype(matrix.dtype, np.number):
        raise _report_artifact_conflict(
            "Persisted report embeddings have an invalid numeric format."
        )
    return artifacts


def _report_content_from_artifacts(file_info: dict, artifacts: dict) -> ReportContent:
    file_id = str(file_info.get("file_id") or "")
    segments = list(artifacts["segments"])
    document_content = DocumentContent(
        document_id=file_id,
        file_path=str(file_info.get("file_path") or ""),
        segments=segments,
        content_revision=max(1, int(artifacts.get("content_revision", 1) or 1)),
        markdown_content="\n\n".join(
            str(getattr(segment, "content", "") or "") for segment in segments
        ),
    )
    report_content = ReportContent(
        document_id=file_id,
        document_content=document_content,
        # Keep the legacy Python-list representation empty.  Retrieval reads
        # the validated native matrix attached below and must not regenerate it.
        embeddings=[],
    )
    object.__setattr__(report_content, "_embedding_matrix", artifacts["embedding_matrix"])
    object.__setattr__(
        report_content,
        "_embedding_segment_ids",
        list(artifacts["embedding_segment_ids"]),
    )
    metric_corpus = artifacts.get("metric_retrieval_corpus")
    if metric_corpus is not None:
        object.__setattr__(
            report_content,
            "_metric_retrieval_corpus",
            metric_corpus,
        )
    return report_content


def _reanalysis_scopes(file_info: dict) -> tuple[str, List[tuple[str, dict]]]:
    fw = str(file_info.get("framework") or "").strip().upper()
    raw_scopes = file_info.get("scope_slugs_json")
    if isinstance(raw_scopes, list):
        raw_scopes = json.dumps(raw_scopes, ensure_ascii=False)

    scopes: List[tuple[str, dict]] = []
    if fw == "GRI":
        sector = str(file_info.get("gri_sector") or "").strip()
        topics = _parse_scope_slugs_json(raw_scopes, file_info.get("gri_topic"))
        if not sector or not topics:
            raise _report_artifact_conflict(
                "The report metadata does not contain the GRI sector and topic required for reanalysis."
            )
        scopes = [(topic, {"griSector": sector, "griTopic": topic}) for topic in topics]
    elif fw == "SASB":
        semis = _parse_scope_slugs_json(raw_scopes, file_info.get("semi_industry"))
        if not semis:
            raise _report_artifact_conflict(
                "The report metadata does not contain a SASB sub-industry required for reanalysis."
            )
        scopes = [(semi, {"semiIndustry": semi}) for semi in semis]
    elif fw in {"CDP", "TCFD"}:
        topics = _parse_scope_slugs_json(raw_scopes, file_info.get("semi_industry"))
        if not topics:
            raise _report_artifact_conflict(
                f"The report metadata does not contain a {fw} topic required for reanalysis."
            )
        scopes = [(topic, {"semiIndustry": topic}) for topic in topics]
    else:
        raise _report_artifact_conflict(
            "The report metadata does not contain a supported assessment framework."
        )
    return fw, scopes


def _load_reanalysis_scope_metrics(
    processor,
    fw: str,
    scope_key: str,
    params: dict,
):
    if fw == "GRI":
        metrics = processor.load_gri_metrics_by_sector_topic(
            params["griSector"], params["griTopic"]
        )
        semi_for_disclosure = (
            f"GRI {params['griSector']} {params['griTopic']}".strip()
        )
        filename_part = _sanitize_compliance_filename_part(
            f"GRI_{params['griSector']}_{params['griTopic']}"
        )
    elif fw == "SASB":
        metrics = processor.load_sasb_metrics_by_industry(params["semiIndustry"])
        semi_for_disclosure = params["semiIndustry"]
        filename_part = _sanitize_compliance_filename_part(params["semiIndustry"])
    elif fw == "CDP":
        metrics = processor.load_cdp_metrics_by_topic(params["semiIndustry"])
        semi_for_disclosure = params["semiIndustry"] or "CDP"
        filename_part = _sanitize_compliance_filename_part(
            f"CDP_{params['semiIndustry']}"
        )
    else:
        metrics = processor.load_tcfd_metrics_by_topic(params["semiIndustry"])
        semi_for_disclosure = params["semiIndustry"] or "TCFD"
        filename_part = _sanitize_compliance_filename_part(
            f"TCFD_{params['semiIndustry']}"
        )
    return metrics, semi_for_disclosure, filename_part


def _assessment_excel_frame(assessment_json: dict) -> pd.DataFrame:
    df_flat = pd.json_normalize(
        assessment_json,
        record_path="metric_analyses",
        meta=[
            "report_id",
            "assessment_date",
            "filename",
            "total_metrics",
            "overall_score",
            ["disclosure_summary", "fully_disclosed"],
            ["disclosure_summary", "partially_disclosed"],
            ["disclosure_summary", "not_disclosed"],
        ],
    )

    def pick_series(*names, default=""):
        for name in names:
            if name in df_flat.columns:
                return df_flat[name]
        return pd.Series([default] * len(df_flat))

    return pd.DataFrame(
        {
            "Metric": pick_series("Metric", "metric_name"),
            "Category": pick_series("Category", "category"),
            "Unit": pick_series("Unit", "unit"),
            "Code": pick_series("Code", "metric_code", "metric_id"),
            "Topic": pick_series("Topic", "topic"),
            "Type": pick_series("Type", "type"),
            "Definition": pick_series("Definition", "definition"),
            "Value": pick_series("Value", "value"),
            "Page": pick_series("Page", "page"),
            "Context": pick_series("Context", "context"),
            "Disclosure Status": pick_series(
                "Disclosure Status", "disclosure_status", "Model Disclosure Status"
            ),
            "LLM Analysis": pick_series("LLM Analysis", "reasoning"),
            "ChatGPT": pick_series("ChatGPT"),
            "InputWrong": pick_series("InputWrong"),
            "comment": pick_series("comment"),
        }
    )


def _stage_reanalysis_scope_outputs(
    *,
    assessment,
    disclosure_engine,
    file_info: dict,
    fw: str,
    scope_key: str,
    filename_part: str,
    llm_model_name: Optional[str],
    compliance_stage_dir: Path,
    markdown_stage_dir: Path,
) -> tuple[dict, List[tuple[Path, Path]], str]:
    file_id = str(file_info["file_id"])
    compliance_dir = Path(file_manager.compliance_outputs)
    markdown_dir = Path(file_manager.markdown_outputs)

    md_stem = _sanitize_compliance_filename_part(filename_part)
    markdown_filename = f"compliance_report_{file_id}_{md_stem}.md"
    final_markdown_path = markdown_dir / markdown_filename
    staged_markdown_path = markdown_stage_dir / markdown_filename
    staged_markdown_path.write_text(
        disclosure_engine.generate_compliance_report(assessment), encoding="utf-8"
    )

    json_filename = f"{filename_part}_{file_id}_compliance.json"
    xlsx_filename = f"{filename_part}_{file_id}_compliance.xlsx"
    sasb_filename = f"{filename_part}_{file_id}_sasb_metrics.json"
    staged_json_path = compliance_stage_dir / json_filename
    staged_xlsx_path = compliance_stage_dir / xlsx_filename
    staged_sasb_path = compliance_stage_dir / sasb_filename

    assessment_json = _build_compliance_assessment_json(
        assessment,
        str(final_markdown_path),
        _compliance_result_filename(file_info.get("original_name") or "", llm_model_name),
    )
    staged_json_path.write_text(
        json.dumps(assessment_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _assessment_excel_frame(assessment_json).to_excel(
        staged_xlsx_path, index=False, sheet_name="Benchmark"
    )

    staged_files = [
        (staged_markdown_path, final_markdown_path),
        (staged_json_path, compliance_dir / json_filename),
        (staged_xlsx_path, compliance_dir / xlsx_filename),
    ]
    sasb_metrics_filename = None
    if fw == "SASB" and assessment_json.get("sasb_metric_rows"):
        staged_sasb_path.write_text(
            json.dumps(
                assessment_json["sasb_metric_rows"], indent=2, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        staged_files.append((staged_sasb_path, compliance_dir / sasb_filename))
        sasb_metrics_filename = sasb_filename

    manifest_row = {
        "scope_key": scope_key,
        "json_filename": json_filename,
        "sasb_metrics_filename": sasb_metrics_filename,
        "overall_score": float(assessment.overall_compliance_score or 0.0),
    }
    return manifest_row, staged_files, str(final_markdown_path)


def _commit_staged_assessment_files(
    staged_files: List[tuple[Path, Path]],
) -> None:
    """Atomically replace each file and roll back the whole bundle on error.

    The manifest must be the final item supplied by the caller; readers thus
    never discover a new manifest before all files referenced by it exist.
    """
    seen_targets = set()
    for staged_path, target_path in staged_files:
        target_key = str(target_path.resolve())
        if target_key in seen_targets or not staged_path.is_file():
            raise RuntimeError("Invalid staged assessment bundle")
        seen_targets.add(target_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)

    backups: List[tuple[Path, Optional[Path]]] = []
    committed: List[tuple[Path, Optional[Path]]] = []
    try:
        for staged_path, target_path in staged_files:
            backup_path = None
            if target_path.exists():
                fd, backup_name = tempfile.mkstemp(
                    prefix=f".{target_path.name}.",
                    suffix=".reanalyze-backup",
                    dir=str(target_path.parent),
                )
                os.close(fd)
                backup_path = Path(backup_name)
                shutil.copy2(target_path, backup_path)
            backups.append((target_path, backup_path))
            os.replace(staged_path, target_path)
            committed.append((target_path, backup_path))
    except Exception:
        for target_path, backup_path in reversed(committed):
            try:
                if backup_path is not None and backup_path.exists():
                    os.replace(backup_path, target_path)
                elif target_path.exists():
                    target_path.unlink()
            except Exception as rollback_error:
                logger.error(
                    f"Assessment output rollback failed for {target_path}: {rollback_error}"
                )
        raise
    finally:
        for _, backup_path in backups:
            if backup_path is not None and backup_path.exists():
                try:
                    backup_path.unlink()
                except Exception:
                    pass


@with_backend_model_task("reanalyze_report")
def _sync_reanalyze_report_body(
    file_info: dict,
    progress_cb=None,
    expected_job_id: Optional[str] = None,
) -> dict:
    """Run retrieval + disclosure using persisted segments/embeddings only."""
    started = time.perf_counter()
    file_id = str(file_info.get("file_id") or "")
    artifacts = _load_validated_report_artifacts(file_id)
    report_content = _report_content_from_artifacts(file_info, artifacts)
    metric_sidecar_persisted = artifacts.get("metric_retrieval_corpus") is not None

    def persist_metric_sidecar_if_ready() -> None:
        nonlocal metric_sidecar_persisted
        if metric_sidecar_persisted:
            return
        metric_corpus = getattr(
            report_content,
            "_metric_retrieval_corpus",
            None,
        )
        if metric_corpus is None or getattr(
            metric_corpus,
            "_embedding_matrix",
            None,
        ) is None:
            return
        try:
            file_manager.save_metric_retrieval_artifacts(
                file_id,
                metric_corpus,
                list(report_content.document_content.segments),
            )
            metric_sidecar_persisted = True
        except Exception as metric_persist_error:
            logger.warning(
                "Failed to persist lazily built metric retrieval "
                f"corpus for {file_id}: {metric_persist_error}"
            )

    fw, scopes = _reanalysis_scopes(file_info)
    _emit_upload_progress(
        progress_cb,
        "artifacts_loaded",
        "Persisted report evidence loaded.",
        5,
        file_id=file_id,
        segment_count=len(report_content.document_content.segments),
    )

    processor = system_components["metric_processor"]
    disclosure_engine = system_components["disclosure_engine"]
    config = system_components.get("config")
    llm_model_name = getattr(config, "llm_model", None) if config else None
    compliance_dir = Path(file_manager.compliance_outputs)
    markdown_dir = Path(file_manager.markdown_outputs)
    compliance_dir.mkdir(parents=True, exist_ok=True)
    markdown_dir.mkdir(parents=True, exist_ok=True)

    manifest_rows: List[dict] = []
    staged_files: List[tuple[Path, Path]] = []
    scope_performance: List[dict] = []
    last_assessment = None
    last_metrics = None
    last_report_path = ""
    expected_scope_keys = [scope_key for scope_key, _ in scopes]

    with tempfile.TemporaryDirectory(
        prefix=f".{file_id}.reanalyze-", dir=str(compliance_dir)
    ) as compliance_stage_name, tempfile.TemporaryDirectory(
        prefix=f".{file_id}.reanalyze-", dir=str(markdown_dir)
    ) as markdown_stage_name:
        compliance_stage_dir = Path(compliance_stage_name)
        markdown_stage_dir = Path(markdown_stage_name)
        _emit_upload_progress(
            progress_cb,
            "assessment_start",
            f"Starting assessment-only analysis for {len(scopes)} scope(s).",
            10,
            file_id=file_id,
            total_scopes=len(scopes),
        )

        for scope_index, (scope_key, params) in enumerate(scopes, 1):
            scope_started = time.perf_counter()
            progress_start = 10 + 75 * ((scope_index - 1) / max(1, len(scopes)))
            _emit_upload_progress(
                progress_cb,
                "assessment_scope",
                f"Analyzing scope {scope_index}/{len(scopes)}: {scope_key}.",
                progress_start,
                file_id=file_id,
                scope_key=scope_key,
                scope_index=scope_index,
                total_scopes=len(scopes),
            )
            metrics_started = time.perf_counter()
            metrics, semi_for_disclosure, filename_part = _load_reanalysis_scope_metrics(
                processor, fw, scope_key, params
            )
            metrics = _prepare_metrics_for_retrieval(processor, metrics)
            metric_pipeline_started = time.perf_counter()
            retrieval_results = iter_metric_collection_results(
                report_content, metrics, config=config
            )
            try:
                assessment = disclosure_engine.analyze_compliance(
                    retrieval_results,
                    report_content,
                    str(file_info.get("file_path") or ""),
                    metrics,
                    framework=fw,
                    industry=file_info.get("industry"),
                    semi_industry=semi_for_disclosure,
                )
            finally:
                metric_pipeline_finished = time.perf_counter()
                persist_metric_sidecar_if_ready()
            metric_pipeline_elapsed = (
                metric_pipeline_finished - metric_pipeline_started
            )
            retrieval_elapsed = float(
                getattr(retrieval_results, "retrieval_seconds", 0.0) or 0.0
            )
            disclosure_active_elapsed = float(
                getattr(
                    retrieval_results,
                    "disclosure_active_seconds",
                    max(0.0, metric_pipeline_elapsed - retrieval_elapsed),
                )
                or 0.0
            )
            disclosure_work_elapsed = float(
                getattr(
                    retrieval_results,
                    "disclosure_work_seconds",
                    disclosure_active_elapsed,
                )
                or 0.0
            )
            pipeline_overlap_elapsed = max(
                0.0,
                retrieval_elapsed
                + disclosure_active_elapsed
                - metric_pipeline_elapsed,
            )
            manifest_row, scope_staged_files, report_path = (
                _stage_reanalysis_scope_outputs(
                    assessment=assessment,
                    disclosure_engine=disclosure_engine,
                    file_info=file_info,
                    fw=fw,
                    scope_key=scope_key,
                    filename_part=filename_part,
                    llm_model_name=llm_model_name,
                    compliance_stage_dir=compliance_stage_dir,
                    markdown_stage_dir=markdown_stage_dir,
                )
            )
            manifest_rows.append(manifest_row)
            staged_files.extend(scope_staged_files)
            last_assessment = assessment
            last_metrics = metrics
            last_report_path = report_path
            scope_performance.append(
                {
                    "scope_key": scope_key,
                    "total_seconds": round(time.perf_counter() - scope_started, 3),
                    "metrics_seconds": round(metric_pipeline_started - metrics_started, 3),
                    "metric_pipeline_seconds": round(metric_pipeline_elapsed, 3),
                    "retrieval_seconds": round(retrieval_elapsed, 3),
                    "analysis_seconds": round(disclosure_active_elapsed, 3),
                    "disclosure_active_seconds": round(disclosure_active_elapsed, 3),
                    "disclosure_work_seconds": round(disclosure_work_elapsed, 3),
                    "pipeline_overlap_seconds": round(pipeline_overlap_elapsed, 3),
                    "metric_count": len(metrics.metrics),
                }
            )
            _emit_upload_progress(
                progress_cb,
                "assessment_scope_done",
                f"Completed scope {scope_index}/{len(scopes)}: {scope_key}.",
                10 + 75 * (scope_index / max(1, len(scopes))),
                file_id=file_id,
                scope_key=scope_key,
                scope_index=scope_index,
                total_scopes=len(scopes),
            )

        _write_compliance_manifest(
            compliance_stage_dir,
            file_id,
            fw,
            manifest_rows,
            expected_scope_keys=expected_scope_keys,
        )
        staged_manifest = _compliance_manifest_path(compliance_stage_dir, file_id)
        final_manifest = _compliance_manifest_path(compliance_dir, file_id)
        # Commit the manifest last so readers only discover a complete bundle.
        staged_files.append((staged_manifest, final_manifest))
        _emit_upload_progress(
            progress_cb,
            "assessment_committing",
            "Saving the completed assessment.",
            90,
            file_id=file_id,
        )
        if not _file_metadata_job_matches(
            file_id,
            expected_job_id,
            job_id_field="reanalysis_job_id",
        ):
            raise RuntimeError(
                "Report reanalysis job was superseded before assessment commit."
            )
        _commit_staged_assessment_files(staged_files)

    # Global state is updated only after the durable bundle is committed.
    system_components["current_report"] = report_content
    system_components["current_assessment"] = last_assessment
    system_components["current_metrics"] = last_metrics
    system_components["current_framework"] = fw
    system_components["current_industry"] = file_info.get("industry")
    system_components["current_semi_industry"] = file_info.get("semi_industry")
    system_components["current_gri_sector"] = file_info.get("gri_sector")
    system_components["current_gri_topic"] = file_info.get("gri_topic")
    system_components["current_company"] = Path(
        str(file_info.get("original_name") or "report")
    ).stem
    if last_assessment is not None and system_components.get("chatbot") is not None:
        try:
            with _chatbot_ops_lock:
                system_components["chatbot"].load_context(report_content, last_assessment)
        except Exception as exc:
            # The durable assessment has already committed.  Chat context is a
            # rebuildable in-memory convenience and must not turn that success
            # into a failed job (which would falsely claim the old files remain).
            logger.warning(
                f"Assessment committed but chatbot context refresh failed for {file_id}: {exc}"
            )

    performance = {
        "total_seconds": round(time.perf_counter() - started, 3),
        "segment_count": len(report_content.document_content.segments),
        "embedding_count": int(artifacts["embedding_matrix"].shape[0]),
        "embedding_dim": int(artifacts["embedding_matrix"].shape[1]),
        "scopes": scope_performance,
        "assessment_only": True,
    }
    return {
        "status": "success",
        "message": "Report assessment completed using persisted evidence.",
        "report_id": file_id,
        "file_id": file_id,
        "scopes": manifest_rows,
        "performance": performance,
        "assessment": {
            "total_metrics": (
                last_assessment.total_metrics_analyzed if last_assessment else 0
            ),
            "overall_score": (
                last_assessment.overall_compliance_score if last_assessment else 0
            ),
            "disclosure_summary": (
                last_assessment.disclosure_summary if last_assessment else {}
            ),
            "report_path": last_report_path,
        },
    }


@with_backend_model_task("upload_report")
def _sync_upload_report_body(
    content: Optional[bytes],
    filename: str,
    industry: Optional[str],
    semiIndustry: Optional[str],
    framework: Optional[str],
    griSector: Optional[str],
    griTopic: Optional[str],
    scopeSlugs: Optional[str],
    user_id: int,
    pre_saved_file_info: Optional[dict] = None,
    progress_cb=None,
    expected_job_id: Optional[str] = None,
) -> dict:
    """PDF encode + assessment off the event loop (keeps /api/files responsive)."""
    pipeline_started = time.perf_counter()
    performance: dict = {"scopes": [], "resources_start": _runtime_resource_snapshot()}
    try:
        if pre_saved_file_info is not None:
            file_info = dict(pre_saved_file_info)
            logger.info(f"Using pre-saved report file: {file_info.get('file_path')}")
            _emit_upload_progress(progress_cb, "file_saved", "File saved. Starting processing.", 5, file_id=file_info.get("file_id"))
        else:
            logger.info("Saving file using file manager...")
            _emit_upload_progress(progress_cb, "saving", "Saving uploaded PDF.", 2)
            file_info = file_manager.save_uploaded_file(
                file_content=content or b"",
                filename=filename,
                file_type="report",
                industry=industry,
                framework=framework,
                semi_industry=semiIndustry,
                gri_sector=griSector,
                gri_topic=griTopic,
                user_id=user_id
            )
            logger.info(f"File saved at: {file_info['file_path']}")
            _emit_upload_progress(progress_cb, "file_saved", "File saved. Starting processing.", 5, file_id=file_info.get("file_id"))

        if not _patch_file_metadata(
            file_info["file_id"],
            expected_job_id=expected_job_id,
            status="processing",
            processing_stage="processing",
            processing_progress=5,
        ):
            raise RuntimeError("Report processing job was superseded before it started.")

        # Process PDF
        logger.info("Starting PDF processing...")
        _emit_upload_progress(progress_cb, "pdf_processing", "Extracting report content with PaddleOCR-VL.", 8, file_id=file_info.get("file_id"))
        encoder = system_components["report_encoder"]
        old_progress_cb = getattr(getattr(encoder, "extractor", None), "progress_callback", None)
        if getattr(encoder, "extractor", None) is not None:
            encoder.extractor.progress_callback = progress_cb
        encode_started = time.perf_counter()
        try:
            report_content = encoder.encode_pdf(file_info["file_path"], save_markdown=True)
        finally:
            if getattr(encoder, "extractor", None) is not None:
                encoder.extractor.progress_callback = old_progress_cb
        performance["encode_seconds"] = round(time.perf_counter() - encode_started, 3)
        table_segments = [segment for segment in report_content.document_content.segments if segment.segment_type == "table"]
        performance["table_quality"] = {
            "first_pass_tables": len(table_segments),
            "review_candidates": sum(1 for segment in table_segments if segment.review_status == "needs_review"),
            "second_pass_tables": sum(1 for segment in table_segments if int(segment.parse_pass or 1) > 1),
            "replaced_tables": sum(1 for segment in table_segments if (segment.structured_data or {}).get("second_pass_replaced")),
            "conflicted_tables": sum(1 for segment in table_segments if segment.conflicts),
            "second_pass_budget_ratio": float(os.getenv("REPORT_TABLE_SECOND_PASS_MAX_RATIO", "0.30") or "0.30"),
        }

        # IMPORTANT: Align document_id with file_id so all downstream (chat/cache/output filenames)
        # use a single stable identifier.
        try:
            report_content.document_id = file_info["file_id"]
            report_content.document_content.document_id = file_info["file_id"]
        except Exception:
            pass

        # Persist segments + embeddings for fast chat retrieval after restart.
        # (This is crucial for "load previous embeddings" requirement.)
        persist_started = time.perf_counter()
        if not _file_metadata_job_matches(
            file_info["file_id"], expected_job_id
        ):
            raise RuntimeError(
                "Report processing job was superseded before artifact persistence."
            )
        try:
            file_manager.save_report_artifacts(file_info["file_id"], report_content)
        except Exception as e:
            logger.warning(f"Failed to persist report artifacts for {file_info['file_id']}: {e}")
        performance["artifact_persist_seconds"] = round(time.perf_counter() - persist_started, 3)
        logger.info("PDF processing completed")
        _emit_upload_progress(progress_cb, "pdf_processed", "Report text extraction and embeddings completed.", 50, file_id=file_info.get("file_id"))

        # Store processing results
        system_components["current_report"] = report_content
        logger.info("Report content stored in system components")
        
        # Store framework and industry / GRI information
        system_components["current_framework"] = framework
        system_components["current_industry"] = industry
        system_components["current_semi_industry"] = semiIndustry
        system_components["current_gri_sector"] = griSector
        system_components["current_gri_topic"] = griTopic
        # Extract company name from filename (remove extension)
        company_name = filename.rsplit('.', 1)[0] if filename else "Unknown Company"
        system_components["current_company"] = company_name
        logger.info(f"Stored framework and industry info - Framework: {framework}, Industry: {industry}, Semi-Industry: {semiIndustry}, GRI: {griSector}/{griTopic}, Company: {company_name}")
        
        # Get report summary
        logger.info("Getting report summary...")
        summary = encoder.get_report_summary(report_content)
        logger.info("Report summary obtained")
        _emit_upload_progress(progress_cb, "summary_ready", "Report summary created.", 52, file_id=file_info.get("file_id"))

        # Build scope list: one retrieval + assessment per slug; single PDF encode above.
        fw = (framework or "").strip()
        processor = system_components["metric_processor"]
        scopes_list: List[tuple[str, dict]] = []

        if fw == "GRI":
            topics = _parse_scope_slugs_json(scopeSlugs, griTopic)
            if not griSector or not str(griSector).strip() or not topics:
                raise ValueError(
                    "GRI sector and at least one topic are required. Use griTopic or scopeSlugs JSON array."
                )
            gs = str(griSector).strip()
            for t in topics:
                scopes_list.append((t, {"griSector": gs, "griTopic": t}))
        elif fw == "SASB":
            semis = _parse_scope_slugs_json(scopeSlugs, semiIndustry)
            if not semis:
                raise ValueError(
                    "SASB sub-industry is required. Use semiIndustry or scopeSlugs JSON array."
                )
            for s in semis:
                scopes_list.append((s, {"semiIndustry": s}))
        elif fw == "CDP":
            topics = _parse_scope_slugs_json(scopeSlugs, semiIndustry)
            if not topics:
                raise ValueError("CDP Topic is required. Use semiIndustry or scopeSlugs JSON array.")
            for t in topics:
                scopes_list.append((t, {"semiIndustry": t}))
        elif fw == "TCFD":
            topics = _parse_scope_slugs_json(scopeSlugs, semiIndustry)
            if not topics:
                raise ValueError("TCFD Topic is required. Use semiIndustry or scopeSlugs JSON array.")
            for t in topics:
                scopes_list.append((t, {"semiIndustry": t}))
        else:
            raise ValueError("Please select a framework (SASB, GRI, CDP, or TCFD) and the required options.")

        _, p0 = scopes_list[0]
        if fw == "GRI":
            system_components["current_gri_topic"] = p0["griTopic"]
            system_components["current_gri_sector"] = p0["griSector"]
            system_components["current_semi_industry"] = semiIndustry
        elif fw == "SASB":
            system_components["current_semi_industry"] = p0["semiIndustry"]
        elif fw in ("CDP", "TCFD"):
            system_components["current_semi_industry"] = p0["semiIndustry"]

        dual_retriever = system_components["dual_retriever"]
        disclosure_engine = system_components["disclosure_engine"]
        json_report_dir = Path(file_manager.compliance_outputs)
        json_report_dir.mkdir(parents=True, exist_ok=True)
        config = system_components.get("config")
        llm_model_name = getattr(config, "llm_model", None) if config else None

        manifest_rows: List[dict] = []
        last_assessment = None
        last_report_path_str = ""
        expected_scope_keys = [s[0] for s in scopes_list]

        if not _patch_file_metadata(
            file_info["file_id"],
            expected_job_id=expected_job_id,
            scope_slugs_json=json.dumps(expected_scope_keys, ensure_ascii=False),
        ):
            raise RuntimeError(
                "Report processing job was superseded before assessment started."
            )

        _write_compliance_manifest(
            json_report_dir,
            file_info["file_id"],
            fw,
            [],
            expected_scope_keys=expected_scope_keys,
        )
        _emit_upload_progress(progress_cb, "assessment_start", f"Starting compliance assessment for {len(scopes_list)} scope(s).", 55, file_id=file_info.get("file_id"), total_scopes=len(scopes_list))

        try:
            for scope_index, (scope_key, params) in enumerate(scopes_list, 1):
                scope_started = time.perf_counter()
                _emit_upload_progress(progress_cb, "assessment_scope", f"Analyzing scope {scope_index}/{len(scopes_list)}: {scope_key}.", 55 + 35 * ((scope_index - 1) / max(1, len(scopes_list))), file_id=file_info.get("file_id"), scope_key=scope_key, scope_index=scope_index, total_scopes=len(scopes_list))
                metrics_started = time.perf_counter()
                if fw == "GRI":
                    metrics = processor.load_gri_metrics_by_sector_topic(
                        params["griSector"], params["griTopic"]
                    )
                    semi_for_disclosure = (
                        f"GRI {params['griSector']} {params['griTopic']}".strip()
                    )
                    sanitized_part = _sanitize_compliance_filename_part(
                        f"GRI_{params['griSector']}_{params['griTopic']}"
                    )
                elif fw == "SASB":
                    metrics = processor.load_sasb_metrics_by_industry(params["semiIndustry"])
                    semi_for_disclosure = params["semiIndustry"]
                    sanitized_part = _sanitize_compliance_filename_part(params["semiIndustry"])
                elif fw == "CDP":
                    metrics = processor.load_cdp_metrics_by_topic(params["semiIndustry"])
                    semi_for_disclosure = params["semiIndustry"] or "CDP"
                    sanitized_part = _sanitize_compliance_filename_part(
                        f"CDP_{params['semiIndustry']}"
                    )
                else:  # TCFD
                    metrics = processor.load_tcfd_metrics_by_topic(params["semiIndustry"])
                    semi_for_disclosure = params["semiIndustry"] or "TCFD"
                    sanitized_part = _sanitize_compliance_filename_part(
                        f"TCFD_{params['semiIndustry']}"
                    )

                metrics = _prepare_metrics_for_retrieval(processor, metrics)
                system_components["current_metrics"] = metrics
                logger.info(
                    f"Loaded metrics for scope_key={scope_key} ({fw}), "
                    f"count={len(metrics.metrics)}, elapsed={time.perf_counter() - metrics_started:.2f}s"
                )

                metric_pipeline_started = time.perf_counter()
                retrieval_results = iter_metric_collection_results(
                    report_content, metrics, config=system_components.get("config")
                )
                assessment = disclosure_engine.analyze_compliance(
                    retrieval_results,
                    report_content,
                    file_info["file_path"],
                    metrics,
                    framework=framework,
                    industry=industry,
                    semi_industry=semi_for_disclosure,
                )
                metric_pipeline_elapsed = (
                    time.perf_counter() - metric_pipeline_started
                )
                retrieval_elapsed = float(
                    getattr(retrieval_results, "retrieval_seconds", 0.0) or 0.0
                )
                disclosure_active_elapsed = float(
                    getattr(
                        retrieval_results,
                        "disclosure_active_seconds",
                        max(0.0, metric_pipeline_elapsed - retrieval_elapsed),
                    )
                    or 0.0
                )
                disclosure_work_elapsed = float(
                    getattr(
                        retrieval_results,
                        "disclosure_work_seconds",
                        disclosure_active_elapsed,
                    )
                    or 0.0
                )
                pipeline_overlap_elapsed = max(
                    0.0,
                    retrieval_elapsed
                    + disclosure_active_elapsed
                    - metric_pipeline_elapsed,
                )
                logger.info(
                    f"Metric retrieval + disclosure pipeline scope={scope_key} "
                    f"took {metric_pipeline_elapsed:.2f}s "
                    f"(active retrieval={retrieval_elapsed:.2f}s, "
                    f"active disclosure={disclosure_active_elapsed:.2f}s, "
                    f"overlap={pipeline_overlap_elapsed:.2f}s)"
                )
                last_assessment = assessment

                if not _file_metadata_job_matches(
                    file_info["file_id"], expected_job_id
                ):
                    raise RuntimeError(
                        "Report processing job was superseded before assessment output."
                    )

                compliance_report = disclosure_engine.generate_compliance_report(assessment)
                md_stem = _sanitize_compliance_filename_part(f"{sanitized_part}")
                report_path = (
                    Path(file_manager.markdown_outputs)
                    / f"compliance_report_{file_info['file_id']}_{md_stem}.md"
                )
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(compliance_report, encoding="utf-8")
                last_report_path_str = str(report_path)

                json_filename = f"{sanitized_part}_{file_info['file_id']}_compliance.json"
                json_report_path = json_report_dir / json_filename
                xlsx_report_path = json_report_dir / f"{sanitized_part}_{file_info['file_id']}_compliance.xlsx"
                sasb_metrics_filename = f"{sanitized_part}_{file_info['file_id']}_sasb_metrics.json"
                sasb_metrics_result_path = json_report_dir / sasb_metrics_filename

                assessment_json = _build_compliance_assessment_json(
                    assessment,
                    str(report_path),
                    _compliance_result_filename(filename or "", llm_model_name),
                )

                with open(json_report_path, "w", encoding="utf-8") as f:
                    json.dump(assessment_json, f, indent=2, ensure_ascii=False)

                # SASB final results are persisted in the original backend/data/sasb_metrics row shape.
                # backend/data/sasb_metric_profiles is retrieval-only and is not used for display/storage rows.
                if (framework or "").strip().upper() == "SASB" and assessment_json.get("sasb_metric_rows"):
                    with open(sasb_metrics_result_path, "w", encoding="utf-8") as f:
                        json.dump(assessment_json["sasb_metric_rows"], f, indent=2, ensure_ascii=False)

                df_flat = pd.json_normalize(
                    assessment_json,
                    record_path="metric_analyses",
                    meta=[
                        "report_id",
                        "assessment_date",
                        "filename",
                        "total_metrics",
                        "overall_score",
                        ["disclosure_summary", "fully_disclosed"],
                        ["disclosure_summary", "partially_disclosed"],
                        ["disclosure_summary", "not_disclosed"],
                    ],
                )
                def _pick_series(*names, default=""):
                    for name in names:
                        if name in df_flat.columns:
                            return df_flat[name]
                    return pd.Series([default] * len(df_flat))

                df_final = pd.DataFrame({
                    "Metric": _pick_series("Metric", "metric_name"),
                    "Category": _pick_series("Category", "category"),
                    "Unit": _pick_series("Unit", "unit"),
                    "Code": _pick_series("Code", "metric_code", "metric_id"),
                    "Topic": _pick_series("Topic", "topic"),
                    "Type": _pick_series("Type", "type"),
                    "Definition": _pick_series("Definition", "definition"),
                    "Value": _pick_series("Value", "value"),
                    "Page": _pick_series("Page", "page"),
                    "Context": _pick_series("Context", "context"),
                    "Disclosure Status": _pick_series("Disclosure Status", "disclosure_status", "Model Disclosure Status"),
                    "LLM Analysis": _pick_series("LLM Analysis", "reasoning"),
                    "ChatGPT": _pick_series("ChatGPT"),
                    "InputWrong": _pick_series("InputWrong"),
                    "comment": _pick_series("comment"),
                })
                df_final.to_excel(xlsx_report_path, index=False, sheet_name="Benchmark")

                manifest_rows.append(
                    {
                        "scope_key": scope_key,
                        "json_filename": json_filename,
                        "sasb_metrics_filename": sasb_metrics_filename if (framework or "").strip().upper() == "SASB" else None,
                        "overall_score": float(assessment.overall_compliance_score or 0.0),
                    }
                )
                _write_compliance_manifest(
                    json_report_dir,
                    file_info["file_id"],
                    fw,
                    manifest_rows,
                    expected_scope_keys=expected_scope_keys,
                )
                logger.info(
                    f"Compliance scope={scope_key} completed in "
                    f"{time.perf_counter() - scope_started:.2f}s"
                )
                performance["scopes"].append({
                    "scope_key": scope_key,
                    "total_seconds": round(time.perf_counter() - scope_started, 3),
                    "metrics_seconds": round(metric_pipeline_started - metrics_started, 3),
                    "metric_pipeline_seconds": round(metric_pipeline_elapsed, 3),
                    "retrieval_seconds": round(retrieval_elapsed, 3),
                    "analysis_seconds": round(disclosure_active_elapsed, 3),
                    "disclosure_active_seconds": round(disclosure_active_elapsed, 3),
                    "disclosure_work_seconds": round(disclosure_work_elapsed, 3),
                    "pipeline_overlap_seconds": round(pipeline_overlap_elapsed, 3),
                    "metric_count": len(metrics.metrics),
                })
                _emit_upload_progress(progress_cb, "assessment_scope_done", f"Completed scope {scope_index}/{len(scopes_list)}: {scope_key}.", 55 + 35 * (scope_index / max(1, len(scopes_list))), file_id=file_info.get("file_id"), scope_key=scope_key, scope_index=scope_index, total_scopes=len(scopes_list))

            system_components["current_assessment"] = last_assessment
            if last_assessment:
                with _chatbot_ops_lock:
                    system_components["chatbot"].load_context(report_content, last_assessment)
            warm_hipporag_after_upload(
                system_components["chatbot"],
                report_content,
            )

            # Persist primary scope on file record for listings / cross-analysis defaults.
            scope_updates = {
                "scope_slugs_json": json.dumps(
                    [s[0] for s in scopes_list], ensure_ascii=False
                )
            }
            if fw == "GRI":
                scope_updates.update(
                    gri_topic=scopes_list[0][0],
                    gri_sector=scopes_list[0][1]["griSector"],
                    semi_industry=None,
                )
            elif fw in ("SASB", "CDP", "TCFD"):
                scope_updates["semi_industry"] = scopes_list[0][0]
            if not _patch_file_metadata(
                file_info["file_id"],
                expected_job_id=expected_job_id,
                **scope_updates,
            ):
                raise RuntimeError(
                    "Report processing job was superseded before finalization."
                )

            if last_assessment:
                logger.info(
                    f"Complete processing chain finished ({len(scopes_list)} scope(s)). "
                    f"Last score: {last_assessment.overall_compliance_score:.2%}"
                )
            finalized = _finalize_report_file_metadata(
                file_info["file_id"],
                destination_status="processed",
                expected_job_id=expected_job_id,
                status="processed",
                processing_job_id=None,
                processing_stage="completed",
                processing_progress=100,
                processing_error=None,
            )
            if not finalized:
                raise RuntimeError(
                    "Could not move and finalize the processed report for the current job."
                )
            _emit_upload_progress(progress_cb, "completed", "Report processing completed.", 100, file_id=file_info.get("file_id"))

            performance["total_seconds"] = round(time.perf_counter() - pipeline_started, 3)
            performance["resources_end"] = _runtime_resource_snapshot()
            logger.info(f"Report performance summary: {json.dumps(performance, ensure_ascii=False)}")
            return {
                "status": "success",
                "message": "Report uploaded and fully processed",
                "report_id": report_content.document_id,
                "file_id": file_info["file_id"],
                "summary": summary,
                "scopes": manifest_rows,
                "performance": performance,
                "assessment": {
                    "total_metrics": last_assessment.total_metrics_analyzed if last_assessment else 0,
                    "overall_score": last_assessment.overall_compliance_score if last_assessment else 0,
                    "disclosure_summary": last_assessment.disclosure_summary if last_assessment else {},
                    "report_path": last_report_path_str,
                },
            }

        except Exception as assessment_error:
            if not _file_metadata_job_matches(
                file_info["file_id"], expected_job_id
            ):
                raise RuntimeError(
                    "Report processing job was superseded during assessment."
                ) from assessment_error
            error_str = str(assessment_error)
            logger.error(f"Error in assessment processing: {assessment_error}")

            try:
                _write_compliance_manifest(
                    json_report_dir,
                    file_info["file_id"],
                    fw,
                    manifest_rows,
                    expected_scope_keys=expected_scope_keys,
                )
            except Exception as me:
                logger.warning(f"Failed to write partial compliance manifest: {me}")

            is_llm_error = "403" in error_str or "AccessDenied" in error_str or "Unpurchased" in error_str or "LLM" in error_str

            warm_hipporag_after_upload(
                system_components["chatbot"],
                report_content,
            )

            error_message = "Report processed but assessment failed"
            if is_llm_error:
                error_message = (
                    "分析失败：LLM模型访问被拒绝。请检查 `backend/config/.env` 文件中的 `LLM_MODEL` 配置，"
                    "确保使用可访问的模型（如 'qwen-plus' 或 'qwen-turbo'）。"
                )

            finalized = _finalize_report_file_metadata(
                file_info["file_id"],
                destination_status="processed",
                expected_job_id=expected_job_id,
                status="processed",
                processing_job_id=None,
                processing_stage="partial_success",
                processing_progress=100,
                processing_error=str(assessment_error)[:1000],
            )
            if not finalized:
                raise RuntimeError(
                    "Could not move and finalize the partially processed report for the current job."
                ) from assessment_error
            _emit_upload_progress(progress_cb, "partial_success", error_message, 100, file_id=file_info.get("file_id"), error=str(assessment_error))

            return {
                "status": "partial_success",
                "message": error_message,
                "report_id": report_content.document_id,
                "file_id": file_info["file_id"],
                "summary": summary,
                "error": str(assessment_error),
                "error_type": "llm_access_denied" if is_llm_error else "unknown"
            }

    except Exception as e:
        logger.error(f"Error processing report: {e}")
        # If processing fails, move to failed directory
        if 'file_info' in locals():
            finalized = _finalize_report_file_metadata(
                file_info["file_id"],
                destination_status="failed",
                expected_job_id=expected_job_id,
                status="failed",
                processing_job_id=None,
                processing_stage="failed",
                processing_progress=100,
                processing_error=str(e)[:1000],
            )
            if not finalized:
                logger.warning(
                    "Could not move/finalize failed report for the current job: file_id={}",
                    file_info["file_id"],
                )
            _emit_upload_progress(progress_cb, "failed", f"Report processing failed: {e}", 100, file_id=file_info.get("file_id"), error=str(e))
        raise



def _run_report_processing_job(
    job_id: str,
    file_info: dict,
    filename: str,
    industry: Optional[str],
    semiIndustry: Optional[str],
    framework: Optional[str],
    griSector: Optional[str],
    griTopic: Optional[str],
    scopeSlugs: Optional[str],
    user_id: int,
) -> None:
    """Background report processing entry point."""

    def progress_cb(*, stage: str, message: str, progress: Optional[float] = None, extra: Optional[dict] = None):
        update_report_job(
            job_id,
            status="processing",
            stage=stage,
            progress=progress,
            message=message,
            extra=extra,
        )
        if file_info.get("file_id"):
            metadata_updates = _report_progress_metadata_updates(
                stage=stage,
                message=message,
                progress=progress,
                job_id=job_id,
                extra=extra,
            )
            _patch_progress_file_metadata(
                file_info["file_id"],
                force=metadata_updates.get("status") in {"processed", "failed"},
                expected_job_id=job_id,
                **metadata_updates,
            )

    try:
        update_report_job(job_id, status="processing", stage="started", progress=1, message="Background processing started.")
        result = _sync_upload_report_body(
            None,
            filename,
            industry,
            semiIndustry,
            framework,
            griSector,
            griTopic,
            scopeSlugs,
            user_id,
            pre_saved_file_info=file_info,
            progress_cb=progress_cb,
            expected_job_id=job_id,
        )
        final_status = str(result.get("status") or "success")
        update_report_job(
            job_id,
            status="partial_success" if final_status == "partial_success" else "success",
            stage="completed",
            progress=100,
            message=result.get("message") or "Report processing completed.",
            result=result,
            event_type="done",
        )
    except Exception as exc:
        logger.exception(f"Background report job failed: job_id={job_id}")
        update_report_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="Processing failed. Please try again.",
            error=str(exc),
            event_type="error",
        )


def _run_report_reanalysis_job(job_id: str, file_info: dict) -> None:
    """Background assessment-only entry point."""
    file_id = str(file_info.get("file_id") or "")

    def progress_cb(
        *,
        stage: str,
        message: str,
        progress: Optional[float] = None,
        extra: Optional[dict] = None,
    ):
        update_report_job(
            job_id,
            status="processing",
            stage=stage,
            progress=progress,
            message=message,
            extra=extra,
        )
        _patch_progress_file_metadata(
            file_id,
            expected_job_id=job_id,
            job_id_field="reanalysis_job_id",
            reanalysis_job_id=job_id,
            reanalysis_stage=stage,
            reanalysis_progress=progress,
            reanalysis_error=None,
        )

    try:
        update_report_job(
            job_id,
            status="processing",
            stage="artifacts_loading",
            progress=1,
            message="Loading persisted report evidence.",
        )
        if not _patch_file_metadata(
            file_id,
            expected_job_id=job_id,
            job_id_field="reanalysis_job_id",
            reanalysis_job_id=job_id,
            reanalysis_stage="artifacts_loading",
            reanalysis_progress=1,
            reanalysis_error=None,
        ):
            raise RuntimeError("Report reanalysis job was superseded before it started.")
        result = _sync_reanalyze_report_body(
            file_info,
            progress_cb=progress_cb,
            expected_job_id=job_id,
        )
        finfo = file_manager.metadata.get("files", {}).get(file_id)
        try:
            previous_version = (
                int(finfo.get("assessment_version") or 0)
                if isinstance(finfo, dict)
                else 0
            )
        except (TypeError, ValueError):
            previous_version = 0
        if not _patch_file_metadata(
            file_id,
            expected_job_id=job_id,
            job_id_field="reanalysis_job_id",
            reanalysis_job_id=None,
            reanalysis_stage="completed",
            reanalysis_progress=100,
            reanalysis_error=None,
            assessment_version=previous_version + 1,
            assessment_updated_at=datetime.now().isoformat(),
        ):
            raise RuntimeError(
                "Report reanalysis job was superseded before finalization."
            )
        update_report_job(
            job_id,
            status="success",
            stage="completed",
            progress=100,
            message=result.get("message") or "Report assessment completed.",
            result=result,
            event_type="done",
        )
    except Exception as exc:
        logger.exception(f"Background report reanalysis failed: job_id={job_id}")
        _patch_file_metadata(
            file_id,
            expected_job_id=job_id,
            job_id_field="reanalysis_job_id",
            reanalysis_job_id=None,
            reanalysis_stage="failed",
            reanalysis_progress=100,
            reanalysis_error=str(exc)[:1000],
        )
        update_report_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="Assessment-only analysis failed. The previous assessment was preserved.",
            error=str(exc),
            event_type="error",
        )


async def get_report_job_status(
    job_id: str,
    user_id: int = Depends(get_current_user),
):
    job = snapshot_report_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    owner = get_report_job_owner(job_id)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="Not allowed to access this report job")
    return {"status": "success", "job": job}


def _reprocess_report_locked(file_id: str, user_id: int) -> dict:
    """Check both job types and install a processing token under one lock."""
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info or file_info.get("file_type") != "report":
        raise HTTPException(status_code=404, detail="Report not found or access denied")
    source = Path(str(file_info.get("file_path") or ""))
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Report PDF is missing")
    for job_field in ("processing_job_id", "reanalysis_job_id"):
        active_job_id = str(file_info.get(job_field) or "").strip()
        active_job = snapshot_report_job(active_job_id) if active_job_id else None
        if active_job and str(active_job.get("status") or "") not in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Report is currently being processed")

    if str(file_info.get("status") or "").lower() == "processing":
        existing_job_id = str(file_info.get("processing_job_id") or "").strip()
        _patch_file_metadata(
            file_id,
            status="failed",
            processing_job_id=None,
            processing_stage="interrupted",
            processing_progress=100,
            processing_error="Processing was interrupted. A replacement job is being created.",
        )

    job = create_report_job(file_id=file_id, filename=source.name, user_id=user_id)
    if not _patch_file_metadata(
        file_id,
        status="processing",
        processing_job_id=job["job_id"],
        processing_stage="queued",
        processing_progress=0,
        processing_error=None,
    ):
        raise HTTPException(
            status_code=500,
            detail="Could not reserve the report processing job.",
        )
    get_report_job_executor().submit(
        _run_report_processing_job,
        job["job_id"],
        dict(file_info),
        source.name,
        file_info.get("industry"),
        file_info.get("semi_industry"),
        file_info.get("framework"),
        file_info.get("gri_sector"),
        file_info.get("gri_topic"),
        json.dumps(file_info.get("scope_slugs") or []) if file_info.get("scope_slugs") else None,
        user_id,
    )
    return {
        "status": "accepted",
        "job_id": job["job_id"],
        "file_id": file_id,
        "report_id": file_id,
        "processing_status_url": f"/api/report-jobs/{job['job_id']}",
        "events_url": f"/api/report-jobs/{job['job_id']}/events",
    }


async def reprocess_report(
    file_id: str,
    user_id: int = Depends(get_current_user),
):
    """Explicitly enqueue the current visual parser for an existing report."""
    with _report_reanalysis_lock:
        return _reprocess_report_locked(file_id, user_id)


async def reanalyze_report(
    file_id: str,
    user_id: int = Depends(get_current_user),
):
    """Queue assessment-only analysis using persisted segments and embeddings."""
    with _report_reanalysis_lock:
        file_info = file_manager.get_file_info(file_id, user_id=user_id)
        if not file_info or file_info.get("file_type") != "report":
            raise HTTPException(status_code=404, detail="Report not found or access denied")

        if str(file_info.get("status") or "").strip().lower() == "processing":
            raise HTTPException(status_code=409, detail="Report is currently being processed")
        for job_field in ("processing_job_id", "reanalysis_job_id"):
            active_job_id = str(file_info.get(job_field) or "").strip()
            active_job = snapshot_report_job(active_job_id) if active_job_id else None
            if active_job and str(active_job.get("status") or "") not in TERMINAL_STATUSES:
                raise HTTPException(status_code=409, detail="Report is currently being processed")

        # Validate synchronously so the API returns 409 instead of accepting a
        # job that would silently regenerate or immediately fail on artifacts.
        _load_validated_report_artifacts(str(file_info.get("file_id") or file_id))
        _reanalysis_scopes(file_info)

        job_file_info = dict(file_info)
        canonical_file_id = str(job_file_info.get("file_id") or file_id)
        job = create_report_job(
            file_id=canonical_file_id,
            filename=str(job_file_info.get("safe_filename") or job_file_info.get("original_name") or canonical_file_id),
            user_id=user_id,
        )
        if not _patch_file_metadata(
            canonical_file_id,
            reanalysis_job_id=job["job_id"],
            reanalysis_stage="queued",
            reanalysis_progress=0,
            reanalysis_error=None,
        ):
            raise HTTPException(
                status_code=500,
                detail="Could not reserve the report reanalysis job.",
            )
        get_report_job_executor().submit(
            _run_report_reanalysis_job,
            job["job_id"],
            job_file_info,
        )

    return {
        "status": "accepted",
        "job_id": job["job_id"],
        "file_id": canonical_file_id,
        "report_id": canonical_file_id,
        "assessment_only": True,
        "processing_status_url": f"/api/report-jobs/{job['job_id']}",
        "events_url": f"/api/report-jobs/{job['job_id']}/events",
    }


async def report_job_events(
    job_id: str,
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    """SSE stream for report processing progress.

    EventSource cannot set custom Authorization headers. The frontend therefore
    passes the JWT token as a query parameter. Header auth is also accepted for
    non-browser clients.
    """
    auth_value = authorization
    if token and not auth_value:
        auth_value = f"Bearer {token}"
    if not auth_value:
        raise HTTPException(status_code=403, detail="Authentication token is required")
    try:
        user_id = get_user_id_from_authorization(auth_value)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    job = snapshot_report_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    owner = get_report_job_owner(job_id)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="Not allowed to access this report job")

    async def event_generator():
        last_seq = 0
        snapshot = snapshot_report_job(job_id)
        if snapshot:
            last_seq = int(snapshot.get("seq", 0) or 0)
            yield _format_sse("snapshot", snapshot)
            if str(snapshot.get("status")) in TERMINAL_STATUSES:
                yield _format_sse("done" if snapshot.get("status") != "failed" else "error", snapshot)
                return
        while True:
            if await request.is_disconnected():
                break
            events = get_report_job_events_since(job_id, last_seq)
            for event in events:
                last_seq = int(event.get("seq", last_seq) or last_seq)
                event_name = str(event.get("event") or "progress")
                yield _format_sse(event_name, event)
                if str(event.get("status")) in TERMINAL_STATUSES:
                    return
            # Heartbeat keeps proxies and browsers from considering the stream idle.
            yield ": heartbeat\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def upload_report(
    file: UploadFile = File(...),
    industry: Optional[str] = Form(None),
    semiIndustry: Optional[str] = Form(None),
    framework: Optional[str] = Form(None),
    griSector: Optional[str] = Form(None),
    griTopic: Optional[str] = Form(None),
    scopeSlugs: Optional[str] = Form(None),
    user_id: int = Depends(get_current_user)
):
    """
    Upload and process ESG report
    
    Args:
        file: PDF file
        industry: Main industry classification (optional, for SASB)
        semiIndustry: Sub-industry (for SASB metrics selection)
        framework: Framework selection (SASB/GRI/CDP)
        griSector: GRI sector slug (when framework=GRI)
        griTopic: GRI topic slug (when framework=GRI); if scopeSlugs is set, this is optional fallback for a single topic
        scopeSlugs: Optional JSON array of scope slugs (GRI topic slugs, SASB semi-industries, CDP topic slugs).
            One PDF encode; one retrieval+assessment per slug; separate *_compliance.json per scope.
        
    Returns:
        Processing results, including complete processing chain output (report processing + metrics matching + classification + knowledge base update)

    Note:
        Heavy work runs in a thread pool (`asyncio.to_thread`) so the event loop can still
        serve GET /api/files and other requests while analysis runs. HippoRAG warm indexing is
        queued in its own background worker; only final ``chatbot.load_context`` uses
        ``_chatbot_ops_lock``. Concurrent uploads still share global ``system_components`` (last
        write wins); use one analysis at a time for stable chat context.
    """
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    if str(framework or "").strip().upper() == "TCFD":
        raise HTTPException(status_code=422, detail="TCFD is no longer available for new uploads")
    
    try:
        content = await file.read()

        # 保存上传文件后立即返回，真正的 OCR / embedding / compliance 分析在后台线程中执行。
        file_info = file_manager.save_uploaded_file(
            file_content=content,
            filename=file.filename or "",
            file_type="report",
            industry=industry,
            framework=framework,
            semi_industry=semiIndustry,
            gri_sector=griSector,
            gri_topic=griTopic,
            user_id=user_id,
        )
        job = create_report_job(file_id=file_info["file_id"], filename=file.filename or "", user_id=user_id)
        try:
            scope_count = max(1, len(_parse_scope_slugs_json(scopeSlugs, semiIndustry or griTopic)))
        except Exception:
            # Logging metadata must never make an otherwise valid upload fail.
            scope_count = 1
        logger.info(
            "Report upload accepted file_id={} job_id={} framework={} file_size={} scope_count={}",
            file_info["file_id"], job["job_id"], framework or "", len(content), scope_count,
        )
        _patch_file_metadata(
            file_info["file_id"],
            status="processing",
            processing_job_id=job["job_id"],
            processing_stage="queued",
            processing_progress=0,
            processing_error=None,
        )
        get_report_job_executor().submit(
            _run_report_processing_job,
            job["job_id"],
            file_info,
            file.filename or "",
            industry,
            semiIndustry,
            framework,
            griSector,
            griTopic,
            scopeSlugs,
            user_id,
        )

        return {
            "status": "accepted",
            "message": "Report uploaded. Processing has started in the background.",
            "job_id": job["job_id"],
            "file_id": file_info["file_id"],
            "report_id": file_info["file_id"],
            "processing_status_url": f"/api/report-jobs/{job['job_id']}",
            "events_url": f"/api/report-jobs/{job['job_id']}/events",
        }
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@with_backend_model_task("upload_metrics")
async def upload_metrics(
    file: Optional[UploadFile] = File(None),
    metrics_json: Optional[str] = Form(None),
    user_id: int = Depends(get_current_user)
):
    """
    上传ESG指标（支持Excel文件或JSON）
    
    Args:
        file: Excel文件（可选）
        metrics_json: JSON格式的指标数据（可选）
        
    Returns:
        处理结果
    """
    try:
        processor = system_components["metric_processor"]
        file_info = None
        
        if file:
            # 处理Excel文件
            if not file.filename.endswith(('.xlsx', '.xls')):
                raise HTTPException(status_code=400, detail="Only Excel files are supported")
            
            # 读取文件内容
            content = await file.read()
            
            # 使用文件管理器保存文件
            file_info = file_manager.save_uploaded_file(
                file_content=content,
                filename=file.filename,
                file_type="metrics",
                user_id=user_id
            )
            
            # 从Excel加载指标
            metrics = processor.load_metrics_from_excel(file_info["file_path"])
            
        elif metrics_json:
            # 从JSON加载指标
            metrics_data = json.loads(metrics_json)
            metrics = MetricCollection(**metrics_data)
            
            # 保存JSON到文件系统
            json_content = metrics_json.encode('utf-8')
            file_info = file_manager.save_uploaded_file(
                file_content=json_content,
                filename="uploaded_metrics.json",
                file_type="metrics",
                user_id=user_id
            )
            
        else:
            # Metrics file is required
            raise HTTPException(status_code=400, detail="Metrics file (Excel or JSON) is required. Please upload a metrics file.")
        
        # 处理指标（语义扩展） - LLM is required
        processed_metrics = processor.process_metric_collection(metrics)
        
        # 存储处理结果
        system_components["current_metrics"] = processed_metrics
        
        logger.info(f"Successfully processed {len(processed_metrics.metrics)} metrics")
        
        result = {
            "status": "success",
            "message": f"Processed {len(processed_metrics.metrics)} metrics",
            "collection_id": processed_metrics.collection_id,
            "metrics_count": len(processed_metrics.metrics)
        }
        
        if file_info:
            result["file_id"] = file_info["file_id"]
        
        # Add report summary information if available
        if system_components["current_report"]:
            summary = encoder.get_report_summary(system_components["current_report"])
            result["total_pages"] = summary.get("total_pages", 0)
            result["total_segments"] = summary.get("total_segments", 0)
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_latest_report(user_id: int = Depends(get_current_user)):
    """
    获取当前用户最新的合规分析报告（markdown）
    
    Returns:
        最新报告内容
    """
    try:
        json_dirs = [
            Path(file_manager.compliance_outputs),
            Path(__file__).resolve().parents[2] / "outputs",  # legacy backend/outputs
        ]
        md_dirs = [
            Path(file_manager.markdown_outputs),
            Path(file_manager.compliance_outputs),  # some legacy runs wrote markdown alongside JSON
            Path(__file__).resolve().parents[2] / "outputs",
        ]

        # 获取用户自己的报告文件列表，按上传时间倒序（支持 Subindustry_fileid_compliance.json 命名）
        user_files = file_manager.list_user_files(user_id, file_type="report")
        for f in sorted(user_files, key=lambda x: x["upload_time"], reverse=True):
            json_file = _find_assessment_json_path(f["file_id"], f)
            if not json_file or not json_file.exists():
                continue

            with open(json_file, "r", encoding="utf-8") as jf:
                assessment_data = json.load(jf)
            report_id = assessment_data.get("report_id")
            if not report_id:
                continue

            # 再用 report_id 找 markdown
            md_file = None
            for d in md_dirs:
                p = d / f"compliance_report_{report_id}.md"
                if p.exists():
                    md_file = p
                    break
            if not md_file:
                continue

            content = md_file.read_text(encoding="utf-8")
            return {
                "status": "success",
                "report_file": md_file.name,
                "content": content,
                "created_at": datetime.fromtimestamp(md_file.stat().st_mtime).isoformat()
            }

        raise HTTPException(status_code=404, detail="No reports found for current user")
        
    except Exception as e:
        logger.error(f"Error fetching latest report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_report_by_file_id(
    file_id: str,
    scope: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user),
):
    """
    Get compliance analysis report for a specific file (只能访问自己的文件)

    Args:
        file_id: The file ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        Report content for the specified file
    """
    try:
        # 检查文件是否属于当前用户
        file_info = file_manager.get_file_info(file_id, user_id=user_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found or access denied")
        canonical_dir = Path(file_manager.compliance_outputs)
        scope_key = str(scope or "").strip()
        json_file = _json_path_from_manifest(canonical_dir, file_id, scope_key)
        if (json_file is None or not json_file.exists()) and scope_key:
            fw = str(file_info.get("framework") or "").strip()
            json_file = _compliance_json_path_for_scope(
                canonical_dir, file_id, fw, file_info, scope_key
            )
            if json_file is None or not json_file.exists():
                safe_scope = _sanitize_compliance_filename_part(scope_key)
                scoped_matches = list(
                    canonical_dir.glob(f"*{file_id}*{safe_scope}*compliance*.json")
                )
                scoped_matches = [p for p in scoped_matches if p.is_file()]
                if scoped_matches:
                    scoped_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    json_file = scoped_matches[0]
        if (json_file is None or not json_file.exists()) and not scope_key:
            json_file = _find_assessment_json_path(file_id, file_info)
        if not json_file or not json_file.exists():
            detail = (
                f"No assessment found for file {file_id} and scope {scope_key}"
                if scope_key
                else f"No assessment found for file {file_id}"
            )
            raise HTTPException(status_code=404, detail=detail)

        # Read JSON to get report_id
        with open(json_file, 'r', encoding='utf-8') as f:
            assessment_data = json.load(f)

        report_id = assessment_data.get('report_id')
        if not report_id:
            logger.warning(f"Report ID not found in assessment data for file_id={file_id}")
            raise HTTPException(status_code=404, detail="Report ID not found in assessment data")

        # Now load the markdown report using scope-aware paths first, then legacy names.
        md_dirs = [
            Path(file_manager.markdown_outputs),
            Path(file_manager.compliance_outputs),
            legacy_outputs_dir,
        ]
        report_file = None

        md_candidates: List[Path] = []
        if scope_key:
            try:
                _, _, scoped_md = _paths_for_scope_compliance_bundle(
                    file_manager, file_id, file_info, scope_key
                )
                md_candidates.append(scoped_md)
            except Exception as exc:
                logger.debug(f"Scope markdown path resolution skipped for {file_id}/{scope_key}: {exc}")

        json_stem = Path(json_file).stem
        json_suffix = f"_{file_id}_compliance"
        if json_stem.endswith(json_suffix):
            scoped_part = json_stem[: -len(json_suffix)]
            if scoped_part:
                md_candidates.append(
                    Path(file_manager.markdown_outputs)
                    / f"compliance_report_{file_id}_{_sanitize_compliance_filename_part(scoped_part)}.md"
                )

        for d in md_dirs:
            md_candidates.append(d / f"compliance_report_{report_id}.md")

        for p in md_candidates:
            if p.exists():
                report_file = p
                break

        # Fallback: try to find any markdown report containing the report_id.
        if not report_file:
            for d in [x for x in md_dirs if x.exists()]:
                patterns = [f"*{report_id}*.md"]
                if scope_key:
                    safe_scope = _sanitize_compliance_filename_part(scope_key)
                    patterns.insert(0, f"*{report_id}*{safe_scope}*.md")
                matches: List[Path] = []
                for pattern in patterns:
                    matches.extend(list(d.glob(pattern)))
                matches = [m for m in matches if m.is_file()]
                if matches:
                    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    report_file = matches[0]
                    break

        if not report_file:
            raise HTTPException(status_code=404, detail=f"Report file not found for report_id {report_id}")

        # Read the markdown content
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()

        return {
            "status": "success",
            "file_id": file_id,
            "report_id": report_id,
            "scope": scope_key or None,
            "report_file": report_file.name,
            "content": content,
            "created_at": datetime.fromtimestamp(report_file.stat().st_mtime).isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
