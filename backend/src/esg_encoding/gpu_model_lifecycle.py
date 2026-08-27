"""GPU model lifecycle helpers for lazy load / unload.

This module centralizes backend model cleanup for SentenceTransformer embeddings,
rerankers, and any cached cross-analysis embedding model.  It is intentionally
best-effort: cleanup never raises to API handlers.
"""

from __future__ import annotations

import functools
import gc
import inspect
import os
import threading
from contextlib import contextmanager
from typing import Callable, TypeVar, Any

from loguru import logger

T = TypeVar("T")

_active_tasks = 0
_active_lock = threading.RLock()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def backend_lazy_load_enabled() -> bool:
    return env_bool("BACKEND_MODEL_LAZY_LOAD", True)


def backend_unload_after_task_enabled() -> bool:
    return env_bool("BACKEND_UNLOAD_AFTER_TASK", True)


def release_cuda_memory(reason: str = "") -> None:
    """Release unused CUDA allocator cache for torch and paddle if present."""
    gc.collect()

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

    try:
        import paddle  # type: ignore

        cuda_mod = getattr(getattr(paddle, "device", None), "cuda", None)
        empty_cache = getattr(cuda_mod, "empty_cache", None)
        if callable(empty_cache):
            empty_cache()
    except Exception as exc:
        logger.debug(f"Paddle CUDA cleanup skipped: {exc}")

    if reason:
        logger.info(f"[ModelLifecycle] CUDA cache cleanup requested: {reason}")


def unload_backend_models(reason: str = "") -> None:
    """Unload backend-owned GPU models and clear CUDA cache.

    This clears:
    - shared SentenceTransformer embedding cache
    - reranker cache (Qwen/Flag/Dense rerankers)
    - cross_analysis module's singleton embedding reference
    """
    if not backend_unload_after_task_enabled():
        return

    # Drop strong references held by long-lived service singletons first.
    try:
        from .services.common import system_components

        report_encoder = system_components.get("report_encoder")
        if report_encoder is not None and getattr(report_encoder, "embedder", None) is not None:
            try:
                report_encoder.embedder.model = None
            except Exception:
                pass

        metric_processor = system_components.get("metric_processor")
        if metric_processor is not None:
            try:
                metric_processor.embedding_model = None
            except Exception:
                pass

        dual_retriever = system_components.get("dual_retriever")
        semantic = getattr(dual_retriever, "semantic_retriever", None) if dual_retriever is not None else None
        if semantic is not None:
            try:
                semantic.embedding_model = None
                semantic.reranker = None
                semantic._reranker_initialized = False
            except Exception:
                pass

        chatbot = system_components.get("chatbot")
        if chatbot is not None:
            try:
                chatbot._embedder_model = None
            except Exception:
                pass
    except Exception as exc:
        logger.debug(f"[ModelLifecycle] singleton reference cleanup skipped: {exc}")

    try:
        from .shared_embedding_model import clear_shared_embedding_models

        clear_shared_embedding_models()
    except Exception as exc:
        logger.warning(f"[ModelLifecycle] embedding cleanup skipped: {exc}")

    try:
        from .retrieval.reranker import clear_reranker_models

        clear_reranker_models()
    except Exception as exc:
        logger.warning(f"[ModelLifecycle] reranker cleanup skipped: {exc}")

    try:
        from . import cross_analysis as _ca

        if hasattr(_ca, "_model"):
            _ca._model = None
        if hasattr(_ca, "_model_id"):
            _ca._model_id = None
    except Exception as exc:
        logger.debug(f"[ModelLifecycle] cross_analysis cleanup skipped: {exc}")

    release_cuda_memory(reason or "backend model task completed")
    logger.info(f"[ModelLifecycle] backend model cleanup completed: {reason or 'task completed'}")


@contextmanager
def backend_model_task(name: str = "model_task"):
    """Mark a task that may load backend GPU models.

    Cleanup is triggered only when the last active model task exits.  This avoids
    unloading shared models while another request/thread is still using them.
    """
    global _active_tasks
    with _active_lock:
        _active_tasks += 1
        logger.debug(f"[ModelLifecycle] enter {name}; active={_active_tasks}")
    try:
        yield
    finally:
        should_cleanup = False
        with _active_lock:
            _active_tasks = max(0, _active_tasks - 1)
            should_cleanup = _active_tasks == 0
            logger.debug(f"[ModelLifecycle] exit {name}; active={_active_tasks}")
        if should_cleanup:
            unload_backend_models(name)


def with_backend_model_task(name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for sync or async API functions that may use backend models."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any):
                with backend_model_task(name):
                    return await func(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any):
            with backend_model_task(name):
                return func(*args, **kwargs)

        return sync_wrapper  # type: ignore[return-value]

    return decorator
