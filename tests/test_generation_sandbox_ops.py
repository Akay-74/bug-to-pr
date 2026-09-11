from backend.generation.sandbox_ops import new_failures, venv_cmd
from backend.tools.sandbox import ExecutionResult


def _result(stdout: str, exit_code: int = 1) -> ExecutionResult:
    return ExecutionResult(exit_code=exit_code, stdout=stdout, stderr="", duration=0.1, timed_out=False)


def test_new_failures_excludes_known_baseline_failures():
    result = _result("FAILED tests/test_add.py::test_add\nFAILED tests/test_add.py::test_add_negative")

    regressions = new_failures(
        result,
        baseline_failure_ids={"tests/test_add.py::test_add", "tests/test_add.py::test_add_negative"},
        regression_test_ids=set(),
    )

    assert regressions == set()


def test_new_failures_excludes_the_regression_tests_being_fixed():
    result = _result("FAILED tests/test_add.py::test_add")

    regressions = new_failures(result, baseline_failure_ids=set(), regression_test_ids={"tests/test_add.py::test_add"})

    assert regressions == set()


def test_new_failures_detects_a_previously_passing_test_now_broken():
    result = _result("FAILED tests/test_add.py::test_add_negative")

    regressions = new_failures(
        result,
        baseline_failure_ids={"tests/test_add.py::test_add"},
        regression_test_ids={"tests/test_add.py::test_add"},
    )

    assert regressions == {"tests/test_add.py::test_add_negative"}


def test_new_failures_empty_when_everything_passes():
    result = _result("5 passed", exit_code=0)

    regressions = new_failures(result, baseline_failure_ids=set(), regression_test_ids=set())

    assert regressions == set()


def test_venv_cmd_prefixes_path_and_workdir():
    cmd = venv_cmd("pytest -q")
    assert "/workspace/.venv/bin" in cmd
    assert "cd /workspace" in cmd
    assert cmd.endswith("pytest -q")


_SYMPY_OUTPUT = """\
________________________________________________________________________________
_______________ sympy/geometry/tests/test_point.py:test_Point2D ________________
  File "/workspace/sympy/geometry/tests/test_point.py", line 236, in test_Point2D
RecursionError: maximum recursion depth exceeded

====== tests finished: 5 passed, 1 failed, 1 exceptions, in 0.14 seconds =======
"""

_PYTEST_OUTPUT = "FAILED tests/test_add.py::test_add - assert 1 == 3\n1 failed in 0.01s\n"


def test_parse_failing_tests_reads_the_sympy_runner_banner():
    from backend.generation.sandbox_ops import parse_failing_tests

    assert parse_failing_tests(_SYMPY_OUTPUT) == {"sympy/geometry/tests/test_point.py:test_Point2D"}


def test_parse_failing_tests_still_reads_pytest_summaries():
    from backend.generation.sandbox_ops import parse_failing_tests

    assert parse_failing_tests(_PYTEST_OUTPUT) == {"tests/test_add.py::test_add"}


def test_matches_test_id_across_id_granularities():
    from backend.generation.sandbox_ops import matches_test_id

    assert matches_test_id("sympy/geometry/tests/test_point.py:test_issue_11617", "test_issue_11617")
    assert matches_test_id("tests/test_add.py::test_add", "tests/test_add.py::test_add")
    assert not matches_test_id("sympy/geometry/tests/test_point.py:test_Point2D", "test_issue_11617")


def test_harness_ran_distinguishes_a_crash_from_a_test_failure():
    from backend.generation.sandbox_ops import harness_ran

    assert harness_ran(_SYMPY_OUTPUT)
    assert harness_ran(_PYTEST_OUTPUT)
    assert not harness_ran("Segmentation fault (core dumped)")


def test_still_failing_reports_only_the_wanted_ids():
    from backend.generation.sandbox_ops import still_failing
    from backend.tools.sandbox import ExecutionResult

    result = ExecutionResult(exit_code=1, stdout=_SYMPY_OUTPUT, stderr="", duration=0.1, timed_out=False)

    assert still_failing(result, ["test_Point2D"]) == {"test_Point2D"}
    assert still_failing(result, ["test_issue_11617"]) == set()


def test_new_failures_ignores_a_preexisting_failure_reported_by_a_non_pytest_runner():
    from backend.generation.sandbox_ops import new_failures
    from backend.tools.sandbox import ExecutionResult

    result = ExecutionResult(exit_code=1, stdout=_SYMPY_OUTPUT, stderr="", duration=0.1, timed_out=False)

    assert new_failures(result, {"sympy/geometry/tests/test_point.py:test_Point2D"}, set()) == set()


def test_apply_test_patch_adds_the_regression_test(tmp_path):
    import subprocess

    from backend.generation.sandbox_ops import apply_test_patch

    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "t.py").write_text("a = 1\n")

    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "test_patch.diff").write_text(
        "--- a/t.py\n+++ b/t.py\n@@ -1 +1,2 @@\n a = 1\n+def test_new(): assert a == 1\n"
    )

    assert apply_test_patch(issue_dir, workspace) == ""
    assert "def test_new()" in (workspace / "t.py").read_text()


def test_apply_test_patch_is_a_no_op_when_the_benchmark_has_none(tmp_path):
    from backend.generation.sandbox_ops import apply_test_patch

    assert apply_test_patch(tmp_path, tmp_path) == ""


def test_apply_test_patch_reports_a_conflicting_patch(tmp_path):
    import subprocess

    from backend.generation.sandbox_ops import apply_test_patch

    workspace = tmp_path / "ws"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    (workspace / "t.py").write_text("something else entirely\n")

    issue_dir = tmp_path / "issue"
    issue_dir.mkdir()
    (issue_dir / "test_patch.diff").write_text("--- a/t.py\n+++ b/t.py\n@@ -1 +1,2 @@\n a = 1\n+b = 2\n")

    assert apply_test_patch(issue_dir, workspace) != ""
