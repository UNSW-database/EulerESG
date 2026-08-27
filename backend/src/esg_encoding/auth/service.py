"""
Authentication service for user login, registration, and JWT token management
"""

import random
from typing import Dict, Any, Optional
from jose import jwt, JWTError
from loguru import logger

from ..exceptions import InputError, AccessError
from .database import get_database

# JWT configuration
JWT_SECRET = "emilywashere"
JWT_ALGORITHM = "HS256"


def generate_user_id() -> int:
    """
    Generate a unique user ID (random integer)
    
    Returns:
        Random integer user ID
    """
    return random.randint(100000, 999999)


async def register(email: str, password: str, name: str) -> Dict[str, Any]:
    """
    Register a new user and return JWT token
    
    Args:
        email: User email address
        password: User password
        name: User name
        
    Returns:
        Dictionary with 'token' and 'userId'
        
    Raises:
        InputError: If email already exists or invalid input
    """
    db = get_database()
    
    # Check if email already exists
    existing_user = db.find_user_by_email(email)
    if existing_user:
        raise InputError("Email already registered")
    
    # Validate input
    if not email or not email.strip():
        raise InputError("Email is required")
    if not password or not password.strip():
        raise InputError("Password is required")
    if not name or not name.strip():
        raise InputError("Name is required")
    
    # Generate unique user ID
    user_id = generate_user_id()
    # Ensure uniqueness (simple approach - in production, use better method)
    while db.get_user(user_id) is not None:
        user_id = generate_user_id()
    
    # Add user to database
    db.add_user(user_id, email, password, name)
    await db.save()
    
    logger.info(f"User registered: {email} (ID: {user_id})")
    
    # Generate JWT token
    token = generate_token(user_id)
    return {
        "token": token,
        "userId": user_id,
        "name": name
    }


async def login(email: str, password: str) -> Dict[str, Any]:
    """
    Login user and return JWT token
    
    Args:
        email: User email address
        password: User password
        
    Returns:
        Dictionary with 'token' and 'userId'
        
    Raises:
        InputError: If email or password is invalid
    """
    db = get_database()
    
    # Find user by email
    user_result = db.find_user_by_email(email)
    if not user_result:
        raise InputError("Invalid email or password")
    
    user_id, user_data = user_result
    
    # Verify password (plain text comparison for now)
    if user_data.get("password") != password:
        raise InputError("Invalid email or password")
    
    # Update session active status
    db.update_user(user_id, sessionActive=True)
    await db.save()
    
    logger.info(f"User logged in: {email} (ID: {user_id})")
    
    # Generate JWT token
    token = generate_token(user_id)
    
    # Include name so frontend can display it after login
    return {
        "token": token,
        "userId": user_id,
        "name": user_data.get("name","")
    }


def generate_token(user_id: int) -> str:
    """
    Generate JWT token for user
    
    Args:
        user_id: User ID
        
    Returns:
        JWT token string
    """
    payload = {"userId": user_id}
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


def get_user_id_from_authorization(authorization: str) -> int:
    """
    Extract and validate user ID from Authorization header
    
    Args:
        authorization: Authorization header value (format: "Bearer <token>")
        
    Returns:
        User ID from token
        
    Raises:
        AccessError: If token is invalid or missing
    """
    if not authorization:
        raise AccessError("Authorization header is required")
    
    # Extract token from "Bearer <token>" format
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AccessError("Invalid authorization format. Expected: Bearer <token>")
    
    token = parts[1]
    
    try:
        # Decode and verify token
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("userId")
        
        if user_id is None:
            raise AccessError("Invalid token: userId not found")
        
        # Verify user exists
        db = get_database()
        user = db.get_user(user_id)
        if not user:
            raise AccessError("User not found")
        
        return user_id
        
    except JWTError as e:
        logger.warning(f"JWT decode error: {e}")
        raise AccessError("Invalid or expired token")
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        raise AccessError("Token validation failed")


def get_user_id_from_email(email: str) -> Optional[int]:
    """
    Get user ID from email address
    
    Args:
        email: User email address
        
    Returns:
        User ID if found, None otherwise
    """
    db = get_database()
    user_result = db.find_user_by_email(email)
    if user_result:
        return user_result[0]
    return None

