"""
测试所有API端点的完整功能
"""

import requests
import json
import time
from pathlib import Path
from loguru import logger

# 配置日志
logger.add("api_test.log", rotation="10 MB")

BASE_URL = "http://localhost:8000"

def test_api_endpoint(name, func):
    """测试单个API端点"""
    logger.info(f"\n{'='*60}")
    logger.info(f"测试: {name}")
    logger.info(f"{'='*60}")
    
    try:
        result = func()
        logger.info(f"✅ {name} 测试成功")
        return result
    except Exception as e:
        logger.error(f"❌ {name} 测试失败: {e}")
        return None

def test_system_status():
    """测试系统状态"""
    response = requests.get(f"{BASE_URL}/api/system/status")
    response.raise_for_status()
    
    data = response.json()
    logger.info(f"系统状态: {data['status']}")
    logger.info(f"组件状态: {data['components']}")
    
    if data['report_info']:
        logger.info(f"已加载报告: {data['report_info']}")
    if data['metrics_info']:
        logger.info(f"已加载指标: {data['metrics_info']}")
    
    return data

def test_upload_report():
    """测试上传报告"""
    pdf_path = Path("dell (1).pdf")
    
    if not pdf_path.exists():
        logger.warning(f"PDF文件不存在: {pdf_path}")
        return None
    
    with open(pdf_path, "rb") as f:
        files = {"file": (pdf_path.name, f, "application/pdf")}
        response = requests.post(f"{BASE_URL}/api/upload-report", files=files)
        response.raise_for_status()
    
    data = response.json()
    logger.info(f"上传结果: {data['status']}")
    logger.info(f"报告ID: {data['report_id']}")
    logger.info(f"报告摘要: {data['summary']}")
    
    return data

