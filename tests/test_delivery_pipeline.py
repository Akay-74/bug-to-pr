"""End-to-end delivery: validated fix -> branch -> commit -> draft PR
(docs/phase4.md).

GitHub is mocked throughout. The sandbox verification is stubbed in most
tests for speed; test_real_sandbox_verification_gates_the_delivery runs the
unstubbed path against the fixture repository so the stub cannot hide a
broken verification step.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.delivery import github
from backend.delivery import pipeline as delivery_pipeline
from backend.delivery.gating import FixNotValidatedError
from backend.delivery.pipeline import DeliveryError, VerificationFailedError, deliver_fix, get_delivery
from backend.models.attempt import Attempt, AttemptStatus, FailureType
from backend.models.pull_request import DeliveryFailureType, DeliveryStatus, PullRequest
from backend.models.run import Run, RunMode, RunStage, RunStatus
from backend.models.verification_result import VerificationResult, VerificationStatus
from tests.conftest import FIXTURE_ISSUE_ID, FIXTURE_REPO_PATH
from tests.test_generation_pipeline import _GOOD_DIFF, _INEFFECTIVE_DIFF, _UNAPPLIABLE_DIFF

_FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

# A patch that edits the benchmark's protected test file instead of the source.
_PROTECTED_TEST_DIFF = """--- a/tests/test_add.py
+++ b/tests/test_add.py
@@ -1,3 +1,3 @@
 from mypkg import add
 def test_add():
