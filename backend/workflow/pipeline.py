"""The complete workflow, end to end (docs/phase5.md).

    issue -> benchmark validation -> localization -> fix generation
          -> sandbox validation -> commit -> draft PR

Each phase already owns its own logic; this module is only the seam between
them. Its job is to run them in order against **one** Run row, move that row
through the stages an observer can see, and turn every way a phase can fail
into a recorded reason rather than a traceback.

Delivery is deliberately not fatal to the workflow: a fix that generated and
validated is a real result even when GitHub access is unconfigured, so the
run completes and records why no PR exists rather than being marked failed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.benchmark.loader import IssueNotFoundError, load_issue
from backend.benchmark.schema import ValidationStatus
from backend.config import settings
from backend.delivery import github
from backend.delivery.gating import FixNotValidatedError
from backend.delivery.pipeline import DeliveryError, deliver_fix
from backend.generation.pipeline import NoLocalizationResultsError, run_fix_generation
from backend.localization import embeddings as embeddings_mod
from backend.localization.service import (
    RepositoryNotAllowedError,
    get_localization,
    localize_issue,
)
from backend.models.pull_request import PullRequest
from backend.models.run import Run, RunMode, RunStage, RunStatus

__all__ = [
    "WorkflowError",
    "BenchmarkNotValidError",
    "WorkflowResult",
    "run_workflow",
    "get_workflow_run",
    "latest_run_for_issue",
]


class WorkflowError(Exception):
    """The workflow could not start."""


class BenchmarkNotValidError(WorkflowError):
    """The benchmark issue has not passed Phase 1 validation."""


@dataclass
class WorkflowResult:
    run: Run
    delivery: PullRequest | None
    delivery_error: str | None


def _fail_run(db: Session, run: Run, stage: RunStage, error: str) -> None:
    run.status = RunStatus.FAILED
    run.current_stage = stage
    # Redacted on the way in: this is rendered by the dashboard and returned
    # by the API, and a delivery error can quote git/gh output.
    run.error = github.redact(error)[-4000:]
    run.completed_at = datetime.now(timezone.utc)
    db.commit()


def _ensure_benchmark_valid(issue_id: str, allow_unvalidated: bool):
    """docs/phase5.md step 1: the workflow starts from a validated benchmark.

    Phase 1's recorded status is used rather than re-running Stage 0
    validation, which would double the container cost of every request. The
    escape hatch exists for local development against a fixture issue that
    has never been through the validator.
    """
    issue = load_issue(issue_id)  # IssueNotFoundError propagates
    status = issue.metadata.validation_status
    if status is not ValidationStatus.VALID and not allow_unvalidated:
        raise BenchmarkNotValidError(
            f"Benchmark issue '{issue_id}' is {status.value}, not valid; "
            "run `python -m backend.benchmark validate` first"
        )
    return issue


def run_workflow(
    issue_id: str,
    db: Session,
    run: Run | None = None,
    generation_backend=None,
    embedding_backend=None,
    candidate_limit: int | None = None,
    create_pr: bool | None = None,
    allow_unvalidated_benchmark: bool = False,
) -> WorkflowResult:
    """Run every phase for one issue.

    `run` adopts an existing Run (the queue hands the worker one); otherwise a
    Run is created up front, so a workflow that fails mid-way is still
    inspectable rather than vanishing.

    Raises IssueNotFoundError, BenchmarkNotValidError or
    RepositoryNotAllowedError before any work begins. After that, failures
    are recorded on the Run and returned rather than raised.
    """
    issue = _ensure_benchmark_valid(issue_id, allow_unvalidated_benchmark)
    metadata = issue.metadata

    if run is None:
        run = Run(
            issue_id=metadata.issue_id,
            issue_url=metadata.issue_url,
            repo=metadata.repo,
            base_commit=metadata.base_commit,
            mode=RunMode.PUBLIC_DEMO,
            status=RunStatus.RUNNING,
            current_stage=RunStage.INTAKE,
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
    else:
        run.status = RunStatus.RUNNING
        run.current_stage = RunStage.INTAKE
        run.started_at = run.started_at or datetime.now(timezone.utc)
        run.error = None
        db.commit()

    # --- localization ------------------------------------------------------
    run.current_stage = RunStage.LOCALIZATION
    db.commit()
    try:
        # Reused when Phase 2 already ran for this issue: re-embedding a
        # repository to get the same ranking back is the single most
        # expensive avoidable step (docs/phase5.md "Performance").
        outcome = get_localization(issue_id, db)
        if outcome is None:
            outcome = localize_issue(
                issue_id, db, backend=embedding_backend or embeddings_mod.get_backend()
            )
    except RepositoryNotAllowedError as exc:
        _fail_run(db, run, RunStage.LOCALIZATION, str(exc))
        raise
    except Exception as exc:
        _fail_run(db, run, RunStage.LOCALIZATION, f"Localization failed: {exc}")
        return WorkflowResult(run=run, delivery=None, delivery_error=None)

    if not outcome.results:
        _fail_run(db, run, RunStage.LOCALIZATION, f"No localization candidates for '{issue_id}'")
        return WorkflowResult(run=run, delivery=None, delivery_error=None)

    # --- generation + sandbox validation -----------------------------------
    try:
        run = run_fix_generation(
            issue_id,
            db,
            generation_backend=generation_backend,
            embedding_backend=embedding_backend,
            candidate_limit=candidate_limit,
            run=run,
        )
    except NoLocalizationResultsError as exc:
        _fail_run(db, run, RunStage.LOCALIZATION, str(exc))
        return WorkflowResult(run=run, delivery=None, delivery_error=None)
    except Exception as exc:
        _fail_run(db, run, RunStage.GENERATION, f"Fix generation failed: {exc}")
        return WorkflowResult(run=run, delivery=None, delivery_error=None)

    if run.status is not RunStatus.COMPLETED:
        # Every candidate failed. run_fix_generation already recorded the
        # per-attempt detail; summarise it so the run itself says why.
        last = max(run.attempts, key=lambda a: a.attempt_number, default=None)
        reason = (
            f"No candidate passed validation after {run.attempts_taken} attempt(s)"
            + (f"; last failure: {last.failure_type.value}" if last and last.failure_type else "")
        )
        run.error = reason
        run.current_stage = RunStage.COMPLETED
        db.commit()
        return WorkflowResult(run=run, delivery=None, delivery_error=None)

    # --- commit (+ draft PR) -----------------------------------------------
    want_pr = settings.GITHUB_ENABLED if create_pr is None else create_pr
    run.current_stage = RunStage.PR_CREATION
    db.commit()

    delivery = None
    delivery_error = None
    try:
        delivery = deliver_fix(run, db, create_pr=want_pr)
    except (FixNotValidatedError, DeliveryError, github.GitHubError) as exc:
        # A validated fix that could not be delivered is still a validated
        # fix. The run stays COMPLETED and carries the reason; the delivery
        # row (when one exists) carries the detail.
        delivery_error = github.redact(str(exc))

    run.current_stage = RunStage.COMPLETED
    run.completed_at = run.completed_at or datetime.now(timezone.utc)
    if delivery_error:
        run.error = delivery_error[-4000:]
    db.commit()
    db.refresh(run)
    return WorkflowResult(run=run, delivery=delivery, delivery_error=delivery_error)


def get_workflow_run(run_id, db: Session) -> Run | None:
    return db.get(Run, run_id)


def latest_run_for_issue(issue_id: str, db: Session) -> Run | None:
    """The most recent run for an issue -- what the dashboard shows when the
    user picks an issue rather than a specific run.
    """
    return (
        db.query(Run)
        .filter(Run.issue_id == issue_id)
        .order_by(Run.created_at.desc())
        .first()
    )
