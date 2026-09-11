from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from backend.benchmark.schema import ValidationStatus
from backend.models.attempt import AttemptStatus, FailureType
from backend.models.pull_request import DeliveryFailureType, DeliveryStatus
from backend.models.run import RunMode, RunStage, RunStatus
from backend.models.verification_result import VerificationStatus


class CreateRunRequest(BaseModel):
    issue_url: str


class RunResponse(BaseModel):
    id: uuid.UUID
    issue_id: str
    issue_url: str
    repo: str
    base_commit: str
    mode: RunMode
    status: RunStatus
    current_stage: RunStage
    model_policy: str | None
    attempts_taken: int
    total_cost: float
    pr_url: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class BenchmarkIssueSummary(BaseModel):
    issue_id: str
    issue_url: str
    repo: str
    validation_status: ValidationStatus


class BenchmarkIssuesResponse(BaseModel):
    issues: list[BenchmarkIssueSummary]


class LocalizationResultItem(BaseModel):
    rank: int
    file: str
    symbol: str
    symbol_type: str
    start_line: int
    end_line: int
    semantic_score: float
    lexical_score: float
    relevance_score: float
    final_score: float
    matched_terms: list[str]


class LocalizationResponse(BaseModel):
    issue_id: str
    model_identifier: str
    index_identifier: str
    results: list[LocalizationResultItem]


class VerificationResultItem(BaseModel):
    check_name: str
    status: VerificationStatus
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

    model_config = {"from_attributes": True}


class FixAttemptItem(BaseModel):
    attempt_number: int
    model: str | None
    hypothesis: str | None
    diff: str | None
    status: AttemptStatus
    failure_type: FailureType | None
    verification_results: list[VerificationResultItem]

    model_config = {"from_attributes": True}


class FixResponse(BaseModel):
    fix_id: uuid.UUID
    issue_id: str
    repo: str
    base_commit: str
    status: RunStatus
    current_stage: RunStage
    attempts_taken: int
    # docs/phase3.md §6: the localization target the fix was generated against.
    localization: list[LocalizationResultItem]
    attempts: list[FixAttemptItem]
    created_at: datetime
    completed_at: datetime | None


class PullRequestResponse(BaseModel):
    """docs/phase4.md "API": branch/commit state plus draft-PR state."""

    delivery_id: uuid.UUID
    fix_id: uuid.UUID
    issue_id: str
    repo: str
    base_commit: str
    branch_name: str
    commit_sha: str | None
    commit_message: str | None
    status: DeliveryStatus
    failure_type: DeliveryFailureType | None
    error: str | None
    pushed: bool
    target_repo: str | None
    base_branch: str | None
    pr_url: str | None
    pr_number: int | None
    pr_state: str | None
    is_draft: bool
    verification_command: str | None
    verification_passed: bool | None
    created_at: datetime
    committed_at: datetime | None
    pr_created_at: datetime | None

    model_config = {"from_attributes": True}


class WorkflowAttemptItem(BaseModel):
    """One candidate: its patch, outcome and the checks that judged it."""

    attempt_number: int
    model: str | None
    hypothesis: str | None
    diff: str | None
    status: AttemptStatus
    failure_type: FailureType | None
    verification_results: list[VerificationResultItem]
    started_at: datetime | None
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class WorkflowDeliveryItem(BaseModel):
    """Commit and draft-PR state (docs/phase5.md "Frontend Integration")."""

    delivery_id: uuid.UUID
    branch_name: str
    commit_sha: str | None
    commit_message: str | None
    status: DeliveryStatus
    failure_type: DeliveryFailureType | None
    error: str | None
    pushed: bool
    target_repo: str | None
    base_branch: str | None
    pr_url: str | None
    pr_number: int | None
    pr_state: str | None
    is_draft: bool
    verification_passed: bool | None
    committed_at: datetime | None
    pr_created_at: datetime | None

    model_config = {"from_attributes": True}


class WorkflowResponse(BaseModel):
    """Everything the dashboard needs for one issue, in one call:
    issue selection, validation state, localization, generated patch, test
    results, candidate attempts, commit state, draft PR state and errors
    (docs/phase5.md "Frontend Integration").
    """

    run_id: uuid.UUID
    issue_id: str
    issue_url: str
    repo: str
    base_commit: str
    title: str
    problem: str
    benchmark_validation: ValidationStatus
    status: RunStatus
    current_stage: RunStage
    attempts_taken: int
    error: str | None
    localization: list[LocalizationResultItem]
    attempts: list[WorkflowAttemptItem]
    delivery: WorkflowDeliveryItem | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowSummaryItem(BaseModel):
    """One row of the dashboard's issue table: the issue plus the state of
    its most recent run, if it has one.
    """

    issue_id: str
    issue_url: str
    repo: str
    title: str
    benchmark_validation: ValidationStatus
    run_id: uuid.UUID | None
    status: RunStatus | None
    current_stage: RunStage | None
    attempts_taken: int | None
    error: str | None
    pr_url: str | None
    delivery_status: DeliveryStatus | None
    completed_at: datetime | None


class WorkflowSummaryResponse(BaseModel):
    issues: list[WorkflowSummaryItem]
