"""Security invariants for Phase 3 fix validation (docs/phase3.md §11):
no Docker socket exposure, network disabled while running candidate tests,
no secrets forwarded into the sandbox, patches confined to an isolated
workspace.
"""
from __future__ import annotations

from backend.benchmark.loader import load_issue
from backend.generation import sandbox_ops
from tests.conftest import FIXTURE_ISSUE_ID


class _RecordingSandbox:
    """Stand-in for DockerSandbox that records constructor kwargs instead of
    starting a real container.
    """

    instances: list[dict] = []

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

        return ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.0, timed_out=False)


def setup_function():
    _RecordingSandbox.instances.clear()


def test_running_candidate_tests_disables_network(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox_ops, "DockerSandbox", _RecordingSandbox)
    metadata = load_issue(FIXTURE_ISSUE_ID).metadata

    sandbox_ops.run_tests(metadata, tmp_path, metadata.regression_test_command)

    assert _RecordingSandbox.instances[-1]["network_disabled"] is True


def test_installing_dependencies_never_forwards_docker_socket_or_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox_ops, "DockerSandbox", _RecordingSandbox)
    metadata = load_issue(FIXTURE_ISSUE_ID).metadata

    sandbox_ops.install_dependencies(metadata, tmp_path)

    for call in _RecordingSandbox.instances:
        # The only bind mount ever configured is the ephemeral workspace
        # directory itself -- never the docker socket, never a broader host
        # path.
        assert call["workspace"] == str(tmp_path)
        assert "docker.sock" not in str(call["workspace"])
        assert call["client"] is None  # no pre-authenticated client with extra credentials threaded through


def test_generation_backend_only_talks_to_configured_local_host():
    from backend.generation.backend import OllamaBackend

    backend = OllamaBackend()
    assert backend.host.startswith("http://localhost") or backend.host.startswith("http://127.0.0.1")


def test_search_replace_edits_cannot_escape_the_offered_context():
    """A SEARCH/REPLACE block naming a path outside the files we supplied --
    a traversal path, an absolute path, or a protected test file -- is
    rejected before any diff exists to apply (docs/phase3.md §3, §11).
    """
    import pytest

    from backend.generation.edits import edits_to_diff, parse_edits
    from backend.generation.patch import MalformedPatchError

    offered = {"mypkg/__init__.py": "value = 1\n"}
    for hostile in ("../../etc/passwd", "/etc/passwd", "tests/test_add.py"):
        raw = f"{hostile}\n<<<<<<< SEARCH\nvalue = 1\n=======\nvalue = 2\n>>>>>>> REPLACE\n"
        # Either the path is not recognised as one of the offered files, or
        # it is recognised and rejected as absent from the context map.
        with pytest.raises(MalformedPatchError):
            edits_to_diff(parse_edits(raw, list(offered) + [hostile]), offered)


def test_pipeline_never_mutates_the_persistent_source_checkout(db_session):
    """docs/phase3.md §3/§11: patches are applied only inside a disposable
    workspace -- the on-disk repository the benchmark points at must come
    out byte-identical, even on a run whose patch applies successfully.
    """
    import subprocess
    from pathlib import Path

    from tests.test_generation_pipeline import _GOOD_DIFF, _run

    checkout = Path(__file__).parent / "fixtures" / "fixture_repo"

    def state():
        return (
            subprocess.run(
                ["git", "status", "--porcelain"], cwd=checkout, capture_output=True, text=True
            ).stdout,
            (checkout / "mypkg" / "__init__.py").read_bytes(),
        )

    before = state()
    _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert state() == before
