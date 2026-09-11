from __future__ import annotations

import json
import uuid

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from backend.api.schemas import (
    BenchmarkIssueSummary,
    BenchmarkIssuesResponse,
    CreateRunRequest,
    FixResponse,
    LocalizationResponse,
    LocalizationResultItem,
    PullRequestResponse,
    RunResponse,
    WorkflowResponse,
    WorkflowSummaryItem,
    WorkflowSummaryResponse,
)
from backend.benchmark.allowlist import is_repo_allowed
from backend.benchmark.loader import IssueNotFoundError, list_issue_ids, load_all_issues, load_issue
from backend.config import REPO_ROOT, settings
from backend.delivery import github
from backend.delivery.gating import FixNotValidatedError
from backend.delivery.pipeline import DeliveryError, deliver_fix, get_delivery, refresh_pr_status
from backend.generation.backend import FixGenerationBackend
from backend.generation.backend import get_backend as get_generation_backend
from backend.generation.pipeline import NoLocalizationResultsError, get_fix, run_fix_generation
from backend.localization.embeddings import EmbeddingBackend, get_backend
from backend.localization.service import RepositoryNotAllowedError, get_localization, localize_issue
from backend.models.database import get_db
from backend.models.pull_request import PullRequest
from backend.models.run import Run
from backend.pipeline.queue import enqueue_run
from backend.workflow.pipeline import (
    BenchmarkNotValidError,
    get_workflow_run,
    latest_run_for_issue,
    run_workflow,
)

app = FastAPI(title="Bug-to-PR Agent API", version="0.1.0")

# The dashboard is a separate origin in development (Next.js on :3000).
# Origins are an explicit allowlist from configuration rather than "*": this
# API can start runs and open pull requests, so it is not something any page
# on the internet should be able to call from a user's browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


def _find_issue_by_url(issue_url: str):
    for issue_id in list_issue_ids():
        issue = load_issue(issue_id)
        if issue.metadata.issue_url == issue_url:
            return issue
    return None


@app.post("/api/runs", response_model=RunResponse, status_code=201)
def create_run(payload: CreateRunRequest, db: Session = Depends(get_db)) -> Run:
    issue = _find_issue_by_url(payload.issue_url)
    if issue is None:
        raise HTTPException(status_code=422, detail="issue_url is not part of the curated benchmark")

    if not is_repo_allowed(issue.metadata.repo):
        raise HTTPException(status_code=422, detail=f"Repository '{issue.metadata.repo}' is not allowlisted")

    run = enqueue_run(
        db,
        issue_id=issue.metadata.issue_id,
        issue_url=issue.metadata.issue_url,
        repo=issue.metadata.repo,
        base_commit=issue.metadata.base_commit,
    )
    return run


@app.get("/api/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> Run:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/api/benchmark/issues", response_model=BenchmarkIssuesResponse)
def get_benchmark_issues() -> BenchmarkIssuesResponse:
    issues = load_all_issues()
    return BenchmarkIssuesResponse(
        issues=[
            BenchmarkIssueSummary(
                issue_id=i.metadata.issue_id,
                issue_url=i.metadata.issue_url,
                repo=i.metadata.repo,
                validation_status=i.metadata.validation_status,
            )
            for i in issues
        ]
    )


def _to_localization_response(outcome) -> LocalizationResponse:
    return LocalizationResponse(
        issue_id=outcome.issue_id,
        model_identifier=outcome.model_identifier,
        index_identifier=outcome.index_identifier,
        results=[LocalizationResultItem(**vars(r)) for r in outcome.results],
    )


def default_embedding_backend() -> EmbeddingBackend:
    """FastAPI dependency, overridden in tests with a fake EmbeddingBackend
    so the API test suite never needs to load a real embedding model.
    """
    return get_backend()


@app.post("/api/localize/{issue_id}", response_model=LocalizationResponse)
def create_localization(
    issue_id: str,
    db: Session = Depends(get_db),
    backend: EmbeddingBackend = Depends(default_embedding_backend),
) -> LocalizationResponse:
    try:
        outcome = localize_issue(issue_id, db, backend=backend)
    except IssueNotFoundError:
        raise HTTPException(status_code=404, detail="Issue not found") from None
    except RepositoryNotAllowedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _to_localization_response(outcome)


