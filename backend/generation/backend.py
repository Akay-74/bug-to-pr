"""Local coding model backend for fix generation (docs/phase3.md §2).

Mirrors backend/localization/embeddings.py's shape: a small Protocol plus one
real, lazily-connected implementation, so importing this module (and running
the rest of the test suite) never requires a running model server. Unit
tests inject a fake `FixGenerationBackend` instead of talking to Ollama.
"""
from __future__ import annotations

from typing import Protocol

import httpx

from backend.config import settings


class ModelUnavailableError(Exception):
    """The configured local coding model backend could not be reached."""


class GenerationTimeoutError(Exception):
    """The model did not respond within the configured timeout."""


class FixGenerationBackend(Protocol):
    model_id: str

    def generate(self, prompt: str, timeout: int) -> str: ...


class OllamaBackend:
    """Calls a local Ollama server's /api/generate endpoint.

    Ollama is used as "the project's configured local coding model/backend"
    (docs/phase3.md §2): it runs entirely on localhost, needs no API key, and
    was already running with a coding-capable model pulled in this
    environment.
    """

    def __init__(self, model: str | None = None, host: str | None = None) -> None:
        self.model_id = model or settings.GENERATION_MODEL
        self.host = (host or settings.GENERATION_OLLAMA_HOST).rstrip("/")

    def generate(self, prompt: str, timeout: int) -> str:
        try:
            response = httpx.post(
                f"{self.host}/api/generate",
                json={
                    "model": self.model_id,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.2},
                },
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise GenerationTimeoutError(f"Model '{self.model_id}' did not respond within {timeout}s") from exc
        except httpx.ConnectError as exc:
            raise ModelUnavailableError(f"Could not reach Ollama at {self.host}: {exc}") from exc

        if response.status_code == 404:
            raise ModelUnavailableError(f"Model '{self.model_id}' is not available on {self.host}")
        response.raise_for_status()
        return response.json().get("response", "")


class OpenAICompatibleBackend:
    """Calls a hosted model through the OpenAI-style /chat/completions API.

    Gemini, Groq, OpenRouter, Cerebras and Mistral all expose this endpoint,
    so one class covers every hosted option. Only the prompt leaves this
    machine: the call is made from the backend process, never from inside
    the sandbox, whose network stays disabled.

    The API key comes from settings (i.e. `.env`), is sent only in the
    Authorization header, and is scrubbed from every error message this
    raises -- those messages are persisted as verification results and shown
    in the dashboard.
    """

    def __init__(
        self,
        model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model_id = model or settings.GENERATION_MODEL
        self.api_base = (api_base or settings.GENERATION_API_BASE).rstrip("/")
        self._api_key = api_key if api_key is not None else settings.GENERATION_API_KEY.get_secret_value()

    def __repr__(self) -> str:
        return f"{type(self).__name__}(model_id={self.model_id!r}, api_base={self.api_base!r})"

    def _error(self, message: str) -> str:
        from backend.delivery.github import redact

        if self._api_key:
            message = message.replace(self._api_key, "[REDACTED]")
        return redact(message)

    def generate(self, prompt: str, timeout: int) -> str:
        if not self._api_key:
            raise ModelUnavailableError(
                "GENERATION_API_KEY is not set. Add it to .env to use a hosted model."
            )
        try:
            response = httpx.post(
                f"{self.api_base}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise GenerationTimeoutError(f"Model '{self.model_id}' did not respond within {timeout}s") from exc
        except httpx.HTTPError as exc:
            raise ModelUnavailableError(self._error(f"Could not reach {self.api_base}: {exc}")) from exc

        if response.status_code >= 400:
            # Every failure here -- bad key, unknown model, free-tier quota --
            # stops generation with a readable reason rather than crashing
            # the run with a raw HTTP error.
            reasons = {
                401: "the API key was rejected",
                403: "the API key was rejected",
                404: f"model '{self.model_id}' was not found",
                429: "rate limit or free-tier quota reached; wait and retry",
            }
            reason = reasons.get(response.status_code, "request failed")
            detail = response.text[:500]
            raise ModelUnavailableError(
                self._error(f"{self.api_base} returned {response.status_code} ({reason}): {detail}")
            )

        try:
            return response.json()["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailableError(
                self._error(f"Unexpected response from {self.api_base}: {response.text[:500]}")
            ) from exc


_BACKENDS: dict[str, type[OllamaBackend] | type[OpenAICompatibleBackend]] = {
    "ollama": OllamaBackend,
    "openai_compatible": OpenAICompatibleBackend,
}


def get_backend(name: str | None = None) -> FixGenerationBackend:
    name = name or settings.GENERATION_BACKEND
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(f"Unknown generation backend '{name}'. Options: {sorted(_BACKENDS)}") from None
