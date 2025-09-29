"""
完整的ESG分析工作流示例
包含所有5个模块的集成使用
"""

import os
from pathlib import Path
from src.esg_encoding import (
    ProcessingConfig,
    ReportEncoder,
    MetricProcessor,
    DualChannelRetriever,
    DisclosureInferenceEngine,
    ESGChatbot,
    ChatRequest
)
from loguru import logger

# 配置日志
logger.add("esg_complete_workflow.log", rotation="10 MB")


def run_complete_esg_workflow(
    pdf_path: str,
    metrics_excel_path: str = None,
    llm_api_key: str = None,
    llm_base_url: str = None
):
    """
    运行完整的ESG分析工作流
    
    Args:
        pdf_path: ESG报告PDF文件路径
        metrics_excel_path: 指标Excel文件路径（可选）
        llm_api_key: LLM API密钥（可选）
        llm_base_url: LLM API基础URL（可选）
        
    Returns:
        tuple: (合规评估结果, 聊天机器人实例)
    """
    
    logger.info("=" * 80)
    logger.info("开始完整的ESG分析工作流")
    logger.info("=" * 80)
    
    # ========== 步骤1: 初始化配置 ==========
    logger.info("\n步骤1: 初始化系统配置")
    config = ProcessingConfig(
        embedding_model="BAAI/bge-m3",
        device="cpu",  # 或 "cuda" 如果有GPU
        batch_size=16,
        max_length=512,
        top_k=10,
        similarity_threshold=0.3,
        llm_api_key=llm_api_key,
        llm_model="gpt-3.5-turbo",
        llm_base_url=llm_base_url
    )
    
    # 初始化各模块
    report_encoder = ReportEncoder(config)
    metric_processor = MetricProcessor(config)
    dual_retriever = DualChannelRetriever(config)
    disclosure_engine = DisclosureInferenceEngine(config)
    chatbot = ESGChatbot(config)
    
    logger.info("✅ 系统组件初始化完成")
    
    # ========== 步骤2: 处理ESG报告 ==========
    logger.info("\n步骤2: 处理ESG报告")
    logger.info(f"报告文件: {pdf_path}")
    
    # 编码PDF报告
    report_content = report_encoder.encode_pdf(pdf_path, save_markdown=True)
    
    # 获取报告摘要
    summary = report_encoder.get_report_summary(report_content)
    logger.info(f"✅ 报告处理完成")
    logger.info(f"   - 文档ID: {report_content.document_id}")
    logger.info(f"   - 总段落数: {summary['total_segments']}")
    logger.info(f"   - 总页数: {summary['total_pages']}")
    
    # ========== 步骤3: 加载和处理ESG指标 ==========
    logger.info("\n步骤3: 加载和处理ESG指标")
    
    if metrics_excel_path and Path(metrics_excel_path).exists():
        # 从Excel加载指标
        logger.info(f"从Excel加载指标: {metrics_excel_path}")
        metric_collection = metric_processor.load_metrics_from_excel(metrics_excel_path)
    else:
        # 使用示例指标
        logger.info("使用示例ESG指标")
        metric_collection = metric_processor.create_sample_metrics()
    
    logger.info(f"加载了 {len(metric_collection.metrics)} 个指标")
    
    # 处理指标（语义扩展）
    if config.llm_api_key:
        logger.info("执行指标语义扩展...")
        processed_collection = metric_processor.process_metric_collection(metric_collection)
        logger.info("✅ 语义扩展完成")
    else:
        processed_collection = metric_collection
        logger.warning("⚠️ 未配置LLM API，跳过语义扩展")
    
    # 保存处理后的指标
    output_path = Path("outputs") / f"processed_metrics_{report_content.document_id}.json"
    output_path.parent.mkdir(exist_ok=True)
    metric_processor.save_metrics_to_file(processed_collection, str(output_path))
    logger.info(f"✅ 指标保存到: {output_path}")
    
    # ========== 步骤4: 双通道检索 ==========
    logger.info("\n步骤4: 执行双通道检索")
    
    retrieval_results = dual_retriever.retrieve_for_collection(
        report_content,
        processed_collection
    )
    
    logger.info(f"✅ 检索完成，处理了 {len(retrieval_results)} 个指标")
    
    # 生成检索报告
    retrieval_report = dual_retriever.generate_retrieval_report(retrieval_results)
    report_path = Path("outputs") / f"retrieval_report_{report_content.document_id}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(retrieval_report)
    logger.info(f"   检索报告保存到: {report_path}")
    
    # ========== 步骤5: 披露合规分析 ==========
    logger.info("\n步骤5: 执行披露合规分析")
    
    compliance_assessment = disclosure_engine.analyze_compliance(
        retrieval_results,
        report_content,
        pdf_path
    )
    
    logger.info(f"✅ 合规分析完成")
    logger.info(f"   - 整体合规分数: {compliance_assessment.overall_compliance_score:.2%}")
    logger.info(f"   - 完全披露: {compliance_assessment.disclosure_summary.get('fully_disclosed', 0)} 个")
    logger.info(f"   - 部分披露: {compliance_assessment.disclosure_summary.get('partially_disclosed', 0)} 个")
    logger.info(f"   - 未披露: {compliance_assessment.disclosure_summary.get('not_disclosed', 0)} 个")
    
    # 生成合规报告
    compliance_report = disclosure_engine.generate_compliance_report(compliance_assessment)
    report_path = Path("outputs") / f"compliance_report_{report_content.document_id}.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(compliance_report)
    logger.info(f"   合规报告保存到: {report_path}")
    
    # ========== 步骤6: 初始化聊天机器人 ==========
    logger.info("\n步骤6: 初始化ESG聊天机器人")
    
    # 加载上下文到聊天机器人
    chatbot.load_context(report_content, compliance_assessment)
    
    # 创建会话
    session_id = chatbot.create_session()
    logger.info(f"✅ 聊天机器人就绪，会话ID: {session_id}")
    
    # ========== 步骤7: 演示聊天功能 ==========
    logger.info("\n步骤7: 演示聊天功能")
    
    # 示例问题
    demo_questions = [
        "这份报告的整体ESG合规情况如何？",
        "哪些指标还没有充分披露？",
        "请解释一下什么是碳排放指标？",
        "报告中有关环境保护的主要措施是什么？"
    ]
    
    for i, question in enumerate(demo_questions[:2], 1):  # 演示前2个问题
        logger.info(f"\n问题{i}: {question}")
        
        request = ChatRequest(
            session_id=session_id,
            message=question,
            include_context=True
        )
        
        response = chatbot.chat(request)
        logger.info(f"回答 (置信度: {response.confidence:.2f}): {response.response[:200]}...")
        
        if response.relevant_segments:
            logger.info(f"相关段落: {', '.join(response.relevant_segments)}")
    
    # ========== 完成 ==========
    logger.info("\n" + "=" * 80)
    logger.info("✅ 完整的ESG分析工作流执行完成！")
    logger.info("=" * 80)
    
    # 输出总结
    logger.info("\n📊 分析总结:")
    logger.info(f"1. 报告包含 {summary['total_segments']} 个段落，{summary['total_pages']} 页")
    logger.info(f"2. 分析了 {len(processed_collection.metrics)} 个ESG指标")
    logger.info(f"3. 整体合规分数: {compliance_assessment.overall_compliance_score:.2%}")
    logger.info(f"4. 聊天机器人已就绪，可通过API进行交互")
    
    logger.info("\n📁 生成的文件:")
    logger.info(f"- Markdown报告: {pdf_path.replace('.pdf', '_extracted.md')}")
    logger.info(f"- 处理后的指标: outputs/processed_metrics_{report_content.document_id}.json")
    logger.info(f"- 检索报告: outputs/retrieval_report_{report_content.document_id}.md")
    logger.info(f"- 合规报告: outputs/compliance_report_{report_content.document_id}.md")
    
    return compliance_assessment, chatbot


