"""
ESG System API Endpoints
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List
import os
import json
from pathlib import Path
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

from .models import (
    ProcessingConfig,
    ChatRequest,
    ChatResponse,
    ComplianceAssessment,
    MetricCollection
)
from .report_encoder import ReportEncoder
from .metric_processor import MetricProcessor
from .dual_channel_retrieval import DualChannelRetriever
from .disclosure_inference import DisclosureInferenceEngine
from .esg_chatbot import ESGChatbot
from .file_manager import file_manager
from .excel_exporter import ExcelExporter

# Create FastAPI application
app = FastAPI(
    title="ESG Analysis System API",
    description="Complete ESG report analysis and compliance assessment system",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:3001", "http://127.0.0.1:3001", "http://localhost:3002", "http://127.0.0.1:3002", "http://localhost:3003", "http://127.0.0.1:3003"],  # Next.js frontend addresses
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables to store system components
system_components = {
    "config": None,
    "report_encoder": None,
    "metric_processor": None,
    "dual_retriever": None,
    "disclosure_engine": None,
    "chatbot": None,
    "current_report": None,
    "current_assessment": None,
    "current_metrics": None,
    "current_industry": None,  # Store main industry
    "current_semi_industry": None,  # Store sub-industry
    "current_company": None  # Store company name
}


def _parse_compliance_report(content: str, report_id: str) -> dict:
    """
    Parse compliance report content
    
    Args:
        content: Report text content
        report_id: Report ID
        
    Returns:
        dict: Parsed assessment results
    """
    import re
    
    metric_analyses = []
    overall_score = 0
    total_metrics = 0
    disclosure_summary = {"fully_disclosed": 0, "partially_disclosed": 0, "not_disclosed": 0}
    
    # Extract basic information - fix regex, asterisks need escaping
    # Extract overall compliance score
    score_match = re.search(r'\*\*Overall Compliance Score\*\*:\s*([\d.]+)%', content)
    if not score_match:
        # Try another format (without asterisks)
        score_match = re.search(r'Overall Compliance Score.*?(\d+\.?\d*)%', content)
    if score_match:
        overall_score = float(score_match.group(1)) / 100
    
    # Extract analyzed metrics count
    metrics_match = re.search(r'\*\*Analyzed Metrics\*\*:\s*(\d+)', content)
    if not metrics_match:
        # Try another format (without asterisks)
        metrics_match = re.search(r'Analyzed Metrics.*?(\d+)', content)
    if metrics_match:
        total_metrics = int(metrics_match.group(1))
    
    # Extract disclosure statistics - fix regex
    fully_match = re.search(r'Fully Disclosed.*?\|\s*(\d+)', content)
    partial_match = re.search(r'Partially Disclosed.*?\|\s*(\d+)', content)  
    not_match = re.search(r'Not Disclosed.*?\|\s*(\d+)', content)
    
    if fully_match:
        disclosure_summary["fully_disclosed"] = int(fully_match.group(1))
    if partial_match:
        disclosure_summary["partially_disclosed"] = int(partial_match.group(1))
    if not_match:
        disclosure_summary["not_disclosed"] = int(not_match.group(1))
    
    # Process by lines
    lines = content.split('\n')
    current_status = ""
    
    for i, line in enumerate(lines):
        line = line.strip()
        
        # Determine current status - fix matching conditions
        if line.startswith('### '):
            if 'Fully Disclosed' in line:
                current_status = 'fully_disclosed'
            elif 'Partially Disclosed' in line:
                current_status = 'partially_disclosed'
            elif 'Not Disclosed' in line:
                current_status = 'not_disclosed'
            continue
        
        # Process metric lines - only process when status is available
        if line.startswith('#### ') and current_status:
            metric_info = line[5:]  # 去掉 "#### "
            
            # Skip empty metric lines
            if not metric_info.strip():
                continue
            
            # Extract metric name and code
            metric_match = re.search(r'^(.+?)\s*\(([^)]+)\)\s*$', metric_info)
            if metric_match:
                metric_name = metric_match.group(1).strip()
                metric_id = metric_match.group(2).strip()
            else:
                metric_name = metric_info.strip()
                metric_id = metric_info.strip()
            
            # Find analysis reasoning (in following lines)
            reasoning = ""
            for j in range(i+1, min(i+10, len(lines))):  # 增加搜索范围
                if j < len(lines):
                    next_line = lines[j].strip()
                    if 'Analysis Reasoning' in next_line and ':' in next_line:
                        # Extract analysis reasoning, may span multiple lines
                        reasoning_start = next_line.find(':') + 1
                        reasoning = next_line[reasoning_start:].strip()
                        
                        # Continue reading subsequent lines until next paragraph
                        k = j + 1
                        while k < len(lines) and k < j + 20:  # Limit search range
                            follow_line = lines[k].strip()
                            if follow_line.startswith('-') or follow_line.startswith('#') or follow_line.startswith('**'):
                                break
                            if follow_line:
                                reasoning += " " + follow_line
                            k += 1
                        break
            
            # Only add valid metrics with deduplication check
            if metric_name and metric_name.strip():
                 # Check if same metric already exists (based on metric_id only)
                 duplicate_found = False
                 for existing in metric_analyses:
                     if existing["metric_id"] == metric_id:
                         duplicate_found = True
                         break
                 
                 if not duplicate_found:
                     # Extract page information from Evidence Segments
                     page_info = None
                     for k in range(i+1, min(i+15, len(lines))):
                         if k < len(lines):
                             evidence_line = lines[k].strip()
                             if 'Evidence Segments' in evidence_line and ':' in evidence_line:
                                 # Extract page from segment IDs like "P102_T000, P024_S001"
                                 segments_text = evidence_line.split(':', 1)[1].strip()
                                 import re
                                 page_matches = re.findall(r'P(\d+)_', segments_text)
                                 if page_matches:
                                     # Use the first page found
                                     page_info = int(page_matches[0])
                                 break
                     
                     # Try to extract SASB fields from the context (best effort)
                     category = "Unknown"
                     unit = ""
                     metric_type = "Disclosure Topics & Metrics"
                     
                     # Simple heuristics based on metric name
                     if any(word in metric_name.lower() for word in ['total', 'number', 'percentage', 'amount']):
                         category = "Quantitative"
                     elif any(word in metric_name.lower() for word in ['discussion', 'description', 'approach']):
                         category = "Discussion and Analysis"
                     
                     # Extract unit if present in metric name
                     unit_matches = re.search(r'\(([^)]+)\)', metric_name)
                     if unit_matches:
                         potential_unit = unit_matches.group(1)
                         # Check if it looks like a unit (contains % or common unit words)
                         if any(unit_word in potential_unit.lower() for unit_word in ['%', 'percentage', 'gj', 'm³', 'cubic', 'number', 'currency']):
                             unit = potential_unit
                     
                     metric_analyses.append({
                         "metric_id": metric_id,
                         "metric_name": metric_name,
                         "disclosure_status": current_status,
                         "reasoning": reasoning[:500] if reasoning else "Analysis reasoning not found",  # Limit length
                         "unit": unit,
                         "category": category,
                         "topic": "General", 
                         "type": metric_type,
                         "page": page_info,
                         "context": None
                     })
                 else:
                     logger.warning(f"Skipping duplicate metric parsing: {metric_name} ({metric_id})")
    
    logger.info(f"Parsed {len(metric_analyses)} metrics from report {report_id}")
    logger.info(f"Total metrics from header: {total_metrics}, Overall score: {overall_score}")
    
    return {
        "report_id": report_id,
        "assessment_date": datetime.now().isoformat(),
        "total_metrics": total_metrics,
        "overall_score": overall_score,
        "disclosure_summary": disclosure_summary,
        "metric_analyses": metric_analyses
    }


@app.on_event("startup")
async def startup_event():
    """Initialize system components on startup"""
    # Load environment variables from .env file
    load_dotenv()
    
    # Create default configuration
    config = ProcessingConfig()
    
    # Read LLM configuration from environment variables
    if os.getenv("LLM_API_KEY"):
        config.llm_api_key = os.getenv("LLM_API_KEY")
    if os.getenv("LLM_BASE_URL"):
        config.llm_base_url = os.getenv("LLM_BASE_URL")
    
    # Initialize components
    system_components["config"] = config
    system_components["report_encoder"] = ReportEncoder(config)
    system_components["metric_processor"] = MetricProcessor(config)
    system_components["dual_retriever"] = DualChannelRetriever(config)
    system_components["disclosure_engine"] = DisclosureInferenceEngine(config)
    system_components["chatbot"] = ESGChatbot(config)
    
    logger.info("ESG Analysis System initialized successfully")


@app.get("/")
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


@app.post("/api/upload-report")
async def upload_report(
    file: UploadFile = File(...),
    industry: Optional[str] = Form(None),
    semiIndustry: Optional[str] = Form(None),
    framework: Optional[str] = Form(None)
):
    """
    Upload and process ESG report
    
    Args:
        file: PDF file
        industry: Main industry classification (optional)
        semiIndustry: Sub-industry (for SASB metrics selection)
        framework: Framework selection (SASB/GRI/TCFD)
        
    Returns:
        Processing results, including complete processing chain output (report processing + metrics matching + classification + knowledge base update)
    """
    # ===== DEBUG: Function called =====
    logger.info(f"=== UPLOAD_REPORT ENDPOINT CALLED ===")
    logger.info(f"File: {file.filename}")
    logger.info(f"Framework: {framework}")
    logger.info(f"Industry: {industry}")
    logger.info(f"SemiIndustry: {semiIndustry}")
    logger.info(f"=== END DEBUG ===")
    
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    try:
        logger.info("=== STARTING FILE PROCESSING ===")
        # Read file content
        content = await file.read()
        logger.info(f"File content read successfully, size: {len(content)} bytes")
        
        # Save file using file manager
        logger.info("Saving file using file manager...")
        file_info = file_manager.save_uploaded_file(
            file_content=content,
            filename=file.filename,
            file_type="report",
            industry=industry,
            framework=framework,
            semi_industry=semiIndustry
        )
        logger.info(f"File saved at: {file_info['file_path']}")
        
        # Process PDF
        logger.info("Starting PDF processing...")
        encoder = system_components["report_encoder"]
        report_content = encoder.encode_pdf(file_info["file_path"], save_markdown=True)
        logger.info("PDF processing completed")
        
        # Store processing results
        system_components["current_report"] = report_content
        logger.info("Report content stored in system components")
        
        # Store industry information
        system_components["current_industry"] = industry
        system_components["current_semi_industry"] = semiIndustry
        # Extract company name from filename (remove extension)
        company_name = file.filename.rsplit('.', 1)[0] if file.filename else "Unknown Company"
        system_components["current_company"] = company_name
        logger.info(f"Stored industry info - Industry: {industry}, Semi-Industry: {semiIndustry}, Company: {company_name}")
        
        # Get report summary
        logger.info("Getting report summary...")
        summary = encoder.get_report_summary(report_content)
        logger.info("Report summary obtained")
        
        # Load corresponding metrics based on user-selected framework and industry
        metrics = None
        if framework == "SASB" and semiIndustry:
            # Use SASB metrics
            processor = system_components["metric_processor"]
            metrics = processor.load_sasb_metrics_by_industry(semiIndustry)
            system_components["current_metrics"] = metrics
            logger.info(f"Loaded SASB metrics for industry: {semiIndustry}")
        else:
            # Use default metrics or existing metrics
            if system_components["current_metrics"] is None:
                processor = system_components["metric_processor"]
                metrics = processor.create_sample_metrics()
                system_components["current_metrics"] = metrics
                logger.info("Using default sample metrics")
            else:
                metrics = system_components["current_metrics"]
        
        # Now with report and metrics, perform complete processing chain
        if metrics:
            try:
                # Execute complete processing chain: dual-channel retrieval + disclosure inference engine classification
                dual_retriever = system_components["dual_retriever"]
                retrieval_results = dual_retriever.retrieve_for_collection(
                    report_content,
                    metrics
                )
                
                # Execute disclosure inference (classification)
                disclosure_engine = system_components["disclosure_engine"]
                assessment = disclosure_engine.analyze_compliance(
                    retrieval_results,
                    report_content,
                    file_info["file_path"],
                    metrics  # Pass all metrics
                )
                
                # Store assessment results
                system_components["current_assessment"] = assessment
                
                # Update chatbot knowledge base (including ESG content + classification results and comments)
                system_components["chatbot"].load_context(
                    report_content,
                    assessment
                )
                
                # Generate and save compliance report
                compliance_report = disclosure_engine.generate_compliance_report(assessment)
                report_path = Path("outputs") / f"compliance_report_{assessment.report_id}.md"
                report_path.parent.mkdir(exist_ok=True)
                with open(report_path, "w", encoding="utf-8") as f:
                    f.write(compliance_report)
                
                # Save JSON assessment data for frontend use
                uploads_dir = Path("../../uploads")
                json_report_dir = uploads_dir / "outputs" / "compliance_reports"
                json_report_dir.mkdir(parents=True, exist_ok=True)
                
                json_report_path = json_report_dir / f"{file_info['file_id']}_compliance.json"
                
                # Convert assessment data to JSON format
                assessment_json = {
                    "report_id": assessment.report_id,
                    "assessment_date": assessment.assessment_date.isoformat(),
                    "total_metrics": assessment.total_metrics_analyzed,
                    "overall_score": assessment.overall_compliance_score,
                    "disclosure_summary": {
                        "fully_disclosed": assessment.disclosure_summary.get("fully_disclosed", 0),
                        "partially_disclosed": assessment.disclosure_summary.get("partially_disclosed", 0),
                        "not_disclosed": assessment.disclosure_summary.get("not_disclosed", 0)
                    },
                    "metric_analyses": [
                        {
                            "metric_id": analysis.metric_id,
                            "metric_name": analysis.metric_name,
                            "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                            "reasoning": analysis.reasoning,
                            "unit": getattr(analysis, 'unit', ''),
                            "category": getattr(analysis, 'category', ''),
                            "topic": getattr(analysis, 'topic', ''),
                            "type": getattr(analysis, 'type', ''),
                            "page": getattr(analysis, 'relevant_pages', None),
                            "context": getattr(analysis, 'relevant_context', None)
                        }
                        for analysis in assessment.metric_analyses
                    ]
                }
                
                with open(json_report_path, "w", encoding="utf-8") as f:
                    json.dump(assessment_json, f, indent=2, ensure_ascii=False)
                
                logger.info(f"Assessment JSON saved to: {json_report_path}")
                
                # Move file to processed directory (processing complete)
                file_manager.move_report_file(file_info["file_id"], "processed")
                
                logger.info(f"Complete processing chain finished. Score: {assessment.overall_compliance_score:.2%}")
                
                return {
                    "status": "success",
                    "message": "Report uploaded and fully processed",
                    "report_id": report_content.document_id,
                    "file_id": file_info["file_id"],
                    "summary": summary,
                    "assessment": {
                        "total_metrics": assessment.total_metrics_analyzed,
                        "overall_score": assessment.overall_compliance_score,
                        "disclosure_summary": assessment.disclosure_summary,
                        "report_path": str(report_path)
                    }
                }
                
            except Exception as assessment_error:
                logger.error(f"Error in assessment processing: {assessment_error}")
                # If inference fails, still save report but mark as partially processed
                file_manager.move_report_file(file_info["file_id"], "processed")
                
                return {
                    "status": "partial_success",
                    "message": "Report processed but assessment failed",
                    "report_id": report_content.document_id,
                    "file_id": file_info["file_id"],
                    "summary": summary,
                    "error": str(assessment_error)
                }
        else:
            # When no metrics, only process report and wait for metrics upload
            file_manager.move_report_file(file_info["file_id"], "processed")
            
            logger.info(f"Report processed, waiting for metrics: {file.filename}")
            
            return {
                "status": "success",
                "message": "Report processed, awaiting metrics for full analysis",
                "report_id": report_content.document_id,
                "file_id": file_info["file_id"],
                "summary": summary
            }
        
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        # If processing fails, move to failed directory
        if 'file_info' in locals():
            file_manager.move_report_file(file_info["file_id"], "failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload-metrics")
async def upload_metrics(
    file: Optional[UploadFile] = File(None),
    metrics_json: Optional[str] = Form(None)
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
                file_type="metrics"
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
                file_type="metrics"
            )
            
        else:
            # 使用示例指标
            metrics = processor.create_sample_metrics()
        
        # 处理指标（语义扩展）
        if system_components["config"].llm_api_key:
            processed_metrics = processor.process_metric_collection(metrics)
        else:
            processed_metrics = metrics
            logger.warning("LLM API key not configured, skipping semantic expansion")
        
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


@app.post("/api/analyze-compliance")
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
        dual_retriever = system_components["dual_retriever"]
        retrieval_results = dual_retriever.retrieve_for_collection(
            system_components["current_report"],
            system_components["current_metrics"]
        )
        
        # 执行披露推理
        disclosure_engine = system_components["disclosure_engine"]
        assessment = disclosure_engine.analyze_compliance(
            retrieval_results,
            system_components["current_report"],
            system_components["current_report"].document_content.file_path,
            system_components["current_metrics"]  # 传入所有指标
        )
        
        # 存储评估结果
        system_components["current_assessment"] = assessment
        
        # 更新聊天机器人上下文
        system_components["chatbot"].load_context(
            system_components["current_report"],
            assessment
        )
        
        # 生成合规报告
        compliance_report = disclosure_engine.generate_compliance_report(assessment)
        
        # 保存报告
        report_path = Path("outputs") / f"compliance_report_{assessment.report_id}.md"
        report_path.parent.mkdir(exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(compliance_report)
        
        # 保存JSON评估数据供前端使用
        uploads_dir = Path("../../uploads")
        json_report_dir = uploads_dir / "outputs" / "compliance_reports"
        json_report_dir.mkdir(parents=True, exist_ok=True)
        
        # 从报告ID提取文件ID（格式类似：doc_20250826_042708_ffd688f6-e1aa-49d3-be2d-2eefdc6ccfd2_9b591e1c）
        report_id_parts = assessment.report_id.split('_')
        if len(report_id_parts) >= 4:
            file_id = '_'.join(report_id_parts[3:4])  # 提取文件ID部分
        else:
            file_id = assessment.report_id
        
        json_report_path = json_report_dir / f"{file_id}_compliance.json"
        
        # 将评估数据转换为JSON格式
        assessment_json = {
            "report_id": assessment.report_id,
            "assessment_date": assessment.assessment_date.isoformat(),
            "total_metrics": assessment.total_metrics_analyzed,
            "overall_score": assessment.overall_compliance_score,
            "disclosure_summary": {
                "fully_disclosed": assessment.disclosure_summary.get("fully_disclosed", 0),
                "partially_disclosed": assessment.disclosure_summary.get("partially_disclosed", 0),
                "not_disclosed": assessment.disclosure_summary.get("not_disclosed", 0)
            },
            "metric_analyses": [
                {
                    "metric_id": analysis.metric_id,
                    "metric_name": analysis.metric_name,
                    "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                    "reasoning": analysis.reasoning,
                    "unit": getattr(analysis, 'unit', ''),
                    "category": getattr(analysis, 'category', ''),
                    "topic": getattr(analysis, 'topic', ''),
                    "type": getattr(analysis, 'type', ''),
                    "page": getattr(analysis, 'relevant_pages', None),
                    "context": getattr(analysis, 'relevant_context', None)
                }
                for analysis in assessment.metric_analyses
            ]
        }
        
        with open(json_report_path, "w", encoding="utf-8") as f:
            json.dump(assessment_json, f, indent=2, ensure_ascii=False)
        
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
                                "type": getattr(metric, 'sasb_type', 'Sustainability Disclosure Topics & Metrics')
                            }
                            break
                
                excel_metrics.append({
                    "metric_id": analysis.metric_code if hasattr(analysis, 'metric_code') else analysis.metric_id,
                    "metric_name": analysis.metric_name,
                    "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                    "reasoning": analysis.reasoning,
                    "value": getattr(analysis, 'value', None),
                    "page": getattr(analysis, 'relevant_pages', None),
                    "context": getattr(analysis, 'relevant_context', None),
                    "category": metric_info.get('category', getattr(analysis, 'category', '')),
                    "unit": metric_info.get('unit', getattr(analysis, 'unit', '')),
                    "topic": metric_info.get('topic', getattr(analysis, 'topic', '')),
                    "type": metric_info.get('type', getattr(analysis, 'type', ''))
                })
            
            excel_path = excel_exporter.export_analysis_results(
                metric_analyses=excel_metrics,
                industry=system_components.get("current_industry", "Unknown"),
                semi_industry=system_components.get("current_semi_industry", "Unknown"),
                company_name=system_components.get("current_company", "Unknown"),
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


def _load_latest_assessment_for_chat():
    """
    为聊天机器人加载最新的评估数据
    """
    try:
        # 获取最新的评估数据
        backend_dir = Path(__file__).parent.parent.parent
        outputs_dir = backend_dir / "outputs"
        report_files = list(outputs_dir.glob("compliance_report_*.md"))
        
        if not report_files:
            return None
            
        # 使用最新的报告文件
        report_file = sorted(report_files, key=lambda x: x.stat().st_mtime)[-1]
        
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析报告内容
        parsed_data = _parse_compliance_report(content, report_file.stem)
        
        # 构造ComplianceAssessment对象兼容的数据结构
        from .models import ComplianceAssessment, DisclosureAnalysis, DisclosureStatus
        
        # 创建metric analyses
        metric_analyses = []
        for item in parsed_data["metric_analyses"]:
            status_map = {
                "fully_disclosed": DisclosureStatus.FULLY_DISCLOSED,
                "partially_disclosed": DisclosureStatus.PARTIALLY_DISCLOSED,
                "not_disclosed": DisclosureStatus.NOT_DISCLOSED
            }
            
            analysis = DisclosureAnalysis(
                metric_id=item["metric_id"],
                metric_name=item["metric_name"],
                metric_code=item.get("metric_code", item["metric_id"]),
                disclosure_status=status_map.get(item["disclosure_status"], DisclosureStatus.NOT_DISCLOSED),
                reasoning=item["reasoning"],
                evidence_segments=[],
                improvement_suggestions=[],
                category=item.get("category", "Unknown"),
                unit=item.get("unit", ""),
                type=item.get("type", "Disclosure Topics & Metrics"),
                value=item.get("value"),
                page=item.get("page")
            )
            metric_analyses.append(analysis)
        
        # 创建ComplianceAssessment对象
        assessment = ComplianceAssessment(
            report_id=parsed_data["report_id"],
            total_metrics_analyzed=parsed_data["total_metrics"],
            overall_compliance_score=parsed_data["overall_score"],
            disclosure_summary=parsed_data["disclosure_summary"],
            metric_analyses=metric_analyses,
            report_file_path=str(report_file)
        )
        
        return assessment
        
    except Exception as e:
        logger.error(f"Failed to load assessment for chat: {e}")
        return None

def _load_report_content_for_chat():
    """
    加载原始报告内容用于聊天检索
    """
    try:
        from .models import ReportContent, ReportSegment
        
        # 查找提取的markdown文件
        uploads_dir = Path(__file__).parent.parent.parent.parent / "uploads"
        markdown_files = list(uploads_dir.glob("*_extracted.md"))
        
        if not markdown_files:
            logger.warning("No extracted markdown files found for chat")
            return None
            
        # 使用最新的markdown文件
        markdown_file = sorted(markdown_files, key=lambda x: x.stat().st_mtime)[-1]
        logger.info(f"Loading report content from: {markdown_file}")
        
        with open(markdown_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 简单分段处理 - 按段落分割
        segments = []
        paragraphs = content.split('\n\n')
        
        for i, paragraph in enumerate(paragraphs):
            if paragraph.strip():
                segment = ReportSegment(
                    segment_id=f"seg_{i}",
                    content=paragraph.strip(),
                    page_number=1  # 简化处理
                )
                segments.append(segment)
        
        # 创建DocumentContent对象
        from .models import DocumentContent, SegmentEmbedding, TextSegment
        
        # 创建TextSegment列表
        text_segments = []
        for segment in segments[:500]:  # 限制段落数量
            text_segment = TextSegment(
                segment_id=segment.segment_id,
                content=segment.content,
                page_number=segment.page_number
            )
            text_segments.append(text_segment)
        
        document_content = DocumentContent(
            document_id=markdown_file.stem,
            file_path=str(markdown_file),
            segments=text_segments,
            markdown_content=content
        )
        
        # 创建空的嵌入列表（简化处理）
        embeddings = []
        
        # 创建ReportContent对象
        report_content = ReportContent(
            document_id=markdown_file.stem,
            document_content=document_content,
            embeddings=embeddings
        )
        
        logger.info(f"Loaded {len(report_content.document_content.segments)} segments for chat")
        return report_content
        
    except Exception as e:
        logger.error(f"Failed to load report content for chat: {e}")
        return None

def _create_enhanced_knowledge_base(assessment, report_content):
    """
    创建增强的知识库，结合评估结果和原始报告内容
    """
    try:
        from .models import ReportSegment
        
        if not assessment:
            return report_content
            
        # 创建评估结果的文档片段
        assessment_segments = []
        
        # 1. 总体评估信息
        summary_text = f"""
