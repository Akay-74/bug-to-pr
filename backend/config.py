"""Central configuration for the Bug-to-PR backend.

All tunables come from environment variables (see .env.example). Nothing here
should hold real credentials.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    DATABASE_URL: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/bug2pr"

    BENCHMARK_PATH: str = str(BACKEND_DIR / "benchmark" / "issues")

    DOCKER_CPU_LIMIT: float = 2.0
    DOCKER_MEMORY_LIMIT: str = "2g"
    DOCKER_TIMEOUT: int = 600

    # Path to a JSON file listing allowed "org/repo" entries, kept separate
    # from code so the allowlist can be reviewed/edited without a deploy.
    REPOSITORY_ALLOWLIST: str = str(BACKEND_DIR / "benchmark" / "allowlist.json")

    # Localization (Phase 2): embedding cache root and default embedding
    # backend. See backend/localization/embeddings.py.
    LOCALIZATION_CACHE_DIR: str = str(BACKEND_DIR / "cache")
    LOCALIZATION_EMBEDDING_MODEL: str = "jina"

    # Fix generation (Phase 3): local coding model backend. See
    # backend/generation/backend.py. Ollama is used because it is already
    # running locally with coding-capable models pulled; no API key, no
    # network egress beyond localhost.
    GENERATION_BACKEND: str = "ollama"
    GENERATION_MODEL: str = "qwen2.5-coder:7b"
    GENERATION_OLLAMA_HOST: str = "http://localhost:11434"
    # Hosted alternative (GENERATION_BACKEND=openai_compatible): any provider
    # with an OpenAI-style /chat/completions endpoint. Defaults to Gemini.
    # The key is read from .env only; SecretStr keeps it out of reprs/logs.
    GENERATION_API_BASE: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    GENERATION_API_KEY: SecretStr = SecretStr("")
    GENERATION_TIMEOUT_SECONDS: int = 120
    GENERATION_CANDIDATE_LIMIT: int = 3
    GENERATION_MAX_PATCH_LINES: int = 400

    # Git & draft PR delivery (Phase 4). See backend/delivery/.
    #
    # No token lives here or anywhere else in source: pushing and PR creation
    # go through the `gh` CLI's own credential store (docs/phase4.md
    # "Security"). GITHUB_ENABLED is the explicit opt-in the spec requires --
    # while it is false, the pipeline still branches, commits and verifies
    # locally, and refuses to push anything.
    GITHUB_ENABLED: bool = False
    # Where a branch may be pushed. Deliberately separate from the benchmark
    # repo: pushing to the upstream project is never the default.
    GITHUB_TARGET_REPO: str = ""
    GITHUB_PR_BASE_BRANCH: str = "main"
    GITHUB_BRANCH_PREFIX: str = "bug2pr"
    GITHUB_CLI: str = "gh"
    GITHUB_TIMEOUT_SECONDS: int = 120
    GIT_AUTHOR_NAME: str = "bug2pr agent"
    GIT_AUTHOR_EMAIL: str = "bug2pr-agent@users.noreply.github.com"

    # Phase 5: browser origins allowed to call this API. An explicit list,
    # never "*" -- these endpoints start runs and open pull requests.
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def benchmark_path(self) -> Path:
        return Path(self.BENCHMARK_PATH)

    @property
    def repository_allowlist_path(self) -> Path:
        return Path(self.REPOSITORY_ALLOWLIST)

    @property
    def localization_cache_dir(self) -> Path:
        return Path(self.LOCALIZATION_CACHE_DIR)

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    def load_allowlist(self) -> set[str]:
        path = self.repository_allowlist_path
        if not path.exists():
            return set()
        data = json.loads(path.read_text())
        return set(data.get("repositories", []))


settings = Settings()
