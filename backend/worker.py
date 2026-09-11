"""Worker loop: claim a queued run and drive it through the real workflow.

Phase 1 shipped this with a placeholder pipeline to prove the queue's
claim/run/complete lifecycle. Phase 5 connects it to the actual workflow
(backend/workflow/pipeline.py), so a queued run now performs the same
localization -> generation -> validation -> commit -> draft PR sequence the
synchronous API endpoint does.

The loop is bounded on request (`--once`, `--max-jobs`): a long benchmark run
should be able to finish and exit rather than only ever being killed
(docs/phase5.md "Reliability": no infinite background jobs).
"""
from __future__ import annotations

import argparse
import logging
import time

from backend.models.database import SessionLocal
from backend.pipeline.queue import claim_next_job, mark_failed
from backend.workflow.pipeline import run_workflow

logger = logging.getLogger("backend.worker")

POLL_INTERVAL_SECONDS = 2


def process_one() -> bool:
    """Claim and execute a single queued job. Returns True if a job was
    processed, False if the queue was empty.

    The workflow records its own terminal status and error on the Run, so
    this only has to catch the case where it raised before doing so.
    """
    db = SessionLocal()
    try:
        run = claim_next_job(db)
        if run is None:
            return False

        run_id, issue_id = run.id, run.issue_id
        logger.info("Claimed run %s (issue=%s)", run_id, issue_id)
        try:
            result = run_workflow(issue_id, db, run=run)
        except Exception as exc:
            logger.exception("Workflow failed for run %s", run_id)
            failed = mark_failed(db, run_id)
            failed.error = str(exc)[-4000:]
            db.commit()
            return True

        logger.info(
            "Run %s finished: status=%s stage=%s pr=%s",
            run_id, result.run.status.value, result.run.current_stage.value, result.run.pr_url,
        )
        return True
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job, then exit")
    parser.add_argument(
        "--max-jobs", type=int, default=None, help="Exit after processing this many jobs"
    )
    parser.add_argument(
        "--idle-exit", action="store_true", help="Exit when the queue is empty instead of polling"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    limit = 1 if args.once else args.max_jobs
    logger.info("Worker started (limit=%s, idle_exit=%s)", limit or "unbounded", args.idle_exit)

    processed = 0
    while limit is None or processed < limit:
        if process_one():
            processed += 1
            continue
        if args.idle_exit or args.once:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    logger.info("Worker finished after %d job(s)", processed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
