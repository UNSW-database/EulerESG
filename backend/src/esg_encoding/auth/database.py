"""
User database management with JSON file storage

Provides thread-safe operations for loading and saving user data.
"""

import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger


class UserDatabase:
    """User database manager with JSON file storage and thread-safe operations"""
    
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the user database
        
        Args:
            db_path: Path to the JSON database file. Defaults to backend/database.json
        """
        if db_path is None:
            # Default to backend/database.json relative to this file
            backend_dir = Path(__file__).parent.parent.parent.parent
            db_path = str(backend_dir / "database.json")
        
        self.db_path = Path(db_path)
        self.lock = asyncio.Lock()
        self._data: Dict[str, Any] = {}
        
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing data
        self._load_sync()
    
    def _load_sync(self) -> None:
        """Synchronously load data from JSON file"""
        try:
            if self.db_path.exists():
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
                logger.info(f"Loaded user database from {self.db_path}")
            else:
                self._data = {"users": {}}
                logger.info(f"Created new user database at {self.db_path}")
        except Exception as e:
            logger.error(f"Error loading database: {e}")
            self._data = {"users": {}}
    
    async def load(self) -> Dict[str, Any]:
        """
        Load data from JSON file (thread-safe)
        
        Returns:
            Dictionary containing user data
        """
        async with self.lock:
            self._load_sync()
            return self._data.copy()
    
    def _save_sync(self) -> None:
        """Synchronously save data to JSON file"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Saved user database to {self.db_path}")
        except Exception as e:
            logger.error(f"Error saving database: {e}")
            raise
    
    async def save(self) -> None:
        """
        Save data to JSON file (thread-safe)
        """
        async with self.lock:
            self._save_sync()
    
    def get_users(self) -> Dict[str, Any]:
        """
        Get all users (synchronous, for internal use)
        
        Returns:
            Dictionary of users {userId: {email, password, name, sessionActive}}
        """
        return self._data.get("users", {})
    
    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific user by ID (synchronous, for internal use)
        
        Args:
            user_id: User ID
            
        Returns:
            User data dictionary or None if not found
        """
        users = self.get_users()
        return users.get(str(user_id))
    
    def add_user(self, user_id: int, email: str, password: str, name: str) -> None:
        """
        Add a new user (synchronous, for internal use)
        
        Args:
            user_id: User ID
            email: User email
            password: User password (plain text)
            name: User name
        """
        if "users" not in self._data:
            self._data["users"] = {}
        
        self._data["users"][str(user_id)] = {
            "email": email,
            "password": password,
            "name": name,
            "sessionActive": True
        }
    
    def update_user(self, user_id: int, **kwargs) -> None:
        """
        Update user data (synchronous, for internal use)
        
        Args:
            user_id: User ID
            **kwargs: Fields to update (email, password, name, sessionActive)
        """
        users = self.get_users()
        if str(user_id) in users:
            users[str(user_id)].update(kwargs)
    
    def find_user_by_email(self, email: str) -> Optional[tuple[int, Dict[str, Any]]]:
        """
        Find user by email (synchronous, for internal use)
        
        Args:
            email: User email
            
        Returns:
            Tuple of (user_id, user_data) or None if not found
        """
        users = self.get_users()
        for user_id_str, user_data in users.items():
            if user_data.get("email") == email:
                return (int(user_id_str), user_data)
        return None


# Global database instance
_db_instance: Optional[UserDatabase] = None


def get_database() -> UserDatabase:
    """
    Get the global database instance (singleton pattern)
    
    Returns:
        UserDatabase instance
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = UserDatabase()
    return _db_instance