ESG合规评估总结:
- 报告ID: {assessment.report_id}
- 分析指标总数: {assessment.total_metrics_analyzed}
- 整体合规分数: {assessment.overall_compliance_score:.1%}
- 完全披露指标: {assessment.disclosure_summary.get('fully_disclosed', 0)}个
- 部分披露指标: {assessment.disclosure_summary.get('partially_disclosed', 0)}个  
- 未披露指标: {assessment.disclosure_summary.get('not_disclosed', 0)}个
"""
        
        summary_segment = ReportSegment(
            segment_id="assessment_summary",
            content=summary_text,
            page_number=0,
            embedding=None
        )
        assessment_segments.append(summary_segment)
        
        # 2. 具体指标分析
        if hasattr(assessment, 'metric_analyses') and assessment.metric_analyses:
            for i, analysis in enumerate(assessment.metric_analyses):
                metric_text = f"""
指标分析 {i+1}:
- 指标ID: {getattr(analysis, 'metric_id', 'Unknown')}
- 指标名称: {getattr(analysis, 'metric_name', 'Unknown')}
- 披露状态: {getattr(analysis, 'disclosure_status', 'Unknown')}
- 分析理由: {getattr(analysis, 'reasoning', 'No reasoning provided')}
"""
                
                metric_segment = ReportSegment(
                    segment_id=f"metric_analysis_{i}",
                    content=metric_text,
                    page_number=0,
                    embedding=None
                )
                assessment_segments.append(metric_segment)
        
        # 合并评估段落和原始报告段落
        if report_content:
            all_segments = assessment_segments + report_content.segments
            total_segments = len(all_segments)
        else:
            all_segments = assessment_segments
            total_segments = len(assessment_segments)
        
        from .models import ReportContent
        enhanced_content = ReportContent(
            document_id=f"enhanced_{assessment.report_id}",
            segments=all_segments,
            total_segments=total_segments
        )
        
        logger.info(f"Created enhanced knowledge base with {len(assessment_segments)} assessment segments and {len(report_content.segments) if report_content else 0} report segments")
        return enhanced_content
        
    except Exception as e:
        logger.error(f"Failed to create enhanced knowledge base: {e}")
        return report_content

@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    处理聊天请求
    
    Args:
        request: 聊天请求
        
    Returns:
        ChatResponse: 聊天响应
    """
    try:
        chatbot = system_components["chatbot"]
        
        # 加载最新的评估数据
        latest_assessment = _load_latest_assessment_for_chat()
        
        # 加载原始报告内容
        report_content = _load_report_content_for_chat()
        
        # 创建增强的知识库
        enhanced_content = _create_enhanced_knowledge_base(latest_assessment, report_content)
        
        # 加载到聊天机器人
        if latest_assessment:
            chatbot.load_context(
                compliance_assessment=latest_assessment,
                report_content=enhanced_content
            )
            logger.info(f"Loaded enhanced knowledge base: {latest_assessment.total_metrics_analyzed} metrics + {len(enhanced_content.document_content.segments) if enhanced_content else 0} content segments")
        elif enhanced_content:
            chatbot.load_context(report_content=enhanced_content)
            logger.info(f"Loaded report content: {len(enhanced_content.document_content.segments)} segments")
        
        response = chatbot.chat(request)
        return response
        
    except Exception as e:
        logger.error(f"Error in chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/assessment")
