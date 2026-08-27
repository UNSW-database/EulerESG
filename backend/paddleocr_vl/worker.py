"""只处理 Redis page_batch 任务的 PaddleOCR-VL v1.6 worker。"""

from __future__ import annotations

import json
import os
import signal
import socket
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from loguru import logger
from logging_config import configure_logging

configure_logging(os.getenv("PADDLEOCR_WORKER_ID", "paddleocr-worker"))

from parse_core import (
    PaddleOCRModelLoadError,
    get_pipeline,
    parse_page_batch,
    release_pipeline,
    schedule_idle_unload,
    should_restart_worker_after_task,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_int(name: str, default: int, *, min_value: int = 1) -> int:
    raw = os.getenv(name)
    try:
        value = int(str(raw if raw is not None else default).strip())
    except Exception:
        value = default
    return max(min_value, value)


def _env_float(name: str, default: float, *, min_value: float = 0.0) -> float:
    raw = os.getenv(name)
    try:
        value = float(str(raw if raw is not None else default).strip())
    except Exception:
        value = default
    return max(min_value, value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _redis_client():
    try:
        import redis  # type: ignore
    except Exception as exc:  # pragma: no cover - 运行时依赖
        raise RuntimeError("redis package is required for paddleocr-worker") from exc

    url = os.getenv("PADDLEOCR_TASK_QUEUE_URL", "redis://redis:6379/0")
    return redis.Redis.from_url(url, decode_responses=True, socket_timeout=30, socket_connect_timeout=30)


class PageBatchTimeoutError(TimeoutError):
    """单个 page-batch 超时。"""


class _PageBatchTimeout:
    """Linux 容器内使用 SIGALRM 限制单个 batch 的最长运行时间。

    如果 PaddleOCR 底层 C++/CUDA 调用长时间不返回，信号可能需要等到控制权回到 Python 后才触发；
    但对于大多数卡在 Python 调度/下载/后处理的情况，可以及时失败并让任务状态可见。
    """

    def __init__(self, seconds: int, label: str) -> None:
        self.seconds = max(0, int(seconds or 0))
        self.label = label
        self._old_handler = None

    def __enter__(self):
        if self.seconds <= 0:
            return self

        def _handler(signum, frame):  # noqa: ARG001
            raise PageBatchTimeoutError(f"PaddleOCR page-batch 超时: {self.label}, timeout={self.seconds}s")

        self._old_handler = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.seconds <= 0:
            return
        signal.setitimer(signal.ITIMER_REAL, 0)
        if self._old_handler is not None:
            signal.signal(signal.SIGALRM, self._old_handler)


def _task_key(job_id: str) -> str:
    prefix = (
        os.getenv("PADDLEOCR_TASK_KEY_PREFIX", "paddleocr:task").strip()
        or "paddleocr:task"
    )
    return f"{prefix}:{job_id}"


def _batch_key(job_id: str, unit_index: int) -> str:
    return f"{_task_key(job_id)}:batch:{unit_index:04d}"


def _processing_queue_name(queue_name: str) -> str:
    return (
        os.getenv("PADDLEOCR_PROCESSING_QUEUE_NAME", "").strip()
        or f"{queue_name}:processing"
    )


def _processing_lease_key(processing_queue_name: str) -> str:
    return (
        os.getenv("PADDLEOCR_PROCESSING_LEASE_KEY", "").strip()
        or f"{processing_queue_name}:leases"
    )


def _processing_owner_key(processing_queue_name: str) -> str:
    return (
        os.getenv("PADDLEOCR_PROCESSING_OWNER_KEY", "").strip()
        or f"{processing_queue_name}:owners"
    )


def _worker_heartbeat_key(worker_id: str) -> str:
    prefix = (
        os.getenv("PADDLEOCR_WORKER_HEARTBEAT_PREFIX", "paddleocr:worker").strip()
        or "paddleocr:worker"
    )
    return f"{prefix}:{worker_id}"


def _processing_lease_seconds() -> int:
    # Keep the default lease longer than the page-batch alarm. A healthy worker
    # renews it continuously, while a hard crash/OOM leaves it to expire and be
    # reclaimed by another worker.
    batch_timeout = _env_int("PADDLEOCR_BATCH_TIMEOUT_SECONDS", 600, min_value=0)
    return _env_int(
        "PADDLEOCR_PROCESSING_LEASE_SECONDS",
        max(900, batch_timeout + 120),
        min_value=60,
    )


_ACK_PROCESSING_PAYLOAD_SCRIPT = """
-- ACK_PROCESSING_PAYLOAD
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('HDEL', KEYS[3], ARGV[1])
return removed
"""


_REQUEUE_PROCESSING_PAYLOAD_SCRIPT = """
-- REQUEUE_PROCESSING_PAYLOAD
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
if removed > 0 then
    redis.call('LPUSH', KEYS[2], ARGV[1])
end
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('HDEL', KEYS[4], ARGV[1])
return removed
"""


_RECOVER_EXPIRED_PAYLOAD_SCRIPT = """
-- RECOVER_EXPIRED_PAYLOAD
local score = redis.call('ZSCORE', KEYS[3], ARGV[1])
if (not score) or (tonumber(score) > tonumber(ARGV[2])) then
    return 0
end
local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
if removed > 0 and ARGV[3] == 'requeue' then
    redis.call('LPUSH', KEYS[2], ARGV[1])
end
redis.call('ZREM', KEYS[3], ARGV[1])
redis.call('HDEL', KEYS[4], ARGV[1])
return removed
"""


def _claim_payload(
    r,
    queue_name: str,
    processing_queue_name: str,
    *,
    timeout: int,
) -> Optional[str]:
    """Atomically claim the oldest RPUSHed payload into the processing list.

    Redis 7's BLMOVE LEFT RIGHT preserves the FIFO order used by the existing
    backend producer while ensuring a claimed item remains recoverable if this
    process exits before acknowledgement.
    """
    payload = r.execute_command(
        "BLMOVE",
        queue_name,
        processing_queue_name,
        "LEFT",
        "RIGHT",
        max(1, int(timeout)),
    )
    if payload is None:
        return None
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def _record_worker_heartbeat(
    r,
    *,
    worker_id: str,
    queue_name: str,
    processing_queue_name: str,
    status: str,
    payload: Optional[Dict[str, Any]] = None,
    lease_expires_at: Optional[float] = None,
) -> None:
    details = payload if isinstance(payload, dict) else {}
    key = _worker_heartbeat_key(worker_id)
    mapping = {
        "worker_id": worker_id,
        "status": status,
        "queue_name": queue_name,
        "processing_queue_name": processing_queue_name,
        "heartbeat_at": _utc_now(),
        "heartbeat_epoch": f"{time.time():.6f}",
        "job_id": str(details.get("job_id") or ""),
        "unit_index": str(details.get("unit_index") or ""),
        "lease_expires_at": (
            f"{float(lease_expires_at):.6f}"
            if lease_expires_at is not None
            else ""
        ),
    }
    r.hset(key, mapping=mapping)
    r.expire(
        key,
        _env_int("PADDLEOCR_WORKER_HEARTBEAT_TTL_SECONDS", 90, min_value=30),
    )


def _renew_processing_lease(
    r,
    *,
    worker_id: str,
    queue_name: str,
    processing_queue_name: str,
    payload_raw: str,
    payload: Optional[Dict[str, Any]] = None,
) -> float:
    lease_expires_at = time.time() + _processing_lease_seconds()
    lease_key = _processing_lease_key(processing_queue_name)
    owner_key = _processing_owner_key(processing_queue_name)
    owner = {
        "worker_id": worker_id,
        "lease_updated_at": _utc_now(),
        "lease_expires_at": lease_expires_at,
        "job_id": str((payload or {}).get("job_id") or ""),
        "unit_index": str((payload or {}).get("unit_index") or ""),
    }
    r.zadd(lease_key, {payload_raw: lease_expires_at})
    r.hset(owner_key, mapping={payload_raw: json.dumps(owner, ensure_ascii=False)})
    ttl = _env_int("PADDLEOCR_TASK_RESULT_TTL", 86400, min_value=60)
    r.expire(lease_key, ttl)
    r.expire(owner_key, ttl)
    _record_worker_heartbeat(
        r,
        worker_id=worker_id,
        queue_name=queue_name,
        processing_queue_name=processing_queue_name,
        status="processing",
        payload=payload,
        lease_expires_at=lease_expires_at,
    )
    return lease_expires_at


class _ProcessingLeaseHeartbeat:
    """Renew a claimed item's lease while native OCR code is blocking."""

    def __init__(
        self,
        r,
        *,
        worker_id: str,
        queue_name: str,
        processing_queue_name: str,
        payload_raw: str,
        payload: Optional[Dict[str, Any]],
    ) -> None:
        self.r = r
        self.worker_id = worker_id
        self.queue_name = queue_name
        self.processing_queue_name = processing_queue_name
        self.payload_raw = payload_raw
        self.payload = payload
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _renew(self) -> None:
        try:
            _renew_processing_lease(
                self.r,
                worker_id=self.worker_id,
                queue_name=self.queue_name,
                processing_queue_name=self.processing_queue_name,
                payload_raw=self.payload_raw,
                payload=self.payload,
            )
        except Exception as exc:
            # The processing-list item remains durable. If Redis stays
            # unavailable beyond the lease, another worker may replay it; batch
            # terminal-state checks keep that replay idempotent.
            logger.warning("Failed to renew PaddleOCR processing lease: {}", exc)

    def _run(self) -> None:
        interval = min(
            _env_float(
                "PADDLEOCR_LEASE_HEARTBEAT_SECONDS",
                15.0,
                min_value=1.0,
            ),
            max(1.0, _processing_lease_seconds() / 3.0),
        )
        while not self._stop.wait(interval):
            self._renew()

    def __enter__(self):
        self._renew()
        self._thread = threading.Thread(
            target=self._run,
            name=f"paddleocr-lease-{self.worker_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ARG002
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def _ack_claimed_payload(
    r,
    *,
    processing_queue_name: str,
    payload_raw: str,
) -> bool:
    removed = r.eval(
        _ACK_PROCESSING_PAYLOAD_SCRIPT,
        3,
        processing_queue_name,
        _processing_lease_key(processing_queue_name),
        _processing_owner_key(processing_queue_name),
        payload_raw,
    )
    return bool(int(removed or 0))


def _requeue_claimed_payload(
    r,
    *,
    queue_name: str,
    processing_queue_name: str,
    payload_raw: str,
) -> bool:
    removed = r.eval(
        _REQUEUE_PROCESSING_PAYLOAD_SCRIPT,
        4,
        processing_queue_name,
        queue_name,
        _processing_lease_key(processing_queue_name),
        _processing_owner_key(processing_queue_name),
        payload_raw,
    )
    return bool(int(removed or 0))


def _recover_expired_processing(
    r,
    *,
    queue_name: str,
    processing_queue_name: str,
    now: Optional[float] = None,
) -> int:
    """Move expired processing items back to the head of the source queue."""
    now_epoch = float(time.time() if now is None else now)
    lease_key = _processing_lease_key(processing_queue_name)
    owner_key = _processing_owner_key(processing_queue_name)

    # A process can die in the tiny interval between BLMOVE and ZADD. Give such
    # an unregistered item one lease interval before reclaiming it, avoiding a
    # false steal from a concurrently claiming worker.
    try:
        processing_items = r.lrange(processing_queue_name, 0, -1) or []
        for raw in processing_items:
            raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            if r.zscore(lease_key, raw_text) is None:
                r.zadd(
                    lease_key,
                    {raw_text: now_epoch + _processing_lease_seconds()},
                    nx=True,
                )
    except Exception as exc:
        logger.warning("Failed to register orphan PaddleOCR processing leases: {}", exc)

    expired = r.zrangebyscore(lease_key, "-inf", now_epoch) or []
    recovered = 0
    terminal_cleaned = 0
    for raw in expired:
        raw_text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        terminal = False
        try:
            parsed_payload = json.loads(raw_text)
            terminal = isinstance(parsed_payload, dict) and _batch_already_terminal(
                r, parsed_payload
            )
        except Exception:
            terminal = False
        removed = r.eval(
            _RECOVER_EXPIRED_PAYLOAD_SCRIPT,
            4,
            processing_queue_name,
            queue_name,
            lease_key,
            owner_key,
            raw_text,
            now_epoch,
            "discard" if terminal else "requeue",
        )
        removed_count = int(removed or 0)
        recovered += removed_count
        if terminal:
            terminal_cleaned += removed_count
    if recovered:
        logger.warning(
            "Recovered {} expired PaddleOCR processing task(s), terminal_cleaned={}: processing={} queue={}",
            recovered,
            terminal_cleaned,
            processing_queue_name,
            queue_name,
        )
    return recovered


def _has_active_queue_work(
    r,
    *,
    queue_name: str,
    processing_queue_name: Optional[str] = None,
) -> bool:
    processing_name = processing_queue_name or _processing_queue_name(queue_name)
    if int(r.llen(queue_name) or 0) > 0:
        return True
    if int(r.llen(processing_name) or 0) > 0:
        return True
    # The list check is authoritative; the zset check also protects the narrow
    # claim/visibility window and makes the active-lease contract explicit.
    return bool(
        int(
            r.zcount(
                _processing_lease_key(processing_name),
                time.time(),
                "+inf",
            )
            or 0
        )
    )


def _batch_already_terminal(r, payload: Dict[str, Any]) -> bool:
    try:
        job_id = str(payload.get("job_id") or "")
        unit_index = int(payload.get("unit_index") or 1)
        if not job_id:
            return False
        state = r.hgetall(_batch_key(job_id, unit_index)) or {}
        return str(state.get("status") or "").strip().lower() in {
            "success",
            "completed",
            "failed",
            "skipped",
        }
    except Exception:
        return False


def _hash_set(r, key: str, mapping: Dict[str, Any]) -> None:
    safe: Dict[str, str] = {}
    for k, v in mapping.items():
        if isinstance(v, (dict, list)):
            safe[k] = json.dumps(v, ensure_ascii=False)
        else:
            safe[k] = "" if v is None else str(v)
    r.hset(key, mapping=safe)
    ttl = _env_int("PADDLEOCR_TASK_RESULT_TTL", 86400, min_value=60)
    r.expire(key, ttl)


def _set_batch_status(r, job_id: str, unit_index: int, mapping: Dict[str, Any]) -> None:
    _hash_set(r, _batch_key(job_id, unit_index), mapping)


def _increment_parent_done(r, job_id: str, *, status: str, unit_index: int) -> None:
    key = _task_key(job_id)
    try:
        r.hincrby(key, "units_done", 1)
    except Exception:
        pass
    _hash_set(
        r,
        key,
        {
            "status": "running" if status == "success" else "partial_running",
            "stage": "batch_done" if status == "success" else "batch_failed",
            "last_finished_unit": unit_index,
            "updated_at": _utc_now(),
        },
    )


def _job_cancelled(r, job_id: str) -> bool:
    """如果 backend 已经判定父 job 失败/取消，则 worker 不再继续消费该 job 的剩余 batch。"""
    try:
        parent = r.hgetall(_task_key(job_id)) or {}
    except Exception:
        return False
    status = str(parent.get("status", "")).lower()
    cancel_requested = str(parent.get("cancel_requested", "")).strip().lower() in {"1", "true", "yes", "y", "on"}
    return cancel_requested or status in {"failed", "cancelled", "cancelling"}


def _handle_page_batch(r, worker_id: str, payload: Dict[str, Any]) -> None:
    task_started = time.monotonic()
    job_id = str(payload.get("job_id") or "")
    if not job_id:
        raise ValueError("page_batch task missing job_id")

    unit_index = int(payload.get("unit_index") or 1)
    total_units = int(payload.get("total_units") or 1)
    start_page = int(payload.get("start_page") or 1)
    end_page = int(payload.get("end_page") or start_page)
    total_pages = int(payload.get("total_pages") or end_page)
    batch_id = str(payload.get("batch_id") or f"batch_{unit_index:04d}")
    input_path = str(payload.get("input_path") or "")
    ready_path = str(payload.get("ready_path") or "")
    filename = str(payload.get("filename") or "")
    prediction_options = payload.get("prediction_options")
    if prediction_options is None:
        prediction_options = {}
    if not isinstance(prediction_options, dict):
        raise ValueError("page_batch prediction_options must be an object")
    parse_pass = max(1, int(payload.get("parse_pass") or 1))
    render_zoom = max(1.0, float(payload.get("render_zoom") or 1.0))
    requested_render_zoom = max(
        1.0,
        float(payload.get("requested_render_zoom") or render_zoom),
    )

    logger.debug(
        "Starting page batch job={} unit={}/{} pages={}-{}",
        job_id,
        unit_index,
        total_units,
        start_page,
        end_page,
    )

    if _job_cancelled(r, job_id):
        logger.warning("Parent job cancelled; skipping page batch job={} unit={}/{}", job_id, unit_index, total_units)
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "skipped",
                "stage": "parent_cancelled",
                "worker_id": worker_id,
                "updated_at": _utc_now(),
                "unit_index": unit_index,
                "total_units": total_units,
                "start_page": start_page,
                "end_page": end_page,
            },
        )
        return

    _set_batch_status(
        r,
        job_id,
        unit_index,
        {
            "status": "running",
            "worker_id": worker_id,
            "started_at": _utc_now(),
            "filename": filename,
            "input_path": input_path,
            "ready_path": ready_path,
            "stage": "predict",
            "batch_id": batch_id,
            "unit_index": unit_index,
            "total_units": total_units,
            "start_page": start_page,
            "end_page": end_page,
            "total_pages": total_pages,
            "prediction_options": prediction_options,
            "parse_pass": parse_pass,
            "render_zoom": render_zoom,
            "requested_render_zoom": requested_render_zoom,
        },
    )

    batch_timeout = _env_int("PADDLEOCR_BATCH_TIMEOUT_SECONDS", 600, min_value=0)
    try:
        timeout_label = f"job={job_id} unit={unit_index}/{total_units} pages={start_page}-{end_page}"
        with _PageBatchTimeout(batch_timeout, timeout_label):
            result = parse_page_batch(
                input_path,
                filename=filename,
                job_id=job_id,
                batch_id=batch_id,
                unit_index=unit_index,
                total_units=total_units,
                start_page=start_page,
                end_page=end_page,
                total_pages=total_pages,
                ready_path=ready_path or None,
                prediction_options=prediction_options,
            )
        result_for_redis = dict(result)
        result_for_redis["parse_pass"] = parse_pass
        result_for_redis["render_zoom"] = render_zoom
        result_for_redis["requested_render_zoom"] = requested_render_zoom
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": result_for_redis.get("status", "success"),
                "stage": "completed",
                "completed_at": _utc_now(),
                "worker_id": worker_id,
                "result_json": result_for_redis,
                "output_dir": result_for_redis.get("output_dir", ""),
                "batch_markdown_path": result_for_redis.get("batch_markdown_path", ""),
                "elapsed_seconds": result_for_redis.get("elapsed_seconds", ""),
            },
        )
        _increment_parent_done(r, job_id, status="success", unit_index=unit_index)
        logger.debug("Completed page batch job={} unit={}/{}", job_id, unit_index, total_units)
    except PageBatchTimeoutError as exc:
        tb = traceback.format_exc()
        elapsed_seconds = time.monotonic() - task_started
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "failed",
                "stage": "timeout",
                "error_type": "batch_timeout",
                "timeout_seconds": batch_timeout,
                "elapsed_seconds": elapsed_seconds,
                "failed_at": _utc_now(),
                "worker_id": worker_id,
                "unit_index": unit_index,
                "total_units": total_units,
                "start_page": start_page,
                "end_page": end_page,
                "error": str(exc),
                "traceback": tb[-8000:],
            },
        )
        _increment_parent_done(r, job_id, status="failed", unit_index=unit_index)
        logger.error(
            "page-batch 超时: job={} unit={}/{} pages={}-{} timeout={}s elapsed={:.3f}s",
            job_id,
            unit_index,
            total_units,
            start_page,
            end_page,
            batch_timeout,
            elapsed_seconds,
        )
        raise
    except PaddleOCRModelLoadError as exc:
        # 模型没有准备好时，不把当前 batch 计为失败。
        # main() 会把 payload 重新放回队列，并退出 worker 等待 Docker 重启。
        tb = traceback.format_exc()
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "waiting_model",
                "stage": "model_load_failed",
                "updated_at": _utc_now(),
                "worker_id": worker_id,
                "error_type": "model_load_error",
                "error": str(exc),
                "traceback": tb[-8000:],
            },
        )
        raise
    except Exception as exc:
        tb = traceback.format_exc()
        _set_batch_status(
            r,
            job_id,
            unit_index,
            {
                "status": "failed",
                "stage": "failed",
                "failed_at": _utc_now(),
                "worker_id": worker_id,
                "error_type": type(exc).__name__,
                "elapsed_seconds": time.monotonic() - task_started,
                "unit_index": unit_index,
                "total_units": total_units,
                "start_page": start_page,
                "end_page": end_page,
                "error": str(exc),
                "traceback": tb[-8000:],
            },
        )
        _increment_parent_done(r, job_id, status="failed", unit_index=unit_index)
        raise


