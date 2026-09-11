"""The complete workflow, phase to phase (docs/phase5.md).

Sandbox validation is real (the Phase 3 pipeline underneath runs containers);
GitHub is mocked throughout.
"""
from __future__ import annotations

import json

import pytest

from backend.benchmark.schema import ValidationStatus
from backend.delivery import github
from backend.delivery import pipeline as delivery_pipeline
from backend.localization.service import RepositoryNotAllowedError
from backend.models.pull_request import DeliveryStatus
from backend.models.run import RunStage, RunStatus
from backend.pipeline.queue import enqueue_run
from backend.workflow.pipeline import (
    BenchmarkNotValidError,
    latest_run_for_issue,
    run_workflow,
)
from backend.benchmark.loader import IssueNotFoundError, load_issue, save_metadata
from tests.conftest import FIXTURE_ISSUE_ID
from tests.test_generation_pipeline import (
    FakeEmbeddingBackend,
    FakeGenerationBackend,
    _GOOD_DIFF,
    _INEFFECTIVE_DIFF,
    _UNAPPLIABLE_DIFF,
)


@pytest.fixture()
def valid_benchmark():
    """The fixture issue starts as `pending`; the workflow requires `valid`.

    Restored afterwards so the rest of the suite still sees the fixture in
    its original state.
    """
    issue = load_issue(FIXTURE_ISSUE_ID)
    original = issue.metadata.validation_status
    issue.metadata.validation_status = ValidationStatus.VALID
    save_metadata(FIXTURE_ISSUE_ID, issue.metadata)
    yield
    issue.metadata.validation_status = original
    save_metadata(FIXTURE_ISSUE_ID, issue.metadata)


@pytest.fixture()
def fake_github(monkeypatch):
    calls: dict[str, list] = {"push": [], "pr": []}
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(github, "push_branch", lambda w, b, t: calls["push"].append((b, t)))
    monkeypatch.setattr(
        github,
        "create_draft_pr",
        lambda **kwargs: calls["pr"].append(kwargs)
        or github.DraftPullRequest(
            url="https://github.com/someone/safe-test-repo/pull/9", number=9, is_draft=True, state="OPEN"
        ),
    )
    return calls


def _workflow(db, diffs, **kwargs):
    return run_workflow(
        FIXTURE_ISSUE_ID,
        db,
        generation_backend=FakeGenerationBackend(diffs),
        embedding_backend=FakeEmbeddingBackend(),
        candidate_limit=kwargs.pop("candidate_limit", 1),
        **kwargs,
    )


# --- the happy path --------------------------------------------------------


def test_complete_workflow_reaches_a_draft_pr(db_session, valid_benchmark, fake_github):
    """docs/phase5.md Definition of Done: issue -> validation -> localization
    -> generation -> sandbox validation -> commit -> draft PR, on one run.
    """
    result = _workflow(db_session, [_GOOD_DIFF])

    run = result.run
    assert run.status is RunStatus.COMPLETED
    assert run.current_stage is RunStage.COMPLETED
    assert run.error is None
    assert run.attempts_taken == 1
    assert run.pr_url == "https://github.com/someone/safe-test-repo/pull/9"

    assert result.delivery.status is DeliveryStatus.PR_CREATED
    assert result.delivery.commit_sha
    assert result.delivery_error is None

    # One Run carries every phase: localization, the attempt with its checks,
    # and the delivery all hang off it.
    attempt = run.attempts[0]
    assert {c.check_name for c in attempt.verification_results} >= {
        "generation", "patch_apply", "regression_test"
    }


def test_workflow_commits_without_github_and_says_why_no_pr_exists(db_session, valid_benchmark, monkeypatch):
    """A validated fix with GitHub unconfigured is still a success: the run
    completes, the commit exists, and the reason there is no PR is recorded.
    """
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)

    result = _workflow(db_session, [_GOOD_DIFF])

    assert result.run.status is RunStatus.COMPLETED
    assert result.delivery.status is DeliveryStatus.COMMITTED
    assert result.delivery.commit_sha
    assert result.run.pr_url is None


# --- failure paths ---------------------------------------------------------


def test_unvalidated_benchmark_issue_is_refused_before_any_work(db_session):
    """The fixture issue is `pending`: the workflow starts from a validated
    benchmark (docs/phase5.md), so nothing runs and no Run is created.
    """
    from backend.models.run import Run

    with pytest.raises(BenchmarkNotValidError, match="pending"):
        _workflow(db_session, [_GOOD_DIFF])

    assert db_session.query(Run).count() == 0


def test_unvalidated_benchmark_can_be_overridden_explicitly(db_session, monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)

    result = _workflow(db_session, [_GOOD_DIFF], allow_unvalidated_benchmark=True)

    assert result.run.status is RunStatus.COMPLETED


def test_unknown_issue_raises(db_session):
    with pytest.raises(IssueNotFoundError):
        run_workflow("does-not-exist", db_session, embedding_backend=FakeEmbeddingBackend())


