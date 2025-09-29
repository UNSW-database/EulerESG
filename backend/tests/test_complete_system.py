"""
测试完整ESG系统的所有功能
"""

import os
from pathlib import Path
import json
from src.esg_encoding import (
    ProcessingConfig,
    ReportEncoder,
    MetricProcessor,
    DualChannelRetriever,
    DisclosureInferenceEngine,
    ESGChatbot,
    ChatRequest,
    DisclosureStatus
)
from loguru import logger

# 配置日志
logger.add("test_complete_system.log", rotation="10 MB")


def test_complete_system():
    """测试完整系统的所有功能"""
    
    logger.info("开始测试完整的ESG分析系统")
    
    # 测试文件
    pdf_path = "dell (1).pdf"
    excel_path = "demo data - updated(1).xlsx"
    
    # 检查测试文件
    if not Path(pdf_path).exists():
        logger.warning(f"测试PDF文件不存在: {pdf_path}")
        logger.info("请确保有测试PDF文件")
        return False
    
    # 创建配置
    config = ProcessingConfig(
        embedding_model="BAAI/bge-m3",
        device="cpu",
        batch_size=8,
        max_length=512,
        top_k=5,
        similarity_threshold=0.3
    )
    
    success_count = 0
    total_tests = 6
    
    # ========== 测试1: 报告编码 ==========
    logger.info("\n测试1: 报告编码模块")
    try:
        report_encoder = ReportEncoder(config)
        report_content = report_encoder.encode_pdf(pdf_path, save_markdown=True)
        summary = report_encoder.get_report_summary(report_content)
        
        assert report_content is not None
        assert len(report_content.document_content.segments) > 0
        assert len(report_content.embeddings) > 0
        
        logger.info(f"✅ 报告编码成功: {summary['total_segments']} 段落, {summary['total_pages']} 页")
        success_count += 1
    except Exception as e:
        logger.error(f"❌ 报告编码失败: {e}")
    
    # ========== 测试2: 指标处理 ==========
    logger.info("\n测试2: 指标处理模块")
    try:
        metric_processor = MetricProcessor(config)
        
        # 测试创建示例指标
        metric_collection = metric_processor.create_sample_metrics()
        assert len(metric_collection.metrics) > 0
        
        # 测试从Excel加载（如果文件存在）
        if Path(excel_path).exists():
            excel_metrics = metric_processor.load_metrics_from_excel(excel_path)
            logger.info(f"从Excel加载了 {len(excel_metrics.metrics)} 个指标")
        
        logger.info(f"✅ 指标处理成功: {len(metric_collection.metrics)} 个指标")
        success_count += 1
    except Exception as e:
        logger.error(f"❌ 指标处理失败: {e}")
    
    # ========== 测试3: 双通道检索 ==========
    logger.info("\n测试3: 双通道检索模块")
    try:
        dual_retriever = DualChannelRetriever(config)
        
        # 执行检索
        retrieval_results = dual_retriever.retrieve_for_collection(
            report_content,
            metric_collection
        )
        
        assert len(retrieval_results) > 0
        
        # 统计检索结果
        total_matches = sum(r.total_matches for r in retrieval_results)
        avg_matches = total_matches / len(retrieval_results) if retrieval_results else 0
        
        logger.info(f"✅ 双通道检索成功: 平均每指标 {avg_matches:.1f} 个匹配")
        success_count += 1
    except Exception as e:
        logger.error(f"❌ 双通道检索失败: {e}")
    
    # ========== 测试4: 披露推理 ==========
    logger.info("\n测试4: 披露推理模块")
    try:
        disclosure_engine = DisclosureInferenceEngine(config)
        
        # 执行合规分析
        compliance_assessment = disclosure_engine.analyze_compliance(
            retrieval_results,
            report_content,
            pdf_path
        )
        
        assert compliance_assessment is not None
        assert compliance_assessment.total_metrics_analyzed > 0
        assert 0 <= compliance_assessment.overall_compliance_score <= 1
        
        # 生成报告
        compliance_report = disclosure_engine.generate_compliance_report(compliance_assessment)
        assert len(compliance_report) > 0
        
        logger.info(f"✅ 披露推理成功: 合规分数 {compliance_assessment.overall_compliance_score:.2%}")
        logger.info(f"   - 完全披露: {compliance_assessment.disclosure_summary.get(DisclosureStatus.FULLY_DISCLOSED, 0)}")
        logger.info(f"   - 部分披露: {compliance_assessment.disclosure_summary.get(DisclosureStatus.PARTIALLY_DISCLOSED, 0)}")
        logger.info(f"   - 未披露: {compliance_assessment.disclosure_summary.get(DisclosureStatus.NOT_DISCLOSED, 0)}")
        success_count += 1
    except Exception as e:
        logger.error(f"❌ 披露推理失败: {e}")
    
    # ========== 测试5: 聊天机器人 ==========
    logger.info("\n测试5: ESG聊天机器人模块")
    try:
        chatbot = ESGChatbot(config)
        
        # 加载上下文
        chatbot.load_context(report_content, compliance_assessment)
        
        # 创建会话
        session_id = chatbot.create_session()
        assert session_id is not None
        
        # 测试问答
        test_questions = [
            "这份报告的整体合规情况如何？",
            "什么是ESG？",
            "报告中提到了哪些环境指标？"
        ]
        
        for question in test_questions:
            request = ChatRequest(
                session_id=session_id,
                message=question,
                include_context=True
            )
            
            response = chatbot.chat(request)
            assert response is not None
            assert len(response.response) > 0
            assert 0 <= response.confidence <= 1
            
            logger.info(f"   问: {question[:50]}...")
            logger.info(f"   答: {response.response[:100]}...")
        
        # 测试会话历史
        history = chatbot.get_session_history(session_id)
        assert history is not None
        assert len(history) == len(test_questions) * 2  # 问题和回答
        
        logger.info(f"✅ 聊天机器人测试成功: 处理了 {len(test_questions)} 个问题")
        success_count += 1
    except Exception as e:
        logger.error(f"❌ 聊天机器人测试失败: {e}")
    
    # ========== 测试6: 数据持久化 ==========
    logger.info("\n测试6: 数据持久化")
    try:
        # 保存指标
        output_dir = Path("test_outputs")
        output_dir.mkdir(exist_ok=True)
        
        metrics_path = output_dir / "test_metrics.json"
        metric_processor.save_metrics_to_file(metric_collection, str(metrics_path))
        assert metrics_path.exists()
        
        # 重新加载验证
        loaded_metrics = metric_processor.load_metrics_from_file(str(metrics_path))
        assert len(loaded_metrics.metrics) == len(metric_collection.metrics)
        
        # 导出聊天会话
        session_data = chatbot.export_session(session_id)
        assert session_data is not None
        
        session_path = output_dir / "test_session.json"
        with open(session_path, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)
        assert session_path.exists()
        
        logger.info("✅ 数据持久化测试成功")
        success_count += 1
        
        # 清理测试文件
        metrics_path.unlink()
        session_path.unlink()
        output_dir.rmdir()
        
    except Exception as e:
        logger.error(f"❌ 数据持久化测试失败: {e}")
    
    # ========== 测试总结 ==========
    logger.info("\n" + "=" * 60)
    logger.info(f"测试完成: {success_count}/{total_tests} 个测试通过")
    
    if success_count == total_tests:
        logger.info("✅ 所有测试通过！系统运行正常。")
        return True
    else:
        logger.warning(f"⚠️ {total_tests - success_count} 个测试失败，请检查系统配置。")
        return False


