from fastapi import APIRouter

from ...services import company_report_service, report_service

router = APIRouter()
router.add_api_route("/api/upload-report", report_service.upload_report, methods=["POST"])
router.add_api_route("/api/report-batches", company_report_service.upload_report_batch, methods=["POST"])
router.add_api_route("/api/report-batches/{batch_id}/retry", company_report_service.retry_company_batch, methods=["POST"])
router.add_api_route("/api/companies", company_report_service.list_companies, methods=["GET"])
router.add_api_route("/api/companies/{company_id}", company_report_service.get_company, methods=["GET"])
router.add_api_route(
    "/api/companies/{company_id}/assessment",
    company_report_service.get_company_assessment,
    methods=["GET"],
)
router.add_api_route("/api/upload-metrics", report_service.upload_metrics, methods=["POST"])
router.add_api_route("/api/reports/latest", report_service.get_latest_report, methods=["GET"])
router.add_api_route("/api/reports/{file_id}", report_service.get_report_by_file_id, methods=["GET"])
router.add_api_route("/api/reports/{file_id}/reprocess", report_service.reprocess_report, methods=["POST"])
router.add_api_route("/api/reports/{file_id}/reanalyze", report_service.reanalyze_report, methods=["POST"])

router.add_api_route("/api/report-jobs/{job_id}", report_service.get_report_job_status, methods=["GET"])
router.add_api_route("/api/report-jobs/{job_id}/events", report_service.report_job_events, methods=["GET"])
