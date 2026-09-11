"""The workflow API the dashboard consumes (docs/phase5.md).

One call has to carry everything the UI renders: issue selection, validation
state, localization, the generated patch, test results, candidate attempts,
commit state, draft PR state and errors.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app, default_embedding_backend, default_generation_backend
from backend.benchmark.loader import load_issue, save_metadata
from backend.benchmark.schema import ValidationStatus
from backend.delivery import github
from backend.models.database import get_db
from tests.conftest import FIXTURE_ISSUE_ID
from tests.test_generation_pipeline import FakeEmbeddingBackend, FakeGenerationBackend, _GOOD_DIFF


@pytest.fixture()
def valid_benchmark():
    issue = load_issue(FIXTURE_ISSUE_ID)
    original = issue.metadata.validation_status
    issue.metadata.validation_status = ValidationStatus.VALID
    save_metadata(FIXTURE_ISSUE_ID, issue.metadata)
    yield
    issue.metadata.validation_status = original
    save_metadata(FIXTURE_ISSUE_ID, issue.metadata)


@pytest.fixture()
def client(db_session, monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[default_embedding_backend] = lambda: FakeEmbeddingBackend()
    app.dependency_overrides[default_generation_backend] = lambda: FakeGenerationBackend([_GOOD_DIFF])
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_workflow_endpoint_returns_every_field_the_dashboard_needs(client, valid_benchmark):
    response = client.post(f"/api/workflow/{FIXTURE_ISSUE_ID}")

    assert response.status_code == 201
    body = response.json()
    for field in (
        "run_id", "issue_id", "issue_url", "repo", "base_commit", "title", "problem",
        "benchmark_validation", "status", "current_stage", "attempts_taken", "error",
        "localization", "attempts", "delivery", "created_at", "started_at", "completed_at",
    ):
        assert field in body, field

    assert body["status"] == "completed"
    assert body["benchmark_validation"] == "valid"
    assert body["title"]
    assert body["localization"], "localization results should be exposed"

    attempt = body["attempts"][0]
    assert attempt["diff"], "the generated patch is shown"
    assert attempt["verification_results"], "test results are shown"
    assert attempt["status"] == "passed"

    delivery = body["delivery"]
    assert delivery["commit_sha"], "commit state is shown"
    assert delivery["status"] == "committed"


def test_workflow_can_be_read_back_by_issue_and_by_run(client, valid_benchmark):
    created = client.post(f"/api/workflow/{FIXTURE_ISSUE_ID}").json()

    by_issue = client.get(f"/api/workflow/{FIXTURE_ISSUE_ID}")
    by_run = client.get(f"/api/workflow/run/{created['run_id']}")

    assert by_issue.status_code == 200
    assert by_run.status_code == 200
    assert by_issue.json()["run_id"] == created["run_id"]
    assert by_run.json()["run_id"] == created["run_id"]


def test_run_route_is_not_shadowed_by_the_issue_route(client, valid_benchmark):
    """`/api/workflow/run/{id}` must not be read as an issue named "run"."""
    created = client.post(f"/api/workflow/{FIXTURE_ISSUE_ID}").json()

    response = client.get(f"/api/workflow/run/{created['run_id']}")

    assert response.status_code == 200
    assert response.json()["issue_id"] == FIXTURE_ISSUE_ID


def test_unknown_issue_is_404(client):
    assert client.post("/api/workflow/does-not-exist").status_code == 404


def test_issue_without_a_run_is_404(client, valid_benchmark):
    assert client.get(f"/api/workflow/{FIXTURE_ISSUE_ID}").status_code == 404


def test_unknown_run_is_404(client):
    assert client.get(f"/api/workflow/run/{uuid.uuid4()}").status_code == 404


def test_unvalidated_benchmark_issue_is_422(client):
    """The fixture issue is `pending` without the valid_benchmark fixture."""
    response = client.post(f"/api/workflow/{FIXTURE_ISSUE_ID}")

    assert response.status_code == 422
    assert "not valid" in response.json()["detail"]


def test_failed_workflow_reports_its_error_rather_than_an_empty_run(db_session, valid_benchmark, monkeypatch):
    from tests.test_generation_pipeline import _UNAPPLIABLE_DIFF

    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[default_embedding_backend] = lambda: FakeEmbeddingBackend()
    app.dependency_overrides[default_generation_backend] = lambda: FakeGenerationBackend([_UNAPPLIABLE_DIFF])
    try:
        body = TestClient(app).post(f"/api/workflow/{FIXTURE_ISSUE_ID}").json()
    finally:
        app.dependency_overrides.clear()

    assert body["status"] == "failed"
    assert body["error"]
    assert body["delivery"] is None
    assert body["attempts"][0]["failure_type"] == "patch_application_error"


def test_benchmark_issue_listing_still_drives_issue_selection(client):
    response = client.get("/api/benchmark/issues")

    assert response.status_code == 200
    issues = response.json()["issues"]
    assert any(i["issue_id"] == FIXTURE_ISSUE_ID for i in issues)
    for issue in issues:
        for field in ("issue_id", "issue_url", "repo", "validation_status"):
            assert field in issue


def test_cors_allows_the_dashboard_origin_but_not_an_arbitrary_one(client):
    from backend.config import settings

    allowed = client.get(
        "/api/benchmark/issues", headers={"Origin": settings.cors_origins[0]}
    )
    hostile = client.get(
        "/api/benchmark/issues", headers={"Origin": "https://evil.example"}
    )

    assert allowed.headers.get("access-control-allow-origin") == settings.cors_origins[0]
    assert "access-control-allow-origin" not in hostile.headers
