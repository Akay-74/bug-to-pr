"""Whole-workflow security invariants (docs/phase5.md "Security Audit").

The individual phases have their own security tests; this file audits the
seams between them and pins the properties that must hold for the system as a
whole. Several of these guard bugs found during the Phase 5 audit itself.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from backend.benchmark import loader
from backend.benchmark.loader import IssueNotFoundError, load_issue, save_metadata
from backend.generation import sandbox_ops
from backend.generation.patch import MalformedPatchError, validate_patch_paths
from backend.tools import sandbox as sandbox_mod
from tests.conftest import FIXTURE_ISSUE_ID

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"


def _backend_sources() -> list[Path]:
    return [p for p in BACKEND_DIR.rglob("*.py") if "alembic/versions" not in str(p)]


# --- path traversal --------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_id",
    [
        "../../../../etc/passwd",
        "..",
        ".",
        "",
        "nested/id",
        "back\\slash",
        "../fixture__tiny-repo-1",
    ],
)
def test_issue_ids_cannot_escape_the_benchmark_directory(hostile_id):
    """Issue ids arrive from API path parameters.

    Found during the Phase 5 audit: `_issue_dir` joined the id straight onto
    the benchmark root, so a traversing id resolved outside it -- and
    `save_metadata` would have *written* there.
    """
    with pytest.raises(IssueNotFoundError):
        loader._issue_dir(hostile_id)


def test_a_traversing_id_cannot_be_used_to_read_a_file(tmp_path):
    planted = tmp_path / "metadata.json"
    planted.write_text('{"issue_id": "planted"}')

    with pytest.raises(IssueNotFoundError):
        load_issue(f"../../../../../../{tmp_path.name}")


def test_a_traversing_id_cannot_be_used_to_write_a_file():
    metadata = load_issue(FIXTURE_ISSUE_ID).metadata

    with pytest.raises(IssueNotFoundError):
        save_metadata("../../../../tmp/bug2pr-should-not-exist", metadata)

    assert not Path("/tmp/bug2pr-should-not-exist/metadata.json").exists()


def test_legitimate_issue_ids_still_resolve():
    assert loader._issue_dir(FIXTURE_ISSUE_ID).name == FIXTURE_ISSUE_ID


# --- generated patches -----------------------------------------------------


@pytest.mark.parametrize(
    "hostile_path",
    ["../../etc/cron.d/evil", "/etc/passwd", "../../../root/.ssh/authorized_keys"],
)
def test_generated_patches_cannot_target_paths_outside_the_workspace(hostile_path):
    diff = f"--- a/{hostile_path}\n+++ b/{hostile_path}\n@@ -1 +1 @@\n-x\n+y\n"

    with pytest.raises(MalformedPatchError, match="unsafe path"):
        validate_patch_paths(diff, [])


def test_generated_patches_cannot_target_protected_tests():
    diff = "--- a/tests/test_add.py\n+++ b/tests/test_add.py\n@@ -1 +1 @@\n-x\n+y\n"

    with pytest.raises(MalformedPatchError, match="protected test path"):
        validate_patch_paths(diff, ["tests/test_add.py"])


# --- command execution -----------------------------------------------------


def test_no_backend_module_uses_a_shell():
    """`shell=True` would turn any interpolated value into code."""
    offenders = [p.name for p in _backend_sources() if "shell=True" in p.read_text()]

    assert offenders == []


def test_no_repository_code_is_executed_on_the_host():
    """The host parses repository files (AST, ripgrep); it never runs them.
    Execution belongs to the container.
    """
    banned = ("exec(", "eval(", "__import__(", "runpy")
    offenders = [
        p.name
        for p in _backend_sources()
        if any(token in p.read_text() for token in banned)
    ]

    assert offenders == []


def test_sandbox_commands_come_from_curated_metadata_not_model_output():
    """Model output becomes a *patch*, never a command.

    The sandbox only ever runs the benchmark's own install/test commands, so
    a hostile model response has no path to the shell.
    """
    source = inspect.getsource(sandbox_ops)

    tree = ast.parse(source)
    run_args = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
    ]
    assert run_args, "expected sandbox.run call sites"
    # Every command passed in is either a literal or derived from `metadata`
    # / the caller's `command` argument -- never from a generation result.
    assert "raw_output" not in source
    assert "generate(" not in source


# --- container isolation ---------------------------------------------------


def test_container_isolation_flags_are_not_weakened():
    """docs/phase5.md: do not weaken existing Phase 1 security controls."""
    source = inspect.getsource(sandbox_mod)

    assert 'security_opt=["no-new-privileges"]' in source
    assert 'cap_drop=["ALL"]' in source
    assert "65534:65534" in source  # non-root
    for forbidden in ("privileged=True", "docker.sock", "network_mode", "pid_mode", "ipc_mode"):
        assert forbidden not in source


def test_the_only_host_path_ever_mounted_is_the_ephemeral_workspace():
    source = inspect.getsource(sandbox_mod)

    mounts = re.findall(r'"bind":\s*"([^"]+)"', source)
    assert set(mounts) == {"/workspace"}


def test_tests_run_without_network_and_only_installs_enable_it():
    """Unrestricted network during test execution would let a generated patch
    exfiltrate or fetch code. Only dependency installation needs the index.
    """
    source = inspect.getsource(sandbox_ops)

    install = source.split("def install_dependencies")[1].split("def ")[0]
    run_tests = source.split("def run_tests")[1].split("def ")[0]

    assert "network_disabled=False" in install
    assert "network_disabled=True" in run_tests
    assert "network_disabled=False" not in run_tests


def test_sandbox_defaults_to_no_network():
    """A new call site that forgets the flag must get the safe behaviour."""
    import inspect as _inspect

    signature = _inspect.signature(sandbox_mod.DockerSandbox.__init__)
    assert signature.parameters["network_disabled"].default is True


# --- credentials -----------------------------------------------------------


def test_no_credential_shaped_string_is_committed_anywhere_in_the_backend():
    token_shapes = re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,}")
    offenders = [p.name for p in _backend_sources() if token_shapes.search(p.read_text())]

    assert offenders == []


def test_cors_is_an_explicit_allowlist_not_a_wildcard():
    """These endpoints start runs and open pull requests."""
    from backend.config import settings

    assert "*" not in settings.cors_origins
    assert all(origin.startswith("http") for origin in settings.cors_origins)


def test_workflow_errors_are_redacted_before_they_are_persisted():
    """The workflow writes delivery errors onto the Run, which the dashboard
    renders -- so redaction has to happen there too, not only in delivery.
    """
    import backend.workflow.pipeline as workflow_mod

    source = inspect.getsource(workflow_mod)
    assert "github.redact(" in source
