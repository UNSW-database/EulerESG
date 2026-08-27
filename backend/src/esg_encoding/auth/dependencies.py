"""
Authentication dependencies for FastAPI routes

Provides dependency functions for protecting routes that require authentication.
"""

from fastapi import Header, HTTPException
from typing import Optional

from ..exceptions import AccessError
from .service import get_user_id_from_authorization


async def get_current_user(authorization: Optional[str] = Header(None)) -> int:
    """
    FastAPI dependency to get current authenticated user ID
    
    This function extracts and validates the JWT token from the Authorization header
    and returns the user ID. Use this as a dependency in routes that require authentication.
    
    Example:
        @app.get("/protected")
        async def protected_route(user_id: int = Depends(get_current_user)):
            return {"user_id": user_id}
    
    Args:
        authorization: Authorization header value (format: "Bearer <token>")
        
    Returns:
        User ID from validated token
        
    Raises:
        HTTPException: 403 if authentication fails
    """
    if not authorization:
        raise HTTPException(
            status_code=403,
            detail="Authorization header is required"
        )
    
    try:
        user_id = get_user_id_from_authorization(authorization)
        return user_id
    except AccessError as e:
        raise HTTPException(
            status_code=403,
            detail=str(e)
        )


async def get_current_user_optional(authorization: Optional[str] = Header(None)) -> Optional[int]:
    """
    Optional authentication dependency - returns user_id if authenticated, None otherwise
    
    Use this for routes that work with or without authentication.
    
    Example:
        @app.get("/optional-auth")
        async def optional_route(user_id: Optional[int] = Depends(get_current_user_optional)):
            if user_id:
                return {"message": f"Hello authenticated user {user_id}"}
            else:
                return {"message": "Hello anonymous user"}
    
    Args:
        authorization: Authorization header value (format: "Bearer <token>")
        
    Returns:
        User ID if authenticated, None otherwise
    """
    if not authorization:
        return None
    
    try:
        user_id = get_user_id_from_authorization(authorization)
        return user_id
    except (AccessError, Exception):
        # 如果认证失败，返回None而不是抛出异常
        return None

