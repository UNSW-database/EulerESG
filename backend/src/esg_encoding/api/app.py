"""FastAPI application factory and router wiring."""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from ..environment import load_backend_environment
from ..logging_config import configure_logging, env_float

load_backend_environment()
configure_logging("backend")

from ..exceptions import AccessError, InputError
from ..file_manager import file_manager
from . import error_handlers
from ..services import system_service
from ..services.standards_library_service import get_standards_catalog
from .routers import (
    auth,
    chat,
    compliance,
    cross_analysis,
    disclosure_graph,
    excel_metrics,
    files,
    reports,
    standards_library,
    system,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle hooks.

    FastAPI/Starlette 1.x removed the legacy add_event_handler API.
    The lifespan protocol is compatible with current FastAPI and keeps
    startup initialization in one place.
    """
    await system_service.startup_event()
    try:
        await asyncio.to_thread(get_standards_catalog)
    except Exception as exc:
        # Standards routes already return a controlled data error. Keep the
        # rest of the backend available if optional catalog data is damaged.
        logger.warning(f"Standards catalog warmup skipped: {exc}")
    try:
        yield
    finally:
        try:
            from ..gpu_model_lifecycle import unload_backend_models
            unload_backend_models("app shutdown")
        except Exception as exc:
            logger.warning(f"Backend model cleanup on shutdown skipped: {exc}")


app = FastAPI(
    title="ESG Analysis System API",
    description="Complete ESG report analysis and compliance assessment system",
    version="1.0.0",
    lifespan=lifespan,
)

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SLOW_REQUEST_SECONDS = env_float("APP_SLOW_REQUEST_MS", 2000, 0) / 1000.0


@app.middleware("http")
async def diagnostic_request_logging(request, call_next):
    supplied_id = str(request.headers.get("x-request-id") or "").strip()
    request_id = supplied_id if _REQUEST_ID_RE.fullmatch(supplied_id) else uuid.uuid4().hex
    started = time.perf_counter()
    with logger.contextualize(request_id=request_id):
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "HTTP request failed method={} path={} elapsed_ms={:.1f}",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            logger.warning(
                "HTTP request rejected method={} path={} status={} elapsed_ms={:.1f}",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        elif _SLOW_REQUEST_SECONDS > 0 and elapsed_ms >= _SLOW_REQUEST_SECONDS * 1000:
            logger.info(
                "Slow HTTP request method={} path={} status={} elapsed_ms={:.1f}",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
        response.headers["X-Request-ID"] = request_id
        return response

FRONTEND_ORIGINS_STR = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001,http://192.168.254.1:3001",
)
FRONTEND_ORIGINS = [origin.strip() for origin in FRONTEND_ORIGINS_STR.split(",") if origin.strip()]
logger.debug(f"CORS allowed origins: {FRONTEND_ORIGINS}")

try:
    _uploads_dir = str(file_manager.base_dir.resolve())
    if os.path.isdir(_uploads_dir):
        app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")
        logger.debug(f"Mounted /uploads -> {_uploads_dir}")
    else:
        logger.warning(f"Uploads dir not found: {_uploads_dir} (skip mount)")
except Exception as _e:
    logger.warning(f"Failed to mount /uploads: {_e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.add_exception_handler(InputError, error_handlers.input_error_handler)
app.add_exception_handler(AccessError, error_handlers.access_error_handler)
app.add_exception_handler(Exception, error_handlers.general_exception_handler)

app.include_router(system.router)
app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(compliance.router)
app.include_router(chat.router)
app.include_router(files.router)
app.include_router(cross_analysis.router)
app.include_router(excel_metrics.router)
app.include_router(standards_library.router)
app.include_router(disclosure_graph.router)
