"""GitHub operations, entirely mocked (docs/phase4.md "Tests").

No test here reaches the network or the real `gh` binary: every subprocess
call is intercepted, so the suite runs identically on a machine with no
GitHub access at all.
"""
from __future__ import annotations

import subprocess

import pytest

from backend.delivery import github
from backend.delivery.github import (
    BranchConflictError,
    GitHubAuthError,
    GitHubError,
    GitHubPermissionError,
    PullRequestCreationError,
    check_auth,
    create_draft_pr,
    get_pr_status,
    push_branch,
    redact,
)

# A token-shaped string that must never survive into an error, a log line, or
# the database. Not a real credential.
_FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture()
def github_enabled(monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")
    return github.settings


def _stub_run(monkeypatch, handler):
    """Replace subprocess.run inside backend.delivery.github."""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return handler(list(args), kwargs)

    monkeypatch.setattr(github.subprocess, "run", fake_run)
    return calls


def test_delivery_is_refused_while_github_is_disabled(monkeypatch):
    """The explicit opt-in docs/phase4.md requires: nothing is pushed until
    the user turns GitHub access on.
    """
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)

    with pytest.raises(GitHubAuthError, match="disabled"):
        check_auth()


def test_delivery_is_refused_when_no_target_repository_is_configured(monkeypatch):
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "")

    with pytest.raises(GitHubAuthError, match="GITHUB_TARGET_REPO"):
        check_auth()


def test_missing_gh_cli_is_an_authentication_error(monkeypatch, github_enabled):
    def handler(args, kwargs):
        raise FileNotFoundError(args[0])

    _stub_run(monkeypatch, handler)

    with pytest.raises(GitHubAuthError, match="not installed"):
        check_auth()


def test_unauthenticated_gh_is_reported_as_an_authentication_error(monkeypatch, github_enabled):
    def handler(args, kwargs):
        return _FakeCompleted(returncode=1, stderr="You are not logged into any GitHub hosts. Run gh auth login")

    _stub_run(monkeypatch, handler)

    with pytest.raises(GitHubAuthError, match="not logged into"):
        check_auth()


def test_successful_auth_returns_the_account_login(monkeypatch, github_enabled):
    def handler(args, kwargs):
        if args[1:3] == ["auth", "status"]:
            return _FakeCompleted(returncode=0, stderr="Logged in to github.com as octocat")
        return _FakeCompleted(returncode=0, stdout="octocat\n")

    _stub_run(monkeypatch, handler)

    assert check_auth() == "octocat"


def test_successful_draft_pr_returns_url_and_number(monkeypatch, github_enabled):
    calls = _stub_run(
        monkeypatch,
        lambda args, kwargs: _FakeCompleted(
            returncode=0, stdout="https://github.com/someone/safe-test-repo/pull/7\n"
        ),
    )

    pr = create_draft_pr(
        "someone/safe-test-repo", head="bug2pr/x-1234", base="main", title="Fix: thing", body="body"
    )

    assert pr.url == "https://github.com/someone/safe-test-repo/pull/7"
    assert pr.number == 7
    assert pr.is_draft is True
    # docs/phase4.md: the PR must be created as a draft, never merged.
    assert "--draft" in calls[0]
    assert not any("merge" in part for part in calls[0])


def test_pr_creation_failure_is_reported(monkeypatch, github_enabled):
    _stub_run(
        monkeypatch,
        lambda args, kwargs: _FakeCompleted(returncode=1, stderr="pull request create failed: validation failed"),
    )

    with pytest.raises(PullRequestCreationError, match="validation failed"):
        create_draft_pr("someone/safe-test-repo", head="h", base="main", title="t", body="b")


def test_permission_failure_is_distinguished_from_a_generic_failure(monkeypatch, github_enabled):
    _stub_run(
        monkeypatch,
        lambda args, kwargs: _FakeCompleted(returncode=1, stderr="HTTP 403: Resource not accessible by integration"),
    )

    with pytest.raises(GitHubPermissionError):
        create_draft_pr("someone/safe-test-repo", head="h", base="main", title="t", body="b")


def test_pr_create_reporting_success_without_a_url_is_an_error(monkeypatch, github_enabled):
    _stub_run(monkeypatch, lambda args, kwargs: _FakeCompleted(returncode=0, stdout="done\n"))

    with pytest.raises(PullRequestCreationError, match="no PR URL"):
        create_draft_pr("someone/safe-test-repo", head="h", base="main", title="t", body="b")


