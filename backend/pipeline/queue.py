"""PostgreSQL-backed job queue.

PostgreSQL itself is the queue: `runs.status = 'queued'` rows are the
backlog, and `SELECT ... FOR UPDATE SKIP LOCKED` gives safe claiming with no
extra broker.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.run import Run, RunMode, RunStage, RunStatus


def enqueue_run(
    db: Session,
    issue_id: str,
    issue_url: str,
    repo: str,
    base_commit: str,
    mode: RunMode = RunMode.PUBLIC_DEMO,
) -> Run:
    run = Run(
        issue_id=issue_id,
        issue_url=issue_url,
        repo=repo,
        base_commit=base_commit,
        mode=mode,
        status=RunStatus.QUEUED,
        current_stage=RunStage.INTAKE,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def claim_next_job(db: Session) -> Run | None:
    """Atomically claim the oldest queued run, if any.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers never claim
    the same row: a worker that already holds the row lock makes it
    invisible to everyone else's SKIP LOCKED select until it commits.
    """
    stmt = (
        select(Run)
        .where(Run.status == RunStatus.QUEUED)
        .order_by(Run.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = db.execute(stmt).scalar_one_or_none()
    if run is None:
        return None

    run.status = RunStatus.RUNNING
    run.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def mark_completed(db: Session, run_id: uuid.UUID) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise ValueError(f"Run {run_id} not found")
    run.status = RunStatus.COMPLETED
    run.current_stage = RunStage.COMPLETED
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def mark_failed(db: Session, run_id: uuid.UUID) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise ValueError(f"Run {run_id} not found")
    run.status = RunStatus.FAILED
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run