async def get_assessment():
    """
    获取当前的合规评估结果
    
    Returns:
        评估结果
    """
    if not system_components["current_assessment"]:
        raise HTTPException(status_code=404, detail="No assessment available")
    
    assessment = system_components["current_assessment"]
    
    return {
        "report_id": assessment.report_id,
        "assessment_date": assessment.assessment_date.isoformat(),
        "total_metrics": assessment.total_metrics_analyzed,
        "overall_score": assessment.overall_compliance_score,
        "disclosure_summary": assessment.disclosure_summary,
        "metric_analyses": [
            {
                "metric_id": analysis.metric_id,
                "metric_name": analysis.metric_name,
                "disclosure_status": analysis.disclosure_status,
                "reasoning": analysis.reasoning
            }
            for analysis in assessment.metric_analyses[:10]  # 返回前10个
        ]
    }


@app.get("/api/test-path")
async def test_path():
    """测试路径配置"""
    try:
        backend_dir = Path(__file__).parent.parent.parent
        outputs_dir = backend_dir / "outputs"
        return {
            "current_file": str(Path(__file__)),
            "backend_dir": str(backend_dir), 
            "outputs_dir": str(outputs_dir),
            "outputs_exists": outputs_dir.exists(),
            "files": [f.name for f in outputs_dir.glob("*.md")] if outputs_dir.exists() else []
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/assessment/latest")
async def get_latest_assessment():
    """
    获取最新的合规评估结果
    
    Returns:
        最新的评估结果
    """
    try:
        # 查找最新的markdown报告文件 - 直接调试路径问题
        import os
        current_working_dir = Path(os.getcwd())
        file_path = Path(__file__)
        backend_dir = Path(__file__).parent.parent.parent  # 从src/esg_encoding到backend根目录
        outputs_dir = backend_dir / "outputs"
        
        logger.info(f"Current working directory: {current_working_dir}")
        logger.info(f"API file path: {file_path}")
        logger.info(f"Calculated backend_dir: {backend_dir}")
        logger.info(f"Outputs directory: {outputs_dir}")
        logger.info(f"Outputs exists: {outputs_dir.exists()}")
        
        report_files = list(outputs_dir.glob("compliance_report_*.md"))
        logger.info(f"Found {len(report_files)} report files")
        
        if not report_files:
            logger.info(f"No reports found in outputs directory: {outputs_dir}")
            logger.info(f"Directory exists: {outputs_dir.exists()}")
            debug_info = {
                "outputs_dir": str(outputs_dir),
                "exists": outputs_dir.exists(),
            }
            if outputs_dir.exists():
                all_files = list(outputs_dir.glob("*"))
                debug_info["all_files"] = [f.name for f in all_files]
                debug_info["md_files"] = [f.name for f in outputs_dir.glob("*.md")]
                logger.info(f"Files in directory: {[f.name for f in all_files]}")
            return {
                "report_id": "unknown",
                "assessment_date": datetime.now().isoformat(),
                "total_metrics": 0,
                "overall_score": 0,
                "disclosure_summary": {},
                "metric_analyses": [],
                "status": "not_analyzed",
                "message": "No analysis reports available",
                "debug": debug_info
            }
        
        # 使用最新的报告文件（按文件名排序）
        report_file = sorted(report_files, key=lambda x: x.stat().st_mtime)[-1]
        logger.info(f"Using latest report file: {report_file}")
        
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析报告内容（使用相同的解析逻辑）
        return _parse_compliance_report(content, report_file.stem)
        
    except Exception as e:
        logger.error(f"Failed to get latest assessment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get latest assessment: {str(e)}")


@app.get("/api/assessment/{file_id}")
async def get_assessment_by_file(file_id: str):
    """
    根据文件ID获取合规评估结果，直接从outputs目录的markdown报告解析
    
    Args:
        file_id: 文件ID
        
    Returns:
        评估结果
    """
    try:
        # 查找对应的markdown报告文件 - 使用相对路径支持跨设备
        backend_dir = Path(__file__).parent.parent.parent  # 从src/esg_encoding到backend根目录
        outputs_dir = backend_dir / "outputs"
        
        # 先尝试精确匹配file_id
        report_files = list(outputs_dir.glob(f"*{file_id}*.md"))
        
        # 如果没找到，尝试更宽松的匹配（去掉可能的扩展名或前缀）
        if not report_files:
            # 尝试匹配file_id的一部分（前8个字符）
            if len(file_id) >= 8:
                short_id = file_id[:8]
                report_files = list(outputs_dir.glob(f"*{short_id}*.md"))
        
        # 如果还是没找到，列出所有文件供调试
        if not report_files:
            all_files = list(outputs_dir.glob("*.md"))
            logger.info(f"No report found for file_id: {file_id}")
            logger.info(f"Available report files: {[f.name for f in all_files]}")
            return {
                "report_id": file_id,
                "assessment_date": datetime.now().isoformat(),
                "total_metrics": 0,
                "overall_score": 0,
                "disclosure_summary": {},
                "metric_analyses": [],
                "status": "not_analyzed",
                "message": f"No analysis available for this file yet. Available reports: {len(all_files)}"
            }
        
        # 使用最新的报告文件
        report_file = sorted(report_files)[-1]
        logger.info(f"Found report file: {report_file}")
        
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析报告内容
        return _parse_compliance_report(content, file_id)
        
    except Exception as e:
        logger.error(f"Failed to parse assessment for {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to parse assessment: {str(e)}")


@app.get("/api/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """
    获取聊天历史
    
    Args:
        session_id: 会话ID
        
    Returns:
        聊天历史
    """
    chatbot = system_components["chatbot"]
    history = chatbot.get_session_history(session_id)
    
    if not history:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "session_id": session_id,
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat()
            }
            for msg in history
        ]
    }


@app.delete("/api/chat/session/{session_id}")
async def clear_chat_session(session_id: str):
    """
    清除聊天会话
    
    Args:
        session_id: 会话ID
        
    Returns:
        操作结果
    """
    chatbot = system_components["chatbot"]
    success = chatbot.clear_session(session_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"status": "success", "message": f"Session {session_id} cleared"}


@app.get("/api/system/status")
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


@app.get("/api/files")
async def list_files(file_type: Optional[str] = None, status: Optional[str] = None):
    """
    列出文件
    
    Args:
        file_type: 文件类型过滤 ('report', 'metrics')
        status: 状态过滤 ('pending', 'processed', 'failed', 'uploaded')
        
    Returns:
        文件列表
    """
    try:
        if file_type:
            files = file_manager.list_files_by_type(file_type, status)
        else:
            all_files = []
            for ftype in ['report', 'metrics']:
                all_files.extend(file_manager.list_files_by_type(ftype, status))
            files = sorted(all_files, key=lambda x: x["upload_time"], reverse=True)
        
        return {
            "status": "success",
            "files": files,
            "total_count": len(files)
        }
        
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/files/{file_id}")
async def get_file_info(file_id: str):
    """
    获取文件详细信息
    
    Args:
        file_id: 文件ID
        
    Returns:
        文件信息
    """
    file_info = file_manager.get_file_info(file_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")
    
    return {
        "status": "success",
        "file_info": file_info
    }


@app.post("/api/system/cleanup-orphaned-reports")
async def cleanup_orphaned_reports():
    """
    清理孤儿报告文件（没有对应元数据的报告）
    """
    try:
        # 获取所有活跃文件ID
        active_file_ids = set(file_manager.metadata["files"].keys())
        
        # 扫描backend/outputs目录
        backend_dir = Path(__file__).parent.parent.parent
        outputs_dir = backend_dir / "outputs"
        
        deleted_items = []
        if outputs_dir.exists():
            for report_file in outputs_dir.glob("*.md"):
                # 检查文件名中是否包含任何活跃的file_id
                is_orphaned = True
                for file_id in active_file_ids:
                    if file_id in report_file.name:
                        is_orphaned = False
                        break
                
                if is_orphaned:
                    report_file.unlink()
                    deleted_items.append(report_file.name)
        
        return {
            "status": "success",
            "message": f"Cleaned up {len(deleted_items)} orphaned report files",
            "deleted_files": deleted_items
        }
    
    except Exception as e:
        logger.error(f"Failed to cleanup orphaned reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")


@app.delete("/api/files/{file_id}")
async def delete_file(file_id: str):
    """
    完全删除文件及其所有相关数据
    
    Args:
        file_id: 文件ID
        
    Returns:
        删除结果
    """
    file_info = file_manager.get_file_info(file_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        deleted_items = []
        
        # 1. 删除主PDF文件
        file_path = Path(file_info["file_path"])
        if file_path.exists():
            file_path.unlink()
            deleted_items.append(f"PDF文件: {file_path.name}")
        
        # 2. 删除提取的Markdown文件
        safe_filename = file_info.get("safe_filename", "")
        base_name = ""
        if safe_filename:
            # 构造markdown文件路径
            base_name = safe_filename.replace(".pdf", "")
            markdown_paths = [
                file_path.parent / f"{base_name}_extracted.md",
                Path("uploads/outputs/markdown") / f"{base_name}_extracted.md",
            ]
            
            for md_path in markdown_paths:
                if md_path.exists():
                    md_path.unlink()
                    deleted_items.append(f"Markdown文件: {md_path.name}")
        else:
            # 如果没有safe_filename，从file_id构造base_name
            base_name = file_id
        
        # 3. 删除嵌入向量文件
        embeddings_paths = [
            Path("uploads/outputs/embeddings") / f"{base_name}_embeddings.json",
            Path("uploads/outputs/embeddings") / f"{base_name}_embeddings.npy",
        ]
        
        for emb_path in embeddings_paths:
            if emb_path.exists():
                emb_path.unlink()
                deleted_items.append(f"嵌入文件: {emb_path.name}")
        
        # 4. 删除合规分析报告
        compliance_paths = [
            # JSON格式的合规报告
            Path("uploads/outputs/compliance_reports") / f"{base_name}_compliance.json",
            Path("uploads/outputs/compliance_reports") / f"{file_id}_compliance.json",
        ]
        
        # 添加backend/outputs目录下的Markdown合规报告
        backend_dir = Path(__file__).parent.parent.parent  # 从src/esg_encoding到backend根目录
        outputs_dir = backend_dir / "outputs"
        
        # 查找所有包含file_id的合规报告文件
        if outputs_dir.exists():
            for report_file in outputs_dir.glob(f"*{file_id}*.md"):
                compliance_paths.append(report_file)
        
        for comp_path in compliance_paths:
            if comp_path.exists():
                comp_path.unlink()
                deleted_items.append(f"合规报告: {comp_path.name}")
        
        # 5. 清理系统组件中的相关数据
        # 如果这是当前加载的报告，清理内存中的数据
        if system_components.get("current_report_content"):
            current_report = system_components["current_report_content"]
            if hasattr(current_report, 'document_id') and file_id in current_report.document_id:
                system_components["current_report_content"] = None
                deleted_items.append("内存中的报告内容")
        
        if system_components.get("current_assessment"):
            current_assessment = system_components["current_assessment"]
            if hasattr(current_assessment, 'report_id') and file_id in current_assessment.report_id:
                system_components["current_assessment"] = None
                deleted_items.append("内存中的评估结果")
        
        # 6. 清理聊天机器人上下文
        if system_components.get("chatbot"):
            chatbot = system_components["chatbot"]
            # 清理与该文件相关的聊天上下文
            if hasattr(chatbot, 'report_content') and chatbot.report_content:
                if hasattr(chatbot.report_content, 'document_id') and file_id in chatbot.report_content.document_id:
                    chatbot.report_content = None
                    chatbot.compliance_assessment = None
                    deleted_items.append("聊天机器人上下文")
        
        # 7. 从元数据中删除
        del file_manager.metadata["files"][file_id]
        file_manager._save_metadata()
        deleted_items.append("文件元数据")
        
        logger.info(f"File and related data deleted: {file_id}")
        logger.info(f"Deleted items: {', '.join(deleted_items)}")
        
        return {
            "status": "success",
            "message": "File and all related data deleted successfully",
            "deleted_items": deleted_items
        }
        
    except Exception as e:
        logger.error(f"Error deleting file and related data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/files/cleanup")
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


@app.get("/api/reports/latest")
async def get_latest_report():
    """
    Get the latest compliance analysis report in markdown format
    
    Returns:
        Latest report content
    """
    try:
        outputs_dir = Path("outputs")
        if not outputs_dir.exists():
            raise HTTPException(status_code=404, detail="No reports directory found")
        
        # Find all markdown files
        report_files = list(outputs_dir.glob("compliance_report_*.md"))
        if not report_files:
            raise HTTPException(status_code=404, detail="No reports found")
        
        # Get the latest file by modification time
        latest_file = max(report_files, key=lambda f: f.stat().st_mtime)
        
        # Read the markdown content
        with open(latest_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        return {
            "status": "success",
            "report_file": latest_file.name,
            "content": content,
            "created_at": datetime.fromtimestamp(latest_file.stat().st_mtime).isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error fetching latest report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)