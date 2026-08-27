"""Authenticated endpoints for deterministic disclosure graph exploration."""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ...auth.dependencies import get_current_user
from ...graph_models import DisclosureGraphResponse
from ...services.disclosure_graph_service import (
    DisclosureGraphNotFound,
    build_company_disclosure_graph,
    build_company_graph_neighbors,
    build_report_disclosure_graph,
    build_report_graph_neighbors,
)


router = APIRouter(tags=["disclosure-graph"])


def _report_filter_values(
    repeated: Optional[List[str]],
    comma_separated: Optional[str],
) -> Optional[List[str]]:
    values: List[str] = []
    for value in repeated or []:
        clean = str(value or "").strip()
        if clean and clean not in values:
            values.append(clean)
    for value in str(comma_separated or "").split(","):
        clean = value.strip()
        if clean and clean not in values:
            values.append(clean)
    return values or None


@router.get(
    "/api/reports/{file_id}/disclosure-graph",
    response_model=DisclosureGraphResponse,
)
async def report_disclosure_graph(
    file_id: str,
    scope: Optional[str] = Query(None),
    include_evidence: bool = Query(False),
    evidence_limit: int = Query(8, ge=1, le=20),
    user_id: int = Depends(get_current_user),
) -> DisclosureGraphResponse:
    try:
        return await asyncio.to_thread(
            build_report_disclosure_graph,
            file_id=file_id,
            user_id=user_id,
            scope_key=scope,
            include_evidence=include_evidence,
            evidence_limit=evidence_limit,
        )
    except DisclosureGraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/reports/{file_id}/disclosure-graph/neighbors",
    response_model=DisclosureGraphResponse,
)
async def report_disclosure_graph_neighbors(
    file_id: str,
    node_id: str = Query(..., min_length=1),
    scope: Optional[str] = Query(None),
    depth: int = Query(2, ge=1, le=3),
    evidence_limit: int = Query(8, ge=1, le=20),
    user_id: int = Depends(get_current_user),
) -> DisclosureGraphResponse:
    try:
        return await asyncio.to_thread(
            build_report_graph_neighbors,
            file_id=file_id,
            user_id=user_id,
            node_id=node_id,
            scope_key=scope,
            depth=depth,
            evidence_limit=evidence_limit,
        )
    except DisclosureGraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/companies/{company_id}/disclosure-graph",
    response_model=DisclosureGraphResponse,
)
async def company_disclosure_graph(
    company_id: str,
    scope: Optional[str] = Query(None),
    include_evidence: bool = Query(False),
    evidence_limit: int = Query(8, ge=1, le=20),
    report_id: Optional[List[str]] = Query(None),
    report_ids: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user),
) -> DisclosureGraphResponse:
    selected_report_ids = _report_filter_values(report_id, report_ids)
    try:
        return await asyncio.to_thread(
            build_company_disclosure_graph,
            company_id=company_id,
            user_id=user_id,
            scope_key=scope,
            include_evidence=include_evidence,
            evidence_limit=evidence_limit,
            selected_report_ids=selected_report_ids,
        )
    except DisclosureGraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/api/companies/{company_id}/disclosure-graph/neighbors",
    response_model=DisclosureGraphResponse,
)
async def company_disclosure_graph_neighbors(
    company_id: str,
    node_id: str = Query(..., min_length=1),
    scope: Optional[str] = Query(None),
    depth: int = Query(2, ge=1, le=3),
    evidence_limit: int = Query(8, ge=1, le=20),
    report_id: Optional[List[str]] = Query(None),
    report_ids: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user),
) -> DisclosureGraphResponse:
    selected_report_ids = _report_filter_values(report_id, report_ids)
    try:
        return await asyncio.to_thread(
            build_company_graph_neighbors,
            company_id=company_id,
            user_id=user_id,
            node_id=node_id,
            scope_key=scope,
            depth=depth,
            evidence_limit=evidence_limit,
            selected_report_ids=selected_report_ids,
        )
    except DisclosureGraphNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


__all__ = ["router"]
