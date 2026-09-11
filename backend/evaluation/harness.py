"""End-to-end benchmark evaluation (docs/phase5.md "Final Benchmark Evaluation").

Runs the complete workflow over the validated benchmark set and reports what
actually happened: localization Top-1/Top-3, candidates generated, validated
fixes, success rate, failure categories, and mean generation/validation time.

Two things this deliberately does not do:

* it does not hide environment failures in the model's score -- a container
  that could not install dependencies is counted separately, because calling
  that a model failure would flatter the model;
* it does not keep results only in memory. Each issue is appended to the
  report file as it finishes, so an evaluation that takes hours can be
  stopped and resumed without losing what it already measured.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.benchmark.loader import list_issue_ids, load_issue
from backend.benchmark.schema import ValidationStatus
from backend.config import settings
from backend.localization.evaluate import _is_hit, parse_ground_truth
from backend.localization.service import LocalizationOutcome, get_localization
from backend.models.attempt import AttemptStatus
from backend.models.run import RunStatus
from backend.models.verification_result import VerificationStatus
from backend.workflow.pipeline import run_workflow

__all__ = ["IssueEvaluation", "EvaluationReport", "evaluate_issue", "load_report", "save_result"]

# Which checks count as "validating" a candidate, for the timing breakdown.
_VALIDATION_CHECKS = ("install", "regression_test", "relevant_tests")


@dataclass
class IssueEvaluation:
    issue_id: str
    repo: str
    status: str                      # completed | failed | error
    outcome: str                     # validated_fix | no_validated_fix | environment_failure | error
    failure_category: str | None
    attempts: int
    top1_hit: bool | None
    top3_hit: bool | None
    fix_file_top1: bool | None
    fix_file_top3: bool | None
    generation_ms: int
    validation_ms: int
    wall_ms: int
    pr_created: bool
    pr_url: str | None
    error: str | None
    finished_at: str


@dataclass
class EvaluationReport:
    model: str
    embedding_model: str
    candidate_limit: int
    generated_at: str
    issues: list[IssueEvaluation] = field(default_factory=list)

    # --- aggregates --------------------------------------------------------

    @property
    def scored(self) -> list[IssueEvaluation]:
        """Issues where the system actually got to try.

        An environment failure is a fact about the harness's machine, not the
        model, so it is reported but excluded from the success denominator.
        """
        return [i for i in self.issues if i.outcome in ("validated_fix", "no_validated_fix")]

    def summary(self) -> dict:
        scored = self.scored
        validated = [i for i in scored if i.outcome == "validated_fix"]
        localized = [i for i in self.issues if i.top1_hit is not None]
        fix_located = [i for i in self.issues if i.fix_file_top1 is not None]
        generated = [i for i in self.issues if i.generation_ms > 0]
        validated_timing = [i for i in self.issues if i.validation_ms > 0]

        categories: dict[str, int] = {}
        for issue in self.issues:
            if issue.failure_category:
                categories[issue.failure_category] = categories.get(issue.failure_category, 0) + 1

        return {
            "model": self.model,
            "embedding_model": self.embedding_model,
            "candidate_limit": self.candidate_limit,
            "issues_attempted": len(self.issues),
            "issues_scored": len(scored),
            "environment_failures": sum(1 for i in self.issues if i.outcome == "environment_failure"),
            "harness_errors": sum(1 for i in self.issues if i.outcome == "error"),
            "validated_fixes": len(validated),
            "success_rate": round(len(validated) / len(scored), 4) if scored else 0.0,
            "localization_top1": round(
                sum(1 for i in localized if i.top1_hit) / len(localized), 4
            ) if localized else None,
            "localization_top3": round(
                sum(1 for i in localized if i.top3_hit) / len(localized), 4
            ) if localized else None,
            "fix_file_top1": round(
                sum(1 for i in fix_located if i.fix_file_top1) / len(fix_located), 4
            ) if fix_located else None,
            "fix_file_top3": round(
                sum(1 for i in fix_located if i.fix_file_top3) / len(fix_located), 4
            ) if fix_located else None,
            "total_candidates_generated": sum(i.attempts for i in self.issues),
            "avg_candidates_per_issue": round(
                sum(i.attempts for i in self.issues) / len(self.issues), 2
            ) if self.issues else 0.0,
            "avg_generation_ms": round(
                sum(i.generation_ms for i in generated) / len(generated)
            ) if generated else 0,
            "avg_validation_ms": round(
                sum(i.validation_ms for i in validated_timing) / len(validated_timing)
            ) if validated_timing else 0,
            "avg_wall_ms": round(sum(i.wall_ms for i in self.issues) / len(self.issues)) if self.issues else 0,
            "failure_categories": dict(sorted(categories.items())),
            "prs_created": sum(1 for i in self.issues if i.pr_created),
            "generated_at": self.generated_at,
        }


def _localization_hits(issue_id: str, db: Session) -> tuple[bool | None, bool | None]:
    """Top-1/Top-3 against the same ground truth Phase 2's evaluator uses."""
    issue = load_issue(issue_id)
    ground_truth = parse_ground_truth(issue)
    outcome: LocalizationOutcome | None = get_localization(issue_id, db)
    if ground_truth is None or outcome is None or not outcome.results:
        return None, None
    return _is_hit(outcome, ground_truth, top_n=1), _is_hit(outcome, ground_truth, top_n=3)


