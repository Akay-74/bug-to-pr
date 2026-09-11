"""Checkout a repo at an exact commit and index its Python symbols.

No repository code is ever executed here — only `git` (checkout) and the
stdlib `ast` module (parsing) touch the checked-out tree, per docs/phase2.md
§10.
"""
from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from backend.localization.chunking import Chunk, enumerate_python_files, index_files
from backend.tools.git import clone_and_checkout


@dataclass
class RepoIndex:
    repo: str
    commit: str
    chunks: list[Chunk]


@contextmanager
def checked_out_workspace(repo: str, base_commit: str) -> Iterator[Path]:
    workspace = Path(tempfile.mkdtemp(prefix="bug2pr-localize-"))
    try:
        clone_and_checkout(repo, base_commit, workspace)
        yield workspace
    finally:
        _remove_workspace(workspace)


def _remove_workspace(workspace: Path) -> None:
    """Delete a disposable workspace, including anything the sandbox owns.

    A workspace that had dependencies installed in it contains files owned by
    the sandbox's uid, which the host user cannot unlink -- so a plain
    `rmtree(ignore_errors=True)` leaves hundreds of megabytes behind on every
    run. When that happens, the same container uid deletes them.
    """
    shutil.rmtree(workspace, ignore_errors=True)
    if not workspace.exists():
        return

    from backend.tools.sandbox import purge_directory  # imported lazily: indexing needs no Docker

    purge_directory(str(workspace))
    shutil.rmtree(workspace, ignore_errors=True)


def index_workspace(workspace: Path, repo: str, commit: str) -> RepoIndex:
    """Index an already-checked-out worktree (used directly by tests against
    the fixture repo's worktree, and by index_repository below).
    """
    files = enumerate_python_files(workspace)
    chunks = index_files(files, workspace, repo, commit)
    return RepoIndex(repo=repo, commit=commit, chunks=chunks)


def index_repository(repo: str, base_commit: str) -> RepoIndex:
    """Checkout `repo` at `base_commit` into a disposable workspace, index
    it, and clean up. Use this for one-shot indexing; `checked_out_workspace`
    + `index_workspace` if the caller also needs the checked-out files (e.g.
    lexical retrieval needs `rg` to search the same tree).
    """
    with checked_out_workspace(repo, base_commit) as workspace:
        return index_workspace(workspace, repo, base_commit)
