"""Build tests/fixtures/fixture_repo: a two-commit git repository the tests run against.

The fixture is a git repository with its own history (a buggy commit, then
the fix), which cannot be committed inside this project's repository -- git
would store it as an empty "embedded repository" pointer. So it is rebuilt
from this script instead, and the tests call `ensure_fixture_repo()` before
they start.

Every commit input (content, author, committer, timestamps, message) is fixed,
so the rebuilt commits get exactly the SHAs that fixture metadata refers to.
`ensure_fixture_repo` checks them and fails loudly if they ever drift.

Run directly to (re)build it:  python tests/fixtures/build_fixture_repo.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

FIXTURE_REPO_PATH = Path(__file__).parent / "fixture_repo"

BASE_COMMIT = "455b03d19a725b5bc08c2f48f2e26c4eb8ed01ca"   # add() is buggy
FIXED_COMMIT = "01c698d23d11585325f3d2758721009210545b92"  # add() is correct

_IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

_BASE_FILES = {
    "mypkg/__init__.py": (
        "def add(a, b):\n"
        '    """Deliberately wrong: subtracts instead of adding, for the fixture bug."""\n'
        "    return a - b\n"
    ),
    "setup.py": (
        "from setuptools import setup, find_packages\n"
        "\n"
        'setup(name="mypkg", version="0.1", packages=find_packages())\n'
    ),
    "tests/test_add.py": (
        "from mypkg import add\n"
        "\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "\n"
        "\n"
        "def test_add_negative():\n"
        "    assert add(-1, -1) == -2\n"
    ),
}

_FIXED_FILES = {
    "mypkg/__init__.py": "def add(a, b):\n    return a + b\n",
}

# (files to write, commit message, unix timestamp) -- timestamps are part of the SHA.
_COMMITS = [
    (_BASE_FILES, "initial buggy version", "1788073543 +0530"),
    (_FIXED_FILES, "fix: correct add() implementation", "1788074465 +0530"),
]


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True
    ).stdout.strip()


def build_fixture_repo(path: Path = FIXTURE_REPO_PATH) -> None:
    """Create the fixture repository from scratch at `path`."""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    # A clean environment, so the user's global git config (signing, hooks,
    # templates) cannot change the commits.
    base_env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(path),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        **_IDENTITY,
    }
    _git(path, "-c", "init.defaultBranch=master", "init", "--quiet", env=base_env)

    for files, message, timestamp in _COMMITS:
        for relative, content in files.items():
            target = path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        env = {**base_env, "GIT_AUTHOR_DATE": timestamp, "GIT_COMMITTER_DATE": timestamp}
        _git(path, "add", "--all", env=env)
        _git(path, "commit", "--quiet", "--no-verify", "--no-gpg-sign", "-m", message, env=env)


def _commits(path: Path) -> list[str]:
    return _git(path, "rev-list", "--reverse", "HEAD").splitlines()


def ensure_fixture_repo(path: Path = FIXTURE_REPO_PATH) -> None:
    """Build the fixture repository if it is missing, and verify its SHAs."""
    if not (path / ".git").exists():
        build_fixture_repo(path)

    commits = _commits(path)
    if commits != [BASE_COMMIT, FIXED_COMMIT]:
        raise RuntimeError(
            f"{path} has commits {commits}, expected {[BASE_COMMIT, FIXED_COMMIT]}. "
            "Delete it and rerun the tests to rebuild it."
        )


if __name__ == "__main__":
    build_fixture_repo()
    ensure_fixture_repo()
    print(f"Built {FIXTURE_REPO_PATH} at {FIXED_COMMIT}")
