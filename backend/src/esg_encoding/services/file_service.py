"""File management service functions."""

import shutil

from .common import *  # noqa: F401,F403
from ..visual_assets import invalidate_visual_manifest, load_visual_manifest, safe_asset_path, visual_asset_dir


_INTERNAL_OCR_FILE_FIELDS = {
    "paddle_progress",
    "paddle_job_id",
    "running_batches",
    "total_units",
    "units_done",
    "units_success",
    "units_failed",
    "units_running",
    "units_queued",
    "pages_done",
    "pages_success",
    "pages_failed",
    "processing_total_pages",
    "processing_pages_done",
    "processing_pages_success",
    "processing_pages_failed",
    "processing_total_units",
    "processing_units_done",
    "processing_units_success",
    "processing_units_failed",
    "processing_units_running",
    "processing_units_queued",
    "processing_page_batch_size",
    "processing_running_batches",
}


def _expose_internal_file_details() -> bool:
    return str(os.getenv("REPORT_JOB_EXPOSE_INTERNAL_DETAILS", "false")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _public_file_record(file_info: Dict[str, Any]) -> Dict[str, Any]:
    if _expose_internal_file_details():
        return file_info
    return {k: v for k, v in file_info.items() if k not in _INTERNAL_OCR_FILE_FIELDS}


async def list_files(
    file_type: Optional[str] = None, 
    status: Optional[str] = None,
    user_id: int = Depends(get_current_user)
):
    """
    列出当前用户的文件
    
    Args:
        file_type: 文件类型过滤 ('report', 'metrics')
        status: 状态过滤 ('pending', 'processed', 'failed', 'uploaded')
        user_id: 当前用户ID (从token自动获取)
        
    Returns:
        文件列表 (只返回当前用户的文件)
    """
    try:
        if file_type:
            files = file_manager.list_files_by_type(file_type, status, user_id=user_id)
        else:
            all_files = []
            for ftype in ['report', 'metrics']:
                all_files.extend(file_manager.list_files_by_type(ftype, status, user_id=user_id))
            files = sorted(all_files, key=lambda x: x["upload_time"], reverse=True)

        files = _enrich_file_records_with_scope_progress(file_manager, files)
        public_files = [_public_file_record(f) for f in files]

        return {
            "status": "success",
            "files": public_files,
            "total_count": len(public_files)
        }
        
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_file_info(file_id: str, user_id: int = Depends(get_current_user)):
    """
    获取文件详细信息 (只能获取自己的文件)

    Args:
        file_id: 文件ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        文件信息
    """
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")

    return {
        "status": "success",
        "file_info": _public_file_record(file_info)
    }


async def serve_pdf(file_id: str, user_id: int = Depends(get_current_user)):
    """
    提供PDF文件下载/查看服务（必须登录且只能访问自己的文件）

    Args:
        file_id: 文件ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        PDF文件响应
    """
    # 只允许访问属于当前用户的文件
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")

    file_path = Path(file_info["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on server")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_info.get("safe_filename", "report.pdf"),
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


async def list_visual_assets(
    file_id: str,
    request: Request,
    user_id: int = Depends(get_current_user),
):
    """Return public visual metadata after checking report ownership."""
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    manifest = load_visual_manifest(file_info["file_path"])
    if manifest is None:
        return JSONResponse({"file_id": file_id, "assets": []})
    assets = []
    for item in manifest.get("assets", []):
        if not isinstance(item, dict) or not item.get("asset_id"):
            continue
        public = {k: item.get(k) for k in (
            "asset_id", "mime_type", "page_number", "bbox", "caption", "summary",
            "ocr_text", "chart_data", "confidence", "parser_version"
        )}
        public["asset_url"] = f"/api/files/{file_id}/visual-assets/{item['asset_id']}"
        assets.append(public)
    payload = {"file_id": file_id, "assets": assets}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    etag = '"' + hashlib.sha256(encoded).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"})
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate"})


async def serve_visual_asset(
    file_id: str,
    asset_id: str,
    user_id: int = Depends(get_current_user),
):
    """Serve one allow-listed crop; never accept a filesystem path from clients."""
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    resolved = safe_asset_path(file_info["file_path"], asset_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Visual asset not found")
    path, record = resolved
    return FileResponse(
        path=str(path),
        media_type=str(record.get("mime_type") or "application/octet-stream"),
        filename=path.name,
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "ETag": '"' + str(record.get("sha256") or asset_id) + '"',
        },
    )


async def delete_file(
    file_id: str,
    scope_key: Optional[str] = None,
    user_id: int = Depends(get_current_user),
):
    """
    删除文件。若提供 scope_key（多子范围上传中的一行），只删除该 scope 的合规 JSON/XLSX/MD 并更新 manifest，
    保留 PDF 与其它 scope；若该 scope 是清单中最后一个配置项，则退化为整份报告删除。
    """
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")

    # get_file_info also supports legacy filename aliases. All destructive
    # operations must use the canonical metadata key, otherwise the artifacts
    # can be removed before ``del metadata[alias]`` raises a KeyError.
    canonical_file_id = str(
        file_manager.resolve_file_id(file_id, user_id=user_id)
        or file_info.get("file_id")
        or file_id
    )

    sk = (scope_key or "").strip()
    if sk and file_info.get("file_type") == "report":
        partial = _try_delete_one_scope_only(canonical_file_id, file_info, sk)
        if partial is not None:
            return partial

    company_id = str(file_info.get("company_id") or "").strip()

    try:
        deleted_items = []
        
        # 1. 删除主PDF文件
        file_path = Path(file_info["file_path"])
        if file_path.exists():
            file_path.unlink()
            deleted_items.append(f"PDF文件: {file_path.name}")
        
        # 2. 删除提取的Markdown文件
        safe_filename = str(file_info.get("safe_filename") or "")
        stem = Path(safe_filename).stem if safe_filename else file_path.stem
        markdown_paths = [
            # Most common: saved next to the PDF (pending/processed/failed)
            file_path.parent / f"{stem}_extracted.md",
            # Optional: centralized markdown outputs
            Path(file_manager.markdown_outputs) / f"{stem}_extracted.md",
        ]

        for md_path in markdown_paths:
            if md_path.exists():
                md_path.unlink()
                deleted_items.append(f"Markdown文件: {md_path.name}")

        # 3. 删除嵌入向量文件（以 FileManager 的落盘规则为准）
        asset_dir = visual_asset_dir(file_path)
        if asset_dir.exists():
            invalidate_visual_manifest(file_path)
            shutil.rmtree(asset_dir)
            deleted_items.append(f"Visual assets: {asset_dir.name}")

        for artifact_name in file_manager.delete_report_artifacts(
            canonical_file_id
        ):
            deleted_items.append(f"嵌入文件: {artifact_name}")

        embeddings_paths = [
            # Legacy variants (best-effort)
            Path(file_manager.embeddings_outputs) / f"{stem}_embeddings.json",
            Path(file_manager.embeddings_outputs) / f"{stem}_embeddings.npy",
        ]

        for emb_path in embeddings_paths:
            if emb_path.exists():
                emb_path.unlink()
                deleted_items.append(f"嵌入文件: {emb_path.name}")

        # 4. 删除合规输出：uploads/outputs/compliance_reports（JSON / XLSX / manifest 及一切含 file_id 的合规文件）
        _unlink_compliance_reports_dir_for_file_id(canonical_file_id, stem, deleted_items)
        _unlink_compliance_markdown_for_file_id(canonical_file_id, deleted_items)

        # Legacy location (older builds): backend/outputs
        legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"
        if legacy_outputs_dir.exists():
            legacy_paths: List[Path] = []
            legacy_paths.extend(legacy_outputs_dir.glob(f"*{canonical_file_id}*.md"))
            legacy_paths.extend(legacy_outputs_dir.glob(f"*{canonical_file_id}*compliance*.json"))
            legacy_paths.extend(legacy_outputs_dir.glob(f"*{canonical_file_id}*compliance*.xlsx"))
            for comp_path in legacy_paths:
                if comp_path.is_file():
                    try:
                        comp_path.unlink()
                        deleted_items.append(f"合规报告(legacy): {comp_path.name}")
                    except Exception as e:
                        logger.warning(f"Failed to remove legacy output {comp_path}: {e}")

        # 5. 清理系统组件中的相关数据
        # NOTE: ReportContent.document_id = doc_<stem>_<hash> (not the file_id)
        cleared_current = False
        current_report = system_components.get("current_report")
        if current_report and stem and hasattr(current_report, "document_id") and stem in str(getattr(current_report, "document_id", "")):
            system_components["current_report"] = None
            cleared_current = True
            deleted_items.append("内存中的报告内容")
        
        current_assessment = system_components.get("current_assessment")
        if current_assessment and hasattr(current_assessment, "report_id") and str(getattr(current_assessment, "report_id", "")) == canonical_file_id:
            system_components["current_assessment"] = None
            cleared_current = True
            deleted_items.append("内存中的评估结果")

        # Clear derived caches only when they are tied to the cleared current context
        if cleared_current:
            system_components["current_metrics"] = None
            system_components["current_framework"] = None
            system_components["current_industry"] = None
            system_components["current_semi_industry"] = None
            system_components["current_gri_sector"] = None
            system_components["current_gri_topic"] = None
            system_components["current_company"] = None
        
        # 6. 清理聊天机器人上下文
        if system_components.get("chatbot"):
            chatbot = system_components["chatbot"]
            with _chatbot_ops_lock:
                if getattr(chatbot, "report_content", None) is not None:
                    rc = chatbot.report_content
                    if stem and hasattr(rc, "document_id") and stem in str(
                        getattr(rc, "document_id", "")
                    ):
                        chatbot.report_content = None
                        chatbot.compliance_assessment = None
                        deleted_items.append("聊天机器人上下文")
        
        # 7. 从元数据中删除
        # pop + locked atomic persistence makes duplicate/concurrent DELETE
        # requests safe and prevents a false success when disk persistence fails.
        file_manager.remove_file_metadata(canonical_file_id)

        if company_id:
            try:
                from ..company_registry import company_registry
                from .company_report_service import schedule_company_reanalysis

                remaining = company_registry.remove_report(company_id, canonical_file_id, user_id)
                if remaining and remaining.get("report_ids"):
                    schedule_company_reanalysis(company_id, user_id)
            except Exception as company_exc:
                logger.warning(
                    f"Company result refresh could not be queued after deleting {canonical_file_id}: "
                    f"{company_exc}"
                )
        deleted_items.append("文件元数据")
        
        logger.info(f"File and related data deleted: {canonical_file_id}")
        logger.info(f"Deleted items: {', '.join(deleted_items)}")
        
        return {
            "status": "success",
            "message": "File and all related data deleted successfully",
            "deleted_items": deleted_items
        }
        
    except Exception as e:
        logger.error(f"Error deleting file and related data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def cleanup_old_files(days: int = 30):
    """
    清理旧文件
    
    Args:
        days: 保留天数
        
    Returns:
        清理结果
    """
    try:
        cleaned_count = file_manager.cleanup_old_files(days)
        return {
            "status": "success",
            "message": f"Cleaned up {cleaned_count} files older than {days} days"
        }
        
    except Exception as e:
        logger.error(f"Error cleaning up files: {e}")
        raise HTTPException(status_code=500, detail=str(e))
