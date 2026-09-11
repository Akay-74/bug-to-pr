"""GitHub access for draft PR creation (docs/phase4.md "GitHub", "Security").

Auth is delegated entirely to the `gh` CLI's own credential store. This
project never reads, holds, forwards or persists a token: there is no
GITHUB_TOKEN setting, the push uses gh's git-credential helper rather than a
URL with a token in it, and every subprocess stream that could echo a
credential is passed through `redact()` before it is returned to a caller or
written to the database.

Nothing here is reachable from the sandbox: these commands run on the host,
against a workspace directory, and the sandbox is never given an environment
(backend/tools/sandbox.py) -- so generated repository code cannot see
credentials even indirectly.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from backend.config import settings

__all__ = [
    "GitHubError",
    "GitHubAuthError",
    "GitHubPermissionError",
    "BranchConflictError",
    "PullRequestCreationError",
    "DraftPullRequest",
    "PullRequestStatus",
    "redact",
    "check_auth",
    "push_branch",
    "create_draft_pr",
    "get_pr_status",
]

# Token shapes GitHub issues, plus a generic "x-access-token:<secret>@" that
# can appear in a remote URL if one is ever configured by hand.
_SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"//[^/\s:@]+:[^/\s@]+@"),
    re.compile(r"\b[A-Fa-f0-9]{40}\b(?=[^\s]*@)"),
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),  # Google / Gemini API keys
]

_PERMISSION_MARKERS = (
    "permission denied",
    "must have admin rights",
    "resource not accessible",
    "not authorized",
    "403",
)
_AUTH_MARKERS = (
    "not logged into",
    "authentication failed",
    "gh auth login",
    "no such remote",
    "could not determine",
    "bad credentials",
    "401",
)


class GitHubError(Exception):
    """A GitHub operation failed."""


class GitHubAuthError(GitHubError):
    """GitHub access is not configured, or `gh` is not authenticated."""


class GitHubPermissionError(GitHubError):
    """Authenticated, but not allowed to perform this operation."""


class BranchConflictError(GitHubError):
    """The branch already exists on the remote, or the push was rejected."""


class PullRequestCreationError(GitHubError):
    """The draft PR could not be created."""


def redact(text: str) -> str:
    """Mask anything credential-shaped.

    Applied to every stdout/stderr string that leaves this module, so a
    token echoed by a failing command cannot reach a log line, an API error
    body, or the pull_requests.error column.

    The configured model API key is also removed by exact value, since
    provider key formats change and a pattern alone may not recognise it.
    """
    api_key = settings.GENERATION_API_KEY.get_secret_value()
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=settings.GITHUB_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise GitHubAuthError(
            f"GitHub CLI '{args[0]}' is not installed; cannot authenticate to GitHub"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GitHubError(f"GitHub command timed out after {settings.GITHUB_TIMEOUT_SECONDS}s") from exc

    result.stdout = redact(result.stdout or "")
    result.stderr = redact(result.stderr or "")
    return result


def _classify(stderr: str, default: type[GitHubError]) -> GitHubError:
    lowered = stderr.lower()
    if any(marker in lowered for marker in _PERMISSION_MARKERS):
        return GitHubPermissionError(stderr)
    if any(marker in lowered for marker in _AUTH_MARKERS):
        return GitHubAuthError(stderr)
    return default(stderr)


def require_enabled() -> None:
    """Refuse to touch GitHub unless the user opted in explicitly.

    docs/phase4.md: "Do not push anything unless the user has explicitly
    configured/authenticated GitHub access for the workflow."
    """
    if not settings.GITHUB_ENABLED:
        raise GitHubAuthError(
            "GitHub delivery is disabled. Set GITHUB_ENABLED=true and authenticate "
            "the gh CLI (`gh auth login`) to push branches and open draft PRs."
        )
    if not settings.GITHUB_TARGET_REPO:
        raise GitHubAuthError(
            "GITHUB_TARGET_REPO is not configured; refusing to guess where to push."
        )


def check_auth() -> str:
    """Confirm `gh` is installed and authenticated. Returns the account login."""
    require_enabled()
    result = _run([settings.GITHUB_CLI, "auth", "status"])
    if result.returncode != 0:
        raise GitHubAuthError(
            result.stderr.strip() or "gh is not authenticated; run `gh auth login`"
        )

    who = _run([settings.GITHUB_CLI, "api", "user", "--jq", ".login"])
    if who.returncode != 0:
        raise _classify(who.stderr.strip(), GitHubAuthError)
    return who.stdout.strip()


def push_branch(workspace: Path, branch: str, target_repo: str) -> None:
    """Push `branch` to `target_repo`.

    Credentials come from gh's git-credential helper, configured for this one
    invocation with `-c`: no token is read by this process, none is written
    into the workspace's git config, and none appears in the remote URL.
    """
    require_enabled()
    remote_url = f"https://github.com/{target_repo}.git"
    result = subprocess.run(
        [
            "git",
            "-c", f"credential.helper=!{settings.GITHUB_CLI} auth git-credential",
            "push", remote_url, f"{branch}:{branch}",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=settings.GITHUB_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        stderr = redact(result.stderr or "").strip()
        lowered = stderr.lower()
        if "already exists" in lowered or "non-fast-forward" in lowered or "rejected" in lowered:
            raise BranchConflictError(stderr)
        raise _classify(stderr, GitHubError)


@dataclass
class DraftPullRequest:
    url: str
    number: int | None
    is_draft: bool
    state: str


def _pr_number_from_url(url: str) -> int | None:
    match = re.search(r"/pull/(\d+)", url)
    return int(match.group(1)) if match else None


def create_draft_pr(target_repo: str, head: str, base: str, title: str, body: str) -> DraftPullRequest:
    """Open a draft PR. Never merges, never marks ready for review."""
    require_enabled()
    result = _run(
        [
            settings.GITHUB_CLI, "pr", "create",
            "--repo", target_repo,
            "--head", head,
            "--base", base,
            "--title", title,
            "--body", body,
            "--draft",
        ]
    )
    if result.returncode != 0:
        raise _classify(result.stderr.strip(), PullRequestCreationError)

    url = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip().startswith("http")),
        "",
    )
    if not url:
        raise PullRequestCreationError(
            f"gh pr create reported success but returned no PR URL: {result.stdout.strip()!r}"
        )
    return DraftPullRequest(url=url, number=_pr_number_from_url(url), is_draft=True, state="OPEN")


@dataclass
class PullRequestStatus:
    url: str
    number: int | None
    state: str
    is_draft: bool


def get_pr_status(target_repo: str, pr_ref: str) -> PullRequestStatus:
    """Current state of an existing PR (`pr_ref` is its number or URL)."""
    require_enabled()
    result = _run(
        [
            settings.GITHUB_CLI, "pr", "view", pr_ref,
            "--repo", target_repo,
            "--json", "url,number,state,isDraft",
        ]
    )
    if result.returncode != 0:
        raise _classify(result.stderr.strip(), GitHubError)

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GitHubError(f"Could not parse gh pr view output: {result.stdout.strip()!r}") from exc

    return PullRequestStatus(
        url=payload.get("url", ""),
        number=payload.get("number"),
        state=payload.get("state", ""),
        is_draft=bool(payload.get("isDraft", False)),
    )
