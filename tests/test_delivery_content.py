"""Commit message and draft-PR text (docs/phase4.md "Git Workflow", "GitHub").

The PR body is the only part of this project a human reviewer reads before
the diff, so these tests pin that it reports what actually ran rather than a
generic template.
"""
from __future__ import annotations

from backend.benchmark.loader import load_issue
from backend.delivery.content import commit_message, pr_body, pr_title
from backend.models.attempt import Attempt, AttemptStatus, FailureType
from backend.models.verification_result import VerificationResult, VerificationStatus
from tests.conftest import FIXTURE_ISSUE_ID
from tests.test_generation_pipeline import _GOOD_DIFF


def _attempt(checks=(("regression_test", VerificationStatus.PASSED), ("relevant_tests", VerificationStatus.PASSED))):
    attempt = Attempt(
        attempt_number=1,
        model="qwen2.5-coder:7b",
        status=AttemptStatus.PASSED,
        failure_type=FailureType.NONE,
        diff=_GOOD_DIFF,
    )
    attempt.verification_results = [
        VerificationResult(
            check_name=name,
            status=status,
            command=f"pytest -rA {name}",
            exit_code=0 if status is VerificationStatus.PASSED else 1,
            duration_ms=1500,
        )
        for name, status in checks
    ]
    return attempt


def test_commit_message_has_a_subject_and_reproduction_trailers():
    issue = load_issue(FIXTURE_ISSUE_ID)

    message = commit_message(issue, _GOOD_DIFF)

    subject = message.splitlines()[0]
    assert subject.startswith("Fix: ")
    assert len(subject) <= 72
    assert f"Benchmark-Issue: {FIXTURE_ISSUE_ID}" in message
    assert f"Base-Commit: {issue.metadata.base_commit}" in message
    assert "mypkg/__init__.py" in message


def test_commit_subject_is_truncated_rather_than_wrapped(monkeypatch):
    issue = load_issue(FIXTURE_ISSUE_ID)
    issue.problem = "x" * 200

    subject = commit_message(issue, _GOOD_DIFF).splitlines()[0]

    assert len(subject) <= 72
    assert subject.endswith("...")


def test_pr_title_summarises_the_issue():
    title = pr_title(load_issue(FIXTURE_ISSUE_ID))

    assert title.startswith("Fix: ")
    assert len(title) <= 100


def test_pr_body_reports_the_tests_that_actually_ran():
    issue = load_issue(FIXTURE_ISSUE_ID)

    body = pr_body(issue, _attempt(), _GOOD_DIFF, commit_sha="abc123", branch="bug2pr/x-1")

    assert "## Summary" in body
    assert "## Issue context" in body
    assert "## Tests run" in body
    assert "## Validation result" in body
    assert "pytest -rA regression_test" in body
    assert "PASS" in body
    assert "`mypkg/__init__.py`" in body


def test_pr_body_carries_the_reproduction_references():
    issue = load_issue(FIXTURE_ISSUE_ID)

    body = pr_body(issue, _attempt(), _GOOD_DIFF, commit_sha="abc123", branch="bug2pr/x-1")

    assert FIXTURE_ISSUE_ID in body
    assert issue.metadata.base_commit in body
    assert issue.metadata.repo in body
    assert "abc123" in body
    assert "bug2pr/x-1" in body
    assert "qwen2.5-coder:7b" in body


def test_pr_body_says_it_is_an_automated_draft():
    body = pr_body(load_issue(FIXTURE_ISSUE_ID), _attempt(), _GOOD_DIFF, "abc123", "bug2pr/x-1")

    assert "draft" in body.lower()
    assert "not for automatic merge" in body.lower()


def test_pr_body_does_not_claim_success_when_a_check_failed():
    """A body is built from persisted results, so it cannot advertise a
    validation that did not happen.
    """
    attempt = _attempt(checks=(("regression_test", VerificationStatus.FAILED),))

    body = pr_body(load_issue(FIXTURE_ISSUE_ID), attempt, _GOOD_DIFF, "abc123", "bug2pr/x-1")

    assert "Validation did not fully pass" in body
    assert "FAILED" in body


def test_pr_body_truncates_a_very_long_issue_description():
    issue = load_issue(FIXTURE_ISSUE_ID)
    issue.problem = "y" * 10_000

    body = pr_body(issue, _attempt(), _GOOD_DIFF, "abc123", "bug2pr/x-1")

    assert "issue text truncated" in body
    assert len(body) < 8000
