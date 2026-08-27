from fastapi import APIRouter

from ...cross_analysis_models import ExcelMetricsResponse
from ...services import cross_analysis_service

router = APIRouter()
router.add_api_route("/api/cross-analysis/excel-metrics", cross_analysis_service.cross_analysis_excel_metrics, methods=["POST"], response_model=ExcelMetricsResponse)
router.add_api_route("/api/cross-analysis/excel-metrics/cache", cross_analysis_service.cross_analysis_excel_metrics_cache, methods=["GET"], response_model=ExcelMetricsResponse)
