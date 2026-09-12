"""The local git mirror cache (docs/phase5.md "Performance").

A single workflow run checks out the same commit about four times. These
tests pin the two things that make caching that safe: it returns the exact
commit asked for, and it is an optimisation rather than a dependency -- a
repository that cannot be mirrored still clones the old way.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.tools import git as git_tools
from backend.tools.git import _mirror_path, clone_and_checkout
from tests.conftest import FIXTURE_REPO_PATH


def _head(worktree: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, check=True
    ).stdout.strip()


def _base_commit() -> str:
    return subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=FIXTURE_REPO_PATH, capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture()
def isolated_cache(tmp_path, monkeypatch):
    """Point the mirror cache at a temporary directory so these tests never
    touch the real one.
    """
    monkeypatch.setattr(git_tools.settings, "LOCALIZATION_CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path


def test_clone_checks_out_the_exact_commit(isolated_cache, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    commit = _base_commit()

    clone_and_checkout(str(FIXTURE_REPO_PATH), commit, workspace)

    assert _head(workspace) == commit
    assert (workspace / "mypkg" / "__init__.py").exists()


def test_a_mirror_is_created_and_reused(isolated_cache, tmp_path):
    commit = _base_commit()
    repo = str(FIXTURE_REPO_PATH)

    first = tmp_path / "first"
    first.mkdir()
    clone_and_checkout(repo, commit, first)

    mirror = _mirror_path(repo)
    assert mirror.exists(), "the first clone should have populated a mirror"

    second = tmp_path / "second"
    second.mkdir()
    clone_and_checkout(repo, commit, second)

    assert _head(second) == commit


def test_the_mirror_serves_a_clone_when_the_origin_is_unreachable(isolated_cache, tmp_path):
    """The strongest evidence the mirror is actually used: once it holds the
    commit, a checkout succeeds with the original repository gone.
    """
    origin = tmp_path / "origin"
    shutil.copytree(FIXTURE_REPO_PATH, origin)
    commit = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=origin, capture_output=True, text=True, check=True,
    ).stdout.strip()

    warm = tmp_path / "warm"
    warm.mkdir()
    clone_and_checkout(str(origin), commit, warm)
    assert _head(warm) == commit

    shutil.rmtree(origin)

    after = tmp_path / "after"
    after.mkdir()
    clone_and_checkout(str(origin), commit, after)

    assert _head(after) == commit


def test_a_corrupt_mirror_falls_back_to_the_network_instead_of_failing_the_run(
    isolated_cache, tmp_path
):
    """Regression: a mirror that exists but cannot serve the commit.

    Found during the Phase 5 benchmark evaluation — the mirror is fetched at
    --depth 1000 so it can be shallow, and serving a clone from a shallow
    repository sometimes fails. That turned a working run
    (sympy__sympy-11618) into "Localization failed: git checkout ... exit
    128". A cache must never fail a run that would otherwise succeed.
    """
    commit = _base_commit()
    repo = str(FIXTURE_REPO_PATH)

    # Populate a mirror, then corrupt it so every use of it fails.
    first = tmp_path / "first"
    first.mkdir()
    clone_and_checkout(repo, commit, first)
    mirror = _mirror_path(repo)
    assert mirror.exists()
    shutil.rmtree(mirror / "objects")
    (mirror / "objects").mkdir()

    workspace = tmp_path / "after"
    workspace.mkdir()
    clone_and_checkout(repo, commit, workspace)

    assert _head(workspace) == commit, "should have fallen back to the real repository"


def test_mirror_checkout_reports_failure_rather_than_a_wrong_commit(isolated_cache, tmp_path):
    """Exiting zero is not success: the workspace must be at the commit asked
    for, or the mirror path must decline.
    """
    from backend.tools.git import _try_mirror_checkout

    workspace = tmp_path / "ws"
    workspace.mkdir()

    assert _try_mirror_checkout(str(FIXTURE_REPO_PATH), "0" * 40, workspace) is False


def test_a_repository_that_cannot_be_mirrored_still_raises_rather_than_silently_passing(
    isolated_cache, tmp_path
):
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(subprocess.CalledProcessError):
        clone_and_checkout(str(tmp_path / "does-not-exist"), "0" * 40, workspace)


def test_mirror_path_is_confined_to_the_cache_directory(isolated_cache):
    """A repository name is not a path: it must not steer the mirror out of
    the cache directory.
    """
    cache_root = Path(git_tools.settings.LOCALIZATION_CACHE_DIR).resolve()

    for repo in ("org/repo", "../../etc/evil", "/absolute/path", "a/b/c"):
        resolved = _mirror_path(repo).resolve()
        assert cache_root in resolved.parents, f"{repo} escaped the cache dir"


# --- clone retries ---------------------------------------------------------


def test_a_transient_clone_failure_is_retried(monkeypatch, tmp_path):
    """Regression: three benchmark issues were lost to `git clone` exiting
    128 under memory pressure; the same clone worked when retried.
    """
    import subprocess

    from backend.tools import git as git_mod

    monkeypatch.setattr(git_mod.time, "sleep", lambda _s: None)
    attempts = []

    def flaky_clone(args, **kwargs):
        attempts.append(args)
        if len(attempts) == 1:
            return subprocess.CompletedProcess(args, 128, "", "fatal: early EOF")
        Path(args[-1]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", flaky_clone)

    git_mod._full_clone_with_retries("https://example.test/repo.git", tmp_path / "ws")

    assert len(attempts) == 2


def test_clone_retries_are_bounded_and_raise_the_real_error(monkeypatch, tmp_path):
    import subprocess

    import pytest

    from backend.tools import git as git_mod

    monkeypatch.setattr(git_mod.time, "sleep", lambda _s: None)
    attempts = []

    def always_fails(args, **kwargs):
        attempts.append(args)
        return subprocess.CompletedProcess(args, 128, "", "fatal: could not read from remote")

    monkeypatch.setattr(subprocess, "run", always_fails)

    with pytest.raises(subprocess.CalledProcessError) as info:
        git_mod._full_clone_with_retries("https://example.test/repo.git", tmp_path / "ws")

    assert len(attempts) == len(git_mod._CLONE_BACKOFF_SECONDS)
    assert "could not read from remote" in info.value.stderr
