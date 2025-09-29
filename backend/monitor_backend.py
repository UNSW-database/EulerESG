#!/usr/bin/env python3
"""
Backend monitoring and auto-recovery script
监控后端服务状态并在崩溃时自动重启
"""

import time
import requests
import subprocess
import sys
import os
from pathlib import Path

class BackendMonitor:
    def __init__(self):
        self.backend_url = "http://localhost:8000"
        self.health_endpoint = f"{self.backend_url}/api/health"
        self.backend_process = None
        self.backend_script = Path(__file__).parent / "src" / "esg_encoding" / "api.py"
        
    def check_health(self):
        """检查后端健康状态"""
        try:
            response = requests.get(self.health_endpoint, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get("status") == "healthy"
            return False
        except requests.RequestException:
            return False
    
    def start_backend(self):
        """启动后端服务"""
        try:
            cmd = [sys.executable, "-m", "src.esg_encoding.api"]
            self.backend_process = subprocess.Popen(
                cmd,
                cwd=Path(__file__).parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            print(f"✅ 后端服务已启动 (PID: {self.backend_process.pid})")
            return True
        except Exception as e:
            print(f"❌ 启动后端服务失败: {e}")
            return False
    
    def stop_backend(self):
        """停止后端服务"""
        if self.backend_process:
            try:
                self.backend_process.terminate()
                self.backend_process.wait(timeout=10)
                print("🛑 后端服务已停止")
            except subprocess.TimeoutExpired:
                self.backend_process.kill()
                print("🔪 强制终止后端服务")
    
    def restart_backend(self):
        """重启后端服务"""
        print("🔄 重启后端服务...")
        self.stop_backend()
        time.sleep(2)  # 等待端口释放
        return self.start_backend()
    
    def monitor(self, check_interval=30):
        """监控循环"""
        print("🎯 开始监控后端服务...")
        print(f"📊 检查间隔: {check_interval}秒")
        
        # 初始检查，如果服务不健康则启动
        if not self.check_health():
            print("⚠️  后端服务未运行，正在启动...")
            self.start_backend()
            time.sleep(10)  # 等待服务启动
        
        consecutive_failures = 0
        max_failures = 3
        
        while True:
            try:
                if self.check_health():
                    if consecutive_failures > 0:
                        print("✅ 服务恢复正常")
                        consecutive_failures = 0
                    # print("💚 服务运行正常")
                else:
                    consecutive_failures += 1
                    print(f"❌ 健康检查失败 ({consecutive_failures}/{max_failures})")
                    
                    if consecutive_failures >= max_failures:
                        print("🚨 服务连续失败，触发重启...")
                        if self.restart_backend():
                            consecutive_failures = 0
                            time.sleep(15)  # 等待服务完全启动
                        else:
                            print("💥 重启失败，等待下次检查...")
                
                time.sleep(check_interval)
                
            except KeyboardInterrupt:
                print("\n👋 监控已停止")
                self.stop_backend()
                break
            except Exception as e:
                print(f"❌ 监控异常: {e}")
                time.sleep(check_interval)

def main():
    """主函数"""
    monitor = BackendMonitor()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "start":
            monitor.start_backend()
        elif sys.argv[1] == "stop":
            monitor.stop_backend()
        elif sys.argv[1] == "restart":
            monitor.restart_backend()
        else:
            print("用法: python monitor_backend.py [start|stop|restart|monitor]")
    else:
        # 默认启动监控
        monitor.monitor()

if __name__ == "__main__":
    main()