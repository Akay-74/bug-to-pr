"""Benchmark evaluation reporting (docs/phase5.md "Final Benchmark Evaluation").

The point of these tests is that the numbers cannot flatter the system: an
environment failure must never be counted as a model failure or fold into the
success rate, and the report must survive being interrupted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.evaluation.harness import (
    EvaluationReport,
    IssueEvaluation,
    _classify,
    _patched_files,
    load_report,
    save_result,
)
from backend.models.attempt import Attempt, AttemptStatus, FailureType
from backend.models.run import Run, RunStatus


def _issue(issue_id: str, outcome: str, **overrides) -> IssueEvaluation:
    defaults = dict(
        issue_id=issue_id, repo="org/repo", status="completed", outcome=outcome,
        failure_category=None, attempts=1, top1_hit=None, top3_hit=None,
        fix_file_top1=None, fix_file_top3=None, generation_ms=1000,
        validation_ms=2000, wall_ms=5000, pr_created=False, pr_url=None,
        error=None, finished_at="2026-09-08T00:00:00+00:00",
    )
    defaults.update(overrides)
    return IssueEvaluation(**defaults)


def _report(*issues: IssueEvaluation) -> EvaluationReport:
    return EvaluationReport(
        model="fake-model", embedding_model="fake", candidate_limit=3,
        generated_at="2026-09-08T00:00:00+00:00", issues=list(issues),
    )


# --- honest accounting -----------------------------------------------------


def test_environment_failures_are_excluded_from_the_success_rate():
    """docs/phase5.md: do not hide environment failures; distinguish them
    from model failures. A container that could not install says nothing
    about the model, so it must not count against it -- or for it.
    """
    report = _report(
        _issue("a", "validated_fix"),
        _issue("b", "no_validated_fix", failure_category="verification_failed"),
        _issue("c", "environment_failure", failure_category="environment_error"),
    )

    summary = report.summary()

    assert summary["issues_attempted"] == 3
    assert summary["issues_scored"] == 2          # the environment failure is not scored
    assert summary["environment_failures"] == 1   # but it is still reported
    assert summary["success_rate"] == 0.5


def test_environment_failures_are_still_visible_in_failure_categories():
    report = _report(_issue("c", "environment_failure", failure_category="environment_error"))

    assert report.summary()["failure_categories"] == {"environment_error": 1}


def test_success_rate_is_zero_rather_than_undefined_when_nothing_scored():
    report = _report(_issue("c", "environment_failure", failure_category="environment_error"))

    assert report.summary()["success_rate"] == 0.0


def test_a_harness_error_is_reported_separately_from_a_model_failure():
    report = _report(_issue("a", "error", failure_category="RuntimeError"))

    summary = report.summary()
    assert summary["harness_errors"] == 1
    assert summary["issues_scored"] == 0


# --- classification --------------------------------------------------------


def _run_with(*attempts: tuple[AttemptStatus, FailureType], status=RunStatus.COMPLETED) -> Run:
    run = Run(
        issue_id="x", issue_url="", repo="org/repo", base_commit="0" * 40, status=status,
    )
    run.attempts = [
        Attempt(attempt_number=i + 1, status=s, failure_type=f, diff="--- a/x\n+++ b/x\n")
        for i, (s, f) in enumerate(attempts)
    ]
    return run


def test_a_passing_attempt_is_a_validated_fix():
    run = _run_with((AttemptStatus.PASSED, FailureType.NONE))

    assert _classify(run) == ("validated_fix", None)


def test_only_environment_errors_means_the_system_was_never_really_tried():
    run = _run_with(
        (AttemptStatus.FAILED, FailureType.ENVIRONMENT_ERROR),
        (AttemptStatus.FAILED, FailureType.TIMEOUT),
        status=RunStatus.FAILED,
    )

    outcome, category = _classify(run)
    assert outcome == "environment_failure"
    assert category == "timeout"


def test_a_real_model_failure_is_scored_even_alongside_an_environment_error():
    """One flaky container must not excuse a genuine failure to fix the bug."""
    run = _run_with(
        (AttemptStatus.FAILED, FailureType.ENVIRONMENT_ERROR),
        (AttemptStatus.FAILED, FailureType.VERIFICATION_FAILED),
        status=RunStatus.FAILED,
    )

    outcome, category = _classify(run)
    assert outcome == "no_validated_fix"
    assert category == "verification_failed"


def test_a_run_with_no_attempts_is_an_environment_failure_not_a_model_failure():
    run = _run_with(status=RunStatus.FAILED)

    assert _classify(run) == ("environment_failure", "no_attempts")


# --- patched-file metric ---------------------------------------------------


def test_patched_files_are_read_from_the_diff():
    diff = "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-a\n+b\n"

    assert _patched_files(diff) == ["pkg/mod.py"]


def test_patched_files_ignores_deletions_and_empty_diffs():
    assert _patched_files(None) == []
    assert _patched_files("--- a/x\n+++ /dev/null\n") == []


# --- resumability ----------------------------------------------------------


def test_results_are_written_after_every_issue_so_an_interrupted_run_survives(tmp_path):
    path = tmp_path / "report.json"
    report = _report()

    report = save_result(path, report, _issue("a", "validated_fix"))
    assert json.loads(path.read_text())["issues"][0]["issue_id"] == "a"

    save_result(path, report, _issue("b", "no_validated_fix"))
    reloaded = load_report(path)

    assert {i.issue_id for i in reloaded.issues} == {"a", "b"}
    assert reloaded.summary()["issues_attempted"] == 2


def test_re_evaluating_an_issue_replaces_its_previous_result(tmp_path):
    path = tmp_path / "report.json"
    report = save_result(tmp_path / "report.json", _report(), _issue("a", "no_validated_fix"))

    save_result(path, report, _issue("a", "validated_fix"))
    reloaded = load_report(path)

    assert len(reloaded.issues) == 1
    assert reloaded.issues[0].outcome == "validated_fix"


def test_a_report_written_before_a_metric_existed_still_loads(tmp_path):
    """Adding a metric must not invalidate hours of measured results."""
    path = tmp_path / "report.json"
    legacy = {
        "model": "old", "embedding_model": "old", "candidate_limit": 3,
        "generated_at": "2026-09-08T00:00:00+00:00",
        "issues": [
            {
                "issue_id": "a", "repo": "org/repo", "status": "completed",
                "outcome": "validated_fix", "failure_category": None, "attempts": 1,
                "top1_hit": True, "top3_hit": True, "generation_ms": 1, "validation_ms": 1,
                "wall_ms": 1, "pr_created": False, "pr_url": None, "error": None,
                "finished_at": "2026-09-08T00:00:00+00:00",
            }
        ],
    }
    path.write_text(json.dumps(legacy))

    report = load_report(path)

    assert report.issues[0].fix_file_top1 is None
    assert report.summary()["validated_fixes"] == 1


def test_a_missing_report_starts_empty_rather_than_failing(tmp_path):
    report = load_report(tmp_path / "nothing.json")

    assert report.issues == []
    assert report.summary()["issues_attempted"] == 0
