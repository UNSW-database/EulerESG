from fastapi import APIRouter

from ...cross_analysis_models import (
    CrossCompareResponse,
    CrossDisclosedCacheResponse,
    CrossRecordsResponse,
    CrossReportsResponse,
)
from ...services import cross_analysis_service

router = APIRouter()
router.add_api_route("/api/cross-analysis/reports", cross_analysis_service.cross_analysis_reports, methods=["GET"], response_model=CrossReportsResponse)
router.add_api_route("/api/cross-analysis/compare", cross_analysis_service.cross_analysis_compare, methods=["POST"], response_model=CrossCompareResponse)
router.add_api_route("/api/cross-analysis/records", cross_analysis_service.cross_analysis_records, methods=["POST"], response_model=CrossRecordsResponse)
router.add_api_route("/api/cross-analysis/disclosed-cache", cross_analysis_service.cross_analysis_disclosed_cache, methods=["GET"], response_model=CrossDisclosedCacheResponse)
