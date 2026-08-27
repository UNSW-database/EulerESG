"""FastAPI exception handlers."""

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from ..exceptions import AccessError, InputError


async def input_error_handler(request: Request, exc: InputError):
    """Handle InputError exceptions (HTTP 400)"""
    return JSONResponse(
        status_code=400,
        content={"error": str(exc)}
    )


async def access_error_handler(request: Request, exc: AccessError):
    """Handle AccessError exceptions (HTTP 403)"""
    return JSONResponse(
        status_code=403,
        content={"error": str(exc)}
    )


async def general_exception_handler(request: Request, exc: Exception):
    """Handle all other exceptions (HTTP 500)"""
    # Use {} placeholder so str(exc) is not interpreted as format string (avoids KeyError when exc contains '{...}')
    logger.error("Unhandled exception: {}", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "A system error ocurred"}
    )
