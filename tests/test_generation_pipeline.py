import numpy as np
import pytest

from backend.benchmark.loader import IssueNotFoundError, load_issue
from backend.generation import sandbox_ops as sandbox_ops_mod
from backend.generation.pipeline import NoLocalizationResultsError, run_fix_generation
from backend.localization.service import RepositoryNotAllowedError
from backend.models.attempt import Attempt, AttemptStatus, FailureType
from backend.models.run import RunStatus
from backend.tools.sandbox import ExecutionResult
from tests.conftest import FIXTURE_ISSUE_ID

_GOOD_DIFF = """diff --git a/mypkg/__init__.py b/mypkg/__init__.py
--- a/mypkg/__init__.py
+++ b/mypkg/__init__.py
@@ -1,3 +1,3 @@
 def add(a, b):
     \"\"\"Deliberately wrong: subtracts instead of adding, for the fixture bug.\"\"\"
-    return a - b
+    return a + b
"""

_UNAPPLIABLE_DIFF = _GOOD_DIFF.replace("return a - b", "this line does not exist in the file")

# What the prompt actually asks a model for (backend/generation/prompting.py):
# SEARCH/REPLACE blocks, which the pipeline resolves into a unified diff.
_GOOD_EDIT_BLOCK = """mypkg/__init__.py
<<<<<<< SEARCH
    return a - b
=======
    return a + b
>>>>>>> REPLACE
"""

# The correct fix, but with a hunk header claiming 5 context lines when the
# body has 3 -- the "corrupt patch" shape models emit constantly, which
# `git apply --recount` rescues.
_MISCOUNTED_DIFF = """--- a/mypkg/__init__.py
+++ b/mypkg/__init__.py
@@ -1,5 +1,5 @@
 def add(a, b):
     \"\"\"Deliberately wrong: subtracts instead of adding, for the fixture bug.\"\"\"
-    return a - b
+    return a + b
"""

_INEFFECTIVE_DIFF = """diff --git a/mypkg/__init__.py b/mypkg/__init__.py
--- a/mypkg/__init__.py
+++ b/mypkg/__init__.py
@@ -1,3 +1,3 @@
 def add(a, b):
-    \"\"\"Deliberately wrong: subtracts instead of adding, for the fixture bug.\"\"\"
+    \"\"\"Updated docstring, bug not actually fixed.\"\"\"
     return a - b
"""


class FakeEmbeddingBackend:
    model_id = "fake-embedding-backend"

    def embed_documents(self, texts):
        return np.array([[float(len(t)), float(t.count("add"))] for t in texts])

    def embed_query(self, text):
        return np.array([float(len(text)), float(text.count("add"))])


class FakeGenerationBackend:
    model_id = "fake-generation-backend"

    def __init__(self, diffs: list[str]):
        self.diffs = diffs
        self.calls = 0

    def generate(self, prompt: str, timeout: int) -> str:
        diff = self.diffs[min(self.calls, len(self.diffs) - 1)]
        self.calls += 1
        return diff


def _run(db_session, diffs, candidate_limit=3):
    return run_fix_generation(
        FIXTURE_ISSUE_ID,
        db_session,
        generation_backend=FakeGenerationBackend(diffs),
        embedding_backend=FakeEmbeddingBackend(),
        candidate_limit=candidate_limit,
    )


def _attempts(db_session, run):
    return db_session.query(Attempt).filter(Attempt.run_id == run.id).order_by(Attempt.attempt_number).all()


def test_successful_fix_is_recorded_and_run_completed(db_session):
    run = _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert run.status == RunStatus.COMPLETED
    assert run.attempts_taken == 1

    attempts = _attempts(db_session, run)
    assert len(attempts) == 1
    assert attempts[0].status == AttemptStatus.PASSED
    assert attempts[0].failure_type == FailureType.NONE
    assert "return a + b" in attempts[0].diff

    checks = {v.check_name: v for v in attempts[0].verification_results}
    assert checks["regression_test"].status.value == "passed"
    assert checks["patch_apply"].status.value == "passed"


def test_search_replace_blocks_are_resolved_into_a_patch_and_validated(db_session):
    run = _run(db_session, [_GOOD_EDIT_BLOCK], candidate_limit=1)

    assert run.status == RunStatus.COMPLETED
    attempts = _attempts(db_session, run)
    assert attempts[0].status == AttemptStatus.PASSED
    # What is persisted is a real unified diff, not the block the model wrote.
    assert attempts[0].diff.startswith("--- a/mypkg/__init__.py")
    assert "+    return a + b" in attempts[0].diff
    assert "SEARCH" not in attempts[0].diff


