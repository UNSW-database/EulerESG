from fastapi import APIRouter

from ...models import AuthResponse
from ...services import auth_service

router = APIRouter()
router.add_api_route("/auth/register", auth_service.register_endpoint, methods=["POST"], response_model=AuthResponse)
router.add_api_route("/auth/login", auth_service.login_endpoint, methods=["POST"], response_model=AuthResponse)
