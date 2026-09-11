"""Patch extraction, validation, and application (docs/phase3.md §2, §3).

The model's raw output is untrusted text: it may wrap the diff in markdown
fences, add commentary, or return something that isn't a diff at all. This
module turns that into either a clean unified diff or a rejection reason,
and applies an accepted diff only inside an isolated, disposable workspace.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_FENCE_RE = re.compile(r"```(?:diff|patch)?\n(.*?)```", re.DOTALL)
_DIFF_START_RE = re.compile(r"^(diff --git |--- )", re.MULTILINE)
_PATH_HEADER_RE = re.compile(r"^(?:---|\+\+\+) (?:a/|b/)?(\S+)", re.MULTILINE)


class MalformedPatchError(Exception):
    """The model's output does not contain a usable unified diff."""


def extract_diff(raw_output: str) -> str:
    """Pull a unified diff out of raw model output.

    Strips a surrounding ```diff fence if present, otherwise looks for the
    first line that looks like a diff header and drops any leading prose.
    Raises MalformedPatchError if nothing diff-shaped is found.
    """
    text = raw_output.strip()
    if not text:
        raise MalformedPatchError("Model returned an empty response")

    fence_match = _FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    start_match = _DIFF_START_RE.search(text)
    if not start_match:
        raise MalformedPatchError("Model output does not contain a unified diff header (---/diff --git)")

    diff = text[start_match.start() :].rstrip() + "\n"
    if not diff.strip():
        raise MalformedPatchError("Extracted diff is empty")
    return diff


def validate_patch_size(diff: str, max_lines: int) -> None:
    line_count = diff.count("\n")
    if line_count > max_lines:
        raise MalformedPatchError(f"Patch has {line_count} lines, exceeding the {max_lines}-line limit")


def _touched_paths(diff: str) -> list[str]:
    return _PATH_HEADER_RE.findall(diff)


def validate_patch_paths(diff: str, protected_test_paths: list[str]) -> None:
    """Reject diffs that escape the workspace or target protected test files.

    A path containing ".." or an absolute path could otherwise make `git
    apply` write outside the checked-out workspace; touching a protected
    test file would let a "passing" run hide a test the agent edited to
    pass rather than a genuine fix (docs/phase3.md §3, §11).
    """
    paths = _touched_paths(diff)
    if not paths:
        raise MalformedPatchError("Diff has no recognizable file headers")

    for path in paths:
        if path == "/dev/null":
            continue
        if path.startswith("/") or ".." in Path(path).parts:
            raise MalformedPatchError(f"Diff touches an unsafe path: {path}")
        for protected in protected_test_paths:
            protected_norm = protected.rstrip("/")
            if path == protected_norm or path.startswith(protected_norm + "/"):
                raise MalformedPatchError(f"Diff modifies a protected test path: {path}")


@dataclass
class ApplyResult:
    success: bool
    error: str = ""


def _git_apply(workspace: Path, diff: str, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "apply", *extra_args, "-"],
        cwd=workspace,
        input=diff,
        capture_output=True,
        text=True,
    )


def apply_patch(workspace: Path, diff: str) -> ApplyResult:
    """Apply `diff` inside `workspace` via `git apply`, after a dry-run
    `--check`. Never touches anything outside `workspace`.

    A diff written directly by a model often carries hunk headers whose line
    counts disagree with the hunk body ("corrupt patch at ..."), so a failed
    check is retried once with --recount, which recomputes those counts from
    the body. That only fixes the bookkeeping: context lines must still match
    the file exactly, so a hallucinated patch is still rejected.
    """
    attempts: list[list[str]] = [[], ["--recount"]]
    error = "git apply --check failed"

    for extra_args in attempts:
        check = _git_apply(workspace, diff, ["--check", *extra_args])
        if check.returncode != 0:
            error = check.stderr.strip() or error
            continue

        applied = _git_apply(workspace, diff, extra_args)
        if applied.returncode != 0:
            return ApplyResult(success=False, error=applied.stderr.strip() or "git apply failed")
        return ApplyResult(success=True)

    return ApplyResult(success=False, error=error)
