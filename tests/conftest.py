import json
import os
import subprocess
from pathlib import Path

TEST_DATABASE_URL = "postgresql+psycopg2://postgres:postgres@localhost:5432/bug2pr_test"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURE_REPO_PATH = FIXTURES_DIR / "fixture_repo"
FIXTURE_ISSUES_DIR = FIXTURES_DIR / "issues"
FIXTURE_ISSUE_ID = "fixture__tiny-repo-1"
FIXTURE_REPO_NAME = str(FIXTURE_REPO_PATH.resolve())

os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL)
os.environ.setdefault("REPOSITORY_ALLOWLIST", str(FIXTURES_DIR / "allowlist.json"))
os.environ.setdefault("BENCHMARK_PATH", str(FIXTURE_ISSUES_DIR))

import psycopg2  # noqa: E402
import pytest  # noqa: E402
from psycopg2 import errors as pg_errors  # noqa: E402

from tests.fixtures.build_fixture_repo import ensure_fixture_repo  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.models.base import Base  # noqa: E402
import backend.models  # noqa: F401,E402  (register all model classes on Base.metadata)
from backend.models.database import SessionLocal, engine  # noqa: E402


def _fixture_repo_base_commit() -> str:
    """The root commit: `add()` is buggy (subtracts instead of adding)."""
    return subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        cwd=FIXTURE_REPO_PATH,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _fixture_repo_fixed_commit() -> str:
    """HEAD: `add()` is correct."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=FIXTURE_REPO_PATH, capture_output=True, text=True, check=True
    ).stdout.strip()


def _write_fixture_allowlist_and_issue() -> None:
    """Regenerate the test allowlist/issue metadata so the absolute path to
    fixture_repo is always correct, regardless of where this repo is checked
    out.
    """
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    (FIXTURES_DIR / "allowlist.json").write_text(
        json.dumps({"repositories": [FIXTURE_REPO_NAME]}, indent=2) + "\n"
    )

    issue_dir = FIXTURE_ISSUES_DIR / FIXTURE_ISSUE_ID
    issue_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "issue_id": FIXTURE_ISSUE_ID,
        "issue_url": "https://example.invalid/fixture/tiny-repo/issues/1",
        "repo": FIXTURE_REPO_NAME,
        "base_commit": _fixture_repo_base_commit(),
        "python_version": "3.11",
        "install_command": "python -m pip install pytest -e .",
        "regression_test_command": "pytest -rA tests/test_add.py::test_add",
        "regression_test_ids": ["tests/test_add.py::test_add"],
        "relevant_test_command": "pytest -rA tests/test_add.py",
        "full_test_command": "pytest -rA",
        "protected_test_paths": ["tests/test_add.py"],
        "baseline_failures": [],
        "validation_status": "pending",
    }
    (issue_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (issue_dir / "problem.md").write_text("`add(a, b)` returns `a - b` instead of `a + b`.\n")


# fixture_repo is not committed (it is a git repository of its own); build it
# on a fresh checkout before anything reads its commits.
ensure_fixture_repo()
_write_fixture_allowlist_and_issue()


def _ensure_test_database() -> None:
    admin_conn = psycopg2.connect(
        host="localhost", port=5432, user="postgres", password="postgres", dbname="postgres"
    )
    admin_conn.autocommit = True
    try:
        with admin_conn.cursor() as cur:
            cur.execute("CREATE DATABASE bug2pr_test")
    except pg_errors.DuplicateDatabase:
        pass
    finally:
        admin_conn.close()


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    _ensure_test_database()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def _fresh_fixture_issue():
    """Every test starts with the fixture issue in its initial `pending` state.

    Validation writes its result back into the issue's metadata.json, so
    without this a test that validates the fixture would leak `valid` into
    every test after it -- and results would depend on which tests happened
    to run together (e.g. all at once in CI versus one file at a time).
    """
    _write_fixture_allowlist_and_issue()


@pytest.fixture()
def fixture_worktree(tmp_path):
    """A disposable clone of fixture_repo, checked out at its base commit,
    so tests can make local modifications without touching the source
    fixture used by other tests.
    """
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "clone", "--quiet", str(FIXTURE_REPO_PATH), str(worktree)], check=True)
    subprocess.run(
        ["git", "checkout", "--quiet", _fixture_repo_base_commit()], cwd=worktree, check=True
    )
    return worktree


@pytest.fixture()
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()
