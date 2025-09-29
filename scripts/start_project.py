#!/usr/bin/env python3
"""
启动整个ESG项目（前端+后端）
"""

import subprocess
import time
import sys
import os
from pathlib import Path

def start_backend():
    """启动后端服务"""
    print("🔧 启动后端服务...")
    backend_script = Path(__file__).parent.parent / "backend" / "scripts" / "start_backend.py"
    
    return subprocess.Popen([
        sys.executable, 
        str(backend_script)
    ], cwd=str(backend_script.parent.parent))

def start_frontend():
    """启动前端服务"""
    print("🎨 启动前端服务...")
    frontend_dir = Path(__file__).parent.parent / "ESG-demo-main" / "frontend"
    
    return subprocess.Popen([
        "npm", "run", "dev"
    ], cwd=str(frontend_dir), shell=True)

def main():
    print("🚀 启动ESG完整系统...")
    print("=" * 50)
    
    # 启动后端
    backend_process = start_backend()
    print("⏳ 等待后端启动...")
    time.sleep(5)
    
    # 启动前端
    frontend_process = start_frontend()
    print("⏳ 等待前端启动...")
    time.sleep(3)
    
    print("=" * 50)
    print("✅ 系统启动完成!")
    print("🌐 前端地址: http://localhost:3001")
    print("🔧 后端地址: http://localhost:8000")
    print("📖 API文档: http://localhost:8000/docs")
    print("=" * 50)
    print("按 Ctrl+C 停止服务")
    
    try:
        # 等待进程结束
        while backend_process.poll() is None and frontend_process.poll() is None:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n🛑 停止服务...")
        
        # 终止进程
        backend_process.terminate()
        frontend_process.terminate()
        
        # 等待进程结束
        backend_process.wait(timeout=10)
        frontend_process.wait(timeout=10)
        
        print("👋 服务已停止")

if __name__ == "__main__":
    main()