"""Fix-generation pipeline orchestration (docs/phase3.md).

issue -> Phase 2 localization -> generate patch -> apply in an isolated
workspace -> sandbox-validate -> record, iterating over a bounded number of
candidates and stopping at the first success (docs/phase3.md §1, §5).
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.benchmark.allowlist import is_repo_allowed
from backend.benchmark.loader import Issue, load_issue
from backend.config import settings
from backend.generation import sandbox_ops
from backend.generation.backend import FixGenerationBackend, GenerationTimeoutError, ModelUnavailableError, get_backend
from backend.generation.context import read_candidate_sources, read_files, read_regression_context
from backend.generation.edits import edits_to_diff, parse_edits
from backend.generation.patch import ApplyResult, MalformedPatchError, apply_patch, extract_diff, validate_patch_paths, validate_patch_size
from backend.generation.prompting import build_prompt
from backend.localization import embeddings as embeddings_mod
from backend.localization.indexer import checked_out_workspace
from backend.localization.service import (
    LocalizationCandidate,
    RepositoryNotAllowedError,
    get_localization,
    localize_issue,
)
from backend.models.attempt import Attempt, AttemptStatus, FailureType
from backend.models.run import Run, RunMode, RunStage, RunStatus
from backend.models.verification_result import VerificationResult, VerificationStatus
from backend.tools.protected_tests import check_protected_tests, is_protected_path

__all__ = [
    "NoLocalizationResultsError",
    "run_fix_generation",
    "get_fix",
]


class NoLocalizationResultsError(Exception):
    """Phase 2 localization produced no candidates for this issue."""


@dataclass
class _CandidateOutcome:
    success: bool
    stop_pipeline: bool  # e.g. environment is broken; further candidates won't help


def _record_check(db: Session, attempt: Attempt, check_name: str, status: VerificationStatus, command: str, exit_code: int, stdout: str, stderr: str, duration_ms: int) -> None:
    db.add(
        VerificationResult(
            attempt_id=attempt.id,
            check_name=check_name,
            status=status,
            command=command,
            exit_code=exit_code,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
            duration_ms=duration_ms,
        )
    )


_FEEDBACK_EXCERPT_CHARS = 1500


def _failure_excerpt(result) -> str:
    """The tail of a failed test run, for the next candidate's prompt.

    A bare "the test still fails" note gives the model nothing to react to --
    it just regenerates the same patch. The actual traceback is what makes
    the retry a different attempt rather than a repeat (docs/phase3.md §5).
    """
    output = (result.stdout + "\n" + result.stderr).strip()
    return output[-_FEEDBACK_EXCERPT_CHARS:]


def _fail_attempt(db: Session, attempt: Attempt, failure_type: FailureType) -> None:
    attempt.status = AttemptStatus.FAILED
    attempt.failure_type = failure_type
    attempt.completed_at = datetime.now(timezone.utc)
    db.commit()


def _prompt_metadata(
    prompt: str,
    generation_backend: FixGenerationBackend,
    candidates: list[LocalizationCandidate],
    source_by_file: dict[str, str],
    previous_notes: list[str],
) -> str:
    """Input metadata for one generation call (docs/phase3.md §2).

    The prompt itself is derived deterministically from the issue, its
    localization candidates and the source at base_commit, so a digest plus
    the inputs that produced it is what's worth persisting -- not tens of
    kilobytes of repeated source text.
    """
    return json.dumps(
        {
            "backend": settings.GENERATION_BACKEND,
            "model": generation_backend.model_id,
            "prompt_chars": len(prompt),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "context_files": sorted(source_by_file),
            "localization_targets": [
                f"{c.file}:{c.start_line}-{c.end_line} ({c.symbol_type} {c.symbol})" for c in candidates
            ],
            "previous_attempt_notes": list(previous_notes),
            "timeout_seconds": settings.GENERATION_TIMEOUT_SECONDS,
            "max_patch_lines": settings.GENERATION_MAX_PATCH_LINES,
        },
        indent=2,
    )


def measure_baseline_failures(issue: Issue) -> set[str]:
    """Which tests already fail at base_commit, with the regression test added.

    The benchmark's recorded `baseline_failures` come from Phase 1's pytest
    summary parser, which does not understand every runner the benchmark
    repositories use -- sympy's, for one. A patch must not be blamed for a
    failure that was already there, so the pipeline measures the baseline
    itself, once per run, in the same sandbox the candidates are judged in.

    Returns an empty set if the baseline cannot be established; the run then
    falls back to the recorded metadata alone rather than failing outright.
    """
    metadata = issue.metadata
    command = metadata.relevant_test_command or metadata.full_test_command or metadata.regression_test_command
    if not command:
        return set()

    with checked_out_workspace(metadata.repo, metadata.base_commit) as workspace:
        if sandbox_ops.apply_test_patch(issue.directory, workspace):
            return set()
        sandbox_ops.make_world_writable(workspace)
        if sandbox_ops.install_dependencies(metadata, workspace).exit_code != 0:
            return set()
        result = sandbox_ops.run_tests(metadata, workspace, command)
        if result.timed_out:
            return set()
        return sandbox_ops.parse_failing_tests(result.stdout + result.stderr)


@dataclass
class _Generated:
    diff: str
    prompt_metadata: str
    duration_ms: int


def _generate_candidate_diff(
    issue: Issue,
    candidates: list[LocalizationCandidate],
    source_by_file: dict[str, str],
    file_contents: dict[str, str],
    generation_backend: FixGenerationBackend,
    previous_notes: list[str],
) -> _Generated:
    prompt = build_prompt(
        issue, candidates, source_by_file, previous_notes, regression_info=read_regression_context(issue)
    )
    metadata = _prompt_metadata(prompt, generation_backend, candidates, source_by_file, previous_notes)
    # Timed so "average generation time" is measured rather than estimated
    # (docs/phase5.md "Final Benchmark Evaluation").
    started = time.monotonic()
    raw_output = generation_backend.generate(prompt, timeout=settings.GENERATION_TIMEOUT_SECONDS)
    duration_ms = int((time.monotonic() - started) * 1000)

    # Preferred path: SEARCH/REPLACE blocks resolved into a diff here. If the
    # model emitted a unified diff instead of blocks, take it as one.
    try:
        edits = parse_edits(raw_output, list(file_contents))
    except MalformedPatchError:
        diff = extract_diff(raw_output)
    else:
        diff = edits_to_diff(edits, file_contents)

    validate_patch_size(diff, settings.GENERATION_MAX_PATCH_LINES)
    validate_patch_paths(diff, issue.metadata.protected_test_paths)
    return _Generated(diff=diff, prompt_metadata=metadata, duration_ms=duration_ms)


def _run_one_candidate(
    db: Session,
    issue: Issue,
    candidates: list[LocalizationCandidate],
    source_by_file: dict[str, str],
    file_contents: dict[str, str],
    attempt: Attempt,
    generation_backend: FixGenerationBackend,
    previous_notes: list[str],
    tried_diffs: set[str],
    baseline_failures: set[str],
) -> _CandidateOutcome:
    metadata = issue.metadata

    # 1. Generate. No repository code runs during this step.
    try:
        generated = _generate_candidate_diff(
            issue, candidates, source_by_file, file_contents, generation_backend, previous_notes
        )
    except (ModelUnavailableError, GenerationTimeoutError) as exc:
        _record_check(db, attempt, "generation", VerificationStatus.ERROR, "generate", -1, "", str(exc), 0)
        _fail_attempt(db, attempt, FailureType.GENERATION_ERROR)
        previous_notes.append(f"Model unavailable/timed out: {exc}")
        return _CandidateOutcome(success=False, stop_pipeline=True)
    except MalformedPatchError as exc:
        _record_check(db, attempt, "generation", VerificationStatus.ERROR, "generate", -1, "", str(exc), 0)
        _fail_attempt(db, attempt, FailureType.GENERATION_ERROR)
        previous_notes.append(f"Generation rejected: {exc}")
        return _CandidateOutcome(success=False, stop_pipeline=False)

    diff = generated.diff
    if diff in tried_diffs:
        # Re-validating an identical patch costs a full clone/install/test
        # cycle and cannot produce a different result (docs/phase3.md §5).
        _record_check(
            db, attempt, "generation", VerificationStatus.ERROR, "generate", -1, "",
            "Model regenerated a patch identical to an earlier candidate.", 0,
        )
        attempt.diff = diff
        _fail_attempt(db, attempt, FailureType.GENERATION_ERROR)
        previous_notes.append(
            "You produced this exact patch before and it was rejected. Propose a genuinely different fix."
        )
        return _CandidateOutcome(success=False, stop_pipeline=False)
    tried_diffs.add(diff)

    attempt.diff = diff
    attempt.hypothesis = f"Fix targeting {candidates[0].file}:{candidates[0].symbol}"
    db.commit()
    # docs/phase3.md §2: persist the prompt/input metadata alongside the patch.
    _record_check(
        db, attempt, "generation", VerificationStatus.PASSED, f"generate ({generation_backend.model_id})",
        0, generated.prompt_metadata, "", generated.duration_ms,
    )

    # 2. Apply, inside a fresh isolated workspace checked out at base_commit.
    # This never touches any persistent checkout.
    with checked_out_workspace(metadata.repo, metadata.base_commit) as workspace:
        apply_result: ApplyResult = apply_patch(workspace, diff)
        if not apply_result.success:
            _record_check(db, attempt, "patch_apply", VerificationStatus.ERROR, "git apply", 1, "", apply_result.error, 0)
            _fail_attempt(db, attempt, FailureType.PATCH_APPLICATION_ERROR)
            previous_notes.append(f"Patch did not apply: {apply_result.error}")
            return _CandidateOutcome(success=False, stop_pipeline=False)
        _record_check(db, attempt, "patch_apply", VerificationStatus.PASSED, "git apply", 0, "", "", 0)

        protection = check_protected_tests(metadata.base_commit, workspace, metadata.protected_test_paths)
        if protection.modified:
            _record_check(
                db, attempt, "protected_test_check", VerificationStatus.FAILED, "git diff",
                1, "", f"Protected files modified: {protection.changed_files}", 0,
            )
            _fail_attempt(db, attempt, FailureType.PROTECTED_TEST_MODIFIED)
            previous_notes.append("Patch modified a protected test file; rejected without running tests.")
            return _CandidateOutcome(success=False, stop_pipeline=False)

        # 3. Add the benchmark's regression test. This happens *after* the
        # protected-test check, so the check still sees only what the model
        # changed, and before any test runs, so the candidate is judged on
        # the test the issue is actually about (docs/phase3.md §4.2).
        test_patch_error = sandbox_ops.apply_test_patch(issue.directory, workspace)
        if test_patch_error:
            _record_check(
                db, attempt, "apply_test_patch", VerificationStatus.ERROR, "git apply test_patch.diff",
                1, "", test_patch_error, 0,
            )
            _fail_attempt(db, attempt, FailureType.ENVIRONMENT_ERROR)
            previous_notes.append(f"The benchmark regression test could not be added: {test_patch_error}")
            return _CandidateOutcome(success=False, stop_pipeline=False)

        # 4. Install dependencies (needs network) then run tests (no network).
        sandbox_ops.make_world_writable(workspace)
        install_result = sandbox_ops.install_dependencies(metadata, workspace)
        if install_result.exit_code != 0:
            _record_check(
                db, attempt, "install", VerificationStatus.ERROR, metadata.install_command,
                install_result.exit_code, install_result.stdout, install_result.stderr, int(install_result.duration * 1000),
            )
            _fail_attempt(db, attempt, FailureType.ENVIRONMENT_ERROR)
            return _CandidateOutcome(success=False, stop_pipeline=True)
        _record_check(
            db, attempt, "install", VerificationStatus.PASSED, metadata.install_command,
            install_result.exit_code, install_result.stdout, install_result.stderr, int(install_result.duration * 1000),
        )

        regression_result = sandbox_ops.run_tests(metadata, workspace, metadata.regression_test_command)
        regression_output = regression_result.stdout + regression_result.stderr
        if regression_result.timed_out or not sandbox_ops.harness_ran(regression_output):
            _record_check(
                db, attempt, "regression_test", VerificationStatus.ERROR, metadata.regression_test_command,
                regression_result.exit_code, regression_result.stdout, regression_result.stderr,
                int(regression_result.duration * 1000),
            )
            _fail_attempt(db, attempt, FailureType.TIMEOUT if regression_result.timed_out else FailureType.ENVIRONMENT_ERROR)
            return _CandidateOutcome(success=False, stop_pipeline=False)

        # The regression command usually runs a whole test file, which may
        # contain failures that predate the issue. What matters is whether
        # *these* tests now pass, not whether the command exits 0
        # (docs/phase3.md §4.2).
        still_broken = sandbox_ops.still_failing(regression_result, metadata.regression_test_ids)
        regression_passed = not still_broken if metadata.regression_test_ids else regression_result.exit_code == 0
        _record_check(
            db, attempt, "regression_test", VerificationStatus.PASSED if regression_passed else VerificationStatus.FAILED,
            metadata.regression_test_command, regression_result.exit_code, regression_result.stdout,
            regression_result.stderr, int(regression_result.duration * 1000),
        )
        if not regression_passed:
            _fail_attempt(db, attempt, FailureType.VERIFICATION_FAILED)
            previous_notes.append(
                "This patch applied cleanly but the regression test still fails:\n"
                f"{_failure_excerpt(regression_result)}"
            )
            return _CandidateOutcome(success=False, stop_pipeline=False)

        relevant_command = metadata.relevant_test_command or metadata.full_test_command
        if relevant_command:
            relevant_result = sandbox_ops.run_tests(metadata, workspace, relevant_command)
            baseline_ids = {f.test_id for f in metadata.baseline_failures} | baseline_failures
            regression_ids = set(metadata.regression_test_ids)
            regressions = sandbox_ops.new_failures(relevant_result, baseline_ids, regression_ids)
            _record_check(
                db, attempt, "relevant_tests", VerificationStatus.PASSED if not regressions else VerificationStatus.FAILED,
                relevant_command, relevant_result.exit_code, relevant_result.stdout,
                relevant_result.stderr, int(relevant_result.duration * 1000),
            )
            if regressions:
                _fail_attempt(db, attempt, FailureType.VERIFICATION_FAILED)
                previous_notes.append(f"Patch broke previously-passing tests: {sorted(regressions)}")
                return _CandidateOutcome(success=False, stop_pipeline=False)

    attempt.status = AttemptStatus.PASSED
    attempt.failure_type = FailureType.NONE
    attempt.completed_at = datetime.now(timezone.utc)
    db.commit()
    return _CandidateOutcome(success=True, stop_pipeline=False)


def run_fix_generation(
    issue_id: str,
    db: Session,
    generation_backend: FixGenerationBackend | None = None,
    embedding_backend=None,
    candidate_limit: int | None = None,
    run: Run | None = None,
) -> Run:
    """Run the full Phase 3 pipeline for one issue and persist a Run with its
    Attempts/VerificationResults. Raises IssueNotFoundError,
    RepositoryNotAllowedError, or NoLocalizationResultsError before any Run
    row is created.

    `run` adopts an existing Run instead of creating one, so a Phase 5
    workflow (or a queued job) can carry a single row through every stage.
    """
    issue = load_issue(issue_id)  # IssueNotFoundError propagates
    metadata = issue.metadata

    outcome = get_localization(issue_id, db)
    if outcome is None:
        outcome = localize_issue(issue_id, db, backend=embedding_backend or embeddings_mod.get_backend())
    if not outcome.results:
        raise NoLocalizationResultsError(f"No localization candidates for issue '{issue_id}'")

    # Re-check the allowlist even though localize_issue already does -- this
    # call may have hit the already-persisted localization branch above,
    # which skips that check.
    if not is_repo_allowed(metadata.repo):
        raise RepositoryNotAllowedError(f"Repository '{metadata.repo}' is not allowlisted")

    if run is None:
        run = Run(
            issue_id=metadata.issue_id,
            issue_url=metadata.issue_url,
            repo=metadata.repo,
            base_commit=metadata.base_commit,
            mode=RunMode.PUBLIC_DEMO,
            status=RunStatus.RUNNING,
            current_stage=RunStage.GENERATION,
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
    else:
        # Adopted from an enclosing workflow (backend/workflow/pipeline.py) or
        # from the queue, so one Run row carries every stage rather than each
        # phase starting a fresh one.
        run.status = RunStatus.RUNNING
        run.current_stage = RunStage.GENERATION
        run.started_at = run.started_at or datetime.now(timezone.utc)
        db.commit()

    gen_backend = generation_backend or get_backend()
    limit = candidate_limit if candidate_limit is not None else settings.GENERATION_CANDIDATE_LIMIT

    # Protected test files are dropped before ranking down to the top few.
    # A localizer legitimately ranks the failing test highly -- it is the code
    # most similar to the issue text -- but offering it as editable context
    # invites the model to "fix" the assertion instead of the bug, which the
    # protected-test check then rejects. That burns every candidate on a patch
    # that could never be accepted, so the file is never offered at all.
    editable = [
        c for c in outcome.results if not is_protected_path(c.file, metadata.protected_test_paths)
    ]
    if not editable:
        raise NoLocalizationResultsError(
            f"Every localization candidate for '{issue_id}' is a protected test file"
        )
    top_candidates = editable[: min(3, len(editable))]

    # Source context is read once from a disposable, read-only checkout --
    # file contents at base_commit never change between candidates, so
    # there's no need to re-clone the repo just to re-read them.
    with checked_out_workspace(metadata.repo, metadata.base_commit) as context_workspace:
        source_by_file = read_candidate_sources(context_workspace, top_candidates)
        # Full text of the same files, so SEARCH/REPLACE edits can be resolved
        # into a diff without re-cloning. Only files offered as context are
        # editable -- an edit naming anything else is rejected.
        file_contents = read_files(context_workspace, [c.file for c in top_candidates])

    # Measured once per run and reused across candidates (docs/phase3.md §10).
    baseline_failures = measure_baseline_failures(issue)

    previous_notes: list[str] = []
    tried_diffs: set[str] = set()
    success = False
    for attempt_number in range(1, limit + 1):
        attempt = Attempt(
            run_id=run.id,
            attempt_number=attempt_number,
            model=gen_backend.model_id,
            status=AttemptStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        db.add(attempt)
        db.commit()
        db.refresh(attempt)

        run.current_stage = RunStage.GENERATION
        run.attempts_taken = attempt_number
        db.commit()

        candidate_outcome = _run_one_candidate(
            db, issue, top_candidates, source_by_file, file_contents, attempt, gen_backend,
            previous_notes, tried_diffs, baseline_failures,
        )
        if candidate_outcome.success:
            success = True
            break
        if candidate_outcome.stop_pipeline:
            break

    run.status = RunStatus.COMPLETED if success else RunStatus.FAILED
    run.current_stage = RunStage.COMPLETED
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


def get_fix(fix_id, db: Session) -> Run | None:
    return db.get(Run, fix_id)
