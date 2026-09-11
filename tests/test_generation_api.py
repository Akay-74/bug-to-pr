import json
import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app, default_embedding_backend, default_generation_backend
from backend.models.database import get_db
from tests.conftest import FIXTURE_ISSUE_ID
from tests.test_generation_pipeline import FakeEmbeddingBackend, FakeGenerationBackend, _GOOD_DIFF, _UNAPPLIABLE_DIFF


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[default_embedding_backend] = lambda: FakeEmbeddingBackend()
    app.dependency_overrides[default_generation_backend] = lambda: FakeGenerationBackend([_GOOD_DIFF])
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_create_fix_for_unknown_issue_404(client):
    response = client.post("/api/fix/does-not-exist")
    assert response.status_code == 404


def test_get_fix_for_unknown_id_404(client):
    response = client.get(f"/api/fix/{uuid.uuid4()}")
    assert response.status_code == 404


def test_create_fix_runs_pipeline_and_returns_schema(client):
    response = client.post(f"/api/fix/{FIXTURE_ISSUE_ID}")

    assert response.status_code == 201
    body = response.json()
    for field in (
        "fix_id", "issue_id", "repo", "base_commit", "status", "current_stage",
        "attempts_taken", "localization", "attempts", "created_at", "completed_at",
    ):
        assert field in body

    assert body["issue_id"] == FIXTURE_ISSUE_ID
    assert body["status"] == "completed"
    assert len(body["attempts"]) == 1
    attempt = body["attempts"][0]
    for field in ("attempt_number", "model", "hypothesis", "diff", "status", "failure_type", "verification_results"):
        assert field in attempt
    assert attempt["status"] == "passed"
    assert attempt["verification_results"]
    for check in attempt["verification_results"]:
        for field in ("check_name", "status", "command", "exit_code", "stdout", "stderr", "duration_ms"):
            assert field in check


def test_get_fix_reads_back_persisted_run(client):
    created = client.post(f"/api/fix/{FIXTURE_ISSUE_ID}").json()

    response = client.get(f"/api/fix/{created['fix_id']}")

    assert response.status_code == 200
    assert response.json() == created


def test_create_fix_with_failing_candidate_reports_failed_status(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[default_embedding_backend] = lambda: FakeEmbeddingBackend()
    app.dependency_overrides[default_generation_backend] = lambda: FakeGenerationBackend([_UNAPPLIABLE_DIFF])
    try:
        client = TestClient(app)
        response = client.post(f"/api/fix/{FIXTURE_ISSUE_ID}")
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "failed"
        assert body["attempts"][0]["failure_type"] == "patch_application_error"
    finally:
        app.dependency_overrides.clear()


def test_fix_response_exposes_the_localization_target_and_prompt_metadata(client):
    body = client.post(f"/api/fix/{FIXTURE_ISSUE_ID}").json()

    # docs/phase3.md §6: the response carries the localization target the
    # patch was generated against, not just the patch.
    assert body["localization"], "expected the Phase 2 candidates to be exposed"
    target = body["localization"][0]
    for field in ("rank", "file", "symbol", "start_line", "end_line", "final_score"):
        assert field in target
    assert target["file"] == "mypkg/__init__.py"

    # docs/phase3.md §2: prompt/input metadata is persisted with the patch.
    generation = next(
        c for c in body["attempts"][0]["verification_results"] if c["check_name"] == "generation"
    )
    metadata = json.loads(generation["stdout"])
    assert metadata["model"] == "fake-generation-backend"
    assert metadata["prompt_sha256"]
    assert metadata["localization_targets"]
