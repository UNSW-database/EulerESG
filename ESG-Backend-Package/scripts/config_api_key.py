"""
配置API Key的简单示例
在这里直接设置你的API密钥，然后运行系统
"""

import os

# 直接设置API密钥（临时方法）
os.environ["LLM_API_KEY"] = "sk-14fc93b5849a41e2bcfef1083657bf46"
os.environ["LLM_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"
os.environ["LLM_MODEL"] = "qwen-plus-2025-07-28"

# 现在可以导入和使用ESG系统
from complete_workflow import run_complete_esg_workflow

if __name__ == "__main__":
    print("使用配置的API密钥运行ESG分析...")
    
    # 检查文件是否存在
    pdf_path = "dell (1).pdf"
    excel_path = "demo data - updated(1).xlsx"
    
    if not os.path.exists(pdf_path):
        print(f"请确保PDF文件存在: {pdf_path}")
        exit(1)
    
    # 运行完整工作流
    assessment, chatbot = run_complete_esg_workflow(
        pdf_path=pdf_path,
        metrics_excel_path=excel_path,
        llm_api_key=os.environ["LLM_API_KEY"],
        llm_base_url=os.environ["LLM_BASE_URL"]
    )
    
    print("\n系统配置成功！现在可以:")
    print("1. 运行 python run_api_server.py 启动API服务器")
    print("2. 访问 http://localhost:8000/docs 查看API文档")