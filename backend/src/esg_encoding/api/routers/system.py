from fastapi import APIRouter

from ...services import file_service, system_service

router = APIRouter()
router.add_api_route("/", system_service.root, methods=["GET"])
router.add_api_route("/api/system/status", system_service.get_system_status, methods=["GET"])
router.add_api_route("/api/gri/options", system_service.get_gri_options, methods=["GET"])
router.add_api_route("/api/system/cleanup-orphaned-reports", system_service.cleanup_orphaned_reports, methods=["POST"])
router.add_api_route("/api/health", system_service.health_check, methods=["GET"])