def test_push_uses_the_gh_credential_helper_and_never_a_token_in_the_url(monkeypatch, github_enabled, tmp_path):
    calls = _stub_run(monkeypatch, lambda args, kwargs: _FakeCompleted(returncode=0))

    push_branch(tmp_path, "bug2pr/x-1234", "someone/safe-test-repo")

    pushed = calls[0]
    assert pushed[0] == "git"
    assert any(part.startswith("credential.helper=!") and "auth git-credential" in part for part in pushed)
    remote = next(part for part in pushed if part.startswith("https://"))
    assert remote == "https://github.com/someone/safe-test-repo.git"
    assert "@" not in remote  # no user:token@host form


def test_push_rejection_is_reported_as_a_branch_conflict(monkeypatch, github_enabled, tmp_path):
    _stub_run(
        monkeypatch,
        lambda args, kwargs: _FakeCompleted(
            returncode=1, stderr="! [rejected] bug2pr/x -> bug2pr/x (non-fast-forward)"
        ),
    )

    with pytest.raises(BranchConflictError):
        push_branch(tmp_path, "bug2pr/x-1234", "someone/safe-test-repo")


def test_pr_status_is_read_back_from_gh(monkeypatch, github_enabled):
    _stub_run(
        monkeypatch,
        lambda args, kwargs: _FakeCompleted(
            returncode=0,
            stdout='{"url":"https://github.com/o/r/pull/7","number":7,"state":"OPEN","isDraft":true}',
        ),
    )

    status = get_pr_status("o/r", "https://github.com/o/r/pull/7")

    assert (status.number, status.state, status.is_draft) == (7, "OPEN", True)


def test_unparseable_pr_status_is_an_error_not_a_silent_default(monkeypatch, github_enabled):
    _stub_run(monkeypatch, lambda args, kwargs: _FakeCompleted(returncode=0, stdout="not json"))

    with pytest.raises(GitHubError, match="Could not parse"):
        get_pr_status("o/r", "7")


def test_command_timeout_is_surfaced(monkeypatch, github_enabled):
    def handler(args, kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=1)

    _stub_run(monkeypatch, handler)

    with pytest.raises(GitHubError, match="timed out"):
        check_auth()


# --- secret handling -------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        f"fatal: could not read Password for 'https://{_FAKE_TOKEN}@github.com'",
        f"Authorization: token {_FAKE_TOKEN}",
        "remote: https://x-access-token:s3cr3t-value@github.com/o/r.git",
        "github_pat_11ABCDEFG0aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789",
    ],
)
def test_redact_masks_credential_shaped_strings(text):
    cleaned = redact(text)

    assert "[REDACTED]" in cleaned
    assert _FAKE_TOKEN not in cleaned
    assert "s3cr3t-value" not in cleaned
    assert "github_pat_11ABCDEFG0aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789" not in cleaned


def test_a_token_echoed_by_gh_never_reaches_the_raised_error(monkeypatch, github_enabled):
    """The failure path is exactly where credentials tend to leak: gh prints
    the URL it tried, and that error is persisted and returned by the API.
    """
    _stub_run(
        monkeypatch,
        lambda args, kwargs: _FakeCompleted(
            returncode=1, stderr=f"failed to authenticate with {_FAKE_TOKEN}"
        ),
    )

    with pytest.raises(GitHubError) as excinfo:
        create_draft_pr("someone/safe-test-repo", head="h", base="main", title="t", body="b")

    assert _FAKE_TOKEN not in str(excinfo.value)
    assert "[REDACTED]" in str(excinfo.value)


def test_a_token_echoed_by_git_push_never_reaches_the_raised_error(monkeypatch, github_enabled, tmp_path):
    _stub_run(
        monkeypatch,
        lambda args, kwargs: _FakeCompleted(
            returncode=1, stderr=f"remote: Invalid username or password: {_FAKE_TOKEN}"
        ),
    )

    with pytest.raises(GitHubError) as excinfo:
        push_branch(tmp_path, "bug2pr/x-1234", "someone/safe-test-repo")

    assert _FAKE_TOKEN not in str(excinfo.value)


def test_no_github_token_setting_exists_to_be_stored_in_source():
    """docs/phase4.md "Security": never store GitHub tokens in source. There
    is deliberately no token setting to populate.
    """
    from backend.config import Settings

    token_fields = [name for name in Settings.model_fields if "TOKEN" in name.upper()]
    assert token_fields == []
