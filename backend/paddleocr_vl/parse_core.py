"""PaddleOCR-VL v1.6 Redis 页批次 worker 核心。"""

from __future__ import annotations

import gc
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# PaddleX/PaddleOCR 会在导入和初始化阶段读取这些环境变量。
# 默认优先使用 HuggingFace；结合 docker-compose 中的 HF_ENDPOINT 可走镜像源，
# 避免自动落到 aistudio/gitea-cdn 下载源时遇到证书过期问题。
os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", os.getenv("PADDLEOCR_DEFAULT_MODEL_SOURCE", "huggingface"))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

from paddleocr import PaddleOCRVL

PIPELINE_VERSION = os.getenv("PADDLEOCR_PIPELINE_VERSION", "v1.6")
OUTPUT_ROOT = Path(os.getenv("PADDLEOCR_OUTPUT_DIR", "/workspace/uploads/paddleocr_vl_output"))
MODEL_CACHE_ROOT = Path(os.getenv("PADDLEOCR_MODEL_CACHE_DIR", "/home/paddleocr/.paddlex/official_models"))


class PaddleOCRModelLoadError(RuntimeError):
    """PaddleOCR-VL 模型下载、缓存或加载失败。

    这类错误通常不是单个 PDF 的问题，而是运行环境或模型缓存问题。
    worker 捕获该异常后会把当前任务重新入队，并退出等待 Docker 重启，
    避免一次性把同一文档的所有 page-batch 标记为失败。
    """


class PageBatchIncompleteError(RuntimeError):
    """PaddleOCR-VL returned fewer, extra, or empty page results for a batch."""


READY_MARKER_NAME = ".paddleocr_vl_preflight_ok.json"


def _vl_rec_backend() -> str:
    return os.getenv("PADDLEOCR_VL_REC_BACKEND", "").strip().lower()


def _remote_vlm_enabled() -> bool:
    return _vl_rec_backend().endswith("-server")


def _vl_model_dir() -> Path:
    version = str(PIPELINE_VERSION).strip().lower().lstrip("v") or "1.6"
    return MODEL_CACHE_ROOT / f"PaddleOCR-VL-{version}"


def _model_ready_marker_path() -> Path:
    if _remote_vlm_enabled():
        version = str(PIPELINE_VERSION).strip().lower().lstrip("v") or "1.6"
        backend = _vl_rec_backend().replace("-", "_")
        return MODEL_CACHE_ROOT / f".paddleocr_vl_{version}_{backend}_preflight_ok.json"
    return _vl_model_dir() / READY_MARKER_NAME


def _paddlex_download_source() -> str:
    return os.getenv("PADDLE_PDX_MODEL_SOURCE", os.getenv("PADDLEOCR_DEFAULT_MODEL_SOURCE", "modelscope")).strip() or "modelscope"


def _allow_model_download() -> bool:
    """是否允许当前进程在线下载模型。

    生产/解析 worker 默认不允许下载。模型下载只应由 model-init/preflight 服务完成。
    这样可以避免 PDF 解析期间两个 worker 同时写同一个 .paddlex 缓存，导致半成品模型被误认为已存在。
    """
    return env_bool("PADDLEOCR_ALLOW_MODEL_DOWNLOAD", True)


def _require_preflight_marker() -> bool:
    """worker 是否必须看到预检成功标记才允许加载模型。"""
    return env_bool("PADDLEOCR_REQUIRE_PREFLIGHT_MARKER", False)


def _min_vl_weight_size() -> int:
    # PaddleOCR-VL-1.6 的 model.safetensors 约 1.79GB。这里给一个保守下限。
    raw = os.getenv("PADDLEOCR_VL_MIN_WEIGHT_BYTES", "1000000000")
    try:
        return max(1, int(str(raw).strip()))
    except Exception:
        return 1_000_000_000


