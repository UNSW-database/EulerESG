"""Company-level single and multi-report upload workflows."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
from fastapi import Depends, File, Form, HTTPException, Query, UploadFile
from loguru import logger

from .common import (
    _build_compliance_assessment_json,
    _compliance_result_filename,
    _parse_scope_slugs_json,
    _prepare_metrics_for_retrieval,
    _sanitize_compliance_filename_part,
    file_manager,
    get_current_user,
    iter_metric_collection_results,
    system_components,
)
from .report_jobs import create_report_job, get_executor, update_report_job
from ..company_registry import (
    CompanyRegistryError,
    build_scope_config,
    company_registry,
)
from ..company_reports import build_company_report_content
from ..gpu_model_lifecycle import with_backend_model_task


def _patch_file(file_id: str, **updates: Any) -> None:
    info = file_manager.metadata.get("files", {}).get(file_id)
    if not isinstance(info, dict):
        return
    info.update({key: value for key, value in updates.items() if value is not None})
    file_manager._save_metadata()


def _discard_pending_uploads(file_ids: Sequence[str]) -> None:
    """Best-effort rollback for files that never entered a company batch."""
    changed = False
    for file_id in file_ids:
        info = file_manager.metadata.get("files", {}).pop(str(file_id), None)
        if not isinstance(info, dict):
            continue
        changed = True
        try:
            Path(str(info.get("file_path") or "")).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"Failed to remove rolled-back upload {file_id}: {exc}")
    if changed:
        file_manager._save_metadata()


def _extract_year(filename: str) -> Optional[int]:
    matches = re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(filename or ""))
    if not matches:
        return None
    year = int(matches[-1])
    return year if 1900 <= year <= 2100 else None


def _parse_report_years(raw: Optional[str], filenames: Sequence[str]) -> List[Optional[int]]:
    values: List[Any] = []
    if raw and str(raw).strip():
        try:
            parsed = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise CompanyRegistryError("reportYears must be a JSON array") from exc
        if not isinstance(parsed, list) or len(parsed) != len(filenames):
            raise CompanyRegistryError("reportYears must contain one entry per PDF")
        values = parsed
    else:
        values = [None] * len(filenames)

    years: List[Optional[int]] = []
    for filename, raw_year in zip(filenames, values):
        if raw_year in (None, ""):
            years.append(_extract_year(filename))
            continue
        try:
            year = int(raw_year)
        except (TypeError, ValueError) as exc:
            raise CompanyRegistryError(f"Invalid report year for {filename}") from exc
        if not 1900 <= year <= 2100:
            raise CompanyRegistryError(f"Invalid report year for {filename}")
        years.append(year)
    return years


def _build_scopes(
    framework: Optional[str],
    *,
    semi_industry: Optional[str],
    gri_sector: Optional[str],
    gri_topic: Optional[str],
    scope_slugs: Optional[str],
) -> List[Tuple[str, Dict[str, str]]]:
    framework_key = str(framework or "").strip().upper()
    scopes: List[Tuple[str, Dict[str, str]]] = []
    if framework_key == "GRI":
        topics = _parse_scope_slugs_json(scope_slugs, gri_topic)
        if not str(gri_sector or "").strip() or not topics:
            raise CompanyRegistryError("GRI sector and at least one topic are required")
        for topic in topics:
            scopes.append((topic, {"griSector": str(gri_sector).strip(), "griTopic": topic}))
    elif framework_key in {"SASB", "CDP", "TCFD"}:
        values = _parse_scope_slugs_json(scope_slugs, semi_industry)
        if not values:
            raise CompanyRegistryError(f"{framework_key} metric scope is required")
        scopes.extend((value, {"semiIndustry": value}) for value in values)
    else:
        raise CompanyRegistryError("Framework must be SASB, GRI, CDP, or TCFD")
    return scopes


def _load_scope_metrics(
    framework: str,
    scope_key: str,
    params: Dict[str, str],
):
    processor = system_components["metric_processor"]
    if framework == "GRI":
        metrics = processor.load_gri_metrics_by_sector_topic(
            params["griSector"], params["griTopic"]
        )
        semi_for_disclosure = f"GRI {params['griSector']} {params['griTopic']}".strip()
        filename_part = f"GRI_{params['griSector']}_{params['griTopic']}"
    elif framework == "SASB":
        metrics = processor.load_sasb_metrics_by_industry(params["semiIndustry"])
        semi_for_disclosure = params["semiIndustry"]
        filename_part = params["semiIndustry"]
    elif framework == "CDP":
        metrics = processor.load_cdp_metrics_by_topic(params["semiIndustry"])
        semi_for_disclosure = params["semiIndustry"] or "CDP"
        filename_part = f"CDP_{params['semiIndustry']}"
    else:
        metrics = processor.load_tcfd_metrics_by_topic(params["semiIndustry"])
        semi_for_disclosure = params["semiIndustry"] or "TCFD"
        filename_part = f"TCFD_{params['semiIndustry']}"
    return (
        _prepare_metrics_for_retrieval(processor, metrics),
        semi_for_disclosure,
        _sanitize_compliance_filename_part(filename_part),
    )


def _write_company_assessment(
    *,
    company: Dict[str, Any],
    scope_key: str,
    filename_part: str,
    assessment,
    source_reports: List[Dict[str, Any]],
    analysis_token: str,
) -> Dict[str, Any]:
    company_id = company["company_id"]
    markdown = system_components["disclosure_engine"].generate_compliance_report(assessment)
    markdown_path = (
        Path(file_manager.markdown_outputs)
        / f"company_{company_id}_{analysis_token}_{filename_part}.md"
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown, encoding="utf-8")

    json_dir = Path(file_manager.compliance_outputs)
    json_dir.mkdir(parents=True, exist_ok=True)
    json_filename = f"company_{company_id}_{analysis_token}_{filename_part}_compliance.json"
    json_path = json_dir / json_filename
    config = system_components.get("config")
    model_name = getattr(config, "llm_model", None) if config else None
    payload = _build_compliance_assessment_json(
        assessment,
        str(markdown_path),
        _compliance_result_filename(company.get("company_name") or company_id, model_name),
    )
    payload.update(
        {
            "company_id": company_id,
            "company_name": company.get("company_name"),
            "scope_key": scope_key,
            "source_reports": source_reports,
            "value_conflicts_preserved": True,
        }
    )
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    xlsx_filename = f"company_{company_id}_{analysis_token}_{filename_part}_compliance.xlsx"
    xlsx_path = json_dir / xlsx_filename
    rows = payload.get("metric_analyses") or []
    if rows:
        pd.json_normalize(rows).to_excel(xlsx_path, index=False, sheet_name="Benchmark")

    return {
        "scope_key": scope_key,
        "json_filename": json_filename,
        "markdown_filename": markdown_path.name,
        "xlsx_filename": xlsx_filename if rows else None,
        "overall_score": float(assessment.overall_compliance_score or 0.0),
        "total_metrics": int(assessment.total_metrics_analyzed or 0),
    }


def _extract_one_report(
    file_info: Dict[str, Any],
    *,
    progress_callback=None,
) -> None:
    file_id = str(file_info["file_id"])
    existing = file_manager.load_report_artifacts(file_id)
    if existing and existing.get("segments") is not None:
        logger.info(f"Reusing existing report artifacts for company report {file_id}")
        return

    encoder = system_components["report_encoder"]
    extractor = getattr(encoder, "extractor", None)
    old_callback = getattr(extractor, "progress_callback", None)
    if extractor is not None:
        extractor.progress_callback = progress_callback
    try:
        report_content = encoder.encode_pdf(file_info["file_path"], save_markdown=True)
    finally:
        if extractor is not None:
            extractor.progress_callback = old_callback
    report_content.document_id = file_id
    report_content.document_content.document_id = file_id
    file_manager.save_report_artifacts(file_id, report_content)


@with_backend_model_task("company_report_batch")
def _run_company_batch_job(
    job_id: str,
    batch_id: str,
    company_id: str,
    extraction_file_ids: Sequence[str],
) -> None:
    batch = company_registry.get_batch(batch_id)
    company = company_registry.get_company(company_id)
    if not batch or not company:
        update_report_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="Company batch metadata is missing.",
            error="company_batch_missing",
            event_type="error",
        )
        return

    file_ids = list(extraction_file_ids)
    processed: List[str] = [
        value
        for value in (batch.get("processed_file_ids") or [])
        if value not in file_ids
    ]
    failed: List[str] = []
    update_report_job(
        job_id,
        status="processing",
        stage="started",
        progress=1,
        message="Company report batch started.",
    )
    company_registry.update_batch(batch_id, status="processing", error="")

    for index, file_id in enumerate(file_ids):
        file_info = file_manager.metadata.get("files", {}).get(file_id)
        if not isinstance(file_info, dict):
            failed.append(file_id)
            continue

        def extraction_progress(
            *,
            stage: str,
            message: str,
            progress: Optional[float] = None,
            extra: Optional[dict] = None,
        ) -> None:
            local = max(0.0, min(100.0, float(progress or 0.0)))
            overall = 5.0 + 50.0 * ((index + local / 100.0) / max(1, len(file_ids)))
            update_report_job(
                job_id,
                status="processing",
                stage=stage,
                progress=overall,
                message=message,
                extra={
                    **(extra or {}),
                    "batch_id": batch_id,
                    "company_id": company_id,
                    "current_file_id": file_id,
                    "file_index": index + 1,
                    "file_count": len(file_ids),
                },
            )
            _patch_file(
                file_id,
                status="processing",
                processing_stage=stage,
                processing_progress=local,
            )

        try:
            _patch_file(file_id, status="processing", processing_stage="pdf_processing")
            _extract_one_report(file_info, progress_callback=extraction_progress)
            file_manager.move_report_file(file_id, "processed")
            _patch_file(
                file_id,
                status="processing",
                processing_stage="extracted",
                processing_progress=60,
            )
            processed.append(file_id)
        except Exception as exc:
            logger.exception(f"Company report extraction failed: file_id={file_id}")
            failed.append(file_id)
            try:
                file_manager.move_report_file(file_id, "failed")
            except Exception as move_exc:
                logger.warning(
                    f"Failed report could not be moved: file_id={file_id}, error={move_exc}"
                )
            _patch_file(
                file_id,
                status="failed",
                processing_stage="failed",
                processing_progress=100,
                processing_error=str(exc)[:1000],
            )

    company_registry.update_batch(
        batch_id,
        processed_file_ids=processed,
        failed_file_ids=failed,
    )
    if failed:
        company_registry.update_batch(
            batch_id,
            status="failed",
            error=f"{len(failed)} report(s) failed extraction",
        )
        company_registry.mark_analysis_failed(company_id, "One or more reports failed extraction")
        update_report_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="One or more reports failed processing.",
            error=f"failed_file_ids={failed}",
            extra={"failed_file_ids": failed, "processed_file_ids": processed},
            event_type="error",
        )
        return

    try:
        company_registry.mark_analysis_started(company_id)
        company = company_registry.get_company(company_id) or company
        report_content, source_reports = build_company_report_content(company)
        scope_config = dict(company.get("scope_config") or {})
        scopes = _build_scopes(
            scope_config.get("framework"),
            semi_industry=scope_config.get("semi_industry"),
            gri_sector=scope_config.get("gri_sector"),
            gri_topic=scope_config.get("gri_topic"),
            scope_slugs=json.dumps(scope_config.get("scope_slugs") or []),
        )
        framework = str(scope_config.get("framework") or "").upper()
        system_components["current_report"] = report_content
        system_components["current_framework"] = framework
        system_components["current_company"] = company.get("company_name")

        outputs: List[Dict[str, Any]] = []
        analysis_token = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
        for scope_index, (scope_key, params) in enumerate(scopes, start=1):
            update_report_job(
                job_id,
                status="processing",
                stage="assessment_scope",
                progress=60 + 35 * ((scope_index - 1) / max(1, len(scopes))),
                message=f"Analyzing company scope {scope_index}/{len(scopes)}.",
                extra={"scope_key": scope_key, "scope_index": scope_index},
            )
            metrics, semi_for_disclosure, filename_part = _load_scope_metrics(
                framework, scope_key, params
            )
            retrieval_results = iter_metric_collection_results(
                report_content,
                metrics,
                config=system_components.get("config"),
            )
            assessment = system_components["disclosure_engine"].analyze_compliance(
                retrieval_results,
                report_content,
                f"company:{company_id}",
                metrics,
                framework=framework,
                industry=scope_config.get("industry"),
                semi_industry=semi_for_disclosure,
            )
            assessment = assessment.model_copy(
                update={
                    "report_id": company_id,
                    "company_id": company_id,
                    "company_name": company.get("company_name"),
                    "source_reports": source_reports,
                }
            )
            outputs.append(
                _write_company_assessment(
                    company=company,
                    scope_key=scope_key,
                    filename_part=filename_part,
                    assessment=assessment,
                    source_reports=source_reports,
                    analysis_token=analysis_token,
                )
            )
            system_components["current_assessment"] = assessment

        company = company_registry.mark_analysis_complete(
            company_id,
            assessment_outputs=outputs,
        )
        company_registry.update_batch(batch_id, status="success", error="")
        for file_id in company.get("report_ids") or []:
            _patch_file(
                file_id,
                status="processed",
                processing_stage="completed",
                processing_progress=100,
                company_analysis_version=company.get("analysis_version"),
            )
        result = {
            "status": "success",
            "message": "Company reports were processed and analyzed.",
            "batch_id": batch_id,
            "company_id": company_id,
            "file_ids": list(company.get("report_ids") or []),
            "analysis_version": company.get("analysis_version"),
            "scopes": outputs,
        }
        update_report_job(
            job_id,
            status="success",
            stage="completed",
            progress=100,
            message=result["message"],
            result=result,
            event_type="done",
        )
    except Exception as exc:
        logger.exception(f"Company aggregate analysis failed: company_id={company_id}")
        company_registry.update_batch(batch_id, status="failed", error=str(exc)[:1000])
        company_registry.mark_analysis_failed(company_id, str(exc))
        update_report_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="Company analysis failed.",
            error=str(exc),
            event_type="error",
        )


async def upload_report_batch(
    files: List[UploadFile] = File(...),
    uploadMode: str = Form("single"),
    companyId: Optional[str] = Form(None),
    companyName: Optional[str] = Form(None),
    reportYears: Optional[str] = Form(None),
    industry: Optional[str] = Form(None),
    semiIndustry: Optional[str] = Form(None),
    framework: Optional[str] = Form(None),
    griSector: Optional[str] = Form(None),
    griTopic: Optional[str] = Form(None),
    scopeSlugs: Optional[str] = Form(None),
    user_id: int = Depends(get_current_user),
):
    mode = str(uploadMode or "single").strip().lower()
    if mode not in {"single", "multi"}:
        raise HTTPException(status_code=422, detail="uploadMode must be single or multi")
    count = len(files or [])
    if (mode == "single" and count != 1) or (mode == "multi" and not 2 <= count <= 8):
        expected = "exactly 1 PDF" if mode == "single" else "between 2 and 8 PDFs"
        raise HTTPException(status_code=422, detail=f"{mode} mode requires {expected}")
    if any(not str(file.filename or "").lower().endswith(".pdf") for file in files):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    if str(framework or "").strip().upper() == "TCFD":
        raise HTTPException(status_code=422, detail="TCFD is no longer available for new uploads")

    try:
        scopes = _build_scopes(
            framework,
            semi_industry=semiIndustry,
            gri_sector=griSector,
            gri_topic=griTopic,
            scope_slugs=scopeSlugs,
        )
        scope_values = [scope_key for scope_key, _ in scopes]
        scope_config = build_scope_config(
            framework=framework,
            industry=industry,
            semi_industry=scope_values[0] if scope_values else semiIndustry,
            gri_sector=griSector,
            gri_topic=scope_values[0] if str(framework or "").upper() == "GRI" else griTopic,
            scope_slugs=scope_values,
        )
        filenames = [str(file.filename or "report.pdf") for file in files]
        years = _parse_report_years(reportYears, filenames)
        contents = [await file.read() for file in files]
        hashes = [hashlib.md5(content).hexdigest() for content in contents]

        created_company = not bool(companyId)
        if companyId:
            company = company_registry.validate_upload(
                company_id=companyId,
                user_id=user_id,
                scope_config=scope_config,
                file_hashes=hashes,
            )
        else:
            company = company_registry.create_company(
                user_id=user_id,
                company_name=str(companyName or "").strip(),
                scope_config=scope_config,
            )
            company_registry.validate_upload(
                company_id=company["company_id"],
                user_id=user_id,
                scope_config=scope_config,
                file_hashes=hashes,
            )

        file_infos: List[Dict[str, Any]] = []
        try:
            for filename, content, year in zip(filenames, contents, years):
                info = file_manager.save_uploaded_file(
                    file_content=content,
                    filename=filename,
                    file_type="report",
                    industry=industry,
                    framework=framework,
                    semi_industry=scope_values[0] if scope_values else semiIndustry,
                    gri_sector=griSector,
                    gri_topic=scope_values[0] if str(framework or "").upper() == "GRI" else griTopic,
                    user_id=user_id,
                )
                _patch_file(
                    info["file_id"],
                    company_id=company["company_id"],
                    company_name=company["company_name"],
                    report_year=year,
                    upload_mode=mode,
                    scope_slugs_json=json.dumps(scope_values, ensure_ascii=False),
                )
                file_infos.append(dict(file_manager.metadata["files"][info["file_id"]]))

            file_ids = [info["file_id"] for info in file_infos]
            batch = company_registry.create_batch(
                company_id=company["company_id"],
                user_id=user_id,
                upload_mode=mode,
                file_ids=file_ids,
                report_years={file_id: year for file_id, year in zip(file_ids, years)},
            )
        except Exception:
            _discard_pending_uploads([info["file_id"] for info in file_infos])
            if created_company:
                company_registry.remove_empty_company(company["company_id"], user_id)
            raise
        job = create_report_job(
            file_id=file_ids[0],
            file_ids=file_ids,
            filename=company["company_name"],
            user_id=user_id,
            company_id=company["company_id"],
            batch_id=batch["batch_id"],
        )
        company_registry.update_batch(batch["batch_id"], job_id=job["job_id"])
        for file_id in file_ids:
            _patch_file(
                file_id,
                batch_id=batch["batch_id"],
                processing_job_id=job["job_id"],
                status="processing",
                processing_stage="queued",
                processing_progress=0,
            )
        get_executor().submit(
            _run_company_batch_job,
            job["job_id"],
            batch["batch_id"],
            company["company_id"],
            file_ids,
        )
        return {
            "status": "accepted",
            "message": "Company reports were uploaded. Processing has started.",
            "batch_id": batch["batch_id"],
            "company_id": company["company_id"],
            "file_ids": file_ids,
            "job_id": job["job_id"],
            "events_url": f"/api/report-jobs/{job['job_id']}/events",
            "processing_status_url": f"/api/report-jobs/{job['job_id']}",
        }
    except CompanyRegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def list_companies(user_id: int = Depends(get_current_user)):
    companies = []
    for company in company_registry.list_companies(user_id):
        report_records = [
            file_manager.metadata.get("files", {}).get(file_id)
            for file_id in company.get("report_ids") or []
        ]
        company["report_count"] = sum(isinstance(record, dict) for record in report_records)
        company.pop("normalized_name", None)
        company.pop("last_error", None)
        companies.append(company)
    return {"status": "success", "companies": companies}


async def get_company(company_id: str, user_id: int = Depends(get_current_user)):
    company = company_registry.get_company(company_id, user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    reports = []
    for file_id in company.get("report_ids") or []:
        info = file_manager.metadata.get("files", {}).get(file_id)
        if not isinstance(info, dict):
            continue
        reports.append(
            {
                "file_id": file_id,
                "filename": info.get("original_name"),
                "report_year": info.get("report_year"),
                "status": info.get("status"),
                "page_count": info.get("page_count"),
                "upload_time": info.get("upload_time"),
            }
        )
    company.pop("normalized_name", None)
    company.pop("last_error", None)
    return {"status": "success", "company": company, "reports": reports}


async def get_company_assessment(
    company_id: str,
    scope: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user),
):
    company = company_registry.get_company(company_id, user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    outputs = list(company.get("assessment_outputs") or [])
    if not outputs:
        raise HTTPException(status_code=404, detail="Company assessment is not available")
    selected = None
    if scope:
        selected = next((item for item in outputs if item.get("scope_key") == scope), None)
    selected = selected or outputs[0]
    path = Path(file_manager.compliance_outputs) / str(selected.get("json_filename") or "")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Company assessment output is missing")
    return {
        "status": "success",
        "company_id": company_id,
        "analysis_version": company.get("analysis_version"),
        "stale": bool(company.get("stale")),
        "assessment": json.loads(path.read_text(encoding="utf-8")),
    }


async def retry_company_batch(
    batch_id: str,
    user_id: int = Depends(get_current_user),
):
    batch = company_registry.get_batch(batch_id, user_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Batch not found")
    failed_ids = list(batch.get("failed_file_ids") or [])
    company = company_registry.get_company(batch["company_id"], user_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if str(batch.get("status") or "") != "failed":
        raise HTTPException(status_code=409, detail="Only a failed batch can be retried")
    report_ids = list(company.get("report_ids") or [])
    if not report_ids:
        raise HTTPException(status_code=409, detail="Company has no reports to analyze")
    # Extraction failures retry only their failed reports. An aggregate-analysis
    # failure has no failed report IDs, so it reuses every persisted artifact and
    # starts directly at company-level retrieval and analysis.
    extraction_file_ids = failed_ids
    primary_file_id = failed_ids[0] if failed_ids else report_ids[0]
    job = create_report_job(
        file_id=primary_file_id,
        file_ids=failed_ids or report_ids,
        filename=company.get("company_name") or "Company reports",
        user_id=user_id,
        company_id=company["company_id"],
        batch_id=batch_id,
    )
    company_registry.update_batch(
        batch_id,
        job_id=job["job_id"],
        status="queued",
        failed_file_ids=[],
        error="",
    )
    get_executor().submit(
        _run_company_batch_job,
        job["job_id"],
        batch_id,
        company["company_id"],
        extraction_file_ids,
    )
    return {
        "status": "accepted",
        "batch_id": batch_id,
        "company_id": company["company_id"],
        "file_ids": failed_ids or report_ids,
        "job_id": job["job_id"],
        "events_url": f"/api/report-jobs/{job['job_id']}/events",
    }


def schedule_company_reanalysis(company_id: str, user_id: int) -> Optional[Dict[str, Any]]:
    """Queue aggregate analysis after a report is removed from a company."""
    company = company_registry.get_company(company_id, user_id)
    if not company or not (company.get("report_ids") or []):
        return None
    batch = company_registry.create_batch(
        company_id=company_id,
        user_id=user_id,
        upload_mode="reanalysis",
        file_ids=[],
        report_years={},
    )
    first_report_id = str((company.get("report_ids") or [""])[0])
    job = create_report_job(
        file_id=first_report_id,
        file_ids=list(company.get("report_ids") or []),
        filename=company.get("company_name") or "Company reports",
        user_id=user_id,
        company_id=company_id,
        batch_id=batch["batch_id"],
    )
    company_registry.update_batch(batch["batch_id"], job_id=job["job_id"])
    get_executor().submit(
        _run_company_batch_job,
        job["job_id"],
        batch["batch_id"],
        company_id,
        [],
    )
    return job


__all__ = [
    "get_company",
    "get_company_assessment",
    "list_companies",
    "retry_company_batch",
    "schedule_company_reanalysis",
    "upload_report_batch",
]
