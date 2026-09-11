import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.models.database import get_db
from tests.conftest import FIXTURE_ISSUE_ID


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_benchmark_issues_lists_fixture_issue(client):
    response = client.get("/api/benchmark/issues")
    assert response.status_code == 200
    issue_ids = [i["issue_id"] for i in response.json()["issues"]]
    assert FIXTURE_ISSUE_ID in issue_ids


def test_create_run_accepts_supported_issue(client):
    response = client.post("/api/runs", json={"issue_url": "https://example.invalid/fixture/tiny-repo/issues/1"})
    assert response.status_code == 201
    body = response.json()
    assert body["issue_id"] == FIXTURE_ISSUE_ID
    assert body["status"] == "queued"
    assert body["current_stage"] == "intake"
    assert body["mode"] == "public_demo"


def test_create_run_rejects_unsupported_issue(client):
    response = client.post("/api/runs", json={"issue_url": "https://github.com/some/unsupported/pull/1"})
    assert response.status_code == 422


def test_run_can_be_retrieved(client):
    create_response = client.post(
        "/api/runs", json={"issue_url": "https://example.invalid/fixture/tiny-repo/issues/1"}
    )
    run_id = create_response.json()["id"]

    get_response = client.get(f"/api/runs/{run_id}")

    assert get_response.status_code == 200
    assert get_response.json()["id"] == run_id


def test_get_unknown_run_returns_404(client):
    import uuid

    response = client.get(f"/api/runs/{uuid.uuid4()}")
    assert response.status_code == 404
