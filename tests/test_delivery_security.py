"""Security invariants for Phase 4 delivery (docs/phase4.md "Security").

No tokens in source, no secrets in logs or persisted errors, no credentials
reachable from the sandbox, and the Phase 1/3 container restrictions
unchanged by anything Phase 4 added.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from backend.delivery import git_ops, github, pipeline as delivery_pipeline
from backend.generation import sandbox_ops
from tests.test_delivery_pipeline import _validated_run

_BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
_FAKE_TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"


class _RecordingSandbox:
    """DockerSandbox stand-in that records how it was constructed and run."""

    instances: list[dict] = []
    commands: list[dict] = []

    def __init__(self, image, cpu_limit, memory_limit, workspace=None, network_disabled=True, client=None):
        self.kwargs = dict(
            image=image, cpu_limit=cpu_limit, memory_limit=memory_limit,
            workspace=workspace, network_disabled=network_disabled, client=client,
        )
        _RecordingSandbox.instances.append(self.kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, command, timeout, workdir="/workspace", user=None):
        from backend.tools.sandbox import ExecutionResult

        _RecordingSandbox.commands.append({"command": command, "workdir": workdir, "user": user})
        return ExecutionResult(exit_code=0, stdout="1 passed", stderr="", duration=0.0, timed_out=False)


def setup_function():
    _RecordingSandbox.instances.clear()
    _RecordingSandbox.commands.clear()


# --- no credentials in source ---------------------------------------------


def test_no_github_token_is_hardcoded_anywhere_in_the_backend():
    """docs/phase4.md: never store GitHub tokens in source."""
    token_shapes = re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}")

    offenders = [
        path
        for path in _BACKEND_DIR.rglob("*.py")
        if token_shapes.search(path.read_text(errors="ignore"))
    ]

    assert offenders == []


def test_delivery_code_never_reads_a_token_from_the_environment():
    """Auth is delegated to the gh CLI's own credential store, so no module
    here has any reason to read the process environment -- which is where a
    token would have to come from.

    Checked against the parsed source rather than the text, so the modules
    stay free to *document* that they never touch GITHUB_TOKEN.
    """
    import ast

    for module in (github, git_ops, delivery_pipeline):
        tree = ast.parse(inspect.getsource(module))
        reads = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv")
        ]
        assert reads == [], f"{module.__name__} reads the environment"


def test_no_token_setting_exists_in_configuration():
    from backend.config import Settings

    assert [name for name in Settings.model_fields if "TOKEN" in name.upper()] == []


def test_env_example_documents_no_credential():
    env_example = (_BACKEND_DIR.parent / ".env.example").read_text()

    assert "GITHUB_TOKEN" not in env_example
    assert re.search(r"gh[pousr]_[A-Za-z0-9]{16,}", env_example) is None


# --- credentials never reach the sandbox -----------------------------------


def test_final_verification_uses_the_unchanged_sandbox_restrictions(monkeypatch, db_session):
    """docs/phase4.md: keep sandbox network restrictions unchanged. The
    delivery's verification must run tests exactly as Phase 3 does --
    network disabled, workspace-only mount, no pre-authenticated client.
    """
    monkeypatch.setattr(sandbox_ops, "DockerSandbox", _RecordingSandbox)
    from backend.benchmark.loader import load_issue
    from backend.localization.indexer import checked_out_workspace
    from tests.conftest import FIXTURE_ISSUE_ID

    issue = load_issue(FIXTURE_ISSUE_ID)
    with checked_out_workspace(issue.metadata.repo, issue.metadata.base_commit) as workspace:
        delivery_pipeline.verify_committed_tree(issue, workspace)

        install, tests = _RecordingSandbox.instances[0], _RecordingSandbox.instances[-1]
        # Installing needs the package index; running the candidate's tests
        # does not, and must not.
        assert install["network_disabled"] is False
        assert tests["network_disabled"] is True
        for call in _RecordingSandbox.instances:
            assert call["workspace"] == str(workspace)
            assert call["client"] is None


def test_no_credential_is_passed_into_any_sandbox_command(monkeypatch, db_session):
    """Generated repository code must not receive GitHub credentials -- it
    runs in a container that is given a workspace and nothing else.
    """
    monkeypatch.setattr(sandbox_ops, "DockerSandbox", _RecordingSandbox)
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")

    from backend.benchmark.loader import load_issue
    from backend.localization.indexer import checked_out_workspace
    from tests.conftest import FIXTURE_ISSUE_ID

    issue = load_issue(FIXTURE_ISSUE_ID)
    with checked_out_workspace(issue.metadata.repo, issue.metadata.base_commit) as workspace:
        delivery_pipeline.verify_committed_tree(issue, workspace)

    for call in _RecordingSandbox.commands:
        lowered = call["command"].lower()
        for forbidden in ("token", "credential", "gh auth", "password", "authorization"):
            assert forbidden not in lowered


def test_the_sandbox_is_never_handed_the_docker_socket_or_a_host_path(monkeypatch, db_session):
    monkeypatch.setattr(sandbox_ops, "DockerSandbox", _RecordingSandbox)
    from backend.benchmark.loader import load_issue
    from backend.localization.indexer import checked_out_workspace
    from tests.conftest import FIXTURE_ISSUE_ID

    issue = load_issue(FIXTURE_ISSUE_ID)
    with checked_out_workspace(issue.metadata.repo, issue.metadata.base_commit) as workspace:
        delivery_pipeline.verify_committed_tree(issue, workspace)

    for call in _RecordingSandbox.instances:
        assert "docker.sock" not in str(call["workspace"])
        # The only path ever mounted is the disposable workspace, never the
        # project checkout or a broader host directory.
        assert str(call["workspace"]).startswith("/tmp/")


# --- credentials never reach the workspace or the database -----------------


def test_push_leaves_no_credential_in_the_workspace_git_config(monkeypatch, fixture_worktree):
    """The credential helper is configured per-invocation with `-c`, so the
    checkout never gains a stored credential.
    """
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", True)
    monkeypatch.setattr(github.settings, "GITHUB_TARGET_REPO", "someone/safe-test-repo")
    monkeypatch.setattr(
        github.subprocess, "run", lambda args, **kwargs: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    )

    github.push_branch(fixture_worktree, "bug2pr/x-1234", "someone/safe-test-repo")

    config = (fixture_worktree / ".git" / "config").read_text()
    assert "credential" not in config
    assert "token" not in config.lower()


def test_a_delivery_error_is_redacted_before_it_is_stored(db_session, monkeypatch):
    monkeypatch.setattr(
        delivery_pipeline,
        "verify_committed_tree",
        lambda issue, workspace: delivery_pipeline._Verification(
            passed=False, command="pytest", detail=f"failed while using {_FAKE_TOKEN}"
        ),
    )
    run = _validated_run(db_session)

    with pytest.raises(delivery_pipeline.VerificationFailedError):
        delivery_pipeline.deliver_fix(run, db_session, create_pr=False)

    delivery = delivery_pipeline.get_delivery(run.id, db_session)
    assert _FAKE_TOKEN not in delivery.error
    assert "[REDACTED]" in delivery.error


def test_no_repository_code_is_executed_on_the_host_during_delivery():
    """docs/phase3.md §11 carried into Phase 4: the host runs git, and the
    sandbox runs the repository's tests. `git_ops` must therefore invoke
    nothing but git.
    """
    source = inspect.getsource(git_ops)

    executables = set(re.findall(r'subprocess\.run\(\s*\[\s*"([^"]+)"', source))
    assert executables == {"git"}


def test_delivery_never_pushes_while_github_is_disabled(db_session, monkeypatch):
    """The single most important negative: opt-in is enforced in the module
    that would do the pushing, not only by the caller.
    """
    monkeypatch.setattr(github.settings, "GITHUB_ENABLED", False)

    with pytest.raises(github.GitHubAuthError):
        github.push_branch(Path("/tmp"), "bug2pr/x", "someone/safe-test-repo")
    with pytest.raises(github.GitHubAuthError):
        github.create_draft_pr("someone/safe-test-repo", head="h", base="main", title="t", body="b")