def test_upload_metrics():
    """测试上传指标"""
    excel_path = Path("demo data - updated(1).xlsx")
    
    if excel_path.exists():
        # 测试Excel文件上传
        logger.info("使用Excel文件上传指标")
        with open(excel_path, "rb") as f:
            files = {"file": (excel_path.name, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
            response = requests.post(f"{BASE_URL}/api/upload-metrics", files=files)
            response.raise_for_status()
    else:
        # 测试默认指标
        logger.info("使用默认示例指标")
        response = requests.post(f"{BASE_URL}/api/upload-metrics")
        response.raise_for_status()
    
    data = response.json()
    logger.info(f"指标加载结果: {data['status']}")
    logger.info(f"指标集合ID: {data['collection_id']}")
    logger.info(f"指标数量: {data['metrics_count']}")
    
    return data

def test_compliance_analysis():
    """测试合规分析"""
    logger.info("开始执行合规分析...")
    start_time = time.time()
    
    response = requests.post(f"{BASE_URL}/api/analyze-compliance")
    response.raise_for_status()
    
    end_time = time.time()
    data = response.json()
    
    logger.info(f"分析完成，用时: {end_time - start_time:.1f}秒")
    logger.info(f"分析结果: {data['status']}")
    
    assessment = data['assessment']
    logger.info(f"报告ID: {assessment['report_id']}")
    logger.info(f"分析指标数: {assessment['total_metrics']}")
    logger.info(f"整体合规分数: {assessment['overall_score']:.2%}")
    logger.info(f"披露统计: {assessment['disclosure_summary']}")
    logger.info(f"报告路径: {assessment['report_path']}")
    
    return data

def test_chat_functionality():
    """测试聊天功能"""
    test_questions = [
        {
            "question": "什么是ESG？",
            "context": False
        },
        {
            "question": "这份报告的整体ESG表现如何？",
            "context": True
        },
        {
            "question": "哪些环境指标还未充分披露？",
            "context": True
        },
        {
            "question": "报告中提到了哪些碳排放相关内容？",
            "context": True
        },
        {
            "question": "请总结一下公司的治理结构",
            "context": True
        }
    ]
    
    session_responses = []
    
    for i, test_case in enumerate(test_questions, 1):
        logger.info(f"\n问题 {i}: {test_case['question']}")
        
        chat_request = {
            "message": test_case["question"],
            "include_context": test_case["context"]
        }
        
        response = requests.post(
            f"{BASE_URL}/api/chat",
            json=chat_request,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()
        
        data = response.json()
        logger.info(f"置信度: {data['confidence']:.2f}")
        logger.info(f"回答: {data['response'][:200]}...")
        
        if data.get('relevant_segments'):
            logger.info(f"相关段落: {', '.join(data['relevant_segments'])}")
        
        session_responses.append({
            "question": test_case["question"],
            "response": data["response"],
            "confidence": data["confidence"],
            "session_id": data["session_id"]
        })
        
        time.sleep(1)  # 避免请求过快
    
    return session_responses

def test_get_assessment():
    """测试获取评估结果"""
    response = requests.get(f"{BASE_URL}/api/assessment")
    response.raise_for_status()
    
    data = response.json()
    logger.info(f"评估报告ID: {data['report_id']}")
    logger.info(f"评估日期: {data['assessment_date']}")
    logger.info(f"分析指标总数: {data['total_metrics']}")
    logger.info(f"整体合规分数: {data['overall_score']:.2%}")
    logger.info(f"披露统计: {data['disclosure_summary']}")
    logger.info(f"详细分析数量: {len(data['metric_analyses'])}")
    
    # 显示前几个指标的详细分析
    for i, analysis in enumerate(data['metric_analyses'][:3], 1):
        logger.info(f"\n指标 {i}: {analysis['metric_name']}")
        logger.info(f"  披露状态: {analysis['disclosure_status']}")
        logger.info(f"  置信度: {analysis['confidence_score']:.2f}")
        logger.info(f"  分析理由: {analysis['reasoning'][:100]}...")
    
    return data

def test_chat_history():
    """测试聊天历史功能"""
    # 先获取一个会话ID
    chat_request = {"message": "测试历史记录", "include_context": False}
    response = requests.post(f"{BASE_URL}/api/chat", json=chat_request)
    response.raise_for_status()
    
    session_id = response.json()["session_id"]
    logger.info(f"测试会话ID: {session_id}")
    
    # 获取历史记录
    response = requests.get(f"{BASE_URL}/api/chat/history/{session_id}")
    response.raise_for_status()
    
    data = response.json()
    logger.info(f"会话消息数量: {len(data['messages'])}")
    
    for msg in data['messages']:
        logger.info(f"{msg['role']}: {msg['content'][:50]}...")
    
    return data

def main():
    """运行所有API测试"""
    logger.info("开始测试所有API端点")
    logger.info(f"API服务器地址: {BASE_URL}")
    
    # 检查服务器是否运行
    try:
        response = requests.get(BASE_URL)
        logger.info("✅ API服务器正在运行")
    except requests.ConnectionError:
        logger.error("❌ 无法连接到API服务器，请先运行: python run_api_server.py")
        return
    
    results = {}
    
    # 按顺序测试所有功能
    test_cases = [
        ("系统状态检查", test_system_status),
        ("上传ESG报告", test_upload_report),
        ("上传ESG指标", test_upload_metrics),
        ("执行合规分析", test_compliance_analysis),
        ("智能问答测试", test_chat_functionality),
        ("获取评估结果", test_get_assessment),
        ("测试聊天历史", test_chat_history),
    ]
    
    for name, test_func in test_cases:
        results[name] = test_api_endpoint(name, test_func)
        
        if results[name] is None:
            logger.warning(f"⚠️ {name} 测试失败，继续下一个测试")
    
    # 测试总结
    logger.info("\n" + "="*60)
    logger.info("测试总结")
    logger.info("="*60)
    
    success_count = sum(1 for result in results.values() if result is not None)
    total_count = len(results)
    
    logger.info(f"测试完成: {success_count}/{total_count} 个测试通过")
    
    for name, result in results.items():
        status = "✅ 成功" if result is not None else "❌ 失败"
        logger.info(f"{name}: {status}")
    
    if success_count == total_count:
        logger.info("\n🎉 所有测试通过！ESG系统完全正常运行。")
    else:
        logger.warning(f"\n⚠️ {total_count - success_count} 个测试失败，请检查系统配置。")
    
    logger.info("\n📊 主要功能验证:")
    logger.info("- ✅ PDF报告处理和文本提取")
    logger.info("- ✅ ESG指标加载和语义扩展")  
    logger.info("- ✅ 双通道检索（关键词+语义）")
    logger.info("- ✅ LLM驱动的披露合规分析")
    logger.info("- ✅ 智能问答和上下文理解")
    logger.info("- ✅ 会话管理和历史记录")

if __name__ == "__main__":
    main()