def _maybe_preflight_model() -> None:
    """可选：worker 启动时先验证模型可加载，再开始消费 Redis 任务。"""
    if not _env_bool("PADDLEOCR_PREFLIGHT_ON_START", False):
        return
    logger.info("执行 PaddleOCR-VL worker 启动前模型预检")
    get_pipeline()
    schedule_idle_unload()
    logger.info("PaddleOCR-VL worker 模型预检通过")


def _release_request_key() -> str:
    return (
        os.getenv(
            "PADDLEOCR_RELEASE_REQUEST_KEY",
            "paddleocr:control:release",
        ).strip()
        or "paddleocr:control:release"
    )


def _maybe_release_requested(
    r,
    *,
    worker_id: str,
    queue_name: str,
    processing_queue_name: Optional[str] = None,
    last_request_id: str,
) -> str:
    """Release this worker only after the shared OCR queue is idle."""
    request_key = _release_request_key()
    try:
        request_id = str(r.hget(request_key, "request_id") or "").strip()
    except Exception as exc:
        logger.warning("Failed to read PaddleOCR release request: {}", exc)
        return last_request_id
    if not request_id or request_id == last_request_id:
        return last_request_id

    try:
        if _has_active_queue_work(
            r,
            queue_name=queue_name,
            processing_queue_name=processing_queue_name,
        ):
            return last_request_id
    except Exception as exc:
        logger.warning("Failed to verify PaddleOCR queue before release: {}", exc)
        return last_request_id

    release_pipeline(f"document completed request={request_id}")
    try:
        r.hset(
            request_key,
            mapping={
                f"ack:{worker_id}": request_id,
                f"ack_at:{worker_id}": _utc_now(),
            },
        )
        r.expire(
            request_key,
            _env_int("PADDLEOCR_TASK_RESULT_TTL", 86400, min_value=60),
        )
    except Exception as exc:
        # GPU memory is already released. Leave the request pending so the next
        # idle poll can retry the acknowledgement.
        logger.warning("PaddleOCR release acknowledgement failed: {}", exc)
        return last_request_id

    logger.info(
        "PaddleOCR worker pipeline released: worker_id={} request_id={}",
        worker_id,
        request_id,
    )
    return request_id


