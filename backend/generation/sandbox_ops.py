"""Sandbox-backed test execution for candidate validation (docs/phase3.md §4).

Thin wrapper around the same DockerSandbox primitives Phase 1's benchmark
validator uses (backend/tools/sandbox.py, backend/benchmark/validator.py) --
same non-root/no-network/no-secrets container, same venv-per-workspace
install step, reused rather than reinvented for Phase 3.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from backend.benchmark.schema import IssueMetadata
from backend.benchmark.validator import parse_pytest_summary
from backend.config import settings
from backend.tools.sandbox import DockerSandbox, ExecutionResult


def make_world_writable(workspace: Path) -> None:
    for p in [workspace, *workspace.rglob("*")]:
        try:
            p.chmod(0o777)
        except OSError:
            pass


def venv_cmd(command: str) -> str:
    return f"export PATH=/workspace/.venv/bin:$PATH && cd /workspace && {command}"


def install_dependencies(metadata: IssueMetadata, workspace: Path) -> ExecutionResult:
    """Create a venv and install the benchmark's dependencies. Needs network
    (package index access) -- identical shape to Stage 0 validation.
    """
    image = metadata.docker_image or f"python:{metadata.python_version or '3.11'}-slim"
    with DockerSandbox(
        image,
        settings.DOCKER_CPU_LIMIT,
        settings.DOCKER_MEMORY_LIMIT,
        workspace=str(workspace),
        network_disabled=False,
    ) as sandbox:
        venv_result = sandbox.run("python -m venv /workspace/.venv", timeout=120)
        if venv_result.exit_code != 0:
            return venv_result
        return sandbox.run(venv_cmd(metadata.install_command), timeout=settings.DOCKER_TIMEOUT)


def run_tests(metadata: IssueMetadata, workspace: Path, command: str) -> ExecutionResult:
    """Run one test command with network disabled -- dependencies are
    already installed, so the patched candidate cannot reach the network.
    """
    image = metadata.docker_image or f"python:{metadata.python_version or '3.11'}-slim"
    with DockerSandbox(
        image,
        settings.DOCKER_CPU_LIMIT,
        settings.DOCKER_MEMORY_LIMIT,
        workspace=str(workspace),
        network_disabled=True,
    ) as sandbox:
        return sandbox.run(venv_cmd(command), timeout=settings.DOCKER_TIMEOUT)


def apply_test_patch(issue_dir: Path, workspace: Path) -> str:
    """Add the benchmark's regression test to the workspace.

    Without this the test the candidate is judged on does not exist at
    base_commit, so the run would "verify" a patch against the repository's
    pre-existing tests only (docs/phase3.md §4.2). Phase 1's validator does
    the same thing at the same point.

    Returns "" on success, or the git error to report as an environment
    failure.
    """
    # Resolved: git runs with cwd=workspace, so a benchmark path relative to
    # the project root would not exist from in there.
    patch_path = (issue_dir / "test_patch.diff").resolve()
    if not patch_path.exists():
        return ""
    result = subprocess.run(
        ["git", "apply", str(patch_path)], cwd=workspace, capture_output=True, text=True
    )
    if result.returncode != 0:
        return result.stderr.strip() or "git apply of test_patch.diff failed"
    return ""


# sympy (and a few other benchmark repos) ship their own runner rather than
# using pytest's summary lines, so failing tests are reported as a banner:
#   ____ sympy/geometry/tests/test_point.py:test_issue_11617 ____
_RUNNER_BANNER_RE = re.compile(r"^_+ (\S+:\S+) _+$", re.MULTILINE)
_HARNESS_RAN_RE = re.compile(r"(tests? finished|passed|failed|error|no tests ran)", re.IGNORECASE)


def parse_failing_tests(output: str) -> set[str]:
    """Every test id the run reported as failing, across the runner styles
    the benchmark repositories actually use.
    """
    return set(parse_pytest_summary(output)) | set(_RUNNER_BANNER_RE.findall(output))


def harness_ran(output: str) -> bool:
    """Whether the test harness got far enough to report results at all --
    a crash before that is an environment failure, not a test failure.
    """
    return bool(_HARNESS_RAN_RE.search(output))


def matches_test_id(failing_id: str, wanted_id: str) -> bool:
    """Whether a reported failure is the test `wanted_id` names.

    Benchmarks record ids at different granularities ("test_issue_11617",
    "tests/test_add.py::test_add"), and runners report them differently
    again, so compare on the most specific shared suffix.
    """
    if failing_id == wanted_id:
        return True
    normalized = [failing_id.replace("::", ":"), wanted_id.replace("::", ":")]
    long, short = sorted(normalized, key=len, reverse=True)
    return long == short or long.endswith(":" + short) or long.split(":")[-1] == short.split(":")[-1]


def still_failing(result: ExecutionResult, wanted_ids: list[str]) -> set[str]:
    """Which of `wanted_ids` are still reported as failing."""
    failing = parse_failing_tests(result.stdout + result.stderr)
    return {
        wanted for wanted in wanted_ids if any(matches_test_id(f, wanted) for f in failing)
    }


def new_failures(
    result: ExecutionResult,
    baseline_failure_ids: set[str],
    regression_test_ids: set[str],
) -> set[str]:
    """Test ids that fail now but were neither a known baseline failure nor
    one of the regression tests this candidate is supposed to fix -- i.e.
    tests the patch itself broke (docs/phase3.md §4.3).
    """
    currently_failing = parse_failing_tests(result.stdout + result.stderr)
    return {
        failing
        for failing in currently_failing
        if not any(matches_test_id(failing, known) for known in baseline_failure_ids | regression_test_ids)
    }
