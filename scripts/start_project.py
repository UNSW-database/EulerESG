#!/usr/bin/env python3
"""Start the complete ESG application with local runtime preflight checks."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterator, Optional
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "ESG-demo-main" / "frontend"
BACKEND_URL = str(os.getenv("BACKEND_URL") or "http://127.0.0.1:8000").rstrip("/")
BACKEND_HEALTH_URL = f"{BACKEND_URL}/api/health"
DEFAULT_FRONTEND_PORT = 3001


def _configure_console_output() -> None:
    """Prevent Windows legacy consoles from crashing on unencodable output."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except (LookupError, OSError, ValueError):
                pass


def _frontend_port() -> int:
    raw = str(os.getenv("FRONTEND_PORT") or DEFAULT_FRONTEND_PORT).strip()
    try:
        port = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"FRONTEND_PORT 必须是整数, 当前值为 {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"FRONTEND_PORT 必须在 1 到 65535 之间, 当前值为 {port}")
    return port


def _frontend_url() -> str:
    configured = str(os.getenv("FRONTEND_URL") or "").strip().rstrip("/")
    return configured or f"http://127.0.0.1:{_frontend_port()}"


def backend_is_healthy(timeout: float = 1.5) -> bool:
    try:
        with urlopen(BACKEND_HEALTH_URL, timeout=timeout) as response:  # noqa: S310
            return int(getattr(response, "status", 0) or 0) == 200
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def frontend_is_healthy(timeout: float = 1.5) -> bool:
    try:
        with urlopen(_frontend_url(), timeout=timeout) as response:  # noqa: S310
            status = int(getattr(response, "status", 0) or 0)
            return 200 <= status < 400
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        return False


def _node_version_key(path: Path) -> tuple[int, ...]:
    version = tuple(int(value) for value in re.findall(r"\d+", path.parent.name))
    if not version:
        return (0,)
    # Prefer an even-numbered LTS line over a newer odd-numbered release.
    return (1 if version[0] % 2 == 0 else 0, *version)


def _node_candidates() -> Iterator[Path]:
    executable_name = "node.exe" if os.name == "nt" else "node"

    for command_name in (executable_name, "node"):
        resolved = shutil.which(command_name)
        if resolved:
            yield Path(resolved)

    nvm_symlink = str(os.getenv("NVM_SYMLINK") or "").strip()
    if nvm_symlink:
        yield Path(nvm_symlink) / executable_name

    nvm_home = str(os.getenv("NVM_HOME") or "").strip()
    if nvm_home:
        try:
            versioned = sorted(
                Path(nvm_home).glob(f"v*/{executable_name}"),
                key=_node_version_key,
                reverse=True,
            )
            yield from versioned
        except OSError:
            pass

    if os.name == "nt":
        for environment_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = str(os.getenv(environment_name) or "").strip()
            if root:
                yield Path(root) / "nodejs" / executable_name
        local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
        if local_app_data:
            yield Path(local_app_data) / "Programs" / "nodejs" / executable_name


def _resolve_node_executable() -> Path:
    configured = str(os.getenv("FRONTEND_NODE") or "").strip().strip('"')
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_dir():
            candidate = candidate / ("node.exe" if os.name == "nt" else "node")
        if candidate.is_file():
            return candidate.resolve()
        raise RuntimeError(f"FRONTEND_NODE 指向的 Node 不存在: {candidate}")

    seen: set[str] = set()
    for candidate in _node_candidates():
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()

    raise RuntimeError(
        "未找到 Node.js。请安装 Node.js, 修复 NVM_SYMLINK, "
        "或设置 FRONTEND_NODE 指向 node.exe。"
    )


def _frontend_next_cli() -> Path:
    next_cli = FRONTEND_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
    if not next_cli.is_file():
        raise RuntimeError(
            f"前端依赖尚未安装: {next_cli}。请先在 {FRONTEND_DIR} 执行 npm install。"
        )
    return next_cli


def _python_in_environment(environment_directory: Path) -> Path:
    if os.name == "nt":
        return environment_directory / "python.exe"
    return environment_directory / "bin" / "python"


def _backend_python_candidates() -> Iterator[Path]:
    yield Path(sys.executable)

    conda_prefix = str(os.getenv("CONDA_PREFIX") or "").strip()
    if conda_prefix:
        yield _python_in_environment(Path(conda_prefix))

    conda_roots: list[Path] = []
    executable = Path(sys.executable).resolve()
    executable_environment = executable.parent
    if executable_environment.parent.name.lower() == "envs":
        conda_roots.append(executable_environment.parent.parent)
    else:
        conda_roots.append(executable_environment)

    conda_executable = str(os.getenv("CONDA_EXE") or "").strip()
    if conda_executable:
        conda_path = Path(conda_executable)
        conda_roots.append(
            conda_path.parent.parent
            if conda_path.parent.name.lower() in {"scripts", "bin"}
            else conda_path.parent
        )

    configured_env_directories = str(os.getenv("CONDA_ENVS_PATH") or "").strip()
    env_directories = [
        Path(value)
        for value in configured_env_directories.split(os.pathsep)
        if value.strip()
    ]
    env_directories.extend(root / "envs" for root in conda_roots)

    seen_directories: set[str] = set()
    for envs_directory in env_directories:
        directory_key = os.path.normcase(str(envs_directory))
        if directory_key in seen_directories:
            continue
        seen_directories.add(directory_key)
        try:
            environments = sorted(
                (path for path in envs_directory.iterdir() if path.is_dir()),
                key=lambda path: (path.name.casefold() != "es", path.name.casefold()),
            )
        except OSError:
            continue
        for environment in environments:
            yield _python_in_environment(environment)


