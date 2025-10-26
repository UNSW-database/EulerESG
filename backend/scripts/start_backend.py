#!/usr/bin/env python3
"""
启动ESG后端服务
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# 添加src目录到Python路径
backend_dir = Path(__file__).parent.parent
src_dir = backend_dir / "src"
sys.path.insert(0, str(src_dir))

# 切换工作目录到backend根目录
os.chdir(backend_dir)

# 加载环境变量
env_file = backend_dir / "config" / ".env"
if env_file.exists():
    load_dotenv(env_file)
    print(f"已加载环境变量: {env_file}")
    if os.getenv('LLM_API_KEY'):
        print(f"已配置: LLM API Key={os.getenv('LLM_API_KEY')}")
        print(f"LLM URL = {os.getenv('LLM_BASE_URL')}")
    else:
        print("未配置 LLM API Key!")
else:
    print(f"环境变量文件不存在: {env_file}")

if __name__ == "__main__":
    try:
        from esg_encoding.api import app
        import uvicorn
        
        print("启动ESG后端服务...")
        print(f"工作目录: {os.getcwd()}")
        print(f"API地址: http://localhost:8000")
        print(f"API文档: http://localhost:8000/docs")
        
        uvicorn.run(
            app, 
            host="0.0.0.0", 
            port=8000, 
            log_level="info"
        )
        
    except KeyboardInterrupt:
        print("\n服务已停止")
    except Exception as e:
        print(f"启动失败: {e}")
        sys.exit(1)