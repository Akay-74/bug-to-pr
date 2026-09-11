"""Branch/commit behaviour for a validated fix (docs/phase4.md "Git Workflow").

These run against the fixture repository with the sandbox verification
stubbed, so they exercise the real git operations without paying for a
container per test. The unstubbed path is covered in
tests/test_delivery_pipeline.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.delivery import git_ops
from backend.delivery.git_ops import GitError, branch_name_for, commit_all, create_branch
from tests.conftest import FIXTURE_ISSUE_ID


def _git(worktree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=worktree, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_branch_name_is_unique_per_run_and_ref_safe():
    import uuid

    run_id = uuid.uuid4()
    name = branch_name_for("astropy__astropy-14508", run_id)

    assert name.startswith("bug2pr/")
    assert str(run_id)[:8] in name
    # Must be usable as a git ref.
    assert subprocess.run(["git", "check-ref-format", f"refs/heads/{name}"]).returncode == 0


def test_branch_name_sanitises_characters_git_rejects():
    name = branch_name_for("weird ~issue^:id?", "0123456789abcdef")

    assert subprocess.run(["git", "check-ref-format", f"refs/heads/{name}"]).returncode == 0


def test_create_branch_starts_from_the_checked_out_base_commit(fixture_worktree):
    base = _git(fixture_worktree, "rev-parse", "HEAD")

    create_branch(fixture_worktree, "bug2pr/test-branch")

    assert _git(fixture_worktree, "rev-parse", "--abbrev-ref", "HEAD") == "bug2pr/test-branch"
    assert _git(fixture_worktree, "rev-parse", "HEAD") == base


def test_create_branch_reports_an_existing_branch_rather_than_overwriting(fixture_worktree):
    create_branch(fixture_worktree, "bug2pr/test-branch")
    _git(fixture_worktree, "checkout", "--quiet", "-")

    with pytest.raises(GitError, match="already exists"):
        create_branch(fixture_worktree, "bug2pr/test-branch")


def test_commit_all_records_the_change_with_the_configured_identity(fixture_worktree):
    create_branch(fixture_worktree, "bug2pr/test-branch")
    (fixture_worktree / "mypkg" / "__init__.py").write_text("def add(a, b):\n    return a + b\n")

    result = commit_all(fixture_worktree, "Fix: add() should add\n\nBenchmark-Issue: x\n")

    assert result.sha == _git(fixture_worktree, "rev-parse", "HEAD")
    assert result.files == ["mypkg/__init__.py"]
    assert _git(fixture_worktree, "log", "-1", "--pretty=%an") == "bug2pr agent"
    assert _git(fixture_worktree, "log", "-1", "--pretty=%s") == "Fix: add() should add"


def test_commit_identity_is_not_written_into_the_workspace_config(fixture_worktree):
    """The identity is passed per-invocation, so nothing is left behind in a
    checkout that might outlive the call.
    """
    create_branch(fixture_worktree, "bug2pr/test-branch")
    (fixture_worktree / "mypkg" / "__init__.py").write_text("def add(a, b):\n    return a + b\n")
    commit_all(fixture_worktree, "Fix: add() should add\n")

    config = (fixture_worktree / ".git" / "config").read_text()
    assert "bug2pr agent" not in config
    assert "bug2pr-agent@" not in config


def test_commit_with_nothing_staged_is_an_error_not_an_empty_commit(fixture_worktree):
    create_branch(fixture_worktree, "bug2pr/test-branch")

    with pytest.raises(GitError, match="Nothing to commit"):
        commit_all(fixture_worktree, "Fix: nothing\n")


def test_run_git_surfaces_the_underlying_error(fixture_worktree):
    with pytest.raises(GitError):
        git_ops.run_git(fixture_worktree, ["checkout", "does-not-exist"])


def test_run_git_can_report_failure_without_raising(fixture_worktree):
    result = git_ops.run_git(fixture_worktree, ["rev-parse", "--verify", "--quiet", "refs/heads/nope"], check=False)

    assert result.returncode != 0


def test_committed_paths_lists_only_that_commit(fixture_worktree):
    create_branch(fixture_worktree, "bug2pr/test-branch")
    (fixture_worktree / "mypkg" / "__init__.py").write_text("def add(a, b):\n    return a + b\n")
    first = commit_all(fixture_worktree, "Fix one\n")
    (fixture_worktree / "NOTES.md").write_text("later\n")
    commit_all(fixture_worktree, "Later change\n")

    assert git_ops.committed_paths(fixture_worktree, first.sha) == ["mypkg/__init__.py"]


def test_issue_id_is_available_for_branch_naming():
    assert branch_name_for(FIXTURE_ISSUE_ID, "abcdef123456") == f"bug2pr/{FIXTURE_ISSUE_ID}-abcdef12"