def _model_cache_looks_incomplete(model_dir: Path) -> bool:
    """判断 PaddleOCR-VL 官方模型缓存是否明显不完整。

    PaddleX 只要发现目录存在，就可能提示 “Model files already exist”，
    但目录可能是上次下载中断留下的半成品。这里主动检查核心权重。
    """
    if not model_dir.exists():
        return False
    if not model_dir.is_dir():
        return True

    weights = list(model_dir.rglob("model.safetensors"))
    if not weights:
        return True

    min_size = _min_vl_weight_size()
    for weight in weights:
        try:
            if weight.stat().st_size < min_size:
                return True
        except Exception:
            return True

    # 若存在 PaddleX 下载临时目录，也视为缓存未完成。
    for tmp_name in {"._tmp", "tmp", ".tmp"}:
        if any(p.name == tmp_name for p in model_dir.rglob(tmp_name)):
            return True
    return False


def _remove_model_cache_dir(model_dir: Path, reason: str) -> None:
    if not model_dir.exists():
        return
    if not env_bool("PADDLEOCR_CLEAN_BAD_MODEL_CACHE", True):
        logger.warning("检测到疑似损坏模型缓存，但未删除: {} ({})", model_dir, reason)
        return
    logger.warning("删除疑似损坏的 PaddleOCR-VL 模型缓存: {} ({})", model_dir, reason)
    shutil.rmtree(model_dir, ignore_errors=True)


def _write_model_ready_marker(reason: str) -> None:
    """模型成功加载后写入预检标记。

    worker 默认要求这个标记存在。它表示模型至少完整加载过一次，
    不再让 worker 在解析 PDF 时触发在线下载。
    """
    marker = _model_ready_marker_path()
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        weights = [] if _remote_vlm_enabled() else [str(p) for p in _vl_model_dir().rglob("model.safetensors")]
        payload = {
            "pipeline_version": PIPELINE_VERSION,
            "source": _paddlex_download_source(),
            "vl_rec_backend": _vl_rec_backend() or "paddle-dynamic",
            "vl_rec_server_url": os.getenv("PADDLEOCR_VL_REC_SERVER_URL", "").strip(),
            "reason": reason,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "weights": weights,
            "min_weight_bytes": _min_vl_weight_size(),
        }
        tmp = marker.with_suffix(marker.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(marker)
        try:
            os.chmod(marker, _shared_file_mode())
        except Exception:
            pass
        logger.info("已写入 PaddleOCR-VL 模型预检标记: {}", marker)
    except Exception as exc:
        logger.warning("写入 PaddleOCR-VL 预检标记失败: {} ({})", marker, exc)


def _model_cache_ready_for_worker() -> bool:
    if _remote_vlm_enabled():
        return not _require_preflight_marker() or _model_ready_marker_path().exists()

    model_dir = _vl_model_dir()
    if not model_dir.exists():
        return False
    if _model_cache_looks_incomplete(model_dir):
        return False
    if _require_preflight_marker() and not _model_ready_marker_path().exists():
        return False
    return True


def _prepare_model_cache_before_load() -> None:
    if _remote_vlm_enabled():
        if not _allow_model_download() and not _model_cache_ready_for_worker():
            raise PaddleOCRModelLoadError(
                "PaddleOCR-VL 远程 VLM 客户端尚未完成预检。"
                " 请确认 paddleocr-vlm-server 健康后重新运行 paddleocr-model-init"
                f"; backend={_vl_rec_backend()}; marker={_model_ready_marker_path()}"
            )
        return

    vl_dir = _vl_model_dir()
    if _model_cache_looks_incomplete(vl_dir):
        _remove_model_cache_dir(vl_dir, "missing or partial model.safetensors")

    # 解析 worker 默认不允许在线下载模型。模型必须先由 model-init 完成预检。
    if not _allow_model_download() and not _model_cache_ready_for_worker():
        raise PaddleOCRModelLoadError(
            "PaddleOCR-VL 模型尚未完成预检，worker 不会在解析 PDF 时在线下载模型。"
            f" 请等待 docker compose up 自动完成 paddleocr-model-init 预检；如需手动重试，可运行: docker compose run --rm paddleocr-model-init"
            f"; cache={vl_dir}; marker={_model_ready_marker_path()}"
        )


def _is_model_load_or_download_error(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}"
    needles = [
        "No valid model files were found",
        "paddle_dynamic",
        "SSLCertVerificationError",
        "CERTIFICATE_VERIFY_FAILED",
        "Max retries exceeded",
        "Error downloading",
        "model.safetensors",
        "official_models/PaddleOCR-VL",
    ]
    return any(x in msg for x in needles)


def _model_load_retry_count() -> int:
    return env_int("PADDLEOCR_MODEL_LOAD_MAX_RETRIES", 1, min_value=0)


def _shared_dir_mode() -> int:
    """返回跨容器共享目录权限，默认 0777。"""
    raw = os.getenv("PADDLEOCR_SHARED_DIR_MODE", "0777")
    try:
        return int(str(raw), 8)
    except Exception:
        return 0o777


def _shared_file_mode() -> int:
    """返回跨容器共享文件权限，默认 0666。"""
    raw = os.getenv("PADDLEOCR_SHARED_FILE_MODE", "0666")
    try:
        return int(str(raw), 8)
    except Exception:
        return 0o666


def ensure_shared_writable_dir(path: Path) -> Path:
    """创建跨容器共享目录，并尽量放宽权限。

    backend 与多个 paddleocr-worker 会共同读写 ./uploads 挂载目录。
    不同基础镜像里的运行用户可能不同，如果目录保持 0755，worker 可能无法创建 batch 输出。
    因此这里默认把共享目录 chmod 到 0777。chmod 失败时只记录警告，避免影响非 Linux/只读文件系统场景。
    """
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, _shared_dir_mode())
    except Exception as exc:
        logger.warning(f"chmod shared dir failed: {path} ({exc})")
    return path