def main() -> int:
    worker_id = os.getenv("PADDLEOCR_WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    queue_name = (
        os.getenv("PADDLEOCR_TASK_QUEUE_NAME", "paddleocr:parse").strip()
        or "paddleocr:parse"
    )
    processing_queue_name = _processing_queue_name(queue_name)
    block_timeout = _env_int("PADDLEOCR_QUEUE_BLOCK_TIMEOUT", 5, min_value=1)
    idle_sleep = _env_float("PADDLEOCR_WORKER_IDLE_SLEEP", 0.25, min_value=0.0)
    recovery_interval = _env_float(
        "PADDLEOCR_PROCESSING_RECOVERY_INTERVAL_SECONDS",
        15.0,
        min_value=1.0,
    )

    r = _redis_client()
    logger.info(
        "PaddleOCR-VL 队列 worker 已启动: worker_id={} queue={} processing={} redis={}",
        worker_id,
        queue_name,
        processing_queue_name,
        os.getenv("PADDLEOCR_TASK_QUEUE_URL", "redis://redis:6379/0"),
    )

    # 如果 Redis 不可达，立即失败；compose 的 restart 策略会自动重试。
    r.ping()
    _record_worker_heartbeat(
        r,
        worker_id=worker_id,
        queue_name=queue_name,
        processing_queue_name=processing_queue_name,
        status="starting",
    )
    _recover_expired_processing(
        r,
        queue_name=queue_name,
        processing_queue_name=processing_queue_name,
    )

    try:
        _maybe_preflight_model()
    except PaddleOCRModelLoadError:
        logger.exception("PaddleOCR-VL 模型预检失败，worker 将退出等待重启")
        release_pipeline("model preflight failed")
        return 2

    try:
        # Ignore a stale request from a previous container lifecycle. Only a
        # request created after startup should undo the worker prewarm.
        last_release_request_id = str(
            r.hget(_release_request_key(), "request_id") or ""
        ).strip()
    except Exception:
        last_release_request_id = ""

    last_recovery_at = time.monotonic()

    while True:
        now_monotonic = time.monotonic()
        if now_monotonic - last_recovery_at >= recovery_interval:
            _recover_expired_processing(
                r,
                queue_name=queue_name,
                processing_queue_name=processing_queue_name,
            )
            last_recovery_at = now_monotonic

        payload_raw = _claim_payload(
            r,
            queue_name,
            processing_queue_name,
            timeout=block_timeout,
        )
        if payload_raw is None:
            _record_worker_heartbeat(
                r,
                worker_id=worker_id,
                queue_name=queue_name,
                processing_queue_name=processing_queue_name,
                status="idle",
            )
            last_release_request_id = _maybe_release_requested(
                r,
                worker_id=worker_id,
                queue_name=queue_name,
                processing_queue_name=processing_queue_name,
                last_request_id=last_release_request_id,
            )
            if idle_sleep:
                time.sleep(idle_sleep)
            continue

        try:
            payload = json.loads(payload_raw)
        except Exception:
            logger.error("Discarding invalid task payload payload_bytes={}", len(payload_raw.encode("utf-8", errors="replace")))
            _ack_claimed_payload(
                r,
                processing_queue_name=processing_queue_name,
                payload_raw=payload_raw,
            )
            continue
        if not isinstance(payload, dict):
            logger.error(
                "Discarding non-object task payload payload_type={}",
                type(payload).__name__,
            )
            _ack_claimed_payload(
                r,
                processing_queue_name=processing_queue_name,
                payload_raw=payload_raw,
            )
            continue

        _renew_processing_lease(
            r,
            worker_id=worker_id,
            queue_name=queue_name,
            processing_queue_name=processing_queue_name,
            payload_raw=payload_raw,
            payload=payload,
        )

        task_type = str(payload.get("task_type") or "").strip().lower()
        if task_type not in {"page_batch", "page-batch", "batch"}:
            logger.error("Discarding unsupported task type task_type={}", task_type or "missing")
            _ack_claimed_payload(
                r,
                processing_queue_name=processing_queue_name,
                payload_raw=payload_raw,
            )
            continue
        if _batch_already_terminal(r, payload):
            logger.warning(
                "Acknowledging replay of terminal PaddleOCR batch job={} unit={}",
                payload.get("job_id", ""),
                payload.get("unit_index", ""),
            )
            _ack_claimed_payload(
                r,
                processing_queue_name=processing_queue_name,
                payload_raw=payload_raw,
            )
            continue
        try:
            with _ProcessingLeaseHeartbeat(
                r,
                worker_id=worker_id,
                queue_name=queue_name,
                processing_queue_name=processing_queue_name,
                payload_raw=payload_raw,
                payload=payload,
            ):
                _handle_page_batch(r, worker_id, payload)

            _ack_claimed_payload(
                r,
                processing_queue_name=processing_queue_name,
                payload_raw=payload_raw,
            )

            if should_restart_worker_after_task():
                logger.info("PADDLEOCR_RESTART_AFTER_TASKS reached; releasing model and exiting for clean restart")
                release_pipeline("restart after configured task count")
                return 0

        except PaddleOCRModelLoadError:
            logger.exception("PaddleOCR-VL 模型加载失败，当前任务不会被计为解析失败")
            if _env_bool("PADDLEOCR_REQUEUE_ON_MODEL_ERROR", True):
                _requeue_claimed_payload(
                    r,
                    queue_name=queue_name,
                    processing_queue_name=processing_queue_name,
                    payload_raw=payload_raw,
                )
            else:
                _ack_claimed_payload(
                    r,
                    processing_queue_name=processing_queue_name,
                    payload_raw=payload_raw,
                )
            release_pipeline("model load failed")
            return 2
        except Exception:
            logger.exception(
                "PaddleOCR task failed task_type={} job_id={} unit_index={}",
                task_type,
                payload.get("job_id", ""),
                payload.get("unit_index", ""),
            )
            # _handle_page_batch persisted a terminal failed status. A hard
            # crash/OOM never reaches here and remains recoverable in processing.
            _ack_claimed_payload(
                r,
                processing_queue_name=processing_queue_name,
                payload_raw=payload_raw,
            )
            release_pipeline("task failed")
            if _env_bool("PADDLEOCR_EXIT_ON_TASK_FAILURE", True):
                logger.warning("任务失败后退出 worker，由 Docker 重启以清理 PaddleOCR/VLM 内部线程状态")
                return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logger.info("PaddleOCR worker interrupted; releasing pipeline")
        release_pipeline("keyboard interrupt")
        raise SystemExit(0)
