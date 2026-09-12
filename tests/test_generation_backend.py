import httpx
import pytest

from backend.generation.backend import (
    GenerationTimeoutError,
    ModelUnavailableError,
    OllamaBackend,
    get_backend,
)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"response": "diff --git a/x b/x\n"}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


def test_generate_returns_model_response_text(monkeypatch):
    backend = OllamaBackend(model="qwen2.5:7b", host="http://localhost:11434")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {"response": "hello patch"}))

    assert backend.generate("prompt", timeout=30) == "hello patch"


def test_generate_raises_model_unavailable_on_connect_error(monkeypatch):
    backend = OllamaBackend()

    def _raise(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", _raise)

    with pytest.raises(ModelUnavailableError):
        backend.generate("prompt", timeout=30)


def test_generate_raises_timeout_on_timeout_exception(monkeypatch):
    backend = OllamaBackend()

    def _raise(*a, **k):
        raise httpx.TimeoutException("too slow")

    monkeypatch.setattr(httpx, "post", _raise)

    with pytest.raises(GenerationTimeoutError):
        backend.generate("prompt", timeout=30)


def test_generate_raises_model_unavailable_on_404(monkeypatch):
    backend = OllamaBackend()
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(404))

    with pytest.raises(ModelUnavailableError):
        backend.generate("prompt", timeout=30)


def test_get_backend_returns_configured_model():
    backend = get_backend("ollama")
    assert backend.model_id


def test_get_backend_rejects_unknown_name():
    with pytest.raises(ValueError):
        get_backend("not-a-real-backend")


# --- hosted, OpenAI-compatible backend (Gemini, Groq, OpenRouter, ...) -----

from backend.generation import backend as backend_module
from backend.generation.backend import OpenAICompatibleBackend

# Google-key-shaped but fake. Assembled at runtime so GitHub's secret-scanning
# push protection does not mistake the source for a leaked key.
_FAKE_KEY = "AI" + "za" + "SyFAKE-key-for-tests-only-0123456789ab"


class _FakeChatResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": "hello patch"}}]
        }
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def _hosted(**overrides):
    kwargs = dict(model="gemini-test", api_base="https://example.test/v1", api_key=_FAKE_KEY)
    kwargs.update(overrides)
    return OpenAICompatibleBackend(**kwargs)


def test_hosted_backend_returns_the_message_content(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeChatResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    assert _hosted().generate("prompt", timeout=30) == "hello patch"
    url, kwargs = calls[0]
    assert url == "https://example.test/v1/chat/completions"
    assert kwargs["json"]["model"] == "gemini-test"
    assert kwargs["json"]["messages"] == [{"role": "user", "content": "prompt"}]


def test_hosted_backend_sends_the_key_only_in_the_authorization_header(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append((url, kw)) or _FakeChatResponse())

    _hosted().generate("prompt", timeout=30)

    url, kwargs = calls[0]
    assert _FAKE_KEY not in url
    assert _FAKE_KEY not in str(kwargs["json"])
    assert kwargs["headers"]["Authorization"] == f"Bearer {_FAKE_KEY}"


def test_hosted_backend_without_a_key_fails_clearly_and_makes_no_request(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("no request should be made without a key")

    monkeypatch.setattr(httpx, "post", fail)

    with pytest.raises(ModelUnavailableError, match="GENERATION_API_KEY"):
        _hosted(api_key="").generate("prompt", timeout=30)


@pytest.mark.parametrize("status, phrase", [
    (401, "rejected"), (403, "rejected"), (404, "not found"),
])
def test_hosted_backend_maps_permanent_http_errors_to_model_unavailable(monkeypatch, status, phrase):
    """A bad key or wrong model name is final: fail immediately, don't retry."""
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _FakeChatResponse(status, {}, "error body")

    monkeypatch.setattr(httpx, "post", fake_post)

    with pytest.raises(ModelUnavailableError, match=phrase):
        _hosted().generate("prompt", timeout=30)
    assert len(calls) == 1, "a permanent error must not be retried"


# --- transient failures ----------------------------------------------------


@pytest.fixture()
def no_sleep(monkeypatch):
    """Backoff without the wait, so these tests stay fast."""
    slept = []
    monkeypatch.setattr(backend_module.time, "sleep", lambda s: slept.append(s))
    return slept


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_transient_failure_is_retried(monkeypatch, no_sleep, status):
    """Regression: a Gemini 503 "high demand" ended a whole benchmark issue
    after one attempt, even though retrying a second later would have worked.
    """
    responses = [_FakeChatResponse(status, {}, "overloaded"), _FakeChatResponse()]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: responses.pop(0))

    assert _hosted().generate("prompt", timeout=30) == "hello patch"
    assert no_sleep == [2.0], "should back off before retrying"


def test_retries_are_bounded_and_report_the_last_error(monkeypatch, no_sleep):
    calls = []

    def always_overloaded(*a, **k):
        calls.append(1)
        return _FakeChatResponse(503, {}, "overloaded")

    monkeypatch.setattr(httpx, "post", always_overloaded)

    with pytest.raises(ModelUnavailableError, match="attempts failed"):
        _hosted().generate("prompt", timeout=30)

    assert len(calls) == backend_module._MAX_ATTEMPTS
    assert no_sleep == list(backend_module._BACKOFF_SECONDS), "backoff should grow"


def test_a_dropped_connection_is_retried(monkeypatch, no_sleep):
    attempts = []

    def flaky(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("connection reset")
        return _FakeChatResponse()

    monkeypatch.setattr(httpx, "post", flaky)

    assert _hosted().generate("prompt", timeout=30) == "hello patch"


def test_the_providers_retry_after_header_wins_over_our_backoff(monkeypatch, no_sleep):
    responses = [_FakeChatResponse(429, {}, "slow down", headers={"retry-after": "7"}),
                 _FakeChatResponse()]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: responses.pop(0))

    _hosted().generate("prompt", timeout=30)

    assert no_sleep == [7.0]


def test_an_absurd_retry_after_is_capped(monkeypatch, no_sleep):
    """A provider asking us to wait an hour must not hang the pipeline."""
    responses = [_FakeChatResponse(429, {}, "later", headers={"retry-after": "99999"}),
                 _FakeChatResponse()]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: responses.pop(0))

    _hosted().generate("prompt", timeout=30)

    assert no_sleep == [backend_module._MAX_RETRY_AFTER_SECONDS]


