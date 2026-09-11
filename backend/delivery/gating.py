"""Preconditions for turning a fix into a commit/PR (docs/phase4.md).

"Never create a PR for an unvalidated candidate" is the one rule in Phase 4
that cannot be satisfied by being careful at the call site: every entry point
-- the API, the pipeline, a future worker -- has to arrive at the same
decision. So the decision lives here, is derived from what Phase 3 actually
persisted, and returns the validated artifact rather than a boolean.

The check is deliberately positive: an attempt qualifies because the records
prove it generated, applied and verified, not because nothing looks wrong.
A Phase 3 schema that grows a new check therefore cannot silently widen what
counts as validated.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.models.attempt import Attempt, AttemptStatus, FailureType
from backend.models.run import Run, RunStatus
from backend.models.verification_result import VerificationStatus

__all__ = [
    "FixNotValidatedError",
    "ValidatedFix",
    "validated_fix_for_run",
]

# The Phase 3 checks that must have passed for a candidate to count as
# validated (backend/generation/pipeline.py records these names).
_REQUIRED_CHECKS = ("generation", "patch_apply", "regression_test")


class FixNotValidatedError(Exception):
    """This run has no candidate that generated, applied and verified."""


@dataclass(frozen=True)
class ValidatedFix:
    run: Run
    attempt: Attempt
    diff: str


def _disqualification(attempt: Attempt) -> str | None:
    """Why `attempt` is not deliverable, or None if it is."""
    if attempt.status is not AttemptStatus.PASSED:
        return f"attempt {attempt.attempt_number} did not pass (status={attempt.status.value})"
    if attempt.failure_type not in (None, FailureType.NONE):
        return f"attempt {attempt.attempt_number} recorded failure_type={attempt.failure_type.value}"
    if not (attempt.diff or "").strip():
        return f"attempt {attempt.attempt_number} passed but persisted no patch"

    checks = {check.check_name: check for check in attempt.verification_results}
    for name in _REQUIRED_CHECKS:
        check = checks.get(name)
        if check is None:
            return f"attempt {attempt.attempt_number} has no '{name}' result"
        if check.status is not VerificationStatus.PASSED:
            return f"attempt {attempt.attempt_number} check '{name}' is {check.status.value}, not passed"

    # Any other check that ran must not have failed either -- notably
    # relevant_tests, which is what proves previously-passing tests still
    # pass. It is absent when the benchmark defines no such command, so it
    # is enforced when present rather than required outright.
    for name, check in checks.items():
        if check.status is not VerificationStatus.PASSED:
            return f"attempt {attempt.attempt_number} check '{name}' is {check.status.value}, not passed"
    return None


def validated_fix_for_run(run: Run, db: Session) -> ValidatedFix:
    """The one deliverable candidate on `run`.

    Raises FixNotValidatedError -- carrying the reason, so an operator can
    see *why* a run was refused -- if the run did not complete, or if no
    attempt on it passed every required check.
    """
    if run.status is not RunStatus.COMPLETED:
        raise FixNotValidatedError(
            f"Run {run.id} is {run.status.value}; only a completed run can be delivered"
        )

    reasons: list[str] = []
    for attempt in sorted(run.attempts, key=lambda a: a.attempt_number):
        reason = _disqualification(attempt)
        if reason is None:
            return ValidatedFix(run=run, attempt=attempt, diff=attempt.diff or "")
        reasons.append(reason)

    detail = "; ".join(reasons) if reasons else "the run recorded no attempts"
    raise FixNotValidatedError(f"Run {run.id} has no validated fix: {detail}")
