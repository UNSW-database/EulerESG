"""Deterministic backend environment loading independent of the current cwd."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = BACKEND_DIR / "config" / ".env"
ROOT_ENV_FILE = BACKEND_DIR.parent / ".env"


def load_backend_environment(*, override: bool = False) -> Optional[Path]:
    """Load the configured backend env file without relying on process cwd.

    Existing process/container environment variables keep precedence by
    default. ``ESG_ENV_FILE`` can point IDE and non-standard deployments to a
    different file.
    """
    explicit = str(os.getenv("ESG_ENV_FILE") or "").strip()
    candidates = [Path(explicit).expanduser()] if explicit else [DEFAULT_ENV_FILE, ROOT_ENV_FILE]

    loaded_path: Optional[Path] = None
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate.absolute()
        if not resolved.is_file():
            continue
        load_dotenv(dotenv_path=resolved, override=override)
        if loaded_path is None:
            loaded_path = resolved
    return loaded_path
