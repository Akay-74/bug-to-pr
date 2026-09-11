from backend.tools.protected_tests import check_protected_tests
from tests.conftest import _fixture_repo_base_commit


def test_unrelated_file_modification_allowed(fixture_worktree):
    (fixture_worktree / "mypkg" / "__init__.py").write_text(
        "def add(a, b):\n    return a + b\n"
    )
    result = check_protected_tests(
        base_commit=_fixture_repo_base_commit(),
        current_worktree=fixture_worktree,
        protected_test_paths=["tests/test_add.py"],
    )
    assert result.modified is False
    assert result.changed_files == []


def test_protected_file_modification_detected(fixture_worktree):
    (fixture_worktree / "tests" / "test_add.py").write_text("def test_add():\n    assert True\n")
    result = check_protected_tests(
        base_commit=_fixture_repo_base_commit(),
        current_worktree=fixture_worktree,
        protected_test_paths=["tests/test_add.py"],
    )
    assert result.modified is True
    assert result.changed_files == ["tests/test_add.py"]


def test_multiple_protected_paths_handled(fixture_worktree):
    (fixture_worktree / "tests" / "test_add.py").write_text("def test_add():\n    assert True\n")
    (fixture_worktree / "mypkg" / "__init__.py").write_text("def add(a, b):\n    return a + b\n")
    (fixture_worktree / "new_test.py").write_text("def test_new():\n    assert True\n")

    result = check_protected_tests(
        base_commit=_fixture_repo_base_commit(),
        current_worktree=fixture_worktree,
        protected_test_paths=["tests/test_add.py", "new_test.py"],
    )
    assert result.modified is True
    assert sorted(result.changed_files) == ["new_test.py", "tests/test_add.py"]