def test_search_replace_with_unmatchable_search_text_is_a_generation_error(db_session):
    block = _GOOD_EDIT_BLOCK.replace("    return a - b", "    return a * b")

    run = _run(db_session, [block], candidate_limit=1)

    assert run.status == RunStatus.FAILED
    attempts = _attempts(db_session, run)
    assert attempts[0].failure_type == FailureType.GENERATION_ERROR
    assert attempts[0].diff is None


def test_miscounted_hunk_header_is_recovered_by_recount(db_session):
    run = _run(db_session, [_MISCOUNTED_DIFF], candidate_limit=1)

    assert run.status == RunStatus.COMPLETED
    assert _attempts(db_session, run)[0].status == AttemptStatus.PASSED


def test_malformed_generation_falls_through_to_next_candidate(db_session):
    run = _run(db_session, ["I refuse to write a diff, sorry.", _GOOD_DIFF], candidate_limit=2)

    assert run.status == RunStatus.COMPLETED
    assert run.attempts_taken == 2

    attempts = _attempts(db_session, run)
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].failure_type == FailureType.GENERATION_ERROR
    assert attempts[1].status == AttemptStatus.PASSED


def test_unappliable_patch_recorded_as_patch_application_error(db_session):
    run = _run(db_session, [_UNAPPLIABLE_DIFF], candidate_limit=1)

    assert run.status == RunStatus.FAILED
    attempts = _attempts(db_session, run)
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].failure_type == FailureType.PATCH_APPLICATION_ERROR
    apply_check = next(v for v in attempts[0].verification_results if v.check_name == "patch_apply")
    assert apply_check.status.value == "error"


def test_patch_that_does_not_fix_the_bug_recorded_as_verification_failed(db_session):
    run = _run(db_session, [_INEFFECTIVE_DIFF], candidate_limit=1)

    assert run.status == RunStatus.FAILED
    attempts = _attempts(db_session, run)
    assert attempts[0].status == AttemptStatus.FAILED
    assert attempts[0].failure_type == FailureType.VERIFICATION_FAILED
    regression_check = next(v for v in attempts[0].verification_results if v.check_name == "regression_test")
    assert regression_check.status.value == "failed"


def test_exhausting_candidate_limit_without_success_marks_run_failed(db_session):
    run = _run(db_session, [_INEFFECTIVE_DIFF, _INEFFECTIVE_DIFF], candidate_limit=2)

    assert run.status == RunStatus.FAILED
    assert run.attempts_taken == 2
    assert len(_attempts(db_session, run)) == 2


def test_environment_failure_stops_the_pipeline_without_burning_all_candidates(db_session, monkeypatch):
    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=1, stdout="", stderr="pip explosion", duration=0.1, timed_out=False),
    )

    run = _run(db_session, [_GOOD_DIFF, _GOOD_DIFF, _GOOD_DIFF], candidate_limit=3)

    assert run.status == RunStatus.FAILED
    assert run.attempts_taken == 1
    attempts = _attempts(db_session, run)
    assert attempts[0].failure_type == FailureType.ENVIRONMENT_ERROR


def test_patch_that_breaks_a_previously_passing_test_is_rejected(db_session, monkeypatch):
    # Force a known baseline regardless of what other test modules (e.g.
    # test_benchmark_validation.py) may have already written to the shared
    # fixture issue's metadata.json -- this test's "new failure" detection
    # must not depend on suite-wide execution order.
    import backend.generation.pipeline as pipeline_mod

    clean_issue = load_issue(FIXTURE_ISSUE_ID)
    clean_issue.metadata.baseline_failures = []
    monkeypatch.setattr(pipeline_mod, "load_issue", lambda issue_id: clean_issue)
    # test_add_negative passed at base_commit; test_add (the regression) did not.
    monkeypatch.setattr(
        pipeline_mod, "measure_baseline_failures", lambda issue: {"tests/test_add.py::test_add"}
    )

    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=False),
    )

    def fake_run_tests(metadata, workspace, command):
        if command == metadata.regression_test_command:
            return ExecutionResult(exit_code=0, stdout="1 passed", stderr="", duration=0.1, timed_out=False)
        return ExecutionResult(
            exit_code=1, stdout="FAILED tests/test_add.py::test_add_negative", stderr="", duration=0.1, timed_out=False
        )

    monkeypatch.setattr(sandbox_ops_mod, "run_tests", fake_run_tests)

    run = _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert run.status == RunStatus.FAILED
    attempts = _attempts(db_session, run)
    assert attempts[0].failure_type == FailureType.VERIFICATION_FAILED
    relevant_check = next(v for v in attempts[0].verification_results if v.check_name == "relevant_tests")
    assert relevant_check.status.value == "failed"


