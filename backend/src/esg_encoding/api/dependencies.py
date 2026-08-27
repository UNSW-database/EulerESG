"""API dependency exports.

Central place for FastAPI dependencies after router split.
"""

from ..auth.dependencies import get_current_user, get_current_user_optional
from ..services.common import system_components

__all__ = [
    "get_current_user",
    "get_current_user_optional",
    "system_components",
]