@app.get("/api/localize/{issue_id}", response_model=LocalizationResponse)
def read_localization(issue_id: str, db: Session = Depends(get_db)) -> LocalizationResponse:
    outcome = get_localization(issue_id, db)
    if outcome is None:
        raise HTTPException(status_code=404, detail="No localization result for this issue yet")
    return _to_localization_response(outcome)


def default_generation_backend() -> FixGenerationBackend:
    """FastAPI dependency, overridden in tests with a fake FixGenerationBackend
    so the API test suite never talks to a real model server.
    """
    return get_generation_backend()


def _to_fix_response(run: Run, db: Session) -> FixResponse:
    # The Phase 2 candidates this fix was generated against; a Run always
    # has them, since run_fix_generation refuses to start without any.
    localization = get_localization(run.issue_id, db)
    return FixResponse.model_validate(
        {
            "fix_id": run.id,
            "issue_id": run.issue_id,
            "repo": run.repo,
            "base_commit": run.base_commit,
            "status": run.status,
            "current_stage": run.current_stage,
            "attempts_taken": run.attempts_taken,
            "localization": [vars(r) for r in (localization.results if localization else [])],
            "attempts": [
                {
                    "attempt_number": a.attempt_number,
                    "model": a.model,
                    "hypothesis": a.hypothesis,
                    "diff": a.diff,
                    "status": a.status,
                    "failure_type": a.failure_type,
                    "verification_results": a.verification_results,
                }
                for a in sorted(run.attempts, key=lambda a: a.attempt_number)
            ],
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }
    )


@app.post("/api/fix/{issue_id}", response_model=FixResponse, status_code=201)
def create_fix(
    issue_id: str,
    db: Session = Depends(get_db),
    generation_backend: FixGenerationBackend = Depends(default_generation_backend),
    embedding_backend: EmbeddingBackend = Depends(default_embedding_backend),
) -> FixResponse:
    try:
        run = run_fix_generation(
            issue_id, db, generation_backend=generation_backend, embedding_backend=embedding_backend
        )
    except IssueNotFoundError:
        raise HTTPException(status_code=404, detail="Issue not found") from None
    except RepositoryNotAllowedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except NoLocalizationResultsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _to_fix_response(run, db)


@app.get("/api/fix/{fix_id}", response_model=FixResponse)
def read_fix(fix_id: uuid.UUID, db: Session = Depends(get_db)) -> FixResponse:
    run = get_fix(fix_id, db)
    if run is None:
        raise HTTPException(status_code=404, detail="Fix not found")
    return _to_fix_response(run, db)


def _to_pr_response(delivery: PullRequest, issue_id: str) -> PullRequestResponse:
    return PullRequestResponse.model_validate(
        {
            "delivery_id": delivery.id,
            "fix_id": delivery.run_id,
            "issue_id": issue_id,
            "repo": delivery.repo,
            "base_commit": delivery.base_commit,
            "branch_name": delivery.branch_name,
            "commit_sha": delivery.commit_sha,
            "commit_message": delivery.commit_message,
            "status": delivery.status,
            "failure_type": delivery.failure_type,
            "error": delivery.error,
            "pushed": delivery.pushed,
            "target_repo": delivery.target_repo,
            "base_branch": delivery.base_branch,
            "pr_url": delivery.pr_url,
            "pr_number": delivery.pr_number,
            "pr_state": delivery.pr_state,
            "is_draft": delivery.is_draft,
            "verification_command": delivery.verification_command,
            "verification_passed": delivery.verification_passed,
            "created_at": delivery.created_at,
            "committed_at": delivery.committed_at,
            "pr_created_at": delivery.pr_created_at,
        }
    )