def interactive_chat_demo(chatbot: ESGChatbot, session_id: str = None):
    """
    交互式聊天演示
    
    Args:
        chatbot: ESG聊天机器人实例
        session_id: 会话ID（可选）
    """
    if not session_id:
        session_id = chatbot.create_session()
    
    print("\n" + "=" * 60)
    print("ESG智能问答系统")
    print("=" * 60)
    print("输入您的问题，或输入 'quit' 退出")
    print("-" * 60)
    
    while True:
        # 获取用户输入
        user_input = input("\n您的问题: ").strip()
        
        if user_input.lower() in ['quit', 'exit', 'q']:
            print("感谢使用，再见！")
            break
        
        if not user_input:
            continue
        
        # 发送请求
        request = ChatRequest(
            session_id=session_id,
            message=user_input,
            include_context=True
        )
        
        # 获取回复
        response = chatbot.chat(request)
        
        # 显示回复
        print(f"\n机器人回复 (置信度: {response.confidence:.2f}):")
        print("-" * 60)
        print(response.response)
        
        if response.relevant_segments:
            print(f"\n参考段落: {', '.join(response.relevant_segments[:3])}")


if __name__ == "__main__":
    # 配置参数
    PDF_PATH = "dell (1).pdf"  # 替换为您的PDF文件路径
    EXCEL_PATH = "demo data - updated(1).xlsx"  # 可选：Excel指标文件
    
    # LLM配置（可选）
    # 如果没有配置，系统将使用基于规则的分析
    LLM_API_KEY = os.getenv("LLM_API_KEY", None)
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", None)
    
    # 检查文件
    if not Path(PDF_PATH).exists():
        logger.error(f"PDF文件不存在: {PDF_PATH}")
        exit(1)
    
    try:
        # 运行完整工作流
        assessment, chatbot = run_complete_esg_workflow(
            pdf_path=PDF_PATH,
            metrics_excel_path=EXCEL_PATH,
            llm_api_key=LLM_API_KEY,
            llm_base_url=LLM_BASE_URL
        )
        
        # 可选：启动交互式聊天
        print("\n是否启动交互式聊天？(y/n): ", end="")
        if input().lower() == 'y':
            interactive_chat_demo(chatbot)
        
    except Exception as e:
        logger.error(f"工作流执行失败: {e}")
        raise