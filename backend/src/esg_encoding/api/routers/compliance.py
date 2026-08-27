from fastapi import APIRouter

from ...services import compliance_service

router = APIRouter()
router.add_api_route("/api/analyze-compliance", compliance_service.analyze_compliance, methods=["POST"])
router.add_api_route("/api/assessment", compliance_service.get_assessment, methods=["GET"])
router.add_api_route("/api/assessment/latest", compliance_service.get_latest_assessment, methods=["GET"])
router.add_api_route("/api/history", compliance_service.get_user_history, methods=["GET"])
router.add_api_route("/api/assessment/{file_id}/scopes", compliance_service.list_assessment_scopes_for_file, methods=["GET"])
router.add_api_route("/api/assessment/{file_id}", compliance_service.get_assessment_by_file, methods=["GET"])
