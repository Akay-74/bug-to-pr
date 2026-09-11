import json

from backend.benchmark.loader import load_issue, save_metadata
from backend.benchmark.schema import ValidationStatus
from backend.benchmark.validator import validate_issue
from tests.conftest import (
    FIXTURE_ISSUE_ID,
    FIXTURE_ISSUES_DIR,
    FIXTURE_REPO_NAME,
    _fixture_repo_base_commit,
    _fixture_repo_fixed_commit,
)


def _write_variant_issue(issue_id: str, **overrides) -> None:
    base = load_issue(FIXTURE_ISSUE_ID).metadata
    data = json.loads(base.model_dump_json())
    data["issue_id"] = issue_id
    data["issue_url"] = f"https://example.invalid/fixture/tiny-repo/issues/{issue_id}"
    data.update(overrides)
    issue_dir = FIXTURE_ISSUES_DIR / issue_id
    issue_dir.mkdir(parents=True, exist_ok=True)
    (issue_dir / "metadata.json").write_text(json.dumps(data, indent=2) + "\n")
    (issue_dir / "problem.md").write_text("test fixture\n")


def test_valid_benchmark_requires_regression_failure():
    report = validate_issue(FIXTURE_ISSUE_ID)

    assert report.status == ValidationStatus.VALID
    updated = load_issue(FIXTURE_ISSUE_ID).metadata
    assert updated.validation_status == ValidationStatus.VALID
    failing_ids = {f.test_id for f in updated.baseline_failures}
    assert "tests/test_add.py::test_add" in failing_ids


def test_baseline_failures_are_captured_with_structured_fields():
    validate_issue(FIXTURE_ISSUE_ID)
    updated = load_issue(FIXTURE_ISSUE_ID).metadata

    assert len(updated.baseline_failures) >= 1
    failure = next(f for f in updated.baseline_failures if f.test_id == "tests/test_add.py::test_add")
    assert failure.exit_code != 0
    assert isinstance(failure.stdout, str)
    assert isinstance(failure.stderr, str)


def test_benchmark_invalid_when_regression_passes_at_base_commit():
    issue_id = "fixture__already-fixed"
    _write_variant_issue(issue_id, base_commit=_fixture_repo_fixed_commit())

    report = validate_issue(issue_id)

    assert report.status == ValidationStatus.INVALID
    assert load_issue(issue_id).metadata.validation_status == ValidationStatus.INVALID


def test_benchmark_environment_error_on_broken_install():
    issue_id = "fixture__broken-install"
    _write_variant_issue(
        issue_id,
        base_commit=_fixture_repo_base_commit(),
        install_command="python -m pip install ./this-path-does-not-exist",
    )

    report = validate_issue(issue_id)

    assert report.status == ValidationStatus.ENVIRONMENT_ERROR
    assert load_issue(issue_id).metadata.validation_status == ValidationStatus.ENVIRONMENT_ERROR


def test_unsupported_repository_rejected_by_validator():
    issue_id = "fixture__unsupported-repo"
    _write_variant_issue(issue_id, base_commit=_fixture_repo_base_commit(), repo="some-org/not-allowlisted")

    report = validate_issue(issue_id)

    assert report.status == ValidationStatus.INVALID
