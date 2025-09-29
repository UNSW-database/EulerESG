#!/usr/bin/env python
"""
手动处理 pending 文件的脚本
用于测试和修复文件处理流程
"""

import sys
import json
from pathlib import Path

# 添加项目路径
sys.path.append(str(Path(__file__).parent / "src"))

from src.esg_encoding.file_manager import FileManager
from src.esg_encoding.report_encoder import ReportEncoder
from src.esg_encoding.metric_processor import MetricProcessor
from src.esg_encoding.dual_channel_retrieval import DualChannelRetriever
from src.esg_encoding.disclosure_inference import DisclosureInferenceEngine
from src.esg_encoding.esg_chatbot import ESGChatbot
from src.esg_encoding.excel_exporter import ExcelExporter
from src.esg_encoding.models import ProcessingConfig

def process_pending_file(file_id: str):
    """处理指定的 pending 文件"""
    print(f"开始处理文件: {file_id}")
    
    # 初始化组件
    file_manager = FileManager("../../uploads")
    
    # 检查文件是否存在
    if file_id not in file_manager.metadata["files"]:
        print(f"错误: 文件 {file_id} 不存在")
        return False
    
    file_info = file_manager.metadata["files"][file_id]
    if file_info["status"] != "pending":
        print(f"文件状态不是 pending: {file_info['status']}")
        return False
    
    print(f"文件信息: {file_info['original_name']}")
    print(f"行业: {file_info.get('industry', 'Unknown')}")
    print(f"子行业: {file_info.get('semi_industry', 'Unknown')}")
    print(f"框架: {file_info.get('framework', 'Unknown')}")
    
    file_path = file_info["file_path"]
    if not Path(file_path).exists():
        print(f"错误: 物理文件不存在: {file_path}")
        return False
    
    try:
        # 1. 处理 PDF
        print("步骤 1: 处理 PDF...")
        encoder = ReportEncoder()
        report_content = encoder.encode_pdf(file_path, save_markdown=True)
        print("PDF 处理完成")
        
        # 2. 加载指标
        print("步骤 2: 加载指标...")
        config = ProcessingConfig()
        processor = MetricProcessor(config)
        semi_industry = file_info.get("semi_industry")
        if semi_industry:
            metrics = processor.load_sasb_metrics_by_industry(semi_industry)
            print(f"加载了 {len(metrics.metrics)} 个指标")
        else:
            print("没有指定子行业，使用默认指标")
            metrics = processor.create_sample_metrics()
        
        # 3. 执行双通道检索
        print("步骤 3: 执行检索...")
        dual_retriever = DualChannelRetriever()
        retrieval_results = dual_retriever.retrieve_for_collection(report_content, metrics)
        print("检索完成")
        
        # 4. 执行合规分析
        print("步骤 4: 执行合规分析...")
        disclosure_engine = DisclosureInferenceEngine()
        assessment = disclosure_engine.analyze_compliance(
            retrieval_results,
            report_content,
            file_path,
            metrics
        )
        print(f"分析完成，总分: {assessment.overall_compliance_score:.2%}")
        
        # 5. 保存结果
        print("步骤 5: 保存结果...")
        uploads_dir = Path("../../uploads")
        json_report_dir = uploads_dir / "outputs" / "compliance_reports"
        json_report_dir.mkdir(parents=True, exist_ok=True)
        
        json_report_path = json_report_dir / f"{file_info['file_id']}_compliance.json"
        
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
        
        print(f"结果保存到: {json_report_path}")
        
        # 5.5. 导出Excel报告
        print("步骤 5.5: 导出Excel报告...")
        try:
            excel_exporter = ExcelExporter()
            
            # 准备Excel导出的指标数据
            excel_metrics = []
            for analysis in assessment.metric_analyses:
                # 查找对应指标的详细信息
                metric_info = {}
                if metrics:
                    for metric in metrics.metrics:
                        if hasattr(metric, 'metric') and metric.metric == analysis.metric_name:
                            metric_info = {
                                "unit": getattr(metric, 'unit', ''),
                                "category": getattr(metric, 'category', ''),
                                "topic": getattr(metric, 'topic', ''),
                                "type": getattr(metric, 'type', 'Sustainability Disclosure Topics & Metrics')
                            }
                            break
                
                excel_metrics.append({
                    "metric_id": analysis.metric_id,
                    "metric_name": analysis.metric_name,
                    "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                    "reasoning": analysis.reasoning,
                    "value": getattr(analysis, 'value', None),
                    "page": getattr(analysis, 'relevant_pages', None),
                    "context": getattr(analysis, 'relevant_context', None),
                    "unit": metric_info.get('unit', getattr(analysis, 'unit', '')),
                    "category": metric_info.get('category', getattr(analysis, 'category', '')),
                    "topic": metric_info.get('topic', getattr(analysis, 'topic', '')),
                    "type": metric_info.get('type', getattr(analysis, 'type', ''))
                })
            
            excel_path = excel_exporter.export_analysis_results(
                metric_analyses=excel_metrics,
                industry=file_info.get("industry", "Unknown"),
                semi_industry=file_info.get("semi_industry", "Unknown"),
                company_name=file_info.get("original_name", "Unknown").replace(".pdf", ""),
                report_id=assessment.report_id
            )
            print(f"Excel报告导出到: {excel_path}")
        except Exception as e:
            print(f"Excel导出失败: {e}")
            import traceback
            traceback.print_exc()
        
        # 6. 移动文件到 processed 目录
        print("步骤 6: 更新文件状态...")
        success = file_manager.move_report_file(file_id, "processed")
        if success:
            print("文件已移动到 processed 目录")
        else:
            print("文件移动失败")
            return False
        
        print("处理完成!")
        return True
        
    except Exception as e:
        print(f"处理过程中出错: {e}")
        import traceback
        traceback.print_exc()
        return False

