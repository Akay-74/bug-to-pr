"""Phase 4 API surface and its error mapping (docs/phase4.md "API").

GitHub is mocked; no test here reaches the network.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from backend.api.main import app
from backend.delivery import github
from backend.delivery import pipeline as delivery_pipeline
from backend.models.attempt import AttemptStatus, FailureType
from backend.models.database import get_db
from backend.models.run import RunStatus
from tests.test_delivery_pipeline import _validated_run

_FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def passing_verification(monkeypatch):
    monkeypatch.setattr(
        delivery_pipeline,
        "verify_committed_tree",
        lambda issue, workspace: delivery_pipeline._Verification(passed=True, command="pytest", detail=""),
    )


@pytest.fixture()
def github_ready(monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")
    monkeypatch.setattr(github.settings, "GITHUB_PR_BASE_BRANCH", "main")
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(github, "push_branch", lambda *a, **k: None)
    monkeypatch.setattr(
        github,
        "create_draft_pr",
        lambda **kwargs: github.DraftPullRequest(
            url="https://github.com/someone/safe-test-repo/pull/7", number=7, is_draft=True, state="OPEN"
        ),
    )


def test_commit_endpoint_returns_the_full_schema(client, db_session, passing_verification):
    run = _validated_run(db_session)

    response = client.post(f"/api/pr/{run.id}/commit")

    assert response.status_code == 201
    body = response.json()
    for field in (
        "delivery_id", "fix_id", "issue_id", "repo", "base_commit", "branch_name", "commit_sha",
        "commit_message", "status", "failure_type", "error", "pushed", "target_repo", "base_branch",
        "pr_url", "pr_number", "pr_state", "is_draft", "verification_command", "verification_passed",
        "created_at", "committed_at", "pr_created_at",
    ):
        assert field in body

    assert body["status"] == "committed"
    assert body["commit_sha"]
    assert body["pushed"] is False
    assert body["pr_url"] is None


def test_draft_pr_endpoint_returns_the_pr(client, db_session, passing_verification, github_ready):
    run = _validated_run(db_session)

    response = client.post(f"/api/pr/{run.id}")

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pr_created"
    assert body["pr_url"] == "https://github.com/someone/safe-test-repo/pull/7"
    assert body["pr_number"] == 7
    assert body["is_draft"] is True
    assert body["pushed"] is True


def test_get_pr_reads_back_the_recorded_delivery(client, db_session, passing_verification, github_ready):
    run = _validated_run(db_session)
    created = client.post(f"/api/pr/{run.id}").json()

    response = client.get(f"/api/pr/{run.id}")

    assert response.status_code == 200
    assert response.json()["delivery_id"] == created["delivery_id"]
    assert response.json()["pr_url"] == created["pr_url"]


def test_get_pr_can_refresh_live_state(client, db_session, passing_verification, github_ready, monkeypatch):
    run = _validated_run(db_session)
    client.post(f"/api/pr/{run.id}")
    monkeypatch.setattr(
        github,
        "get_pr_status",
        lambda repo, ref: github.PullRequestStatus(url=ref, number=7, state="CLOSED", is_draft=False),
    )

    response = client.get(f"/api/pr/{run.id}?refresh=true")

    assert response.status_code == 200
    assert response.json()["pr_state"] == "CLOSED"


def test_unknown_fix_is_404(client):
    assert client.post(f"/api/pr/{uuid.uuid4()}/commit").status_code == 404
    assert client.post(f"/api/pr/{uuid.uuid4()}").status_code == 404
    assert client.get(f"/api/pr/{uuid.uuid4()}").status_code == 404


def test_fix_without_a_delivery_yet_is_404(client, db_session):
    run = _validated_run(db_session)

    assert client.get(f"/api/pr/{run.id}").status_code == 404


def test_unvalidated_fix_is_422(client, db_session, passing_verification):
    run = _validated_run(db_session)
    run.status = RunStatus.FAILED
    run.attempts[0].status = AttemptStatus.FAILED
    run.attempts[0].failure_type = FailureType.VERIFICATION_FAILED
    db_session.commit()

    response = client.post(f"/api/pr/{run.id}")

    assert response.status_code == 422
    # The refusal says why, so an operator does not have to guess.
    assert "only a completed run can be delivered" in response.json()["detail"]


def test_missing_github_authentication_is_503(client, db_session, passing_verification, monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)
    run = _validated_run(db_session)

    response = client.post(f"/api/pr/{run.id}")

    assert response.status_code == 503
    assert "GITHUB_ENABLED" in response.json()["detail"]


def test_permission_failure_is_403(client, db_session, passing_verification, github_ready, monkeypatch):
    monkeypatch.setattr(
        github, "push_branch",
        lambda *a, **k: (_ for _ in ()).throw(github.GitHubPermissionError("HTTP 403: not authorized")),
    )
    run = _validated_run(db_session)

    assert client.post(f"/api/pr/{run.id}").status_code == 403


def test_branch_conflict_is_409(client, db_session, passing_verification, github_ready, monkeypatch):
    monkeypatch.setattr(
        github, "push_branch",
        lambda *a, **k: (_ for _ in ()).throw(github.BranchConflictError("! [rejected] non-fast-forward")),
    )
    run = _validated_run(db_session)

    assert client.post(f"/api/pr/{run.id}").status_code == 409


def test_pr_creation_failure_is_502(client, db_session, passing_verification, github_ready, monkeypatch):
    monkeypatch.setattr(
        github, "create_draft_pr",
        lambda **kwargs: (_ for _ in ()).throw(github.PullRequestCreationError("create failed")),
    )
    run = _validated_run(db_session)

    assert client.post(f"/api/pr/{run.id}").status_code == 502


def test_commit_failure_is_422_and_reports_why(client, db_session, passing_verification):
    from tests.test_generation_pipeline import _UNAPPLIABLE_DIFF

    run = _validated_run(db_session, diff=_UNAPPLIABLE_DIFF)

    response = client.post(f"/api/pr/{run.id}/commit")

    assert response.status_code == 422
    assert response.json()["detail"]


def test_an_error_body_never_carries_a_token(client, db_session, passing_verification, github_ready, monkeypatch):
    """The API is the most public place a leaked credential could surface."""
    monkeypatch.setattr(
        github, "push_branch",
        lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(f"auth failed for {_FAKE_TOKEN}")),
    )
    run = _validated_run(db_session)

    response = client.post(f"/api/pr/{run.id}")

    assert _FAKE_TOKEN not in response.text
    stored = client.get(f"/api/pr/{run.id}").json()
    assert _FAKE_TOKEN not in (stored["error"] or "")
