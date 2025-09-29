#!/usr/bin/env python3
"""
ESG系统健康检查脚本
"""

import requests
import json
import time
from pathlib import Path
from loguru import logger

BASE_URL = "http://localhost:8000"

def check_backend_health():
    """检查后端健康状态"""
    print("=" * 60)
    print("🔧 后端健康检查")
    print("=" * 60)
    
    try:
        # 1. 检查系统状态
        print("1. 检查系统状态...")
        response = requests.get(f"{BASE_URL}/api/system/status", timeout=5)
        status_data = response.json()
        
        print(f"   ✅ 系统状态: {status_data['status']}")
        print(f"   📊 组件状态:")
        for component, status in status_data['components'].items():
            print(f"      - {component}: {'✅' if status else '❌'}")
        
        # 2. 测试聊天API
        print("\n2. 测试聊天API...")
        chat_request = {
            "message": "ESG是什么？",
            "include_context": False
        }
        response = requests.post(f"{BASE_URL}/api/chat", json=chat_request, timeout=10)
        chat_data = response.json()
        
        print(f"   ✅ 聊天API正常响应")
        print(f"   📝 会话ID: {chat_data['session_id']}")
        print(f"   💬 响应长度: {len(chat_data['response'])}字符")
        
        return True
        
    except Exception as e:
        print(f"   ❌ 后端检查失败: {e}")
        return False

def check_frontend_health():
    """检查前端健康状态"""
    print("\n=" * 60)
    print("🌐 前端健康检查")
    print("=" * 60)
    
    try:
        # 检查前端是否可访问
        print("1. 检查前端可访问性...")
        response = requests.get("http://localhost:3001", timeout=5)
        
        if response.status_code == 200:
            print("   ✅ 前端服务器正常运行")
            print(f"   📄 响应大小: {len(response.text)}字节")
            return True
        else:
            print(f"   ❌ 前端响应异常: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"   ❌ 前端检查失败: {e}")
        return False

def check_integration():
    """检查前后端集成"""
    print("\n=" * 60)
    print("🔗 前后端集成检查")
    print("=" * 60)
    
    try:
        # 测试CORS
        print("1. 测试CORS配置...")
        headers = {
            'Origin': 'http://localhost:3001',
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'Content-Type'
        }
        response = requests.options(f"{BASE_URL}/api/chat", headers=headers)
        
        if 'Access-Control-Allow-Origin' in response.headers:
            print("   ✅ CORS配置正确")
        else:
            print("   ⚠️ CORS可能有问题")
            
        return True
        
    except Exception as e:
        print(f"   ❌ 集成检查失败: {e}")
        return False

def generate_summary_report():
    """生成总结报告"""
    print("\n" + "=" * 60)
    print("📋 系统状态总结")
    print("=" * 60)
    
    backend_ok = check_backend_health()
    frontend_ok = check_frontend_health()
    integration_ok = check_integration()
    
    print(f"\n🎯 总体状态:")
    print(f"   后端服务: {'✅ 正常' if backend_ok else '❌ 异常'}")
    print(f"   前端服务: {'✅ 正常' if frontend_ok else '❌ 异常'}")
    print(f"   前后端集成: {'✅ 正常' if integration_ok else '❌ 异常'}")
    
    if backend_ok and frontend_ok and integration_ok:
        print(f"\n🎉 系统完全正常运行！")
        print(f"   📍 前端地址: http://localhost:3001")
        print(f"   🔧 后端API: http://localhost:8000")
        print(f"   📚 API文档: http://localhost:8000/docs")
        return True
    else:
        print(f"\n⚠️ 系统存在问题，请检查以上错误信息")
        return False

def main():
    """主函数"""
    print("🏥 ESG系统健康检查")
    print(f"检查时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    success = generate_summary_report()
    
    if success:
        print("\n✅ 所有检查通过 - 系统运行正常！")
        return 0
    else:
        print("\n❌ 发现问题 - 请根据上述信息排查")
        return 1

if __name__ == "__main__":
    exit(main())