def _patched_files(diff: str | None) -> list[str]:
    files = []
    for line in (diff or "").splitlines():
        if line.startswith("+++ ") and "/dev/null" not in line:
            path = line[4:].strip()
            files.append(path[2:] if path.startswith(("a/", "b/")) else path)
    return files


def _fix_file_hits(run, issue_id: str, db: Session) -> tuple[bool | None, bool | None]:
    """Did localization point the generator at the file the fix actually changed?

    The Top-1/Top-3 above use Phase 2's ground truth, which is the location
    of the *regression test*. That is the right question for a test-retrieval
    metric and the wrong one for this pipeline, whose localizer is supposed
    to find the source to modify. This measures that instead, and is only
    defined for issues that produced a validated fix -- for anything else
    there is no confirmed correct file to compare against.
    """
    passing = next((a for a in run.attempts if a.status is AttemptStatus.PASSED), None)
    outcome = get_localization(issue_id, db)
    if passing is None or outcome is None or not outcome.results:
        return None, None

    patched = set(_patched_files(passing.diff))
    if not patched:
        return None, None

    ranked = [c.file for c in outcome.results]
    return bool(patched & set(ranked[:1])), bool(patched & set(ranked[:3]))


def _timings(run) -> tuple[int, int]:
    generation_ms = 0
    validation_ms = 0
    for attempt in run.attempts:
        for check in attempt.verification_results:
            if check.check_name == "generation":
                generation_ms += check.duration_ms
            elif check.check_name in _VALIDATION_CHECKS:
                validation_ms += check.duration_ms
    return generation_ms, validation_ms


def _classify(run) -> tuple[str, str | None]:
    """(outcome, failure_category) for one finished run.

    Environment failures are separated from model failures: a candidate whose
    container never installed says nothing about the patch it proposed.
    """
    if run.status is RunStatus.COMPLETED and any(
        a.status is AttemptStatus.PASSED for a in run.attempts
    ):
        return "validated_fix", None

    categories = [
        a.failure_type.value
        for a in sorted(run.attempts, key=lambda a: a.attempt_number)
        if a.failure_type and a.failure_type.value != "none"
    ]
    if not categories:
        return "environment_failure", "no_attempts"

    # An issue that only ever hit environment errors was never really tried.
    if all(category in ("environment_error", "timeout") for category in categories):
        return "environment_failure", categories[-1]
    return "no_validated_fix", categories[-1]


