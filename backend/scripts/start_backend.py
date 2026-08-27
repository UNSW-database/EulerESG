#!/usr/bin/env python3
"""Start the ESG backend with a safe endpoint preflight."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BACKEND_DIR / "src"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def _parse_port(raw_value: object) -> int:
    raw = str(raw_value or DEFAULT_PORT).strip()
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"API_PORT 必须是整数，当前值为 {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"API_PORT 必须在 1 到 65535 之间，当前值为 {port}")
    return port


def _client_host(server_host: str) -> str:
    host = str(server_host or DEFAULT_HOST).strip()
    if host in {"0.0.0.0", "::", "[::]"}:
        return "127.0.0.1"
    return host.strip("[]")


def _format_http_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _probe_existing_backend(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True only when the occupied endpoint is this backend's health API."""
    client_host = _format_http_host(_client_host(host))
    health_url = f"http://{client_host}:{port}/api/health"
    try:
        with urlopen(health_url, timeout=timeout) as response:  # noqa: S310
            if int(getattr(response, "status", 0) or 0) != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return False
    except OSError:
        return False

    return bool(
        isinstance(payload, dict)
        and payload.get("status") == "healthy"
        and isinstance(payload.get("services"), dict)
        and payload["services"].get("api") == "running"
    )


def _bind_error(host: str, port: int) -> Optional[OSError]:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    if family == socket.AF_INET6:
        address = (host.strip("[]"), port, 0, 0)
    else:
        address = (host, port)

    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind(address)
    except OSError as exc:
        return exc
    finally:
        probe.close()
    return None


def _classify_endpoint(
    host: str,
    port: int,
    *,
    health_attempts: int = 5,
    health_retry_seconds: float = 0.25,
) -> Tuple[str, Optional[str]]:
    """Classify the configured endpoint before importing/initializing the app."""
    if _probe_existing_backend(host, port):
        return "existing_backend", None
    bind_error = _bind_error(host, port)
    if bind_error is None:
        return "available", None
    # Uvicorn reload keeps the socket occupied while the health endpoint is
    # briefly unavailable. Retry before reporting a false port conflict.
    for _ in range(max(0, health_attempts - 1)):
        time.sleep(max(0.0, health_retry_seconds))
        if _probe_existing_backend(host, port):
            return "existing_backend", None
    return "occupied", str(bind_error)


def _load_environment() -> Path:
    env_file = BACKEND_DIR / "config" / ".env"
    if env_file.is_file():
        load_dotenv(env_file, override=False)
        print(f"已加载环境变量: {env_file}")
    else:
        print(f"环境变量文件不存在: {env_file}")

    print(
        "LLM API Key: 已配置"
        if os.getenv("LLM_API_KEY")
        else "LLM API Key: 未配置"
    )
    if os.getenv("LLM_BASE_URL"):
        print(f"LLM URL: {os.getenv('LLM_BASE_URL')}")
    return env_file


def _missing_runtime_dependency(exc: ModuleNotFoundError) -> str:
    missing_name = str(getattr(exc, "name", "") or "unknown")
    return (
        f"当前 Python 缺少后端依赖 {missing_name!r}: {sys.executable}\n"
        "请先运行：python -m pip install -r requirements.txt\n"
        "如果依赖已安装在 Conda 环境中，请先激活该环境再启动后端。"
    )


def main() -> int:
    os.chdir(BACKEND_DIR)
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    try:
        _load_environment()
        host = str(os.getenv("API_HOST", DEFAULT_HOST) or DEFAULT_HOST).strip()
        port = _parse_port(os.getenv("API_PORT", str(DEFAULT_PORT)))
        log_level = str(os.getenv("API_LOG_LEVEL", "info") or "info").strip().lower()
        client_host = _format_http_host(_client_host(host))
        base_url = f"http://{client_host}:{port}"

        endpoint_state, endpoint_error = _classify_endpoint(host, port)
        if endpoint_state == "existing_backend":
            print(f"后端已经在 {base_url} 运行，无需重复启动。")
            print(f"API 文档: {base_url}/docs")
            return 0
        if endpoint_state == "occupied":
            print(f"启动失败: {host}:{port} 已被其他程序占用或当前用户无权监听。")
            if endpoint_error:
                print(f"系统错误: {endpoint_error}")
            print(
                "请停止占用该端口的程序，或在 backend/config/.env 中设置其他 "
                "API_PORT；更改端口时也需同步配置前端 NEXT_PUBLIC_API_BASE_URL。"
            )
            return 2

        try:
            from esg_encoding.api import app
            import uvicorn
        except ModuleNotFoundError as exc:
            print(_missing_runtime_dependency(exc))
            return 3

        print("启动 ESG 后端服务...")
        print(f"工作目录: {os.getcwd()}")
        print(f"API 地址: {base_url}")
        print(f"API 文档: {base_url}/docs")

        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=log_level,
        )
        return 0
    except KeyboardInterrupt:
        print("\n服务已停止")
        return 0
    except ValueError as exc:
        print(f"启动配置错误: {exc}")
        return 2
    except Exception as exc:
        print(f"启动失败: {exc}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
