"""Repository allowlist enforcement.

The allowlist is data (backend/benchmark/allowlist.json), not code, so it can
be reviewed and edited without touching program logic.
"""
from __future__ import annotations

from backend.config import settings


def is_repo_allowed(repo: str) -> bool:
    return repo in settings.load_allowlist()


def get_allowlist() -> set[str]:
    return settings.load_allowlist()