-    assert add(1, 2) == 3
+    assert add(1, 2) == -1
"""


def _validated_run(db, diff=_GOOD_DIFF, issue_id=FIXTURE_ISSUE_ID):
    """A Run shaped exactly as Phase 3 leaves a successful one."""
    from backend.benchmark.loader import load_issue

    metadata = load_issue(issue_id).metadata
    run = Run(
        issue_id=issue_id,
        issue_url=metadata.issue_url,
        repo=metadata.repo,
        base_commit=metadata.base_commit,
        mode=RunMode.PUBLIC_DEMO,
        status=RunStatus.COMPLETED,
        current_stage=RunStage.COMPLETED,
        attempts_taken=1,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    attempt = Attempt(
        run_id=run.id,
        attempt_number=1,
        model="fake-generation-backend",
        status=AttemptStatus.PASSED,
        failure_type=FailureType.NONE,
        diff=diff,
        hypothesis="Fix targeting mypkg/__init__.py:add",
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    for name, command in (
        ("generation", "generate (fake-generation-backend)"),
        ("patch_apply", "git apply"),
        ("regression_test", metadata.regression_test_command),
        ("relevant_tests", metadata.relevant_test_command),
    ):
        db.add(
            VerificationResult(
                attempt_id=attempt.id,
                check_name=name,
                status=VerificationStatus.PASSED,
                command=command,
                exit_code=0,
                duration_ms=1200,
            )
        )
    db.commit()
    db.refresh(run)
    return run


@pytest.fixture()
def passing_verification(monkeypatch):
    """Stub the sandbox verification with a passing result."""
    monkeypatch.setattr(
        delivery_pipeline,
        "verify_committed_tree",
        lambda issue, workspace: delivery_pipeline._Verification(
            passed=True, command="pytest -rA tests/test_add.py::test_add", detail=""
        ),
    )


@pytest.fixture()
def github_configured(monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")
    monkeypatch.setattr(github.settings, "GITHUB_PR_BASE_BRANCH", "main")


@pytest.fixture()
def fake_github(monkeypatch):
    """Record GitHub calls instead of performing them."""
    calls: dict[str, list] = {"auth": [], "push": [], "pr": []}

    monkeypatch.setattr(github, "check_auth", lambda: calls["auth"].append(True) or "octocat")
    monkeypatch.setattr(
        github, "push_branch", lambda workspace, branch, target: calls["push"].append((branch, target))
    )
    monkeypatch.setattr(
        github,
        "create_draft_pr",
        lambda target_repo, head, base, title, body: calls["pr"].append(
            {"repo": target_repo, "head": head, "base": base, "title": title, "body": body}
        )
        or github.DraftPullRequest(
            url="https://github.com/someone/safe-test-repo/pull/7", number=7, is_draft=True, state="OPEN"
        ),
    )
    return calls


# --- commit-only delivery --------------------------------------------------


def test_commit_only_delivery_records_branch_and_commit(db_session, passing_verification):
    run = _validated_run(db_session)

    delivery = deliver_fix(run, db_session, create_pr=False)

    assert delivery.status is DeliveryStatus.COMMITTED
    assert delivery.commit_sha
    assert delivery.branch_name.startswith("bug2pr/")
    assert delivery.pushed is False
    assert delivery.pr_url is None
    assert delivery.verification_passed is True
    assert "Benchmark-Issue: " + FIXTURE_ISSUE_ID in delivery.commit_message


def test_commit_only_delivery_needs_no_github_access(db_session, passing_verification, monkeypatch):
    """GITHUB_ENABLED stays false: branching and committing must still work
    (docs/phase4.md "GitHub" -- push only on explicit configuration).
    """
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)

    def explode(*args, **kwargs):
        raise AssertionError("GitHub must not be contacted for a commit-only delivery")

    monkeypatch.setattr(github, "check_auth", explode)
    monkeypatch.setattr(github, "push_branch", explode)

    delivery = deliver_fix(_validated_run(db_session), db_session, create_pr=False)

    assert delivery.status is DeliveryStatus.COMMITTED


def test_delivery_never_touches_the_source_checkout(db_session, passing_verification):
    """docs/phase4.md: do not modify the user's main working branch. The
    on-disk repository must be byte-identical afterwards, on the same branch.
    """

    def state():
        return (
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=FIXTURE_REPO_PATH, capture_output=True, text=True
            ).stdout,
            subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=FIXTURE_REPO_PATH, capture_output=True, text=True
            ).stdout,
            subprocess.run(
                ["git", "branch", "--list"], cwd=FIXTURE_REPO_PATH, capture_output=True, text=True
            ).stdout,
            (FIXTURE_REPO_PATH / "mypkg" / "__init__.py").read_bytes(),
        )

    before = state()
    deliver_fix(_validated_run(db_session), db_session, create_pr=False)

    assert state() == before


def test_commit_contains_only_the_fix_not_the_benchmark_test(db_session, monkeypatch):
    """The regression test is applied for verification but must never be
    committed -- otherwise a PR could carry a modified test.

    Inspected inside the verification hook and again at push time, since the
    workspace is deleted as soon as the delivery finishes.
    """
    captured: dict[str, object] = {}

    def fake_verify(issue, workspace):
        # Mimic what the real verification leaves in the working tree: the
        # benchmark's regression test, plus whatever the test run drops.
        from backend.generation import sandbox_ops

        sandbox_ops.apply_test_patch(issue.directory, workspace)
        (workspace / "tests" / "test_regression_added_by_benchmark.py").write_text("def test_x():\n    pass\n")
        captured["committed"] = subprocess.run(
            ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
            cwd=workspace, capture_output=True, text=True,
        ).stdout.split()
        captured["dirty_during_verification"] = subprocess.run(
            ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True
        ).stdout.strip()
        return delivery_pipeline._Verification(passed=True, command="pytest", detail="")

    def record_push(workspace, branch, target):
        # The state the branch would be pushed in.
        captured["dirty_at_push"] = subprocess.run(
            ["git", "status", "--porcelain"], cwd=workspace, capture_output=True, text=True
        ).stdout.strip()

    monkeypatch.setattr(delivery_pipeline, "verify_committed_tree", fake_verify)
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(github, "push_branch", record_push)
    monkeypatch.setattr(
        github,
        "create_draft_pr",
        lambda **kwargs: github.DraftPullRequest(
            url="https://github.com/o/r/pull/1", number=1, is_draft=True, state="OPEN"
        ),
    )

    deliver_fix(_validated_run(db_session), db_session, create_pr=True)

    assert captured["committed"] == ["mypkg/__init__.py"]
    # The benchmark's test file was present in the working tree for the test
    # run...
    assert captured["dirty_during_verification"] != ""
    # ...and gone again before anything could be pushed.
    assert captured["dirty_at_push"] == ""


# --- gating ----------------------------------------------------------------


def test_unvalidated_run_is_refused_before_any_delivery_row_exists(db_session):
    run = _validated_run(db_session)
    run.status = RunStatus.FAILED
    db_session.commit()

    with pytest.raises(FixNotValidatedError):
        deliver_fix(run, db_session, create_pr=True)

    assert db_session.query(PullRequest).count() == 0


def test_failed_attempt_is_refused_even_with_github_configured(
    db_session, github_configured, fake_github, passing_verification
):
    run = _validated_run(db_session)
    run.attempts[0].status = AttemptStatus.FAILED
    run.attempts[0].failure_type = FailureType.VERIFICATION_FAILED
    run.status = RunStatus.FAILED
    db_session.commit()

    with pytest.raises(FixNotValidatedError):
        deliver_fix(run, db_session, create_pr=True)

    assert fake_github["pr"] == []
    assert fake_github["push"] == []


def test_patch_touching_a_protected_test_path_is_refused(db_session, passing_verification):
    run = _validated_run(db_session, diff=_PROTECTED_TEST_DIFF)

    with pytest.raises(DeliveryError):
        deliver_fix(run, db_session, create_pr=False)

    delivery = get_delivery(run.id, db_session)
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.failure_type is DeliveryFailureType.PATCH_APPLICATION_ERROR


def test_unappliable_patch_is_recorded_as_a_patch_application_failure(db_session, passing_verification):
    run = _validated_run(db_session, diff=_UNAPPLIABLE_DIFF)

    with pytest.raises(DeliveryError):
        deliver_fix(run, db_session, create_pr=False)

    delivery = get_delivery(run.id, db_session)
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.failure_type is DeliveryFailureType.PATCH_APPLICATION_ERROR
    assert delivery.commit_sha is None


def test_failed_final_verification_blocks_the_pull_request(
    db_session, github_configured, fake_github, monkeypatch
):
    """docs/phase4.md: final verification runs before the PR exists, so a
    patch that stops working must not reach GitHub.
    """
    monkeypatch.setattr(
        delivery_pipeline,
        "verify_committed_tree",
        lambda issue, workspace: delivery_pipeline._Verification(
            passed=False, command="pytest", detail="still failing: ['tests/test_add.py::test_add']"
        ),
    )
    run = _validated_run(db_session)

    with pytest.raises(VerificationFailedError):
        deliver_fix(run, db_session, create_pr=True)

    delivery = get_delivery(run.id, db_session)
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.failure_type is DeliveryFailureType.VERIFICATION_FAILED
    assert delivery.verification_passed is False
    assert fake_github["push"] == []
    assert fake_github["pr"] == []


# --- draft PR --------------------------------------------------------------


def test_successful_draft_pull_request(db_session, passing_verification, github_configured, fake_github):
    run = _validated_run(db_session)

    delivery = deliver_fix(run, db_session, create_pr=True)

    assert delivery.status is DeliveryStatus.PR_CREATED
    assert delivery.pr_url == "https://github.com/someone/safe-test-repo/pull/7"
    assert delivery.pr_number == 7
    assert delivery.is_draft is True
    assert delivery.pushed is True
    assert delivery.failure_type is DeliveryFailureType.NONE
    assert run.pr_url == delivery.pr_url

    created = fake_github["pr"][0]
    assert created["head"] == delivery.branch_name
    assert created["base"] == "main"
    assert FIXTURE_ISSUE_ID in created["body"]
    assert delivery.commit_sha in created["body"]
    assert "Tests run" in created["body"]


def test_missing_github_authentication_is_recorded_and_stops_before_pushing(
    db_session, passing_verification, monkeypatch
):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)
    pushed = []
    monkeypatch.setattr(github, "push_branch", lambda *a, **k: pushed.append(a))
    run = _validated_run(db_session)

    with pytest.raises(github.GitHubAuthError):
        deliver_fix(run, db_session, create_pr=True)

    delivery = get_delivery(run.id, db_session)
    assert delivery.failure_type is DeliveryFailureType.GITHUB_AUTH_ERROR
    assert delivery.pushed is False
    assert pushed == []
    # The commit still happened and is still recorded -- the delivery failed
    # at the GitHub step, not before it.
    assert delivery.commit_sha


def test_push_conflict_is_recorded_as_a_branch_conflict(
    db_session, passing_verification, github_configured, monkeypatch
):
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")

    def conflicting_push(workspace, branch, target):
        raise github.BranchConflictError("! [rejected] (non-fast-forward)")

    monkeypatch.setattr(github, "push_branch", conflicting_push)
    run = _validated_run(db_session)

    with pytest.raises(github.BranchConflictError):
        deliver_fix(run, db_session, create_pr=True)

    delivery = get_delivery(run.id, db_session)
    assert delivery.failure_type is DeliveryFailureType.BRANCH_CONFLICT


def test_pr_creation_failure_is_recorded(db_session, passing_verification, github_configured, monkeypatch):
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(github, "push_branch", lambda *a, **k: None)

    def failing_create(**kwargs):
        raise github.PullRequestCreationError("pull request create failed")

    monkeypatch.setattr(github, "create_draft_pr", failing_create)
    run = _validated_run(db_session)

    with pytest.raises(github.PullRequestCreationError):
        deliver_fix(run, db_session, create_pr=True)

    delivery = get_delivery(run.id, db_session)
    assert delivery.status is DeliveryStatus.FAILED
    assert delivery.failure_type is DeliveryFailureType.PR_CREATION_ERROR
    assert delivery.pr_url is None


def test_permission_failure_is_recorded_distinctly(
    db_session, passing_verification, github_configured, monkeypatch
):
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(github, "push_branch", lambda *a, **k: None)
    monkeypatch.setattr(
        github,
        "create_draft_pr",
        lambda **kwargs: (_ for _ in ()).throw(github.GitHubPermissionError("HTTP 403")),
    )
    run = _validated_run(db_session)

    with pytest.raises(github.GitHubPermissionError):
        deliver_fix(run, db_session, create_pr=True)

    assert get_delivery(run.id, db_session).failure_type is DeliveryFailureType.GITHUB_PERMISSION_ERROR


def test_a_token_in_a_github_error_is_redacted_before_it_is_persisted(
    db_session, passing_verification, github_configured, monkeypatch
):
    """docs/phase4.md "Security": never log secrets. The delivery row is read
    back by the API, so it is a place a leaked token would be durable.
    """
    monkeypatch.setattr(github, "check_auth", lambda: "octocat")
    monkeypatch.setattr(
        github,
        "push_branch",
        lambda *a, **k: (_ for _ in ()).throw(
            github.GitHubError(f"fatal: authentication failed for {_FAKE_TOKEN}")
        ),
    )
    run = _validated_run(db_session)

    with pytest.raises(github.GitHubError):
        deliver_fix(run, db_session, create_pr=True)

    delivery = get_delivery(run.id, db_session)
    assert _FAKE_TOKEN not in (delivery.error or "")
    assert "[REDACTED]" in delivery.error


def test_pr_status_is_refreshed_from_github(db_session, passing_verification, github_configured, fake_github, monkeypatch):
    run = _validated_run(db_session)
    delivery = deliver_fix(run, db_session, create_pr=True)

    monkeypatch.setattr(
        github,
        "get_pr_status",
        lambda repo, ref: github.PullRequestStatus(url=delivery.pr_url, number=7, state="MERGED", is_draft=False),
    )
    refreshed = delivery_pipeline.refresh_pr_status(delivery, db_session)

    assert refreshed.pr_state == "MERGED"
    assert refreshed.is_draft is False


def test_delivery_is_never_marked_merged_by_the_agent(db_session, passing_verification, github_configured, fake_github):
    """docs/phase4.md: do not merge automatically. The pipeline only ever
    opens a draft.
    """
    delivery = deliver_fix(_validated_run(db_session), db_session, create_pr=True)

    assert delivery.is_draft is True
    assert delivery.pr_state == "OPEN"


# --- unstubbed verification ------------------------------------------------


def test_real_sandbox_verification_gates_the_delivery(db_session, github_configured, fake_github, monkeypatch):
    """The unstubbed path, in the real Docker sandbox: a genuinely correct
    patch is committed and reaches a draft PR, and a patch that does not fix
    the bug is stopped at verification.
    """
    pushed_commits: list[list[str]] = []

    def record_push(workspace, branch, target):
        fake_github["push"].append((branch, target))
        pushed_commits.append(
            subprocess.run(
                ["git", "show", "--name-only", "--pretty=format:", branch],
                cwd=workspace, capture_output=True, text=True,
            ).stdout.split()
        )

    monkeypatch.setattr(github, "push_branch", record_push)

    good = deliver_fix(_validated_run(db_session, diff=_GOOD_DIFF), db_session, create_pr=True)

    assert good.status is DeliveryStatus.PR_CREATED
    assert good.verification_passed is True
    # Whatever the sandbox left in the workspace, the branch that was pushed
    # carries the fix and nothing else.
    assert pushed_commits == [["mypkg/__init__.py"]]

    bad_run = _validated_run(db_session, diff=_INEFFECTIVE_DIFF)
    with pytest.raises(VerificationFailedError):
        deliver_fix(bad_run, db_session, create_pr=True)

    bad = get_delivery(bad_run.id, db_session)
    assert bad.verification_passed is False
    assert bad.status is DeliveryStatus.FAILED
    assert len(fake_github["pr"]) == 1  # only the good one ever reached GitHub
