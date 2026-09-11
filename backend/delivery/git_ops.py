"""Branch/commit for a validated fix (docs/phase4.md "Git Workflow").

Everything here happens inside a disposable clone checked out at the
benchmark's exact base commit -- the same isolation Phase 3 applies to
candidate validation, for the same reason: the user's own checkout must
never be a participant in the workflow, so there is no path by which a
generated patch reaches it.

No repository code is executed here. This module runs `git` only; the final
verification that gates the commit runs in the Phase 1/3 Docker sandbox
(backend/delivery/pipeline.py).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.config import settings

__all__ = [
    "GitError",
    "CommitResult",
    "branch_name_for",
    "create_branch",
    "commit_all",
    "current_commit_sha",
    "committed_paths",
    "run_git",
]

_UNSAFE_REF_CHARS = re.compile(r"[^A-Za-z0-9._/-]+")


class GitError(Exception):
    """A git command failed."""


def run_git(workspace: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one git command inside `workspace`.

    `cwd` is always the disposable workspace, so a relative path in any
    argument resolves there rather than in the server's working directory.
    """
    result = subprocess.run(
        ["git", *args], cwd=workspace, capture_output=True, text=True
    )
    if check and result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def branch_name_for(issue_id: str, run_id) -> str:
    """A branch name that is unique per delivery and safe as a git ref.

    The run id is included so re-delivering the same issue cannot collide
    with an earlier branch on the remote (docs/phase4.md: branch conflicts
    are an error worth reporting, not something to silently overwrite).
    """
    slug = _UNSAFE_REF_CHARS.sub("-", issue_id).strip("-/.") or "issue"
    return f"{settings.GITHUB_BRANCH_PREFIX}/{slug}-{str(run_id)[:8]}"


def create_branch(workspace: Path, branch: str) -> None:
    """Cut `branch` from whatever is checked out (the base commit)."""
    existing = run_git(workspace, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], check=False)
    if existing.returncode == 0:
        raise GitError(f"Branch '{branch}' already exists in the workspace")
    run_git(workspace, ["checkout", "--quiet", "-b", branch])


def current_commit_sha(workspace: Path) -> str:
    return run_git(workspace, ["rev-parse", "HEAD"]).stdout.strip()


@dataclass
class CommitResult:
    sha: str
    message: str
    files: list[str]


def committed_paths(workspace: Path, sha: str) -> list[str]:
    """Files touched by commit `sha`."""
    result = run_git(workspace, ["show", "--name-only", "--pretty=format:", sha])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def commit_all(workspace: Path, message: str) -> CommitResult:
    """Commit the working tree as the agent.

    Identity is passed per-invocation with `-c` rather than written into the
    workspace's config: the commit is reproducible from the recorded metadata
    and nothing is left behind in a checkout that outlives the call.
    """
    run_git(workspace, ["add", "-A"])

    staged = run_git(workspace, ["diff", "--cached", "--name-only"]).stdout.strip()
    if not staged:
        raise GitError("Nothing to commit: the patch left the working tree unchanged")

    run_git(
        workspace,
        [
            "-c", f"user.name={settings.GIT_AUTHOR_NAME}",
            "-c", f"user.email={settings.GIT_AUTHOR_EMAIL}",
            "commit", "--quiet", "--no-verify", "-m", message,
        ],
    )
    sha = current_commit_sha(workspace)
    return CommitResult(sha=sha, message=message, files=committed_paths(workspace, sha))
