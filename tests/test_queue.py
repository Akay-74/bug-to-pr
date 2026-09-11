import threading

from backend.models.database import SessionLocal
from backend.models.run import RunStatus
from backend.pipeline.queue import claim_next_job, enqueue_run


def test_queued_job_can_be_claimed(db_session):
    enqueue_run(db_session, issue_id="i1", issue_url="u1", repo="r1", base_commit="c1")

    claimed = claim_next_job(db_session)

    assert claimed is not None
    assert claimed.issue_id == "i1"


def test_claimed_job_becomes_running(db_session):
    run = enqueue_run(db_session, issue_id="i1", issue_url="u1", repo="r1", base_commit="c1")

    claimed = claim_next_job(db_session)

    assert claimed.id == run.id
    assert claimed.status == RunStatus.RUNNING
    assert claimed.started_at is not None


def test_empty_queue_returns_none(db_session):
    assert claim_next_job(db_session) is None


def test_two_workers_cannot_claim_the_same_job(db_session):
    run = enqueue_run(db_session, issue_id="i1", issue_url="u1", repo="r1", base_commit="c1")
    run_id = run.id

    results: list = []
    barrier = threading.Barrier(2)

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=5)
            claimed = claim_next_job(session)
            results.append(claimed.id if claimed else None)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    successful_claims = [r for r in results if r is not None]
    assert successful_claims == [run_id]
    assert results.count(None) == 1
