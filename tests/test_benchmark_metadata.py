from backend.benchmark.allowlist import is_repo_allowed
from backend.benchmark.loader import IssueNotFoundError, list_issue_ids, load_issue
from backend.benchmark.schema import ValidationStatus
from tests.conftest import FIXTURE_ISSUE_ID, FIXTURE_REPO_NAME


def test_list_issue_ids_finds_fixture_issue():
    assert FIXTURE_ISSUE_ID in list_issue_ids()


def test_load_issue_reads_metadata_and_problem():
    issue = load_issue(FIXTURE_ISSUE_ID)
    assert issue.metadata.issue_id == FIXTURE_ISSUE_ID
    assert issue.metadata.repo == FIXTURE_REPO_NAME
    assert issue.metadata.validation_status == ValidationStatus.PENDING
    assert "add(a, b)" in issue.problem


def test_load_issue_missing_raises():
    try:
        load_issue("does-not-exist")
        assert False, "expected IssueNotFoundError"
    except IssueNotFoundError:
        pass


def test_all_validation_status_values_supported():
    assert {s.value for s in ValidationStatus} == {
        "pending",
        "valid",
        "invalid",
        "environment_error",
        "test_error",
    }


def test_repo_allowlist_accepts_fixture_repo():
    assert is_repo_allowed(FIXTURE_REPO_NAME)


def test_repo_allowlist_rejects_unknown_repo():
    assert not is_repo_allowed("some-org/some-unrelated-repo")
