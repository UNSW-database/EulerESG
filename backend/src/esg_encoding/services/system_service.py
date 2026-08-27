"""System/status service functions."""

from ..environment import load_backend_environment
from .common import *  # noqa: F401,F403


async def startup_event():
    """Initialize system components on startup"""
    # Keep direct Uvicorn/IDE startup independent from the current directory.
    load_backend_environment()

    # Recover disk space left by interrupted/OOM OCR jobs. This only touches
    # expired children of the dedicated work roots, never report-owned assets.
    from ..paddleocr_cleanup import cleanup_stale_paddleocr_artifacts
    cleanup_stale_paddleocr_artifacts()
    file_manager.recover_interrupted_reports()
    
    # Create default configuration
    config = ProcessingConfig()
    
    # Read LLM configuration from environment variables
    if os.getenv("LLM_API_KEY"):
        config.llm_api_key = os.getenv("LLM_API_KEY")
    if os.getenv("LLM_BASE_URL"):
        config.llm_base_url = os.getenv("LLM_BASE_URL")
    if os.getenv("LLM_MODEL"):
        config.llm_model = os.getenv("LLM_MODEL")

    # OCR configuration (optional; PaddleOCR must be installed separately)
    # Example:
    #   ENABLE_OCR=true
    #   OCR_LANG=ch
    #   OCR_USE_GPU=false
    #   OCR_RENDER_ZOOM=2.0
    #   OCR_PAGE_TEXT_THRESHOLD=50
    #   OCR_MIN_TEXT_LEN=12
    #   OCR_IMAGE_MIN_AREA=50000
    #   OCR_MAX_IMAGES_PER_PAGE=4
    #   ENABLE_OCR_TABLE=true
    #   OCR_TABLE_MAX_PER_IMAGE=2
    if os.getenv("ENABLE_OCR") is not None:
        config.enable_ocr = os.getenv("ENABLE_OCR", "true").lower() in ("1", "true", "yes", "y")
    if os.getenv("OCR_LANG"):
        config.ocr_lang = os.getenv("OCR_LANG")  # ch / en
    if os.getenv("OCR_USE_GPU") is not None:
        config.ocr_use_gpu = os.getenv("OCR_USE_GPU", "false").lower() in ("1", "true", "yes", "y")
    if os.getenv("OCR_RENDER_ZOOM"):
        try:
            config.ocr_render_zoom = float(os.getenv("OCR_RENDER_ZOOM", "2.0"))
        except Exception:
            pass
    if os.getenv("OCR_PAGE_TEXT_THRESHOLD"):
        try:
            config.ocr_page_text_threshold = int(os.getenv("OCR_PAGE_TEXT_THRESHOLD", "50"))
        except Exception:
            pass
    if os.getenv("OCR_MIN_TEXT_LEN"):
        try:
            config.ocr_min_text_len = int(os.getenv("OCR_MIN_TEXT_LEN", "12"))
        except Exception:
            pass
    if os.getenv("OCR_IMAGE_MIN_AREA"):
        try:
            config.ocr_image_min_area = int(os.getenv("OCR_IMAGE_MIN_AREA", "50000"))
        except Exception:
            pass
    if os.getenv("OCR_MAX_IMAGES_PER_PAGE"):
        try:
            config.ocr_max_images_per_page = int(os.getenv("OCR_MAX_IMAGES_PER_PAGE", "4"))
        except Exception:
            pass
    if os.getenv("ENABLE_OCR_TABLE") is not None:
        config.enable_ocr_table = os.getenv("ENABLE_OCR_TABLE", "true").lower() in ("1", "true", "yes", "y")
    if os.getenv("OCR_TABLE_MAX_PER_IMAGE"):
        try:
            config.ocr_table_max_per_image = int(os.getenv("OCR_TABLE_MAX_PER_IMAGE", "2"))
        except Exception:
            pass

    
    # Initialize components
    system_components["config"] = config
    system_components["report_encoder"] = ReportEncoder(config)
    system_components["metric_processor"] = MetricProcessor(config)
    system_components["dual_retriever"] = DualChannelRetriever(config)
    system_components["disclosure_engine"] = DisclosureInferenceEngine(config)
    system_components["chatbot"] = ESGChatbot(config)

    # Share the already-loaded embedding model with chatbot (avoid double load)
    try:
        system_components["chatbot"].set_embedding_model(
            system_components["report_encoder"].embedder.model
        )
    except Exception as e:
        logger.warning(f"Failed to share embedding model with chatbot: {e}")

    # Share embedding model with CrossAnalysis module (avoid first-request model load timeout)
    try:
        from .. import cross_analysis as _ca
        _ca._model = system_components["report_encoder"].embedder.model
    except Exception as e:
        logger.warning(f"Failed to share embedding model with cross_analysis: {e}")

    # Enable HippoRAG augmentation (safe even if HippoRAG not installed; patched_search falls back)
    try:
        enable_hipporag(system_components["chatbot"], config)
        from .. import cross_analysis as _ca

        _ca.set_hipporag_retriever(
            getattr(system_components["chatbot"], "_hipporag_retriever", None)
        )
    except Exception as e:
        logger.warning(f"Failed to enable HippoRAG (will fallback to base retrieval): {e}")
    
    logger.info("ESG Analysis System initialized successfully")


