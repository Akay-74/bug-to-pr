"""Commit message and draft-PR text (docs/phase4.md "Git Workflow", "GitHub").

Both are built only from what the run actually recorded -- the benchmark
issue, the committed diff, and the verification results Phase 3 persisted --
so a PR cannot describe tests that were never run.
"""
from __future__ import annotations

from backend.benchmark.loader import Issue
from backend.models.attempt import Attempt
from backend.models.verification_result import VerificationStatus

__all__ = ["commit_message", "pr_title", "pr_body"]

_TEST_CHECKS = ("regression_test", "relevant_tests")


def _summary_line(issue: Issue) -> str:
    """The issue's own first line, which is its title in this benchmark."""
    for line in issue.problem.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return f"issue {issue.metadata.issue_id}"


def _changed_files(diff: str) -> list[str]:
    files = []
    for line in diff.splitlines():
        if line.startswith("+++ ") and not line.startswith("+++ /dev/null"):
            path = line[4:].strip()
            path = path[2:] if path.startswith(("a/", "b/")) else path
            if path and path not in files:
                files.append(path)
    return files


def commit_message(issue: Issue, diff: str) -> str:
    """A conventional subject plus the trailers that make the fix reproducible."""
    metadata = issue.metadata
    subject = f"Fix: {_summary_line(issue)}"
    if len(subject) > 72:
        subject = subject[:69].rstrip() + "..."

    files = _changed_files(diff)
    body_lines = [
        subject,
        "",
        f"Addresses benchmark issue {metadata.issue_id}.",
        "",
        "Files changed:",
        *[f"- {path}" for path in files],
        "",
        f"Benchmark-Issue: {metadata.issue_id}",
    ]
    if metadata.issue_url:
        body_lines.append(f"Issue: {metadata.issue_url}")
    body_lines.append(f"Base-Commit: {metadata.base_commit}")
    return "\n".join(body_lines) + "\n"


def pr_title(issue: Issue) -> str:
    title = f"Fix: {_summary_line(issue)}"
    return title if len(title) <= 100 else title[:97].rstrip() + "..."


def _tests_section(attempt: Attempt) -> list[str]:
    """What actually ran, from the persisted verification results."""
    rows = []
    for check in sorted(attempt.verification_results, key=lambda c: c.check_name):
        if check.check_name not in _TEST_CHECKS:
            continue
        mark = "PASS" if check.status is VerificationStatus.PASSED else check.status.value.upper()
        rows.append(f"| `{check.command}` | {mark} | {check.duration_ms} ms |")

    if not rows:
        return ["No test commands were recorded for this fix."]
    return ["| Command | Result | Duration |", "| --- | --- | --- |", *rows]


def pr_body(issue: Issue, attempt: Attempt, diff: str, commit_sha: str, branch: str) -> str:
    """The draft PR description.

    Includes the issue context, the change summary, the tests that ran and
    their result, and the benchmark/base-commit references a reviewer needs
    to reproduce the run (docs/phase4.md "GitHub").
    """
    metadata = issue.metadata
    problem = issue.problem.strip()
    if len(problem) > 4000:
        problem = problem[:4000].rstrip() + "\n\n_(issue text truncated)_"

    files = _changed_files(diff)
    validated = all(
        check.status is VerificationStatus.PASSED for check in attempt.verification_results
    )

    sections = [
        "## Summary",
        "",
        f"Fixes the behaviour reported in benchmark issue `{metadata.issue_id}`.",
        "",
        "Files changed:",
        *[f"- `{path}`" for path in files],
        "",
        "## Issue context",
        "",
        problem or "_No issue description was recorded._",
        "",
        "## Tests run",
        "",
        *_tests_section(attempt),
        "",
        "## Validation result",
        "",
        (
            "The regression test that reproduced this issue fails at the base commit and "
            "passes with this change; the repository's previously-passing tests still pass."
            if validated
            else "Validation did not fully pass; see the run record."
        ),
        "",
        "## Reproduction details",
        "",
        f"- Benchmark issue: `{metadata.issue_id}`",
    ]
    if metadata.issue_url:
        sections.append(f"- Upstream issue: {metadata.issue_url}")
    sections += [
        f"- Base repository: `{metadata.repo}`",
        f"- Base commit: `{metadata.base_commit}`",
        f"- Branch: `{branch}`",
        f"- Commit: `{commit_sha}`",
        f"- Model: `{attempt.model or 'unknown'}`",
        "",
        "> Opened as a draft by an automated bug-to-PR agent. Not for automatic merge.",
    ]
    return "\n".join(sections) + "\n"
