"""Delivery pipeline: validated fix -> branch -> commit -> draft PR (docs/phase4.md).

Ordering is deliberate and load-bearing:

1. gate on Phase 3's persisted validation -- nothing else happens first;
2. clone at the exact base commit into a disposable workspace, branch;
3. apply the validated patch and commit *only* that patch;
4. re-verify the committed tree in the sandbox, with the benchmark's
   regression test applied on top but never committed;
5. push and open a draft PR, only if GitHub access was explicitly enabled.

Step 4 comes after the commit so the commit contains the fix alone, and the
verification still runs against the code that was actually committed. The
benchmark's own test files are added to the working tree for the test run and
then discarded, so a delivery can never smuggle a modified test into the PR.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.benchmark.loader import Issue, load_issue
from backend.config import settings
from backend.delivery import content, git_ops, github
from backend.delivery.gating import FixNotValidatedError, ValidatedFix, validated_fix_for_run
from backend.generation import sandbox_ops
from backend.generation.patch import apply_patch, validate_patch_paths, validate_patch_size
from backend.localization.indexer import checked_out_workspace
from backend.models.pull_request import DeliveryFailureType, DeliveryStatus, PullRequest
from backend.models.run import Run, RunStage
from backend.tools.protected_tests import check_protected_tests

__all__ = [
    "DeliveryError",
    "VerificationFailedError",
    "deliver_fix",
    "get_delivery",
    "refresh_pr_status",
]


class DeliveryError(Exception):
    """The delivery could not be completed."""


class VerificationFailedError(DeliveryError):
    """The committed patch did not pass final verification."""


@dataclass
class _Verification:
    passed: bool
    command: str
    detail: str


def verify_committed_tree(issue: Issue, workspace: Path) -> _Verification:
    """Re-run the benchmark's regression test against the committed tree.

    docs/phase4.md requires a final verification before the PR exists, and it
    must not be a cheaper check than the one that validated the candidate:
    same sandbox, same commands, network disabled for the test run
    (backend/generation/sandbox_ops.py).
    """
    metadata = issue.metadata
    command = metadata.regression_test_command
    if not command:
        return _Verification(passed=False, command="", detail="Benchmark defines no regression test command")

    test_patch_error = sandbox_ops.apply_test_patch(issue.directory, workspace)
    if test_patch_error:
        return _Verification(passed=False, command=command, detail=f"Could not apply regression test: {test_patch_error}")

    sandbox_ops.make_world_writable(workspace)
    install = sandbox_ops.install_dependencies(metadata, workspace)
    if install.exit_code != 0:
        return _Verification(
            passed=False, command=metadata.install_command, detail=(install.stderr or install.stdout)[-2000:]
        )

    result = sandbox_ops.run_tests(metadata, workspace, command)
    output = result.stdout + result.stderr
    if result.timed_out or not sandbox_ops.harness_ran(output):
        return _Verification(passed=False, command=command, detail=output[-2000:] or "test harness did not report results")

    if metadata.regression_test_ids:
        still_broken = sandbox_ops.still_failing(result, metadata.regression_test_ids)
        passed = not still_broken
        detail = "" if passed else f"still failing: {sorted(still_broken)}"
    else:
        passed = result.exit_code == 0
        detail = "" if passed else output[-2000:]
    return _Verification(passed=passed, command=command, detail=detail)


def _fail(db: Session, delivery: PullRequest, failure: DeliveryFailureType, error: str) -> None:
    delivery.status = DeliveryStatus.FAILED
    delivery.failure_type = failure
    # Redacted unconditionally: this string is returned by the API and stored
    # in the database, and some of these errors come from git/gh stderr.
    delivery.error = github.redact(error)[-4000:]
    db.commit()


def deliver_fix(run: Run, db: Session, create_pr: bool = True) -> PullRequest:
    """Turn `run`'s validated fix into a commit, and (if GitHub access is
    configured) a pushed branch and draft PR.

    Raises FixNotValidatedError before creating any record if the run has no
    validated candidate. Every later failure is persisted on the PullRequest
    row and re-raised, so a partial delivery is inspectable rather than lost.
    """
    validated: ValidatedFix = validated_fix_for_run(run, db)  # FixNotValidatedError propagates
    issue = load_issue(run.issue_id)
    metadata = issue.metadata

    branch = git_ops.branch_name_for(run.issue_id, run.id)
    delivery = PullRequest(
        run_id=run.id,
        attempt_id=validated.attempt.id,
        repo=metadata.repo,
        base_commit=metadata.base_commit,
        branch_name=branch,
        diff=validated.diff,
        status=DeliveryStatus.PENDING,
        is_draft=True,
    )
    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    run.current_stage = RunStage.PR_CREATION
    db.commit()

    # The patch was already validated in Phase 3, but it is re-checked here
    # rather than trusted: this is the step that writes a commit, and the
    # database row it came from is not the same trust boundary as the
    # generator's output was.
    try:
        validate_patch_size(validated.diff, settings.GENERATION_MAX_PATCH_LINES)
        validate_patch_paths(validated.diff, metadata.protected_test_paths)
    except Exception as exc:
        _fail(db, delivery, DeliveryFailureType.PATCH_APPLICATION_ERROR, str(exc))
        raise DeliveryError(str(exc)) from None

    with checked_out_workspace(metadata.repo, metadata.base_commit) as workspace:
        try:
            git_ops.create_branch(workspace, branch)
        except git_ops.GitError as exc:
            _fail(db, delivery, DeliveryFailureType.BRANCH_CONFLICT, str(exc))
            raise DeliveryError(str(exc)) from None

        applied = apply_patch(workspace, validated.diff)
        if not applied.success:
            _fail(db, delivery, DeliveryFailureType.PATCH_APPLICATION_ERROR, applied.error)
            raise DeliveryError(applied.error)

        protection = check_protected_tests(metadata.base_commit, workspace, metadata.protected_test_paths)
        if protection.modified:
            error = f"Patch modifies protected test files: {protection.changed_files}"
            _fail(db, delivery, DeliveryFailureType.PATCH_APPLICATION_ERROR, error)
            raise DeliveryError(error)

        message = content.commit_message(issue, validated.diff)
        try:
            commit = git_ops.commit_all(workspace, message)
        except git_ops.GitError as exc:
            _fail(db, delivery, DeliveryFailureType.GIT_ERROR, str(exc))
            raise DeliveryError(str(exc)) from None

        delivery.commit_sha = commit.sha
        delivery.commit_message = message
        delivery.status = DeliveryStatus.COMMITTED
        delivery.committed_at = datetime.now(timezone.utc)
        db.commit()

        verification = verify_committed_tree(issue, workspace)
        delivery.verification_command = verification.command
        delivery.verification_passed = verification.passed
        db.commit()
        if not verification.passed:
            _fail(db, delivery, DeliveryFailureType.VERIFICATION_FAILED, verification.detail)
            raise VerificationFailedError(verification.detail or "final verification failed")

        # Restore the tree to the commit before anything is pushed. `reset
        # --hard` alone would leave the benchmark's added test files behind
        # as untracked, so untracked files are cleaned too.
        #
        # Best-effort by design: the sandbox runs as a different user and
        # leaves root-owned build artifacts (.venv, __pycache__, *.egg-info)
        # the host cannot delete, and failing the delivery over those would
        # break every real run. What actually keeps them out of the PR is
        # that only commits are pushed, and the sole commit was made before
        # any of this existed.
        git_ops.run_git(workspace, ["reset", "--hard", "--quiet", commit.sha], check=False)
        git_ops.run_git(workspace, ["clean", "-fdq"], check=False)

        if not create_pr:
            return delivery

        try:
            github.check_auth()
            target_repo = settings.GITHUB_TARGET_REPO
            github.push_branch(workspace, branch, target_repo)
            delivery.pushed = True
            delivery.target_repo = target_repo
            delivery.base_branch = settings.GITHUB_PR_BASE_BRANCH
            db.commit()

            pull_request = github.create_draft_pr(
                target_repo=target_repo,
                head=branch,
                base=settings.GITHUB_PR_BASE_BRANCH,
                title=content.pr_title(issue),
                body=content.pr_body(issue, validated.attempt, validated.diff, commit.sha, branch),
            )
        except github.GitHubAuthError as exc:
            _fail(db, delivery, DeliveryFailureType.GITHUB_AUTH_ERROR, str(exc))
            raise
        except github.GitHubPermissionError as exc:
            _fail(db, delivery, DeliveryFailureType.GITHUB_PERMISSION_ERROR, str(exc))
            raise
        except github.BranchConflictError as exc:
            _fail(db, delivery, DeliveryFailureType.BRANCH_CONFLICT, str(exc))
            raise
        except github.GitHubError as exc:
            _fail(db, delivery, DeliveryFailureType.PR_CREATION_ERROR, str(exc))
            raise

    delivery.pr_url = pull_request.url
    delivery.pr_number = pull_request.number
    delivery.pr_state = pull_request.state
    delivery.is_draft = pull_request.is_draft
    delivery.status = DeliveryStatus.PR_CREATED
    delivery.failure_type = DeliveryFailureType.NONE
    delivery.pr_created_at = datetime.now(timezone.utc)

    run.pr_url = pull_request.url
    run.current_stage = RunStage.COMPLETED
    db.commit()
    db.refresh(delivery)
    return delivery


def get_delivery(run_id, db: Session) -> PullRequest | None:
    """The most recent delivery recorded for a run."""
    return (
        db.query(PullRequest)
        .filter(PullRequest.run_id == run_id)
        .order_by(PullRequest.created_at.desc())
        .first()
    )


def refresh_pr_status(delivery: PullRequest, db: Session) -> PullRequest:
    """Re-read the PR's live state from GitHub, if one was opened."""
    if not delivery.pr_url or not delivery.target_repo:
        return delivery

    status = github.get_pr_status(delivery.target_repo, delivery.pr_url)
    delivery.pr_state = status.state
    delivery.is_draft = status.is_draft
    db.commit()
    db.refresh(delivery)
    return delivery
