"""Stage 0 benchmark validation.

For a candidate issue: clone the repo at base_commit into an isolated
temp workspace, apply the issue's test patch (so the regression test
exists), prepare the environment, require the regression test to FAIL,
capture baseline failures from the relevant/full suite, and classify the
result. Historical environment breakage is reported as environment_error,
never conflated with a genuine invalid benchmark.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from backend.benchmark.allowlist import is_repo_allowed
from backend.benchmark.loader import load_issue, save_metadata
from backend.benchmark.schema import BaselineFailure, ValidationStatus
from backend.config import settings
from backend.tools.git import clone_and_checkout
from backend.tools.sandbox import DockerSandbox, ExecutionResult

# Markers that indicate the underlying test harness actually ran, as opposed
# to crashing before it got that far (missing deps, syntax errors, etc).
_HARNESS_RAN_MARKERS = (
    "passed",
    "failed",
    "error",
    "Ran ",  # unittest / Django: "Ran 42 tests in 1.234s"
    "tests finished",  # sympy's own runner
)

_PYTEST_SUMMARY_RE = re.compile(r"^(FAILED|ERROR) (\S+)", re.MULTILINE)


@dataclass
class StepReport:
    name: str
    result: ExecutionResult | None
    error: str | None = None


@dataclass
class ValidationReport:
    issue_id: str
    status: ValidationStatus
    message: str
    steps: list[StepReport] = field(default_factory=list)


def _apply_test_patch(issue_dir: Path, workspace: Path) -> None:
    patch_path = issue_dir / "test_patch.diff"
    if not patch_path.exists():
        return
    subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def _make_world_writable(workspace: Path) -> None:
    """The sandbox container runs as a non-root, non-host UID; the bind-
    mounted workspace has to be writable by whatever that UID turns out to
    be, since we don't control container/host UID mapping here.
    """
    for p in [workspace, *workspace.rglob("*")]:
        try:
            p.chmod(0o777)
        except OSError:
            pass


def _venv_cmd(command: str) -> str:
    return f"export PATH=/workspace/.venv/bin:$PATH && cd /workspace && {command}"


def _harness_ran(output: str) -> bool:
    return any(marker in output for marker in _HARNESS_RAN_MARKERS)


def parse_pytest_summary(output: str) -> dict[str, str]:
    """Map test node id -> 'FAILED' or 'ERROR' from a pytest run's output."""
    return {test_id: status for status, test_id in _PYTEST_SUMMARY_RE.findall(output)}


def validate_issue(issue_id: str) -> ValidationReport:
    issue = load_issue(issue_id)
    metadata = issue.metadata
    steps: list[StepReport] = []

    def finish(status: ValidationStatus, message: str) -> ValidationReport:
        metadata.validation_status = status
        save_metadata(issue_id, metadata)
        return ValidationReport(issue_id=issue_id, status=status, message=message, steps=steps)

    if not metadata.repo or not metadata.base_commit or not metadata.regression_test_command:
        return finish(ValidationStatus.PENDING, "Metadata is incomplete; cannot validate yet.")

    if not is_repo_allowed(metadata.repo):
        return finish(ValidationStatus.INVALID, f"Repository '{metadata.repo}' is not on the allowlist.")

    workspace = Path(tempfile.mkdtemp(prefix=f"bug2pr-{issue_id}-"))
    try:
        try:
            clone_and_checkout(metadata.repo, metadata.base_commit, workspace)
            _apply_test_patch(issue.directory, workspace)
        except subprocess.CalledProcessError as exc:
            steps.append(StepReport("clone_and_checkout", None, exc.stderr))
            return finish(ValidationStatus.ENVIRONMENT_ERROR, f"Clone/checkout/patch failed: {exc.stderr}")

        _make_world_writable(workspace)
        image = metadata.docker_image or f"python:{metadata.python_version or '3.11'}-slim"

        # 1. Environment prep. Needs network to fetch dependencies.
        with DockerSandbox(
            image,
            settings.DOCKER_CPU_LIMIT,
            settings.DOCKER_MEMORY_LIMIT,
            workspace=str(workspace),
            network_disabled=False,
        ) as sandbox:
            venv_result = sandbox.run("python -m venv /workspace/.venv", timeout=120)
            steps.append(StepReport("create_venv", venv_result))
            if venv_result.exit_code != 0:
                return finish(ValidationStatus.ENVIRONMENT_ERROR, "Failed to create virtualenv.")

            install_result = sandbox.run(_venv_cmd(metadata.install_command), timeout=settings.DOCKER_TIMEOUT)
            steps.append(StepReport("install", install_result))
            if install_result.exit_code != 0:
                return finish(
                    ValidationStatus.ENVIRONMENT_ERROR,
                    f"Environment install failed (exit {install_result.exit_code}).",
                )

        # 2. Regression test. No network needed; deps are already installed.
        with DockerSandbox(
            image,
            settings.DOCKER_CPU_LIMIT,
            settings.DOCKER_MEMORY_LIMIT,
            workspace=str(workspace),
            network_disabled=True,
        ) as sandbox:
            regression_result = sandbox.run(
                _venv_cmd(metadata.regression_test_command), timeout=settings.DOCKER_TIMEOUT
            )
            steps.append(StepReport("regression_test", regression_result))

            regression_output = regression_result.stdout + regression_result.stderr
            if regression_result.timed_out or not _harness_ran(regression_output):
                return finish(
                    ValidationStatus.ENVIRONMENT_ERROR,
                    "Regression test did not run cleanly (crash/timeout before the harness reported results).",
                )

            if regression_result.exit_code == 0:
                return finish(
                    ValidationStatus.INVALID,
                    "Regression test passed at base_commit; the bug does not reproduce.",
                )

            summary = parse_pytest_summary(regression_output)
            if summary and metadata.regression_test_ids:
                statuses = {summary.get(tid) for tid in metadata.regression_test_ids}
                if statuses and statuses.issubset({"ERROR"}):
                    return finish(
                        ValidationStatus.TEST_ERROR,
                        "Regression test errored (fixture/collection error) rather than cleanly failing.",
                    )

            # 3. Relevant/full suite, once, to capture the baseline.
            test_command = metadata.relevant_test_command or metadata.full_test_command
            relevant_result = None
            if test_command:
                relevant_result = sandbox.run(_venv_cmd(test_command), timeout=settings.DOCKER_TIMEOUT)
                steps.append(StepReport("relevant_tests", relevant_result))

        baseline_failures = _build_baseline_failures(metadata.regression_test_ids, regression_result, relevant_result)
        metadata.baseline_failures = baseline_failures
        return finish(ValidationStatus.VALID, "Regression test fails at base_commit as expected.")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _build_baseline_failures(
    regression_test_ids: list[str],
    regression_result: ExecutionResult,
    relevant_result: ExecutionResult | None,
) -> list[BaselineFailure]:
    failures: dict[str, BaselineFailure] = {}

    for test_id in regression_test_ids:
        failures[test_id] = BaselineFailure(
            test_id=test_id,
            exit_code=regression_result.exit_code,
            stdout=regression_result.stdout[-4000:],
            stderr=regression_result.stderr[-4000:],
        )

    if relevant_result is not None:
        summary = parse_pytest_summary(relevant_result.stdout + relevant_result.stderr)
        for test_id in summary:
            if test_id in failures:
                continue
            failures[test_id] = BaselineFailure(
                test_id=test_id,
                exit_code=relevant_result.exit_code,
                stdout=relevant_result.stdout[-4000:],
                stderr=relevant_result.stderr[-4000:],
            )

    return list(failures.values())
