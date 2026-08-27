"""Compliance analysis and assessment service functions."""

from .common import *  # noqa: F401,F403
from ..gpu_model_lifecycle import with_backend_model_task


@with_backend_model_task("analyze_compliance")
async def analyze_compliance():
    """
    执行合规分析
    
    Returns:
        合规评估结果
    """
    # 检查是否已加载报告和指标
    if not system_components["current_report"]:
        raise HTTPException(status_code=400, detail="No report loaded. Please upload a report first.")
    
    if not system_components["current_metrics"]:
        raise HTTPException(status_code=400, detail="No metrics loaded. Please upload metrics first.")
    
    try:
        # 执行双通道检索
        processor = system_components["metric_processor"]
        system_components["current_metrics"] = _prepare_metrics_for_retrieval(
            processor,
            system_components["current_metrics"],
        )
        retrieval_results = iter_metric_collection_results(
            system_components["current_report"],
            system_components["current_metrics"],
            config=system_components.get("config"),
        )
        
        # 执行披露推理
        disclosure_engine = system_components["disclosure_engine"]
        fw = system_components.get("current_framework")
        semi_label = system_components.get("current_semi_industry")
        if fw == "GRI":
            gs, gt = system_components.get("current_gri_sector"), system_components.get("current_gri_topic")
            semi_label = f"GRI {gs or ''} {gt or ''}".strip() or "GRI"
        assessment = disclosure_engine.analyze_compliance(
            retrieval_results,
            system_components["current_report"],
            system_components["current_report"].document_content.file_path,
            system_components["current_metrics"],  # 传入所有指标
            framework=fw,
            industry=system_components.get("current_industry"),
            semi_industry=semi_label
        )
        
        # 存储评估结果
        system_components["current_assessment"] = assessment
        
        # 更新聊天机器人上下文
        with _chatbot_ops_lock:
            system_components["chatbot"].load_context(
                system_components["current_report"],
                assessment
            )

        # 生成合规报告
        compliance_report = disclosure_engine.generate_compliance_report(assessment)
        
        # 保存报告（canonical location: uploads/outputs/markdown/）
        report_path = Path(file_manager.markdown_outputs) / f"compliance_report_{assessment.report_id}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(compliance_report, encoding="utf-8")
        
        # 保存JSON评估数据供前端使用（canonical: uploads/outputs/compliance_reports/）
        json_report_dir = Path(file_manager.compliance_outputs)
        json_report_dir.mkdir(parents=True, exist_ok=True)

        # Best-effort resolve file_id from current report path
        file_id = None
        try:
            current_path = str(system_components["current_report"].document_content.file_path)
            for fid, info in file_manager.metadata.get("files", {}).items():
                if info.get("file_path") == current_path:
                    file_id = fid
                    break
        except Exception:
            file_id = None
        if not file_id:
            file_id = str(getattr(assessment, "report_id", "unknown"))
        fw = system_components.get("current_framework")
        gs, gt = None, None
        if fw == "GRI":
            gs, gt = system_components.get("current_gri_sector"), system_components.get("current_gri_topic")
            sanitized_subindustry = _sanitize_compliance_filename_part(f"GRI_{gs or ''}_{gt or ''}" if (gs or gt) else "GRI_report")
        else:
            sanitized_subindustry = _sanitize_compliance_filename_part(system_components.get("current_semi_industry") or "report")
        json_report_path = json_report_dir / f"{sanitized_subindustry}_{file_id}_compliance.json"

        # 将评估数据转换为JSON格式（assessment_date 悉尼时间，filename = 报告名 + LLM 模型名）
        file_meta = file_manager.metadata.get("files", {}).get(file_id, {})
        report_name = file_meta.get("original_name") or file_meta.get("safe_filename") or file_id
        config = system_components.get("config")
        llm_model_name = getattr(config, "llm_model", None) if config else None
        assessment_json = _build_compliance_assessment_json(
            assessment,
            str(report_path),
            _compliance_result_filename(report_name, llm_model_name),
        )

        with open(json_report_path, "w", encoding="utf-8") as f:
            json.dump(assessment_json, f, indent=2, ensure_ascii=False)

        # For SASB, persist a second JSON in the original backend/data/sasb_metrics row shape.
        # Metric retrieval profiles remain retrieval-only and are not used for backend storage/display rows.
        if (fw or "").strip().upper() == "SASB" and assessment_json.get("sasb_metric_rows"):
            sasb_metrics_result_path = json_report_dir / f"{sanitized_subindustry}_{file_id}_sasb_metrics.json"
            with open(sasb_metrics_result_path, "w", encoding="utf-8") as f:
                json.dump(assessment_json["sasb_metric_rows"], f, indent=2, ensure_ascii=False)

        logger.info(f"Assessment JSON saved to: {json_report_path}")
        
        # Export results to Excel
        excel_path = None
        try:
            excel_exporter = ExcelExporter()
            
            # Prepare metric analyses for Excel export
            excel_metrics = []
            for analysis in assessment.metric_analyses:
                # Find corresponding metric for additional info
                metric_info = {}
                if system_components["current_metrics"]:
                    for metric in system_components["current_metrics"].metrics:
                        if metric.metric_id == analysis.metric_id or metric.metric_code == analysis.metric_code:
                            metric_info = {
                                "category": getattr(metric, 'sasb_category', analysis.category),
                                "unit": metric.unit or "",
                                "topic": getattr(metric, 'sasb_topic', ''),
                                "type": getattr(metric, 'sasb_type', ''),
                                "definition": getattr(metric, 'definition', '') or ''
                            }
                            break
                
                excel_metrics.append({
                    "metric_id": analysis.metric_code if hasattr(analysis, 'metric_code') else analysis.metric_id,
                    "metric_code": getattr(analysis, 'metric_code', '') or analysis.metric_id,
                    "metric_name": analysis.metric_name,
                    "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                    "reasoning": analysis.reasoning,
                    "value": getattr(analysis, 'value', None),
                    "year_values": list(getattr(analysis, 'year_values', None) or []),
                    "selected_year": getattr(analysis, 'selected_year', None),
                    "page": getattr(analysis, 'page', None),
                    "context": getattr(analysis, 'context', None),
                    "category": metric_info.get('category', getattr(analysis, 'category', '')),
                    "unit": metric_info.get('unit', getattr(analysis, 'unit', '')),
                    "topic": metric_info.get('topic', getattr(analysis, 'topic', '')),
                    "type": metric_info.get('type', getattr(analysis, 'type', '')),
                    "definition": metric_info.get('definition', getattr(analysis, 'definition', '')),
                })
            
            # Validate required metadata exists before export
            if not system_components.get("current_industry") or not system_components.get("current_semi_industry"):
                raise ValueError("Industry information missing. Cannot export Excel report.")

            excel_path = excel_exporter.export_analysis_results(
                metric_analyses=excel_metrics,
                industry=system_components["current_industry"],
                semi_industry=system_components["current_semi_industry"],
                company_name=system_components.get("current_company", "Unknown Company"),
                report_id=assessment.report_id
            )
            logger.info(f"Excel report exported to: {excel_path}")
        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}")
            # Don't fail the whole request if Excel export fails
        
        logger.info(f"Compliance analysis completed. Score: {assessment.overall_compliance_score:.2%}")
        
        result = {
            "status": "success",
            "assessment": {
                "report_id": assessment.report_id,
                "total_metrics": assessment.total_metrics_analyzed,
                "overall_score": assessment.overall_compliance_score,
                "disclosure_summary": assessment.disclosure_summary,
                "report_path": str(report_path)
            }
        }
        
        # Add Excel path if export was successful
        if excel_path:
            result["assessment"]["excel_path"] = str(excel_path)
        
        return result
        
    except Exception as e:
        logger.error(f"Error in compliance analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def get_assessment(limit: int = 0, year: Optional[int] = None):
    """
    获取当前的合规评估结果（内存态）。

    修复点：补齐前端需要的 page/value/unit/context/evidence_segments 等字段。
    limit=0 表示返回全部；否则返回前 limit 条。
    """
    if not system_components["current_assessment"]:
        raise HTTPException(status_code=404, detail="No assessment available")

    assessment = system_components["current_assessment"]

    def to_item(analysis: DisclosureAnalysis) -> dict:
        disclosure_status = analysis.disclosure_status.value if hasattr(analysis.disclosure_status, "value") else analysis.disclosure_status
        reasoning = analysis.reasoning
        page = getattr(analysis, "page", None)
        value = getattr(analysis, "value", None)
        year_values = list(getattr(analysis, "year_values", None) or [])
        selected_year = getattr(analysis, "selected_year", None)
        context = getattr(analysis, "context", None) or ""
        unit = getattr(analysis, "unit", None) or ""
        category = getattr(analysis, "category", "")
        topic = getattr(analysis, "topic", "")
        type_name = getattr(analysis, "type", "")
        definition = getattr(analysis, "definition", "")
        metric_code = getattr(analysis, "metric_code", "") or analysis.metric_id
        return {
            "metric_id": analysis.metric_id,
            "metric_name": analysis.metric_name,
            "metric_code": metric_code,
            "disclosure_status": disclosure_status,
            "reasoning": reasoning,
            "page": page,
            "value": value,
            "year_values": year_values,
            "selected_year": selected_year,
            "unit": unit,
            "category": category,
            "topic": topic,
            "type": type_name,
            "definition": definition,
            "Metric": analysis.metric_name,
            "Category": category,
            "Unit": unit,
            "Code": metric_code,
            "Topic": topic,
            "Type": type_name,
            "Definition": definition,
            "Value": value,
            "Year Values": year_values,
            "Selected Year": selected_year,
            "Page": page,
            "Context": context,
            "Disclosure Status": disclosure_status,
            "LLM Analysis": reasoning,
            "context": context,
            "evidence_segments": getattr(analysis, "evidence_segments", None) or [],
            "improvement_suggestions": getattr(analysis, "improvement_suggestions", None) or [],
        }

    items = [to_item(a) for a in (assessment.metric_analyses or [])]
    if limit and limit > 0:
        items = items[:limit]

    payload = {
        "report_id": assessment.report_id,
        "assessment_date": assessment.assessment_date.isoformat(),
        "total_metrics": assessment.total_metrics_analyzed,
        "overall_score": assessment.overall_compliance_score,
        "disclosure_summary": assessment.disclosure_summary,
        "metric_analyses": items,
    }
    return _apply_assessment_year_selection(
        _normalize_assessment_payload(payload),
        year,
    )


async def get_latest_assessment(
    year: Optional[int] = None,
    user_id: int = Depends(get_current_user),
):
    """
    获取当前用户最新的合规评估结果（从JSON文件）

    Returns:
        最新的评估结果
    """
    try:
        canonical_dir = Path(file_manager.compliance_outputs)
        legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # legacy backend/outputs

        # 从元数据中过滤当前用户的报告
        user_files = file_manager.list_user_files(user_id, file_type="report")
        if not user_files:
            return {
                "report_id": "unknown",
                "assessment_date": datetime.now().isoformat(),
                "total_metrics": 0,
                "overall_score": 0,
                "disclosure_summary": {},
                "metric_analyses": [],
                "status": "not_analyzed",
                "message": "No analysis reports available"
            }

        # 按上传时间倒序，找到第一个存在合规 JSON 的文件（支持 Subindustry_fileid_compliance.json 命名）
        for f in sorted(user_files, key=lambda x: x["upload_time"], reverse=True):
            json_file = _find_assessment_json_path(f["file_id"], f)
            if json_file and json_file.exists():
                logger.info(f"Loading latest assessment for user {user_id} from: {json_file}")
                with open(json_file, "r", encoding="utf-8") as fp:
                    return _apply_assessment_year_selection(
                        _normalize_assessment_payload(json.load(fp)),
                        year,
                    )

        # 若用户有文件但尚未生成合规结果
        return {
            "report_id": "unknown",
            "assessment_date": datetime.now().isoformat(),
            "total_metrics": 0,
            "overall_score": 0,
            "disclosure_summary": {},
            "metric_analyses": [],
            "status": "not_analyzed",
            "message": "No analysis reports available"
        }

    except Exception as e:
        logger.error(f"Failed to get latest assessment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get latest assessment: {str(e)}")


async def get_user_history(
    file_type: Optional[str] = None,
    status: Optional[str] = None,
    user_id: int = Depends(get_current_user)
):
    """
    获取当前用户的历史记录
    
    Args:
        file_type: 文件类型过滤 (可选)
        status: 状态过滤 (可选)
        user_id: 当前用户ID (从token自动获取)
        
    Returns:
        用户的历史文件列表
    """
    try:
        files = file_manager.list_user_files(user_id, file_type=file_type, status=status)
        
        return {
            "status": "success",
            "user_id": user_id,
            "files": files,
            "total_count": len(files)
        }
    except Exception as e:
        logger.error(f"Error getting user history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def list_assessment_scopes_for_file(file_id: str, user_id: int = Depends(get_current_user)):
    """List per-scope compliance outputs when upload used multiple scopeSlugs (manifest)."""
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    canonical_dir = Path(file_manager.compliance_outputs)
    m = _load_compliance_manifest(canonical_dir, file_id)
    if not m:
        return {
            "file_id": file_id,
            "outputs": [],
            "default_scope_key": None,
            "expected_scope_keys": [],
            "pending_scope_keys": [],
        }
    outs = m.get("outputs") or []
    exp = m.get("expected_scope_keys")
    expected = exp if isinstance(exp, list) else []
    done_keys = {str(o.get("scope_key", "")) for o in outs if isinstance(o, dict)}
    pending = [k for k in expected if str(k) not in done_keys]
    return {
        "file_id": file_id,
        "framework": m.get("framework"),
        "default_scope_key": m.get("default_scope_key"),
        "outputs": outs,
        "expected_scope_keys": expected,
        "pending_scope_keys": pending,
    }


async def get_assessment_by_file(
    file_id: str,
    user_id: int = Depends(get_current_user),
    scope: Optional[str] = None,
    year: Optional[int] = None,
    compact: bool = False,
):
    """
    根据文件ID获取合规评估结果（从JSON文件）(只能访问自己的文件)

    Args:
        file_id: 文件ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        评估结果
    """
    try:
        # 检查文件是否属于当前用户
        file_info = file_manager.get_file_info(file_id, user_id=user_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found or access denied")
        # --- Locate assessment JSON ---
        # Canonical location:
        #   uploads/outputs/compliance_reports/{file_id}_compliance.json
        # Legacy location (older builds):
        #   backend/outputs/*.json
        safe_filename = str(file_info.get("safe_filename") or "")
        base_name = Path(safe_filename).stem if safe_filename else ""

        canonical_dir = Path(file_manager.compliance_outputs)
        legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # backend/outputs

        json_file = _json_path_from_manifest(canonical_dir, file_id, scope)
        if json_file is None or not json_file.is_file():
            json_file = None

        search_dirs = [canonical_dir]
        if legacy_dir.exists():
            search_dirs.append(legacy_dir)

        if json_file is None:
            candidate_names = [f"{file_id}_compliance.json"]
            if base_name:
                candidate_names.append(f"{base_name}_compliance.json")

            for d in search_dirs:
                for name in candidate_names:
                    p = d / name
                    if p.exists():
                        json_file = p
                        break
                if json_file is not None:
                    break

        if json_file is None:
            # Best-effort fuzzy match (keep strict to compliance-like names to avoid false positives)
            fuzzy_patterns = [
                f"*{file_id}*compliance*.json",
                f"*{file_id}*_compliance.json",
            ]
            if base_name:
                fuzzy_patterns.extend([
                    f"*{base_name}*compliance*.json",
                    f"*{base_name}*_compliance.json",
                ])
            matches = []
            for d in search_dirs:
                for pat in fuzzy_patterns:
                    matches.extend(list(d.glob(pat)))
            matches = [m for m in matches if m.is_file()]
            if matches:
                matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                json_file = matches[0]

        if json_file is None:
            logger.warning(f"No JSON assessment found for file_id: {file_id}")
            return {
                "report_id": file_id,
                "assessment_date": datetime.now().isoformat(),
                "total_metrics": 0,
                "overall_score": 0,
                "disclosure_summary": {},
                "metric_analyses": [],
                "status": "not_analyzed",
                "message": "No analysis available for this file yet"
            }

        logger.info(f"Loading assessment from: {json_file}")

        with open(json_file, 'r', encoding='utf-8') as f:
            assessment_data = json.load(f)

        assessment_payload = _apply_assessment_year_selection(
            _normalize_assessment_payload(assessment_data),
            year,
        )
        if compact:
            return _compact_assessment_payload(assessment_payload)
        return assessment_payload

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to load assessment for {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load assessment: {str(e)}")
