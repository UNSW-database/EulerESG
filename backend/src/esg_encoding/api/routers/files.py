from fastapi import APIRouter

from ...services import file_service

router = APIRouter()
router.add_api_route("/api/files", file_service.list_files, methods=["GET"])
router.add_api_route("/api/files/cleanup", file_service.cleanup_old_files, methods=["POST"])
router.add_api_route("/api/files/{file_id}", file_service.get_file_info, methods=["GET"])
router.add_api_route("/api/files/{file_id}/pdf", file_service.serve_pdf, methods=["GET"])
router.add_api_route("/api/files/{file_id}/visual-assets", file_service.list_visual_assets, methods=["GET"])
router.add_api_route("/api/files/{file_id}/visual-assets/{asset_id}", file_service.serve_visual_asset, methods=["GET"])
router.add_api_route("/api/files/{file_id}", file_service.delete_file, methods=["DELETE"])
