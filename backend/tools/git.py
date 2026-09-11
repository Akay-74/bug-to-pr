"""Shared git checkout helper.

Used by both benchmark validation (backend/benchmark/validator.py) and
repository localization (backend/localization/indexer.py) — both need the
same "get this exact commit onto disk, cheaply" operation.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from backend.config import settings


def resolve_clone_source(repo: str) -> str:
    if repo.startswith("file://"):
        return repo[len("file://") :]
    if repo.startswith("/"):
        return repo
    return f"https://github.com/{repo}.git"


def _mirror_path(repo: str) -> Path:
    """Where the local bare mirror for `repo` lives."""
    slug = repo.replace("/", "_").replace(":", "_").strip("._")
    return settings.localization_cache_dir / "git-mirrors" / f"{slug}.git"


def _mirror_source(repo: str, base_commit: str) -> str | None:
    """A local mirror holding `base_commit`, creating or topping it up if needed.

    Measured on this project's benchmark: a single workflow run clones the
    same repository about four times (baseline measurement, source context,
    each candidate's validation, and delivery), and each clone of a real
    benchmark repository costs ~19s of network time -- far more than the
    embedding work the caches already avoid. Fetching once into a bare
    mirror and cloning from disk removes that repetition without changing
    which commit is checked out.

    Returns None if the mirror cannot be prepared, so the caller falls back
    to cloning from the network exactly as before.
    """
    mirror = _mirror_path(repo)
    try:
        if not (mirror / "HEAD").exists():
            mirror.mkdir(parents=True, exist_ok=True)
            init = subprocess.run(
                ["git", "init", "--bare", "--quiet", str(mirror)], capture_output=True, text=True
            )
            if init.returncode != 0:
                return None

        has_commit = subprocess.run(
            ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"],
            cwd=mirror, capture_output=True, text=True,
        )
        if has_commit.returncode != 0:
            fetched = subprocess.run(
                ["git", "fetch", "--quiet", "--depth", "1000", "--tags",
                 resolve_clone_source(repo), base_commit],
                cwd=mirror, capture_output=True, text=True,
            )
            if fetched.returncode != 0:
                return None
            # Keep the fetched objects reachable, otherwise a later `git gc`
            # in this bare repo could drop them.
            subprocess.run(
                ["git", "update-ref", f"refs/bug2pr/{base_commit}", "FETCH_HEAD"],
                cwd=mirror, capture_output=True, text=True,
            )
        return str(mirror)
    except OSError:
        return None


def _try_mirror_checkout(repo: str, base_commit: str, workspace: Path) -> bool:
    """Check `base_commit` out of the local mirror. True only on success.

    Success means the workspace really is at `base_commit` -- not merely that
    the commands exited zero.
    """
    mirror = _mirror_source(repo, base_commit)
    if mirror is None:
        return False

    init = subprocess.run(
        ["git", "init", "--quiet", str(workspace)], capture_output=True, text=True
    )
    if init.returncode != 0:
        return False

    fetched = subprocess.run(
        ["git", "fetch", "--quiet", "--depth", "1000", "--tags", mirror, base_commit],
        cwd=workspace, capture_output=True, text=True,
    )
    if fetched.returncode != 0:
        return False

    checkout = subprocess.run(
        ["git", "checkout", "--quiet", "FETCH_HEAD"], cwd=workspace, capture_output=True, text=True
    )
    if checkout.returncode != 0:
        return False

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=workspace, capture_output=True, text=True
    )
    return head.returncode == 0 and head.stdout.strip() == base_commit


def clone_and_checkout(repo: str, base_commit: str, workspace: Path) -> None:
    source = resolve_clone_source(repo)

    # Prefer a local mirror when one can be prepared: same commit, no network.
    #
    # Strictly best-effort. The mirror is itself fetched at --depth 1000, so
    # it can be a shallow repository, and serving a clone from a shallow repo
    # does not always work -- one sympy commit failed exactly this way during
    # the Phase 5 benchmark evaluation. Every step below is therefore
    # checked, and any disappointment falls through to the network path with
    # a clean workspace, because a cache must never be able to fail a run
    # that would otherwise have succeeded.
    if _try_mirror_checkout(repo, base_commit, workspace):
        return
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)

    # Try a shallow fetch first (fast for large histories like the real
    # benchmark repos); fall back to a full clone for sources that don't
    # support fetching an arbitrary SHA (e.g. local fixture repos).
    #
    # Depth is 1000, not 1, and --tags is included: packages that derive
    # their version from git (setuptools-scm, versioneer, etc) need `git
    # describe` to find a reachable tag ancestor, which a single-commit
    # checkout can never provide regardless of whether tags exist in the
    # repo. 1000 commits comfortably covers the gap to the nearest release
    # tag for these benchmark repos' cadence while staying far cheaper than
    # a full clone.
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True, capture_output=True, text=True)
    shallow = subprocess.run(
        ["git", "fetch", "--quiet", "--depth", "1000", "--tags", source, base_commit],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    if shallow.returncode == 0:
        subprocess.run(
            ["git", "checkout", "--quiet", "FETCH_HEAD"], cwd=workspace, check=True, capture_output=True, text=True
        )
        return

    shutil.rmtree(workspace)
    subprocess.run(
        ["git", "clone", "--quiet", source, str(workspace)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", base_commit],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