def test_no_localization_results_raises(db_session, monkeypatch):
    import backend.generation.pipeline as pipeline_mod
    from backend.localization.service import LocalizationOutcome

    monkeypatch.setattr(
        pipeline_mod,
        "localize_issue",
        lambda issue_id, db, backend: LocalizationOutcome(
            issue_id=issue_id, model_identifier="fake", index_identifier="fake", results=[]
        ),
    )

    with pytest.raises(NoLocalizationResultsError):
        _run(db_session, [_GOOD_DIFF])


def test_protected_test_files_are_never_offered_as_editable_context(db_session, monkeypatch):
    """A localizer ranks the failing test highly -- it reads most like the
    issue. Offering it as editable invites the model to edit the assertion
    instead of the bug, which the protected-test check then rejects, burning
    every candidate. So it is filtered out before the prompt is built.
    """
    import backend.generation.pipeline as pipeline_mod

    prompts: list[str] = []

    class PromptCapturingBackend(FakeGenerationBackend):
        def generate(self, prompt: str, timeout: int) -> str:
            prompts.append(prompt)
            return super().generate(prompt, timeout)

    run_fix_generation(
        FIXTURE_ISSUE_ID,
        db_session,
        generation_backend=PromptCapturingBackend([_GOOD_EDIT_BLOCK]),
        embedding_backend=FakeEmbeddingBackend(),
        candidate_limit=1,
    )

    assert prompts, "the pipeline should have called the model"
    editable_section = prompts[0].split("## Editable files")[1].split("##")[0]
    assert "tests/test_add.py" not in editable_section
    assert "mypkg/__init__.py" in editable_section


def test_run_is_refused_when_every_candidate_is_a_protected_test(db_session, monkeypatch):
    import backend.generation.pipeline as pipeline_mod
    from backend.localization.service import LocalizationCandidate, LocalizationOutcome

    only_tests = LocalizationOutcome(
        issue_id=FIXTURE_ISSUE_ID,
        model_identifier="fake",
        index_identifier="fake",
        results=[
            LocalizationCandidate(
                rank=1, file="tests/test_add.py", symbol="test_add", symbol_type="function",
                start_line=1, end_line=5, semantic_score=1.0, lexical_score=1.0,
                relevance_score=1.0, final_score=1.0, matched_terms=["add"],
            )
        ],
    )
    monkeypatch.setattr(pipeline_mod, "get_localization", lambda issue_id, db: only_tests)

    with pytest.raises(NoLocalizationResultsError, match="protected test file"):
        _run(db_session, [_GOOD_DIFF])


def test_unknown_issue_raises_issue_not_found(db_session):
    with pytest.raises(IssueNotFoundError):
        run_fix_generation(
            "does-not-exist",
            db_session,
            generation_backend=FakeGenerationBackend([_GOOD_DIFF]),
            embedding_backend=FakeEmbeddingBackend(),
        )


def test_unallowlisted_repository_is_rejected(db_session, monkeypatch):
    import backend.generation.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "is_repo_allowed", lambda repo: False)

    with pytest.raises(RepositoryNotAllowedError):
        _run(db_session, [_GOOD_DIFF])


def test_repeated_identical_candidate_is_rejected_without_revalidating(db_session, monkeypatch):
    """A model that regenerates the same rejected patch must not cost a
    second clone/install/test cycle (docs/phase3.md §5).
    """
    runs: list[str] = []

    def counting_run_tests(metadata, workspace, command):
        runs.append(command)
        return ExecutionResult(
            exit_code=1, stdout="FAILED tests/test_add.py::test_add - still broken\n1 failed",
            stderr="", duration=0.1, timed_out=False,
        )

    import backend.generation.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "measure_baseline_failures", lambda issue: set())
    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=False),
    )
    monkeypatch.setattr(sandbox_ops_mod, "run_tests", counting_run_tests)

    run = _run(db_session, [_GOOD_DIFF, _GOOD_DIFF], candidate_limit=2)

    assert run.status == RunStatus.FAILED
    attempts = _attempts(db_session, run)
    assert attempts[0].failure_type == FailureType.VERIFICATION_FAILED
    assert attempts[1].failure_type == FailureType.GENERATION_ERROR
    # Only the first candidate reached the sandbox.
    assert len(runs) == 1


def test_failing_test_output_is_fed_back_into_the_next_prompt(db_session, monkeypatch):
    import backend.generation.pipeline as pipeline_mod

    prompts: list[str] = []

    class RecordingBackend(FakeGenerationBackend):
        def generate(self, prompt, timeout):
            prompts.append(prompt)
            return super().generate(prompt, timeout)

    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=False),
    )
    monkeypatch.setattr(
        sandbox_ops_mod,
        "run_tests",
        lambda metadata, workspace, command: ExecutionResult(
            exit_code=1,
            stdout="FAILED tests/test_add.py::test_add\nE   AssertionError: distance ignored the z axis\n1 failed",
            stderr="", duration=0.1, timed_out=False,
        ),
    )
    monkeypatch.setattr(pipeline_mod, "measure_baseline_failures", lambda issue: set())

    run_fix_generation(
        FIXTURE_ISSUE_ID,
        db_session,
        generation_backend=RecordingBackend([_GOOD_DIFF, _INEFFECTIVE_DIFF]),
        embedding_backend=FakeEmbeddingBackend(),
        candidate_limit=2,
    )

    assert len(prompts) == 2
    assert "distance ignored the z axis" in prompts[1]


