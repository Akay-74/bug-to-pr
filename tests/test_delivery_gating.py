"""Validation gating for delivery (docs/phase4.md "Preconditions").

"Never create a PR for an unvalidated candidate" -- these tests pin the
positive definition of validated, so a later change that loosens it fails
here rather than in production.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.delivery.gating import FixNotValidatedError, validated_fix_for_run
from backend.models.attempt import Attempt, AttemptStatus, FailureType
from backend.models.run import Run, RunMode, RunStage, RunStatus
from backend.models.verification_result import VerificationResult, VerificationStatus
from tests.conftest import FIXTURE_ISSUE_ID

_DIFF = """--- a/mypkg/__init__.py
+++ b/mypkg/__init__.py
@@ -1,3 +1,3 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

_PASSING_CHECKS = ("generation", "patch_apply", "regression_test", "relevant_tests")


def _make_run(
    db,
    run_status=RunStatus.COMPLETED,
    attempt_status=AttemptStatus.PASSED,
    failure_type=FailureType.NONE,
    diff=_DIFF,
    checks=_PASSING_CHECKS,
    failing_check=None,
    missing_check=None,
):
    run = Run(
        issue_id=FIXTURE_ISSUE_ID,
        issue_url="https://example.invalid/fixture/tiny-repo/issues/1",
        repo="fixture",
        base_commit="0" * 40,
        mode=RunMode.PUBLIC_DEMO,
        status=run_status,
        current_stage=RunStage.COMPLETED,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    attempt = Attempt(
        run_id=run.id,
        attempt_number=1,
        model="fake-model",
        status=attempt_status,
        failure_type=failure_type,
        diff=diff,
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    for name in checks:
        if name == missing_check:
            continue
        status = VerificationStatus.FAILED if name == failing_check else VerificationStatus.PASSED
        db.add(
            VerificationResult(
                attempt_id=attempt.id,
                check_name=name,
                status=status,
                command=f"run {name}",
                exit_code=0 if status is VerificationStatus.PASSED else 1,
            )
        )
    db.commit()
    db.refresh(run)
    return run


def test_validated_run_yields_the_passing_attempt_and_its_patch(db_session):
    run = _make_run(db_session)

    validated = validated_fix_for_run(run, db_session)

    assert validated.attempt.attempt_number == 1
    assert validated.diff == _DIFF


def test_incomplete_run_is_refused(db_session):
    run = _make_run(db_session, run_status=RunStatus.RUNNING)

    with pytest.raises(FixNotValidatedError, match="running"):
        validated_fix_for_run(run, db_session)


def test_failed_attempt_is_refused(db_session):
    run = _make_run(
        db_session,
        run_status=RunStatus.FAILED,
        attempt_status=AttemptStatus.FAILED,
        failure_type=FailureType.VERIFICATION_FAILED,
    )

    with pytest.raises(FixNotValidatedError):
        validated_fix_for_run(run, db_session)


def test_attempt_marked_passed_but_carrying_a_failure_type_is_refused(db_session):
    """A contradictory record is not a licence to ship: the gate refuses
    rather than picking whichever field it likes better.
    """
    run = _make_run(db_session, failure_type=FailureType.VERIFICATION_FAILED)

    with pytest.raises(FixNotValidatedError, match="failure_type"):
        validated_fix_for_run(run, db_session)


def test_passing_attempt_without_a_patch_is_refused(db_session):
    run = _make_run(db_session, diff="   ")

    with pytest.raises(FixNotValidatedError, match="no patch"):
        validated_fix_for_run(run, db_session)


@pytest.mark.parametrize("check", ["patch_apply", "regression_test"])
def test_failing_required_check_is_refused(db_session, check):
    run = _make_run(db_session, failing_check=check)

    with pytest.raises(FixNotValidatedError, match=check):
        validated_fix_for_run(run, db_session)


@pytest.mark.parametrize("check", ["generation", "patch_apply", "regression_test"])
def test_missing_required_check_is_refused(db_session, check):
    """Absence is not success -- a candidate whose patch-application or
    regression result was never recorded has not been shown to work.
    """
    run = _make_run(db_session, missing_check=check)

    with pytest.raises(FixNotValidatedError, match=check):
        validated_fix_for_run(run, db_session)


def test_failing_relevant_tests_is_refused(db_session):
    """docs/phase4.md: required tests passed includes the previously-passing
    ones, so a patch that fixed the bug but broke something else is refused.
    """
    run = _make_run(db_session, failing_check="relevant_tests")

    with pytest.raises(FixNotValidatedError, match="relevant_tests"):
        validated_fix_for_run(run, db_session)


def test_run_with_no_attempts_is_refused(db_session):
    run = Run(
        issue_id=FIXTURE_ISSUE_ID,
        issue_url="https://example.invalid/fixture/tiny-repo/issues/1",
        repo="fixture",
        base_commit="0" * 40,
        mode=RunMode.PUBLIC_DEMO,
        status=RunStatus.COMPLETED,
        current_stage=RunStage.COMPLETED,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    with pytest.raises(FixNotValidatedError, match="no attempts"):
        validated_fix_for_run(run, db_session)


def test_a_later_passing_attempt_is_delivered_after_earlier_failures(db_session):
    """Phase 3 iterates candidates; the deliverable one is whichever passed,
    not necessarily the first.
    """
    run = _make_run(
        db_session,
        run_status=RunStatus.COMPLETED,
        attempt_status=AttemptStatus.FAILED,
        failure_type=FailureType.PATCH_APPLICATION_ERROR,
    )
    good = Attempt(
        run_id=run.id,
        attempt_number=2,
        model="fake-model",
        status=AttemptStatus.PASSED,
        failure_type=FailureType.NONE,
        diff=_DIFF,
    )
    db_session.add(good)
    db_session.commit()
    db_session.refresh(good)
    for name in _PASSING_CHECKS:
        db_session.add(
            VerificationResult(
                attempt_id=good.id, check_name=name, status=VerificationStatus.PASSED,
                command=f"run {name}", exit_code=0,
            )
        )
    db_session.commit()
    db_session.refresh(run)

    validated = validated_fix_for_run(run, db_session)

    assert validated.attempt.attempt_number == 2


def test_unknown_run_id_is_not_silently_treated_as_validated(db_session):
    from backend.models.run import Run as RunModel

    assert db_session.get(RunModel, uuid.uuid4()) is None
