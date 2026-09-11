"""Disposable workspaces are actually disposed of (docs/phase5.md "Reliability").

Regression: a workspace that had dependencies installed in it contains files
owned by the sandbox's uid (65534), which the host user cannot unlink. The
teardown used `shutil.rmtree(ignore_errors=True)`, so those directories were
silently left behind -- 150-350 MB per workflow run. During the Phase 5
benchmark evaluation this accumulated 243 leaked workspaces and filled /tmp,
which then broke unrelated commands.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from backend.localization import indexer
from backend.localization.indexer import _remove_workspace


def test_an_ordinary_workspace_is_removed_without_starting_a_container(tmp_path, monkeypatch):
    """The fallback is for the awkward case only; normal teardown must not
    pay for a container.
    """
    import backend.tools.sandbox as sandbox_mod

    def fail(*args, **kwargs):
        raise AssertionError("purge_directory should not be needed here")

    monkeypatch.setattr(sandbox_mod, "purge_directory", fail)

    workspace = tmp_path / "ws"
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "mod.py").write_text("x = 1\n")

    _remove_workspace(workspace)

    assert not workspace.exists()


def test_a_workspace_the_host_cannot_delete_falls_back_to_the_container(tmp_path, monkeypatch):
    """Simulates the sandbox-owned tree: a directory whose contents cannot be
    unlinked because the parent is not writable.
    """
    import backend.tools.sandbox as sandbox_mod

    workspace = tmp_path / "ws"
    stubborn = workspace / "venv"
    stubborn.mkdir(parents=True)
    (stubborn / "pyvenv.cfg").write_text("home = /usr\n")
    stubborn.chmod(0o555)  # cannot unlink pyvenv.cfg through this directory

    called: list[str] = []

    def fake_purge(path: str, image: str = "python:3.11-slim") -> bool:
        # What the real container does, as the uid that owns the files.
        called.append(path)
        Path(path, "venv").chmod(0o755)
        return True

    monkeypatch.setattr(sandbox_mod, "purge_directory", fake_purge)

    try:
        _remove_workspace(workspace)
    finally:
        if stubborn.exists():
            stubborn.chmod(0o755)

    assert called == [str(workspace)], "the container fallback should have run"
    assert not workspace.exists()


def test_cleanup_failure_never_raises(tmp_path, monkeypatch):
    """A workspace that cannot be purged is a disk-space problem, not a
    reason to fail the run that produced it.
    """
    import backend.tools.sandbox as sandbox_mod

    workspace = tmp_path / "ws"
    stubborn = workspace / "venv"
    stubborn.mkdir(parents=True)
    (stubborn / "file").write_text("x")
    stubborn.chmod(0o555)

    monkeypatch.setattr(sandbox_mod, "purge_directory", lambda path, image="x": False)

    try:
        _remove_workspace(workspace)  # must not raise
    finally:
        stubborn.chmod(0o755)


def test_the_purge_container_keeps_every_sandbox_restriction():
    """The cleanup path must not become a hole in the sandbox model."""
    import backend.tools.sandbox as sandbox_mod

    source = inspect.getsource(sandbox_mod.purge_directory)

    assert "network_disabled=True" in source
    assert 'cap_drop=["ALL"]' in source
    assert 'security_opt=["no-new-privileges"]' in source
    assert "user=_SANDBOX_UID" in source  # never root
    assert "privileged" not in source
    assert "docker.sock" not in source


def test_checked_out_workspace_uses_the_thorough_cleanup():
    source = inspect.getsource(indexer.checked_out_workspace)

    assert "_remove_workspace" in source