async def root():
    """API root path"""
    return {
        "message": "ESG Analysis System API",
        "version": "1.0.0",
        "endpoints": {
            "upload_report": "/api/upload-report",
            "upload_metrics": "/api/upload-metrics",
            "analyze_compliance": "/api/analyze-compliance",
            "chat": "/api/chat",
            "get_assessment": "/api/assessment",
            "get_session_history": "/api/chat/history/{session_id}"
        }
    }


async def get_system_status():
    """
    获取系统状态
    
    Returns:
        系统状态信息
    """
    storage_stats = file_manager.get_storage_stats()
    
    return {
        "status": "operational",
        "components": {
            "report_loaded": system_components["current_report"] is not None,
            "metrics_loaded": system_components["current_metrics"] is not None,
            "assessment_available": system_components["current_assessment"] is not None,
            "llm_configured": system_components["config"].llm_api_key is not None
        },
        "report_info": {
            "document_id": system_components["current_report"].document_id if system_components["current_report"] else None,
            "segments_count": len(system_components["current_report"].document_content.segments) if system_components["current_report"] else 0
        } if system_components["current_report"] else None,
        "metrics_info": {
            "collection_id": system_components["current_metrics"].collection_id if system_components["current_metrics"] else None,
            "metrics_count": len(system_components["current_metrics"].metrics) if system_components["current_metrics"] else 0
        } if system_components["current_metrics"] else None,
        "storage_stats": storage_stats
    }


async def get_gri_options(user_id: int = Depends(get_current_user)):
    """
    Return GRI sector and topic options for framework dropdowns.
    sectors: [{ slug, label }]; topicsBySector: { sector_slug: [{ slug, label }] }.
    """
    return _get_gri_sectors_and_topics()


async def cleanup_orphaned_reports():
    """
    清理孤儿报告文件（没有对应元数据的报告）
    """
    try:
        # Active IDs / base names derived from metadata
        active_files = list((file_manager.metadata or {}).get("files", {}).values())
        active_file_ids = {str(x.get("file_id") or "").strip() for x in active_files if str(x.get("file_id") or "").strip()}
        active_base_names = {
            Path(str(x.get("safe_filename") or "")).stem
            for x in active_files
            if str(x.get("safe_filename") or "").strip()
        }

        def _is_orphan(name: str) -> bool:
            return (not any(fid and fid in name for fid in active_file_ids)) and (not any(bn and bn in name for bn in active_base_names))

        deleted_items = []

        # Canonical output locations under uploads/
        scan_specs = [
            (Path(file_manager.markdown_outputs), "*.md"),
            (Path(file_manager.compliance_outputs), "*.json"),
            (Path(file_manager.embeddings_outputs), "*.*"),
        ]

        # Legacy output location (older builds): backend/outputs
        legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"
        if legacy_outputs_dir.exists():
            scan_specs.append((legacy_outputs_dir, "*.md"))
            scan_specs.append((legacy_outputs_dir, "*compliance*.json"))

        for d, pat in scan_specs:
            if not d.exists():
                continue
            for p in d.glob(pat):
                if not p.is_file():
                    continue
                if _is_orphan(p.name):
                    try:
                        p.unlink()
                        deleted_items.append(str(p))
                    except Exception:
                        # Best-effort cleanup: ignore individual failures
                        pass
        
        return {
            "status": "success",
            "message": f"Cleaned up {len(deleted_items)} orphaned output files",
            "deleted_files": deleted_items
        }
    
    except Exception as e:
        logger.error(f"Failed to cleanup orphaned reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")


async def health_check():
    """
    Health check endpoint to monitor system status
    """
    try:
        import time
        
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "services": {
                "api": "running",
                "llm_client": bool(system_components.get("llm_client")),
                "embedding_model": bool(system_components.get("content_embedder"))
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }
