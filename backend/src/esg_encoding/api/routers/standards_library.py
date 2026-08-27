"""Read-only Standards Library API routes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from ...auth.dependencies import get_current_user
from ...services.standards_library_service import (
    StandardsDataError,
    StandardsScopeNotFound,
    get_standard_metrics,
    get_standards_catalog,
)


router = APIRouter(prefix="/api/standards-library", tags=["standards-library"])


@router.get("/catalog")
async def standards_catalog(user_id: int = Depends(get_current_user)):
    del user_id
    try:
        return await asyncio.to_thread(get_standards_catalog)
    except StandardsDataError as exc:
        raise HTTPException(status_code=500, detail="Standards catalog data is unavailable") from exc


@router.get("/{framework}/metrics")
async def standards_metrics(
    framework: str,
    scope_id: str = Query(..., min_length=1, max_length=180),
    group_id: str | None = Query(default=None, max_length=180),
    user_id: int = Depends(get_current_user),
):
    del user_id
    try:
        return await asyncio.to_thread(
            get_standard_metrics,
            framework,
            scope_id,
            group_id,
        )
    except StandardsScopeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StandardsDataError as exc:
        raise HTTPException(status_code=500, detail="Standards metric data is unavailable") from exc
