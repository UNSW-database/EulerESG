from fastapi import APIRouter

from ...models import ChatResponse
from ...services import chat_service

router = APIRouter()
router.add_api_route("/api/chat/{file_id}/history", chat_service.get_file_chat_history, methods=["GET"])
router.add_api_route("/api/chat/{file_id}", chat_service.chat_with_file, methods=["POST"])
router.add_api_route("/api/chat/{file_id}", chat_service.clear_file_chat, methods=["DELETE"])
router.add_api_route("/api/chat", chat_service.chat, methods=["POST"], response_model=ChatResponse)
router.add_api_route("/api/chat/history/{session_id}", chat_service.get_chat_history, methods=["GET"])
router.add_api_route("/api/chat/session/{session_id}", chat_service.clear_chat_session, methods=["DELETE"])