def test_a_timeout_is_not_retried(monkeypatch, no_sleep):
    """The per-call timeout is already the budget; retrying multiplies it."""
    def slow(*a, **k):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "post", slow)

    with pytest.raises(GenerationTimeoutError):
        _hosted().generate("prompt", timeout=30)
    assert no_sleep == []


def test_hosted_backend_never_puts_the_key_in_an_error_message(monkeypatch):
    """Error messages are persisted and shown in the dashboard."""
    echoed = f"invalid key {_FAKE_KEY} supplied"
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeChatResponse(400, {}, echoed))

    with pytest.raises(ModelUnavailableError) as info:
        _hosted().generate("prompt", timeout=30)

    assert _FAKE_KEY not in str(info.value)
    assert "[REDACTED]" in str(info.value)


def test_hosted_backend_maps_timeout_and_connection_errors(monkeypatch, no_sleep):
    def timeout(*a, **k):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "post", timeout)
    with pytest.raises(GenerationTimeoutError):
        _hosted().generate("prompt", timeout=30)

    def refused(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", refused)
    with pytest.raises(ModelUnavailableError):
        _hosted().generate("prompt", timeout=30)


def test_hosted_backend_rejects_a_malformed_response(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeChatResponse(200, {"choices": []}))

    with pytest.raises(ModelUnavailableError):
        _hosted().generate("prompt", timeout=30)


def test_hosted_backend_repr_and_settings_never_show_the_key():
    from backend.config import Settings

    assert _FAKE_KEY not in repr(_hosted())
    settings = Settings(GENERATION_API_KEY=_FAKE_KEY)
    assert _FAKE_KEY not in repr(settings)
    assert _FAKE_KEY not in str(settings.GENERATION_API_KEY)


def test_get_backend_knows_the_hosted_backend():
    assert isinstance(get_backend("openai_compatible"), OpenAICompatibleBackend)


def test_redact_masks_google_api_keys():
    from backend.delivery.github import redact

    assert _FAKE_KEY not in redact(f"key={_FAKE_KEY}")


def test_redact_removes_the_configured_api_key_whatever_its_format(monkeypatch):
    """Provider key formats change; the exact configured value is always masked."""
    from pydantic import SecretStr

    from backend.config import settings
    from backend.delivery.github import redact

    unusual = "XYZnew-format-key-0123456789abcdefghijklmnopqrstuvwx"
    monkeypatch.setattr(settings, "GENERATION_API_KEY", SecretStr(unusual))

    assert unusual not in redact(f"upstream said: bad key {unusual}")


def test_a_retry_delay_in_the_response_body_is_honoured(monkeypatch, no_sleep):
    """Gemini sends no Retry-After header; it writes the delay into the
    error message ("Please retry in 36.04359451s").
    """
    body = '{"error": {"code": 429, "message": "Quota exceeded. Please retry in 36.04359451s."}}'
    responses = [_FakeChatResponse(429, {}, body), _FakeChatResponse()]
    monkeypatch.setattr(httpx, "post", lambda *a, **k: responses.pop(0))

    _hosted().generate("prompt", timeout=30)

    assert no_sleep == [36.04359451]