def ensure_shared_file(path: Path) -> Path:
    """尽量把共享文件权限放宽，便于其他容器读取。"""
    try:
        os.chmod(path, _shared_file_mode())
    except Exception as exc:
        logger.debug(f"chmod shared file skipped: {path} ({exc})")
    return path


ensure_shared_writable_dir(OUTPUT_ROOT)

_pipeline: Optional[PaddleOCRVL] = None
_pipeline_lock = threading.RLock()
_idle_timer: Optional[threading.Timer] = None
_tasks_since_start = 0


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int, *, min_value: int = 0) -> int:
    raw = os.getenv(name)
    try:
        value = int(str(raw if raw is not None else default).strip())
    except Exception:
        value = default
    return max(min_value, value)


def env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    raw = os.getenv(name)
    try:
        value = float(str(raw if raw is not None else default).strip())
    except Exception:
        value = default
    return max(min_value, value)


def env_str(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return str(value if value is not None else default).strip()


def _pipeline_init_options() -> Dict[str, Any]:
    """Return explicit, reproducible options for the long-lived worker pipeline."""
    adaptive_preprocessing = env_bool("PADDLEOCR_ADAPTIVE_PREPROCESSING_ENABLED", False)
    options: Dict[str, Any] = {
        "pipeline_version": PIPELINE_VERSION,
        "device": env_str("PADDLEOCR_DEVICE", "gpu:0") or "gpu:0",
        # Components must be available in the long-lived pipeline before an
        # individual batch can selectively enable them at prediction time.
        "use_doc_orientation_classify": env_bool("PADDLEOCR_USE_DOC_ORIENTATION_CLASSIFY", False) or adaptive_preprocessing,
        "use_doc_unwarping": env_bool("PADDLEOCR_USE_DOC_UNWARPING", False) or adaptive_preprocessing,
        "use_layout_detection": env_bool("PADDLEOCR_USE_LAYOUT_DETECTION", True),
        "use_chart_recognition": env_bool("PADDLEOCR_USE_CHART_RECOGNITION", False),
        "use_seal_recognition": env_bool("PADDLEOCR_USE_SEAL_RECOGNITION", False),
        "use_ocr_for_image_block": env_bool("PADDLEOCR_USE_OCR_FOR_IMAGE_BLOCK", False),
        # Redis already provides page-level concurrency. PaddleX's internal queue
        # creates three non-daemon threads and was the source of the stuck VLM worker.
        "use_queues": env_bool("PADDLEOCR_USE_INTERNAL_QUEUES", False),
    }
    precision = env_str("PADDLEOCR_PRECISION", "fp16")
    if precision:
        options["precision"] = precision
    engine = env_str("PADDLEOCR_ENGINE", "")
    if engine:
        options["engine"] = engine
    if os.getenv("PADDLEOCR_ENABLE_HPI") is not None:
        options["enable_hpi"] = env_bool("PADDLEOCR_ENABLE_HPI", False)

    vl_rec_backend = env_str("PADDLEOCR_VL_REC_BACKEND", "")
    if vl_rec_backend:
        vl_rec_server_url = env_str("PADDLEOCR_VL_REC_SERVER_URL", "")
        if not vl_rec_server_url:
            raise ValueError("PADDLEOCR_VL_REC_SERVER_URL is required when PADDLEOCR_VL_REC_BACKEND is set")
        options.update(
            {
                "vl_rec_backend": vl_rec_backend,
                "vl_rec_server_url": vl_rec_server_url,
                "vl_rec_max_concurrency": env_int("PADDLEOCR_VL_REC_MAX_CONCURRENCY", 16, min_value=1),
            }
        )
        api_model_name = env_str("PADDLEOCR_VL_REC_API_MODEL_NAME", "")
        api_key = env_str("PADDLEOCR_VL_REC_API_KEY", "")
        if api_model_name:
            options["vl_rec_api_model_name"] = api_model_name
        if api_key:
            options["vl_rec_api_key"] = api_key
    return options


def _prediction_options(overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Build bounded options for one PDF/image unit passed to ``predict``."""
    min_pixels = env_int("PADDLEOCR_VLM_MIN_PIXELS", 112896, min_value=784)
    max_pixels = max(
        min_pixels,
        env_int("PADDLEOCR_VLM_MAX_PIXELS", 1003520, min_value=784),
    )
    layout_shape_mode = env_str("PADDLEOCR_LAYOUT_SHAPE_MODE", "rect").lower()
    if layout_shape_mode not in {"rect", "quad", "poly", "auto"}:
        logger.warning(
            "PADDLEOCR_LAYOUT_SHAPE_MODE={} 无效，使用 rect",
            layout_shape_mode,
        )
        layout_shape_mode = "rect"

    options: Dict[str, Any] = {
        "use_queues": env_bool("PADDLEOCR_USE_INTERNAL_QUEUES", False),
        "use_doc_orientation_classify": env_bool("PADDLEOCR_USE_DOC_ORIENTATION_CLASSIFY", False),
        "use_doc_unwarping": env_bool("PADDLEOCR_USE_DOC_UNWARPING", False),
        "use_layout_detection": env_bool("PADDLEOCR_USE_LAYOUT_DETECTION", True),
        "use_chart_recognition": env_bool("PADDLEOCR_USE_CHART_RECOGNITION", False),
        "use_seal_recognition": env_bool("PADDLEOCR_USE_SEAL_RECOGNITION", False),
        "use_ocr_for_image_block": env_bool("PADDLEOCR_USE_OCR_FOR_IMAGE_BLOCK", False),
        "layout_shape_mode": layout_shape_mode,
        "min_pixels": min_pixels,
        "max_pixels": max_pixels,
        # PaddleX defaults to 4096 tokens per layout block. A lower bounded value
        # prevents one repetitive block from monopolising a page task for minutes.
        "max_new_tokens": env_int("PADDLEOCR_VLM_MAX_NEW_TOKENS", 2048, min_value=128),
    }
    allowed_booleans = {
        "use_doc_orientation_classify",
        "use_doc_unwarping",
        "use_layout_detection",
        "use_chart_recognition",
        "use_seal_recognition",
        "use_ocr_for_image_block",
    }
    allowed_integers = {"min_pixels", "max_pixels", "max_new_tokens"}
    for key, value in dict(overrides or {}).items():
        if key in allowed_booleans:
            if not isinstance(value, bool):
                raise ValueError(f"prediction option {key} must be boolean")
            options[key] = value
        elif key in allowed_integers:
            if isinstance(value, bool):
                raise ValueError(f"prediction option {key} must be an integer")
            try:
                parsed = int(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"prediction option {key} must be an integer") from exc
            lower = 128 if key == "max_new_tokens" else 784
            upper = 8192 if key == "max_new_tokens" else 4_014_080
            options[key] = max(lower, min(upper, parsed))
        else:
            raise ValueError(f"unsupported prediction option: {key}")
    options["max_pixels"] = max(int(options["min_pixels"]), int(options["max_pixels"]))
    return options


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _collect_markdown(output_dir: Path) -> str:
    md_files = sorted(output_dir.rglob("*.md"))
    parts: List[str] = []
    for md_path in md_files:
        text = _read_text_file(md_path).strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def _model_load_lock_enabled() -> bool:
    """是否在多个 worker 之间串行化模型下载/加载。

    首次运行时两个 worker 同时下载 PaddleOCR-VL-1.6 容易互相抢缓存、拖慢速度，
    因此默认使用共享文件锁，让一个 worker 先完成模型下载和初始化，其他 worker 等待。
    """
    return env_bool("PADDLEOCR_SERIALIZE_MODEL_LOAD", True)


class _ModelLoadFileLock:
    """跨进程模型加载文件锁。

    使用 ./uploads 挂载目录中的 lock 文件，因此 paddleocr-worker-1/2 可以共享同一把锁。
    在非 Linux 或 fcntl 不可用时会自动降级为无锁。
    """

    def __init__(self) -> None:
        self._fh = None

    def __enter__(self):
        if not _model_load_lock_enabled():
            return self
        try:
            import fcntl  # type: ignore

            lock_root = Path(os.getenv("PADDLEOCR_LOCK_DIR", "/workspace/uploads/paddleocr_vl_locks"))
            ensure_shared_writable_dir(lock_root)
            lock_path = lock_root / "model_load.lock"
            self._fh = lock_path.open("a+", encoding="utf-8")
            logger.info("等待 PaddleOCR-VL 模型加载锁: {}", lock_path)
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            logger.info("已获得 PaddleOCR-VL 模型加载锁")
        except Exception as exc:
            logger.warning("PaddleOCR-VL 模型加载锁不可用，将继续无锁加载: {}", exc)
            self._fh = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fh is None:
            return
        try:
            import fcntl  # type: ignore

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            logger.info("已释放 PaddleOCR-VL 模型加载锁")
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None


def cleanup_gpu_memory() -> None:
    gc.collect()
    try:
        import paddle  # type: ignore

        cuda_mod = getattr(getattr(paddle, "device", None), "cuda", None)
        empty_cache = getattr(cuda_mod, "empty_cache", None)
        if callable(empty_cache):
            empty_cache()
    except Exception as exc:
        logger.debug(f"Paddle CUDA cleanup skipped: {exc}")

    # 部分镜像可能间接引入 torch；如果存在，也顺手清理 PyTorch 的 CUDA 缓存。
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            try:
                torch.cuda.ipc_collect()
            except Exception:
                pass
    except Exception as exc:
        logger.debug(f"Torch CUDA cleanup skipped: {exc}")


def load_pipeline(reason: str = "worker") -> PaddleOCRVL:
    """加载完整 PaddleOCR-VL v1.6。

    这里做三件事：
    1. 用跨 worker 文件锁串行化模型下载和初始化；
    2. 加载前检查 PaddleOCR-VL 缓存是否是半成品，发现后自动删除；
    3. 如果加载失败且属于模型缓存/下载问题，清理后重试一次。
    """
    with _ModelLoadFileLock():
        logger.info(
            "开始加载完整 PaddleOCRVL pipeline_version={} source={} ({})",
            PIPELINE_VERSION,
            _paddlex_download_source(),
            reason,
        )
        last_exc: BaseException | None = None
        max_retries = _model_load_retry_count()

        for attempt in range(max_retries + 1):
            _prepare_model_cache_before_load()
            started = time.time()
            try:
                init_options = _pipeline_init_options()
                logger.info("PaddleOCRVL 初始化参数: {}", init_options)
                loaded = PaddleOCRVL(**init_options)
                _write_model_ready_marker(reason)
                logger.info("PaddleOCRVL 加载完成 ({})，耗时 {:.1f}s", reason, time.time() - started)
                return loaded
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if _is_model_load_or_download_error(exc) and not _remote_vlm_enabled():
                    _remove_model_cache_dir(_vl_model_dir(), f"load failed: {type(exc).__name__}: {exc}")
                    if attempt < max_retries:
                        logger.warning(
                            "PaddleOCRVL 加载失败，将清理缓存后重试: attempt={}/{} error={}",
                            attempt + 1,
                            max_retries + 1,
                            exc,
                        )
                        continue

                raise PaddleOCRModelLoadError(
                    "PaddleOCR-VL 模型未能加载。请检查模型下载源、网络/SSL、以及 .paddlex 缓存。"
                    f" source={_paddlex_download_source()} cache={_vl_model_dir()} error={type(exc).__name__}: {exc}"
                ) from exc

        raise PaddleOCRModelLoadError(
            "PaddleOCR-VL 模型未能加载。"
            f" source={_paddlex_download_source()} cache={_vl_model_dir()} error={last_exc}"
        )


def cancel_idle_unload() -> None:
    global _idle_timer
    with _pipeline_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None


def get_pipeline() -> PaddleOCRVL:
    """返回当前 worker 持有的共享 pipeline；首次真正需要时才加载模型。"""
    global _pipeline
    cancel_idle_unload()
    with _pipeline_lock:
        if _pipeline is None:
            _pipeline = load_pipeline("queue-worker lazy load")
        return _pipeline


def release_pipeline(reason: str = "") -> None:
    """释放当前 worker 持有的模型引用，并清理 CUDA 分配器缓存。"""
    global _pipeline, _idle_timer
    with _pipeline_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        if _pipeline is not None:
            logger.info("释放 PaddleOCRVL pipeline ({})", reason or "requested")
        old = _pipeline
        _pipeline = None
    if old is not None:
        del old
    cleanup_gpu_memory()


def schedule_idle_unload() -> None:
    """如果启用了空闲释放，则在 TTL 到期后卸载模型。"""
    global _idle_timer
    if env_bool("PADDLEOCR_UNLOAD_AFTER_TASK", False):
        release_pipeline("after task")
        return
    if not env_bool("PADDLEOCR_UNLOAD_WHEN_IDLE", True):
        return
    idle_seconds = env_float("PADDLEOCR_IDLE_UNLOAD_SECONDS", 180.0, min_value=1.0)
    with _pipeline_lock:
        if _pipeline is None:
            return
        if _idle_timer is not None:
            _idle_timer.cancel()
        _idle_timer = threading.Timer(idle_seconds, lambda: release_pipeline(f"idle {idle_seconds:.0f}s"))
        _idle_timer.daemon = True
        _idle_timer.start()
        logger.info("已计划 PaddleOCRVL 空闲释放: {:.0f}s", idle_seconds)


def should_restart_worker_after_task() -> bool:
    """当达到任务数上限时返回 True，让 Docker 重启 worker 以清理显存碎片。"""
    global _tasks_since_start
    _tasks_since_start += 1
    limit = env_int("PADDLEOCR_RESTART_AFTER_TASKS", 0, min_value=0)
    return bool(limit and _tasks_since_start >= limit)


def _save_result_markdown(res: Any, page_dir: Path) -> str:
    ensure_shared_writable_dir(page_dir)
    # Markdown alone loses the crop and layout metadata produced by PaddleOCR-VL.
    # Keep both when supported; backend promotes the useful files to the report's
    # durable visual-asset directory before this worker output is cleaned.
    for method_name in ("save_to_json", "save_to_img"):
        method = getattr(res, method_name, None)
        if not callable(method):
            continue
        try:
            method(save_path=str(page_dir))
        except TypeError:
            try:
                method(str(page_dir))
            except Exception as exc:
                logger.warning(f"{method_name} failed for {page_dir.name}: {exc}")
        except Exception as exc:
            logger.warning(f"{method_name} failed for {page_dir.name}: {exc}")
    try:
        res.save_to_markdown(save_path=str(page_dir))
    except Exception as exc:
        logger.warning(f"save_to_markdown failed for {page_dir.name}: {exc}")
    return _collect_markdown(page_dir)


def parse_page_batch(
    input_path: str | Path,
    *,
    filename: str | None = None,
    job_id: str | None = None,
    batch_id: str | None = None,
    unit_index: int = 1,
    total_units: int = 1,
    start_page: int = 1,
    end_page: int = 1,
    total_pages: int | None = None,
    output_root: str | Path | None = None,
    ready_path: str | Path | None = None,
    prediction_options: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """解析一个页级 batch PDF/图片任务，并持久化该 batch 的输出。

    这是页级 batch 队列 worker 实际消费的最小任务单元。
    同一份父文档的不同 batch 可以由多个 worker 并发处理，
    因为每个 batch 都写入独立的输出目录。
    """
    started = time.time()
    source = Path(input_path).resolve()

    # Backend writes batch PDFs into the shared uploads volume and then enqueues Redis tasks.
    # On Docker Desktop / bind-mount setups, a worker can consume the Redis task before
    # the newly created file is visible in the worker container. Backend therefore writes
    # a `.ready` marker after atomically renaming the PDF. The worker waits for both the
    # PDF and the marker before parsing.
    input_wait_seconds = 0.0
    try:
        input_wait_seconds = max(0.0, float(os.getenv("PADDLEOCR_INPUT_WAIT_SECONDS", "120") or "120"))
    except Exception:
        input_wait_seconds = 120.0
    marker = Path(ready_path).resolve() if ready_path else source.with_name(source.name + ".ready")
    input_deadline = time.time() + input_wait_seconds
    last_reason = ""
    while True:
        try:
            source_ok = source.exists() and source.stat().st_size > 0
            marker_ok = marker.exists() and marker.stat().st_size > 0
            if source_ok and marker_ok:
                break
            last_reason = f"source_ok={source_ok}, marker_ok={marker_ok}"
        except Exception as exc:
            last_reason = str(exc)
        if time.time() >= input_deadline:
            raise FileNotFoundError(f"Input batch file is not available after waiting {input_wait_seconds:.0f}s: {source} ({last_reason})")
        time.sleep(0.25)

    root = Path(output_root or OUTPUT_ROOT)
    parent_id = job_id or f"batch_{int(time.time())}"
    safe_batch = batch_id or f"batch_{unit_index:04d}"
    unit_label = f"pages_{start_page:04d}_{end_page:04d}"

    # 先创建并放宽父级共享目录权限，避免两个 worker 并发写同一 job 时出现 PermissionError。
    job_dir = ensure_shared_writable_dir(root / parent_id)
    batches_dir = ensure_shared_writable_dir(job_dir / "batches")
    persist_dir = batches_dir / f"{safe_batch}_{unit_label}"
    if persist_dir.exists() and env_bool("PADDLEOCR_OVERWRITE_OUTPUT", True):
        shutil.rmtree(persist_dir, ignore_errors=True)
    ensure_shared_writable_dir(persist_dir)

    model_wait_started = time.monotonic()
    pipeline = get_pipeline()
    model_ready_seconds = time.monotonic() - model_wait_started
    predict_options = _prediction_options(prediction_options)
    expected_result_count = end_page - start_page + 1
    if expected_result_count <= 0:
        raise ValueError(
            f"Invalid page batch range: start_page={start_page}, end_page={end_page}"
        )

    batch_markdown_parts: List[str] = []
    empty_markdown_pages: List[int] = []
    result_count = 0

    logger.info(
        "PaddleOCR-VL page-batch job={} batch={} unit={}/{} pages={}-{} input={} "
        "input_bytes={} model_ready_seconds={:.3f} predict_options={} output={}",
        parent_id,
        safe_batch,
        unit_index,
        total_units,
        start_page,
        end_page,
        source,
        source.stat().st_size,
        model_ready_seconds,
        predict_options,
        persist_dir,
    )

    results = None
    predict_started = time.monotonic()
    first_result_seconds: float | None = None
    try:
        results = pipeline.predict(str(source), **predict_options)
        for local_count, res in enumerate(results, 1):
            result_count = local_count
            if local_count > expected_result_count:
                raise PageBatchIncompleteError(
                    f"PaddleOCR-VL returned too many page results for batch {safe_batch}: "
                    f"expected={expected_result_count}, returned_at_least={local_count}, "
                    f"pages={start_page}-{end_page}"
                )
            if first_result_seconds is None:
                first_result_seconds = time.monotonic() - predict_started
                logger.info(
                    "PaddleOCR-VL 首个结果: job={} unit={}/{} pages={}-{} elapsed={:.3f}s",
                    parent_id,
                    unit_index,
                    total_units,
                    start_page,
                    end_page,
                    first_result_seconds,
                )
            page_no = start_page + local_count - 1
            page_dir = persist_dir / f"page_{page_no:04d}_part_{local_count:02d}"
            md = _save_result_markdown(res, page_dir)
            if md.strip():
                batch_markdown_parts.append(
                    f"\n\n<!-- Page {page_no} | PaddleOCR-VL batch {unit_index}/{total_units} part {local_count} -->\n\n{md.strip()}"
                )
            else:
                empty_markdown_pages.append(page_no)
    finally:
        close_results = getattr(results, "close", None)
        if callable(close_results):
            try:
                close_results()
            except Exception as exc:
                logger.warning("关闭 PaddleOCR-VL 结果迭代器失败: {}", exc)
        results = None

    predict_seconds = time.monotonic() - predict_started
    logger.info(
        "PaddleOCR-VL 推理完成: job={} unit={}/{} pages={}-{} results={} elapsed={:.3f}s",
        parent_id,
        unit_index,
        total_units,
        start_page,
        end_page,
        result_count,
        predict_seconds,
    )

    if result_count != expected_result_count or empty_markdown_pages:
        raise PageBatchIncompleteError(
            f"Incomplete PaddleOCR-VL page batch {safe_batch}: "
            f"expected={expected_result_count}, returned={result_count}, "
            f"nonempty_markdown={len(batch_markdown_parts)}, "
            f"empty_pages={empty_markdown_pages}, pages={start_page}-{end_page}"
        )

    markdown = "\n".join(batch_markdown_parts).strip()
    if not markdown:
        raise RuntimeError(f"No Markdown produced for batch {safe_batch} pages {start_page}-{end_page}")

    batch_md_path = persist_dir / "batch.md"
    batch_md_path.write_text(markdown, encoding="utf-8")
    ensure_shared_file(batch_md_path)

    elapsed = time.time() - started
    result: Dict[str, Any] = {
        "status": "success",
        "parser": "paddleocr-vl",
        "pipeline_version": PIPELINE_VERSION,
        "task_type": "page_batch",
        "job_id": parent_id,
        "batch_id": safe_batch,
        "unit_index": unit_index,
        "total_units": total_units,
        "start_page": start_page,
        "end_page": end_page,
        "total_pages": total_pages or end_page,
        "expected_result_count": expected_result_count,
        "result_count": result_count,
        "returned_pages": list(range(start_page, end_page + 1)),
        "elapsed_seconds": elapsed,
        "model_ready_seconds": model_ready_seconds,
        "predict_seconds": predict_seconds,
        "first_result_seconds": first_result_seconds,
        "predict_options": predict_options,
        "output_dir": str(persist_dir),
        "batch_markdown_path": str(batch_md_path),
        "filename": filename or source.name,
    }

    schedule_idle_unload()
    return result