def test_failing_candidates_leave_a_failed_run_that_says_why(db_session, valid_benchmark, fake_github):
    result = _workflow(db_session, [_UNAPPLIABLE_DIFF], candidate_limit=2)

    run = result.run
    assert run.status is RunStatus.FAILED
    assert run.current_stage is RunStage.COMPLETED
    assert "No candidate passed validation after 2 attempt(s)" in run.error
    # The per-attempt detail stays on the attempts; the run summarises the
    # last one. (The second candidate is the same patch again, which Phase 3
    # rejects as a duplicate rather than re-validating.)
    assert run.attempts[0].failure_type.value == "patch_application_error"
    assert "last failure: generation_error" in run.error
    # Nothing was delivered, and nothing reached GitHub.
    assert result.delivery is None
    assert fake_github["pr"] == []


def test_a_patch_that_does_not_fix_the_bug_fails_the_run(db_session, valid_benchmark, fake_github):
    result = _workflow(db_session, [_INEFFECTIVE_DIFF])

    assert result.run.status is RunStatus.FAILED
    assert "verification_failed" in result.run.error
    assert fake_github["push"] == []


def test_localization_failure_is_recorded_on_the_run(db_session, valid_benchmark, monkeypatch):
    import backend.workflow.pipeline as workflow_mod

    def boom(issue_id, db, backend):
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(workflow_mod, "localize_issue", boom)

    result = _workflow(db_session, [_GOOD_DIFF])

    assert result.run.status is RunStatus.FAILED
    assert result.run.current_stage is RunStage.LOCALIZATION
    assert "embedding backend unavailable" in result.run.error


def test_delivery_failure_does_not_discard_a_validated_fix(db_session, valid_benchmark, monkeypatch):
    """A GitHub outage is not a reason to call a verified fix a failure."""
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(
        github, "push_branch",
        lambda *a, **k: (_ for _ in ()).throw(github.GitHubError("github is down")),
    )

    result = _workflow(db_session, [_GOOD_DIFF])

    assert result.run.status is RunStatus.COMPLETED  # the fix is still valid
    assert "github is down" in result.delivery_error
    assert "github is down" in result.run.error
    assert result.run.pr_url is None


def test_a_token_in_a_delivery_error_is_redacted_on_the_run(db_session, valid_benchmark, monkeypatch):
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(
        github, "push_branch",
        lambda *a, **k: (_ for _ in ()).throw(github.GitHubError(f"failed with {token}")),
    )

    result = _workflow(db_session, [_GOOD_DIFF])

    assert token not in result.run.error
    assert "[REDACTED]" in result.run.error


# --- state, reuse and the queue -------------------------------------------


def test_workflow_reuses_an_existing_localization_instead_of_recomputing(
    db_session, valid_benchmark, monkeypatch
):
    """docs/phase5.md "Performance": localization reuse. The second run of the
    same issue must not embed the repository again.
    """
    import backend.workflow.pipeline as workflow_mod

    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)
    calls = []
    real_localize = workflow_mod.localize_issue

    def counting_localize(issue_id, db, backend):
        calls.append(issue_id)
        return real_localize(issue_id, db, backend=backend)

    monkeypatch.setattr(workflow_mod, "localize_issue", counting_localize)

    _workflow(db_session, [_GOOD_DIFF])
    _workflow(db_session, [_GOOD_DIFF])

    assert len(calls) == 1


def test_workflow_adopts_a_queued_run_rather_than_creating_another(
    db_session, valid_benchmark, monkeypatch
):
    """The worker claims a queued Run; the workflow must carry that same row
    through every stage so the queue and the dashboard agree.
    """
    from backend.models.run import Run

    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)
    issue = load_issue(FIXTURE_ISSUE_ID)
    queued = enqueue_run(
        db_session,
        issue_id=FIXTURE_ISSUE_ID,
        issue_url=issue.metadata.issue_url,
        repo=issue.metadata.repo,
        base_commit=issue.metadata.base_commit,
    )

    result = _workflow(db_session, [_GOOD_DIFF], run=queued)

    assert result.run.id == queued.id
    assert db_session.query(Run).count() == 1
    assert result.run.status is RunStatus.COMPLETED


def test_latest_run_for_issue_returns_the_most_recent(db_session, valid_benchmark, monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)

    _workflow(db_session, [_GOOD_DIFF])
    second = _workflow(db_session, [_GOOD_DIFF])

    assert latest_run_for_issue(FIXTURE_ISSUE_ID, db_session).id == second.run.id


def test_candidate_generation_stays_bounded(db_session, valid_benchmark, fake_github):
    """docs/phase5.md "Reliability": bounded retries, no infinite loop."""
    result = _workflow(db_session, [_UNAPPLIABLE_DIFF], candidate_limit=2)

    assert result.run.attempts_taken == 2
    assert len(result.run.attempts) == 2