def test_preexisting_unrelated_failure_does_not_reject_a_correct_patch(db_session, monkeypatch):
    """The regression command usually runs a whole test file. A failure that
    was already there at base_commit must not make every candidate fail
    (docs/phase3.md §4.2/§4.3).
    """
    import backend.generation.pipeline as pipeline_mod

    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=False),
    )
    # Baseline: an unrelated test is already broken, and so is the regression test.
    monkeypatch.setattr(
        pipeline_mod,
        "measure_baseline_failures",
        lambda issue: {"tests/test_add.py::test_unrelated"},
    )
    # After the patch: the regression test passes, the unrelated one still
    # fails, and the command still exits non-zero because of it.
    monkeypatch.setattr(
        sandbox_ops_mod,
        "run_tests",
        lambda metadata, workspace, command: ExecutionResult(
            exit_code=1,
            stdout="FAILED tests/test_add.py::test_unrelated - pre-existing\n1 failed, 1 passed",
            stderr="",
            duration=0.1,
            timed_out=False,
        ),
    )

    run = _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert run.status == RunStatus.COMPLETED
    assert _attempts(db_session, run)[0].status == AttemptStatus.PASSED


def test_regression_test_still_failing_is_a_verification_failure(db_session, monkeypatch):
    import backend.generation.pipeline as pipeline_mod

    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=False),
    )
    monkeypatch.setattr(pipeline_mod, "measure_baseline_failures", lambda issue: set())
    monkeypatch.setattr(
        sandbox_ops_mod,
        "run_tests",
        lambda metadata, workspace, command: ExecutionResult(
            exit_code=1, stdout="FAILED tests/test_add.py::test_add - still broken\n1 failed", stderr="",
            duration=0.1, timed_out=False,
        ),
    )

    run = _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert run.status == RunStatus.FAILED
    assert _attempts(db_session, run)[0].failure_type == FailureType.VERIFICATION_FAILED


def test_test_harness_crash_is_an_environment_failure_not_a_test_failure(db_session, monkeypatch):
    import backend.generation.pipeline as pipeline_mod

    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=False),
    )
    monkeypatch.setattr(pipeline_mod, "measure_baseline_failures", lambda issue: set())
    monkeypatch.setattr(
        sandbox_ops_mod,
        "run_tests",
        lambda metadata, workspace, command: ExecutionResult(
            exit_code=139, stdout="", stderr="Segmentation fault (core dumped)", duration=0.1, timed_out=False,
        ),
    )

    run = _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert run.status == RunStatus.FAILED
    assert _attempts(db_session, run)[0].failure_type == FailureType.ENVIRONMENT_ERROR


def test_benchmark_test_patch_is_applied_before_tests_run(db_session, monkeypatch, tmp_path):
    """The regression test only exists in the benchmark's test patch, so the
    pipeline must add it to the workspace (docs/phase3.md §4.2).
    """
    import backend.generation.pipeline as pipeline_mod

    applied: list[str] = []

    monkeypatch.setattr(
        sandbox_ops_mod,
        "apply_test_patch",
        lambda issue_dir, workspace: applied.append(str(workspace)) or "",
    )
    monkeypatch.setattr(
        sandbox_ops_mod,
        "install_dependencies",
        lambda metadata, workspace: ExecutionResult(exit_code=0, stdout="", stderr="", duration=0.1, timed_out=False),
    )
    monkeypatch.setattr(pipeline_mod, "measure_baseline_failures", lambda issue: set())
    monkeypatch.setattr(
        sandbox_ops_mod,
        "run_tests",
        lambda metadata, workspace, command: ExecutionResult(
            exit_code=0, stdout="1 passed", stderr="", duration=0.1, timed_out=False
        ),
    )

    _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert len(applied) == 1


def test_test_patch_that_cannot_be_applied_is_an_environment_failure(db_session, monkeypatch):
    import backend.generation.pipeline as pipeline_mod

    monkeypatch.setattr(sandbox_ops_mod, "apply_test_patch", lambda issue_dir, workspace: "patch does not apply")
    monkeypatch.setattr(pipeline_mod, "measure_baseline_failures", lambda issue: set())

    run = _run(db_session, [_GOOD_DIFF], candidate_limit=1)

    assert run.status == RunStatus.FAILED
    assert _attempts(db_session, run)[0].failure_type == FailureType.ENVIRONMENT_ERROR