def _run_for_delivery(fix_id: uuid.UUID, db: Session) -> Run:
    run = db.get(Run, fix_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Fix not found")
    return run


def _delivery_http_error(exc: Exception) -> HTTPException:
    """Map a delivery failure to a status a client can act on (docs/phase4.md
    "API").

    Every detail is redacted on the way out: an HTTP body is the most public
    place a credential echoed by git or gh could surface, so this boundary
    does not rely on the raising code having scrubbed it.
    """
    # Ordered most-specific first: the GitHub subclasses must be matched
    # before the GitHubError they inherit from.
    status = next(
        (
            code
            for exc_type, code in (
                (FixNotValidatedError, 422),
                (github.GitHubAuthError, 503),
                (github.GitHubPermissionError, 403),
                (github.BranchConflictError, 409),
                (github.GitHubError, 502),
                (DeliveryError, 422),
            )
            if isinstance(exc, exc_type)
        ),
        500,
    )
    return HTTPException(status_code=status, detail=github.redact(str(exc)))


def _deliver(fix_id: uuid.UUID, db: Session, create_pr: bool) -> PullRequestResponse:
    """Shared body of the commit-only and draft-PR endpoints."""
    run = _run_for_delivery(fix_id, db)
    try:
        delivery = deliver_fix(run, db, create_pr=create_pr)
    except (FixNotValidatedError, DeliveryError, github.GitHubError) as exc:
        raise _delivery_http_error(exc) from None
    return _to_pr_response(delivery, run.issue_id)


@app.post("/api/pr/{fix_id}/commit", response_model=PullRequestResponse, status_code=201)
def create_commit(fix_id: uuid.UUID, db: Session = Depends(get_db)) -> PullRequestResponse:
    """Branch from the base commit, apply the validated fix, verify, commit.

    Touches GitHub not at all, so it works with GitHub access unconfigured.
    """
    return _deliver(fix_id, db, create_pr=False)


@app.post("/api/pr/{fix_id}", response_model=PullRequestResponse, status_code=201)
def create_pull_request(fix_id: uuid.UUID, db: Session = Depends(get_db)) -> PullRequestResponse:
    """Everything the commit endpoint does, then push and open a draft PR."""
    return _deliver(fix_id, db, create_pr=True)


@app.get("/api/pr/{fix_id}", response_model=PullRequestResponse)
def read_pull_request(
    fix_id: uuid.UUID, refresh: bool = False, db: Session = Depends(get_db)
) -> PullRequestResponse:
    """The recorded delivery for a fix.

    `?refresh=true` re-reads the PR's live state from GitHub; without it the
    endpoint answers from the database and needs no GitHub access at all.
    """
    run = _run_for_delivery(fix_id, db)
    delivery = get_delivery(fix_id, db)
    if delivery is None:
        raise HTTPException(status_code=404, detail="No branch/commit or pull request for this fix yet")

    if refresh:
        try:
            delivery = refresh_pr_status(delivery, db)
        except github.GitHubError as exc:
            raise _delivery_http_error(exc) from None
    return _to_pr_response(delivery, run.issue_id)


def _issue_title(problem: str, issue_id: str) -> str:
    for line in problem.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return issue_id


def _to_workflow_delivery(delivery: PullRequest | None) -> dict | None:
    """The delivery row keyed as the dashboard reads it (`delivery_id`, not
    the raw column name).
    """
    if delivery is None:
        return None
    return {
        "delivery_id": delivery.id,
        "branch_name": delivery.branch_name,
        "commit_sha": delivery.commit_sha,
        "commit_message": delivery.commit_message,
        "status": delivery.status,
        "failure_type": delivery.failure_type,
        "error": delivery.error,
        "pushed": delivery.pushed,
        "target_repo": delivery.target_repo,
        "base_branch": delivery.base_branch,
        "pr_url": delivery.pr_url,
        "pr_number": delivery.pr_number,
        "pr_state": delivery.pr_state,
        "is_draft": delivery.is_draft,
        "verification_passed": delivery.verification_passed,
        "committed_at": delivery.committed_at,
        "pr_created_at": delivery.pr_created_at,
    }


def _to_workflow_response(run: Run, db: Session) -> WorkflowResponse:
    issue = load_issue(run.issue_id)
    localization = get_localization(run.issue_id, db)
    delivery = get_delivery(run.id, db)

    return WorkflowResponse.model_validate(
        {
            "run_id": run.id,
            "issue_id": run.issue_id,
            "issue_url": run.issue_url,
            "repo": run.repo,
            "base_commit": run.base_commit,
            "title": _issue_title(issue.problem, run.issue_id),
            "problem": issue.problem,
            "benchmark_validation": issue.metadata.validation_status,
            "status": run.status,
            "current_stage": run.current_stage,
            "attempts_taken": run.attempts_taken,
            "error": run.error,
            "localization": [vars(r) for r in (localization.results if localization else [])],
            "attempts": [
                {
                    "attempt_number": a.attempt_number,
                    "model": a.model,
                    "hypothesis": a.hypothesis,
                    "diff": a.diff,
                    "status": a.status,
                    "failure_type": a.failure_type,
                    "verification_results": a.verification_results,
                    "started_at": a.started_at,
                    "completed_at": a.completed_at,
                }
                for a in sorted(run.attempts, key=lambda a: a.attempt_number)
            ],
            "delivery": _to_workflow_delivery(delivery),
            "created_at": run.created_at,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
        }
    )


@app.post("/api/workflow/{issue_id}", response_model=WorkflowResponse, status_code=201)
def create_workflow(
    issue_id: str,
    create_pr: bool | None = None,
    db: Session = Depends(get_db),
    generation_backend: FixGenerationBackend = Depends(default_generation_backend),
    embedding_backend: EmbeddingBackend = Depends(default_embedding_backend),
) -> WorkflowResponse:
    """Run the complete workflow for one issue (docs/phase5.md).

    Synchronous: the caller gets the finished run. Long benchmark repositories
    are better driven through the queue (`POST /api/runs` plus the worker),
    which runs exactly the same workflow.
    """
    try:
        result = run_workflow(
            issue_id,
            db,
            generation_backend=generation_backend,
            embedding_backend=embedding_backend,
            create_pr=create_pr,
        )
    except IssueNotFoundError:
        raise HTTPException(status_code=404, detail="Issue not found") from None
    except BenchmarkNotValidError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RepositoryNotAllowedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _to_workflow_response(result.run, db)


@app.get("/api/workflow/run/{run_id}", response_model=WorkflowResponse)
def read_workflow_run(run_id: uuid.UUID, db: Session = Depends(get_db)) -> WorkflowResponse:
    run = get_workflow_run(run_id, db)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _to_workflow_response(run, db)


@app.get("/api/workflow/{issue_id}", response_model=WorkflowResponse)
def read_workflow(issue_id: str, db: Session = Depends(get_db)) -> WorkflowResponse:
    """The latest run for an issue, which is what the dashboard opens."""
    run = latest_run_for_issue(issue_id, db)
    if run is None:
        raise HTTPException(status_code=404, detail="No run for this issue yet")
    return _to_workflow_response(run, db)


@app.get("/api/workflow", response_model=WorkflowSummaryResponse)
def list_workflows(db: Session = Depends(get_db)) -> WorkflowSummaryResponse:
    """Every benchmark issue with the state of its latest run.

    One call rather than one per issue: the dashboard renders this table on
    every page load (docs/phase5.md "Frontend Integration").
    """
    # Latest run per issue in a single pass, so the endpoint stays one query
    # regardless of how many issues the benchmark holds.
    latest_by_issue: dict[str, Run] = {}
    for run in db.query(Run).order_by(Run.created_at.desc()).all():
        latest_by_issue.setdefault(run.issue_id, run)

    rows = []
    for issue in load_all_issues():
        run = latest_by_issue.get(issue.metadata.issue_id)
        delivery = get_delivery(run.id, db) if run is not None else None
        rows.append(
            WorkflowSummaryItem(
                issue_id=issue.metadata.issue_id,
                issue_url=issue.metadata.issue_url,
                repo=issue.metadata.repo,
                title=_issue_title(issue.problem, issue.metadata.issue_id),
                benchmark_validation=issue.metadata.validation_status,
                run_id=run.id if run else None,
                status=run.status if run else None,
                current_stage=run.current_stage if run else None,
                attempts_taken=run.attempts_taken if run else None,
                error=run.error if run else None,
                pr_url=run.pr_url if run else None,
                delivery_status=delivery.status if delivery else None,
                completed_at=run.completed_at if run else None,
            )
        )
    return WorkflowSummaryResponse(issues=rows)


@app.get("/api/benchmark/evaluation")
def get_benchmark_evaluation() -> dict:
    """The end-to-end evaluation report, if one has been produced.

    Served from the file the harness writes (`python -m backend.evaluation
    run`) rather than recomputed: a full evaluation takes hours and must not
    be triggered by loading a page.
    """
    path = REPO_ROOT / "backend" / "evaluation" / "report.json"
    if not path.exists():
        return {"available": False, "summary": None, "issues": []}

    report = json.loads(path.read_text())
    return {
        "available": True,
        "summary": report.get("summary"),
        "issues": report.get("issues", []),
        "model": report.get("model"),
        "embedding_model": report.get("embedding_model"),
        "generated_at": report.get("generated_at"),
    }
