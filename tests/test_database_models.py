import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.attempt import Attempt, AttemptStatus
from backend.models.run import Run, RunMode, RunStage, RunStatus
from backend.models.verification_result import VerificationResult, VerificationStatus


def test_create_run(db_session):
    run = Run(
        issue_id="fixture__tiny-repo-1",
        issue_url="https://example.invalid/1",
        repo="fixture/tiny-repo",
        base_commit="deadbeef",
        mode=RunMode.PUBLIC_DEMO,
    )
    db_session.add(run)
    db_session.commit()

    fetched = db_session.get(Run, run.id)
    assert fetched is not None
    assert fetched.status == RunStatus.QUEUED
    assert fetched.current_stage == RunStage.INTAKE
    assert fetched.attempts_taken == 0


def test_create_attempt_with_foreign_key(db_session):
    run = Run(issue_id="i1", issue_url="u1", repo="r1", base_commit="c1")
    db_session.add(run)
    db_session.commit()

    attempt = Attempt(run_id=run.id, attempt_number=1, status=AttemptStatus.PENDING)
    db_session.add(attempt)
    db_session.commit()

    fetched = db_session.get(Attempt, attempt.id)
    assert fetched.run_id == run.id
    assert fetched.run.issue_id == "i1"


def test_attempt_requires_valid_run_id(db_session):
    import uuid

    attempt = Attempt(run_id=uuid.uuid4(), attempt_number=1)
    db_session.add(attempt)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_create_verification_result_with_foreign_key(db_session):
    run = Run(issue_id="i2", issue_url="u2", repo="r2", base_commit="c2")
    db_session.add(run)
    db_session.commit()

    attempt = Attempt(run_id=run.id, attempt_number=1)
    db_session.add(attempt)
    db_session.commit()

    verification = VerificationResult(
        attempt_id=attempt.id,
        check_name="regression_test",
        status=VerificationStatus.PASSED,
        command="pytest -rA tests/test_add.py::test_add",
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_ms=123,
    )
    db_session.add(verification)
    db_session.commit()

    fetched = db_session.get(VerificationResult, verification.id)
    assert fetched.attempt_id == attempt.id
    assert fetched.attempt.run_id == run.id


def test_deleting_run_cascades_to_attempts(db_session):
    run = Run(issue_id="i3", issue_url="u3", repo="r3", base_commit="c3")
    db_session.add(run)
    db_session.commit()
    attempt = Attempt(run_id=run.id, attempt_number=1)
    db_session.add(attempt)
    db_session.commit()
    attempt_id = attempt.id

    db_session.delete(run)
    db_session.commit()

    assert db_session.get(Attempt, attempt_id) is None
