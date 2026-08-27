"""Authentication API service functions."""

from .common import *  # noqa: F401,F403


async def register_endpoint(request: RegisterRequest):
    """
    Register a new user
    
    Args:
        request: Registration request with email, password, and name
        
    Returns:
        AuthResponse with token and userId
    """
    result = await register(request.email, request.password, request.name)
    return AuthResponse(**result)


async def login_endpoint(request: LoginRequest):
    """
    Login user
    
    Args:
        request: Login request with email and password
        
    Returns:
        AuthResponse with token and userId
    """
    result = await login(request.email, request.password)
    return AuthResponse(**result)
