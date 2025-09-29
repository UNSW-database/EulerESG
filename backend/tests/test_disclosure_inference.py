"""
测试LLM驱动的披露推理模块（第4个核心模块）
"""

import requests
import json
import time
from pathlib import Path
from loguru import logger

BASE_URL = "http://localhost:8000"

def test_full_disclosure_workflow():
    """测试完整的披露分析工作流"""
    logger.info("开始测试LLM驱动的披露推理模块")
    
    # 步骤1: 检查系统状态
    logger.info("1. 检查系统状态")
    response = requests.get(f"{BASE_URL}/api/system/status")
    status = response.json()
    logger.info(f"系统状态: {status['status']}")
    logger.info(f"组件状态: {status['components']}")
    
    # 步骤2: 上传报告（如果需要）
    if not status['components']['report_loaded']:
        logger.info("2. 上传ESG报告")
        pdf_path = Path("dell (1).pdf")
        with open(pdf_path, "rb") as f:
            files = {"file": (pdf_path.name, f, "application/pdf")}
            response = requests.post(f"{BASE_URL}/api/upload-report", files=files)
            response.raise_for_status()
        logger.info("✅ 报告上传完成")
    else:
        logger.info("2. 报告已加载，跳过上传")
    
    # 步骤3: 上传指标（如果需要）
    if not status['components']['metrics_loaded']:
        logger.info("3. 上传ESG指标")
        response = requests.post(f"{BASE_URL}/api/upload-metrics")
        response.raise_for_status()
        logger.info("✅ 指标上传完成")
    else:
        logger.info("3. 指标已加载，跳过上传")
    
    # 步骤4: 执行合规分析（核心测试）
    logger.info("4. 🔥 执行LLM驱动的披露推理分析")
    logger.info("这是第4个核心模块的关键测试...")
    
    start_time = time.time()
    response = requests.post(f"{BASE_URL}/api/analyze-compliance")
    response.raise_for_status()
    end_time = time.time()
    
    result = response.json()
    logger.info(f"✅ 披露推理分析完成！用时: {end_time - start_time:.1f}秒")
    
    # 解析结果
    assessment = result['assessment']
    logger.info("🎉 LLM驱动的披露推理结果:")
    logger.info(f"  - 报告ID: {assessment['report_id']}")
    logger.info(f"  - 分析指标数: {assessment['total_metrics']}")
    logger.info(f"  - 整体合规分数: {assessment['overall_score']:.2%}")
    logger.info(f"  - 披露统计:")
    
    disclosure_summary = assessment['disclosure_summary']
    logger.info(f"    • 完全披露: {disclosure_summary.get('fully_disclosed', 0)} 个")
    logger.info(f"    • 部分披露: {disclosure_summary.get('partially_disclosed', 0)} 个") 
    logger.info(f"    • 未披露: {disclosure_summary.get('not_disclosed', 0)} 个")
    
    logger.info(f"  - 合规报告路径: {assessment['report_path']}")
    
    # 步骤5: 获取详细评估结果
    logger.info("5. 获取详细的披露分析结果")
    response = requests.get(f"{BASE_URL}/api/assessment")
    response.raise_for_status()
    
    detailed_result = response.json()
    logger.info("📊 详细分析结果:")
    logger.info(f"  - 评估日期: {detailed_result['assessment_date']}")
    logger.info(f"  - 分析的指标总数: {detailed_result['total_metrics']}")
    logger.info(f"  - 整体合规分数: {detailed_result['overall_score']:.2%}")
    
    # 显示前5个指标的具体分析
    logger.info("🔍 前5个指标的LLM分析结果:")
    for i, analysis in enumerate(detailed_result['metric_analyses'][:5], 1):
        logger.info(f"  指标 {i}: {analysis['metric_name']}")
        logger.info(f"    披露状态: {analysis['disclosure_status']}")
        logger.info(f"    Qwen置信度: {analysis.get('confidence_score', 1.0):.2f}")
        logger.info(f"    LLM分析理由: {analysis['reasoning'][:100]}...")
        logger.info("")
    
    return result

def test_chatbot_with_compliance_context():
    """测试聊天机器人基于合规分析的问答"""
    logger.info("6. 测试基于合规分析的智能问答")
    
    test_questions = [
        "根据分析结果，这份报告的整体合规表现如何？",
        "哪些指标还没有充分披露？请给出具体建议",
        "Qwen分析发现了哪些重要的披露问题？"
    ]
    
    for i, question in enumerate(test_questions, 1):
        logger.info(f"问题 {i}: {question}")
        
        chat_request = {
            "message": question,
            "include_context": True
        }
        
        response = requests.post(f"{BASE_URL}/api/chat", json=chat_request)
        response.raise_for_status()
        
        chat_result = response.json()
        logger.info(f"Qwen回答 (置信度: {chat_result.get('confidence', 1.0):.2f}):")
        logger.info(f"{chat_result['response'][:300]}...")
        logger.info("")

def main():
    """测试LLM驱动的披露推理模块"""
    try:
        # 测试完整工作流
        compliance_result = test_full_disclosure_workflow()
        
        # 测试基于结果的问答
        test_chatbot_with_compliance_context()
        
        logger.info("🎉 第4个核心模块测试完成！")
        logger.info("LLM驱动的披露推理功能完全正常！")
        return True
        
    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        return False

if __name__ == "__main__":
    success = main()
    if success:
        print("✅ 第4个核心模块（LLM驱动的披露推理）测试成功！")
    else:
        print("❌ 测试失败，请检查错误")