def _python_has_backend_dependencies(python_executable: Path) -> bool:
    try:
        completed = subprocess.run(
            [
                str(python_executable),
                "-c",
                "import fastapi, openai, uvicorn",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _resolve_backend_python() -> Path:
    configured = str(os.getenv("BACKEND_PYTHON") or "").strip().strip('"')
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_file():
            raise RuntimeError(f"BACKEND_PYTHON 指向的 Python 不存在: {candidate}")
        if not _python_has_backend_dependencies(candidate):
            raise RuntimeError(
                f"BACKEND_PYTHON 缺少后端依赖 fastapi/openai/uvicorn: {candidate}"
            )
        return candidate.resolve()

    seen: set[str] = set()
    for candidate in _backend_python_candidates():
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and _python_has_backend_dependencies(candidate):
            return candidate.resolve()

    raise RuntimeError(
        "未找到包含后端依赖的 Python 环境。请激活正确的 Conda 环境, "
        "或设置 BACKEND_PYTHON 指向该环境的 python.exe。"
    )


def start_backend() -> Optional[subprocess.Popen]:
    """Start the backend unless a healthy instance already owns the endpoint."""
    if backend_is_healthy():
        print(f"[OK] 后端已经运行: {BACKEND_URL}")
        return None

    print("[INFO] 启动后端服务...")
    backend_script = PROJECT_ROOT / "backend" / "scripts" / "start_backend.py"
    backend_python = _resolve_backend_python()
    print(f"[INFO] Python: {backend_python}")
    return subprocess.Popen(
        [str(backend_python), str(backend_script)],
        cwd=str(backend_script.parent.parent),
    )


def wait_for_backend(process: Optional[subprocess.Popen], timeout_seconds: float) -> None:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        if backend_is_healthy():
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"后端进程提前退出, exit_code={process.returncode}。"
                "请检查上方错误; 如使用 Conda, 可设置 BACKEND_PYTHON 指向正确环境。"
            )
        time.sleep(0.5)
    raise TimeoutError(
        f"后端未在 {timeout_seconds:.0f} 秒内通过健康检查: {BACKEND_HEALTH_URL}"
    )


def start_frontend() -> Optional[subprocess.Popen]:
    """Start Next.js directly with a discovered Node executable."""
    if frontend_is_healthy():
        print(f"[OK] 前端已经运行: {_frontend_url()}")
        return None

    node_executable = _resolve_node_executable()
    next_cli = _frontend_next_cli()
    child_environment = os.environ.copy()
    existing_path = str(child_environment.get("PATH") or "")
    child_environment["PATH"] = (
        str(node_executable.parent)
        + (os.pathsep + existing_path if existing_path else "")
    )

    print("[INFO] 启动前端服务...")
    print(f"[INFO] Node: {node_executable}")
    return subprocess.Popen(
        [
            str(node_executable),
            str(next_cli),
            "dev",
            "-p",
            str(_frontend_port()),
        ],
        cwd=str(FRONTEND_DIR),
        env=child_environment,
    )


def wait_for_frontend(process: Optional[subprocess.Popen], timeout_seconds: float) -> None:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while time.monotonic() < deadline:
        if frontend_is_healthy():
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError(
                f"前端进程提前退出, exit_code={process.returncode}。请检查上方 Next.js 错误。"
            )
        time.sleep(0.5)
    raise TimeoutError(
        f"前端未在 {timeout_seconds:.0f} 秒内启动: {_frontend_url()}"
    )


def _stop_process(process: Optional[subprocess.Popen]) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    _configure_console_output()
    print("[INFO] 启动 ESG 完整系统...")
    print("=" * 50)
    backend_process: Optional[subprocess.Popen] = None
    frontend_process: Optional[subprocess.Popen] = None
    try:
        backend_process = start_backend()
        print("[INFO] 等待后端健康检查...")
        wait_for_backend(
            backend_process,
            float(os.getenv("BACKEND_START_TIMEOUT_SECONDS", "90") or "90"),
        )

        frontend_process = start_frontend()
        print("[INFO] 等待前端健康检查...")
        wait_for_frontend(
            frontend_process,
            float(os.getenv("FRONTEND_START_TIMEOUT_SECONDS", "120") or "120"),
        )

        print("=" * 50)
        print("[OK] 系统启动完成!")
        print(f"[INFO] 前端地址: {_frontend_url()}")
        print(f"[INFO] 后端地址: {BACKEND_URL}")
        print(f"[INFO] API 文档: {BACKEND_URL}/docs")
        print("=" * 50)

        if backend_process is None and frontend_process is None:
            print("[OK] 前端和后端均已运行, 无需重复启动。")
            return 0

        print("[INFO] 按 Ctrl+C 停止本次启动的服务")
        while True:
            if backend_process is not None and backend_process.poll() is not None:
                raise RuntimeError(
                    f"后端进程已退出, exit_code={backend_process.returncode}"
                )
            if frontend_process is not None and frontend_process.poll() is not None:
                raise RuntimeError(
                    f"前端进程已退出, exit_code={frontend_process.returncode}"
                )
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] 正在停止本次启动的服务...")
    except Exception as exc:
        print(f"[ERROR] 系统启动失败: {exc}")
        return 1
    finally:
        _stop_process(frontend_process)
        _stop_process(backend_process)

    print("[OK] 本次启动的服务已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