def generate_excel_for_processed_file(file_id: str):
    """为已处理的文件生成Excel报告"""
    print(f"为已处理文件生成Excel报告: {file_id}")
    
    # 初始化组件
    file_manager = FileManager("../../uploads")
    
    # 检查文件是否存在
    if file_id not in file_manager.metadata["files"]:
        print(f"错误: 文件 {file_id} 不存在")
        return False
    
    file_info = file_manager.metadata["files"][file_id]
    print(f"文件信息: {file_info['original_name']}")
    print(f"行业: {file_info.get('industry', 'Unknown')}")
    print(f"子行业: {file_info.get('semi_industry', 'Unknown')}")
    
    # 读取已生成的合规报告
    uploads_dir = Path("../../uploads")
    json_report_path = uploads_dir / "outputs" / "compliance_reports" / f"{file_id}_compliance.json"
    
    if not json_report_path.exists():
        print(f"错误: 合规报告不存在: {json_report_path}")
        return False
    
    try:
        # 读取JSON报告
        with open(json_report_path, "r", encoding="utf-8") as f:
            assessment_json = json.load(f)
        
        # 加载指标信息
        config = ProcessingConfig()
        processor = MetricProcessor(config)
        semi_industry = file_info.get("semi_industry")
        if semi_industry:
            metrics = processor.load_sasb_metrics_by_industry(semi_industry)
        else:
            metrics = processor.create_sample_metrics()
        
        # 导出Excel报告
        print("导出Excel报告...")
        excel_exporter = ExcelExporter()
        
        # 准备Excel导出的指标数据
        excel_metrics = []
        for metric_analysis in assessment_json["metric_analyses"]:
            # 查找对应指标的详细信息
            metric_info = {}
            if metrics:
                for metric in metrics.metrics:
                    if hasattr(metric, 'metric') and metric.metric == metric_analysis["metric_name"]:
                        metric_info = {
                            "unit": getattr(metric, 'unit', ''),
                            "category": getattr(metric, 'category', ''),
                            "topic": getattr(metric, 'topic', ''),
                            "type": getattr(metric, 'type', 'Sustainability Disclosure Topics & Metrics')
                        }
                        break
            
            excel_metrics.append({
                "metric_id": metric_analysis["metric_id"],
                "metric_name": metric_analysis["metric_name"],
                "disclosure_status": metric_analysis["disclosure_status"],
                "reasoning": metric_analysis["reasoning"],
                "value": metric_analysis.get("value"),
                "page": metric_analysis.get("page"),
                "context": metric_analysis.get("context"),
                "unit": metric_info.get('unit', metric_analysis.get('unit', '')),
                "category": metric_info.get('category', metric_analysis.get('category', '')),
                "topic": metric_info.get('topic', metric_analysis.get('topic', '')),
                "type": metric_info.get('type', metric_analysis.get('type', ''))
            })
        
        excel_path = excel_exporter.export_analysis_results(
            metric_analyses=excel_metrics,
            industry=file_info.get("industry", "Unknown"),
            semi_industry=file_info.get("semi_industry", "Unknown"),
            company_name=file_info.get("original_name", "Unknown").replace(".pdf", ""),
            report_id=assessment_json["report_id"]
        )
        print(f"Excel报告导出到: {excel_path}")
        return True
        
    except Exception as e:
        print(f"Excel导出失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # 为最新的MCG文件生成Excel报告
    mcg_file_id = "4420d330-4eb8-46e7-8fa6-1449946c57ec"
    success = generate_excel_for_processed_file(mcg_file_id)
    if success:
        print("Excel报告生成成功!")
    else:
        print("Excel报告生成失败!")