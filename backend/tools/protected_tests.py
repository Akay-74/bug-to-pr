"""Protected-test-file modification detection.

Given a benchmark's explicit `protected_test_paths`, determine whether an
agent's changes touched any of them. Later agent verification calls this
before trusting a "green" test run.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProtectionCheckResult:
    modified: bool
    changed_files: list[str]


def _git_changed_files(worktree: Path, base_commit: str) -> list[str]:
    """Files that differ between base_commit and the current worktree state,
    including uncommitted changes and untracked files.
    """
    committed = subprocess.run(
        ["git", "diff", "--name-only", base_commit],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    return sorted(set(f for f in committed + untracked if f))


def is_protected_path(path: str, protected_test_paths: list[str]) -> bool:
    """Whether `path` is, or is inside, one of the protected test paths.

    Public because both the verification check below and fix generation need
    the same answer -- generation to avoid *offering* a protected file as
    editable in the first place (backend/generation/pipeline.py).
    """
    for protected in protected_test_paths:
        protected_norm = protected.rstrip("/")
        if path == protected_norm:
            return True
        if path.startswith(protected_norm + "/"):
            return True
    return False


def check_protected_tests(
    base_commit: str,
    current_worktree: Path,
    protected_test_paths: list[str],
) -> ProtectionCheckResult:
    changed_files = _git_changed_files(current_worktree, base_commit)
    matched = [f for f in changed_files if is_protected_path(f, protected_test_paths)]
    return ProtectionCheckResult(modified=len(matched) > 0, changed_files=matched)