def evaluate_issue(
    issue_id: str,
    db: Session,
    candidate_limit: int | None = None,
    create_pr: bool = False,
) -> IssueEvaluation:
    """Run the whole workflow for one issue and measure it."""
    started = time.monotonic()
    repo = load_issue(issue_id).metadata.repo

    try:
        result = run_workflow(issue_id, db, candidate_limit=candidate_limit, create_pr=create_pr)
    except Exception as exc:
        return IssueEvaluation(
            issue_id=issue_id, repo=repo, status="error", outcome="error",
            failure_category=type(exc).__name__, attempts=0, top1_hit=None, top3_hit=None,
            fix_file_top1=None, fix_file_top3=None,
            generation_ms=0, validation_ms=0, wall_ms=int((time.monotonic() - started) * 1000),
            pr_created=False, pr_url=None, error=str(exc)[:2000],
            finished_at=datetime.now(timezone.utc).isoformat(),
        )

    run = result.run
    outcome, failure_category = _classify(run)
    generation_ms, validation_ms = _timings(run)
    top1, top3 = _localization_hits(issue_id, db)
    fix_top1, fix_top3 = _fix_file_hits(run, issue_id, db)

    return IssueEvaluation(
        issue_id=issue_id,
        repo=repo,
        status=run.status.value,
        outcome=outcome,
        failure_category=failure_category,
        attempts=run.attempts_taken,
        top1_hit=top1,
        top3_hit=top3,
        fix_file_top1=fix_top1,
        fix_file_top3=fix_top3,
        generation_ms=generation_ms,
        validation_ms=validation_ms,
        wall_ms=int((time.monotonic() - started) * 1000),
        pr_created=bool(run.pr_url),
        pr_url=run.pr_url,
        error=run.error,
        finished_at=datetime.now(timezone.utc).isoformat(),
    )


# --- resumable report file -------------------------------------------------


# Fields that may be absent from a report written by an earlier version.
_ISSUE_DEFAULTS = {"fix_file_top1": None, "fix_file_top3": None}


def load_report(path: Path) -> EvaluationReport:
    if not path.exists():
        return EvaluationReport(
            model=settings.GENERATION_MODEL,
            embedding_model=settings.LOCALIZATION_EMBEDDING_MODEL,
            candidate_limit=settings.GENERATION_CANDIDATE_LIMIT,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    raw = json.loads(path.read_text())
    return EvaluationReport(
        model=raw.get("model", settings.GENERATION_MODEL),
        embedding_model=raw.get("embedding_model", settings.LOCALIZATION_EMBEDDING_MODEL),
        candidate_limit=raw.get("candidate_limit", settings.GENERATION_CANDIDATE_LIMIT),
        generated_at=raw.get("generated_at", datetime.now(timezone.utc).isoformat()),
        # Tolerant of reports written before a metric existed, so adding one
        # does not invalidate hours of already-measured results.
        issues=[
            IssueEvaluation(**{**_ISSUE_DEFAULTS, **i})
            for i in raw.get("issues", [])
        ],
    )


def save_result(path: Path, report: EvaluationReport, result: IssueEvaluation) -> EvaluationReport:
    """Append one issue's result and rewrite the report.

    Written after every issue so a long evaluation is never lost, and so
    `--resume` can pick up exactly where it stopped.
    """
    report.issues = [i for i in report.issues if i.issue_id != result.issue_id] + [result]
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **{k: v for k, v in asdict(report).items() if k != "issues"},
        "summary": report.summary(),
        "issues": [asdict(i) for i in report.issues],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return report


def validated_issue_ids() -> list[str]:
    """The benchmark set the evaluation runs over: Phase 1's `valid` issues."""
    return [
        issue_id
        for issue_id in list_issue_ids()
        if load_issue(issue_id).metadata.validation_status is ValidationStatus.VALID
    ]