def test_api_endpoints():
    """测试API端点（需要先启动API服务器）"""
    import requests
    
    base_url = "http://localhost:8000"
    
    logger.info("\n测试API端点")
    logger.info("注意：需要先运行 python run_api_server.py 启动API服务器")
    
    try:
        # 测试根路径
        response = requests.get(f"{base_url}/")
        if response.status_code == 200:
            logger.info("✅ API服务器运行正常")
            data = response.json()
            logger.info(f"   版本: {data.get('version')}")
        else:
            logger.warning("⚠️ API服务器未响应")
            return False
        
        # 测试系统状态
        response = requests.get(f"{base_url}/api/system/status")
        if response.status_code == 200:
            status = response.json()
            logger.info("✅ 系统状态端点正常")
            logger.info(f"   状态: {status.get('status')}")
        
        return True
        
    except requests.ConnectionError:
        logger.error("❌ 无法连接到API服务器，请先运行: python run_api_server.py")
        return False
    except Exception as e:
        logger.error(f"❌ API测试失败: {e}")
        return False


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("ESG完整系统测试")
    logger.info("=" * 60)
    
    # 运行完整系统测试
    system_ok = test_complete_system()
    
    # 可选：测试API端点
    print("\n是否测试API端点？(需要先启动API服务器) (y/n): ", end="")
    if input().lower() == 'y':
        api_ok = test_api_endpoints()
    else:
        api_ok = None
    
    # 最终结果
    logger.info("\n" + "=" * 60)
    if system_ok:
        logger.info("✅ 系统测试通过")
        if api_ok:
            logger.info("✅ API测试通过")
        elif api_ok is False:
            logger.info("❌ API测试失败")
        
        logger.info("\n下一步:")
        logger.info("1. 运行完整工作流: python complete_workflow.py")
        logger.info("2. 启动API服务器: python run_api_server.py")
        logger.info("3. 访问API文档: http://localhost:8000/docs")
    else:
        logger.error("❌ 系统测试失败，请检查错误日志")
    logger.info("=" * 60)