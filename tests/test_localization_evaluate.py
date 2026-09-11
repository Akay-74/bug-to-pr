from dataclasses import dataclass

from backend.benchmark.schema import IssueMetadata
from backend.benchmark.loader import Issue
from backend.localization.evaluate import (
    GroundTruth,
    _is_hit,
    parse_ground_truth,
)
from backend.localization.service import LocalizationCandidate, LocalizationOutcome


def _issue(regression_test_ids, protected_test_paths=()):
    metadata = IssueMetadata(
        issue_id="x",
        repo="org/repo",
        base_commit="abc",
        regression_test_ids=list(regression_test_ids),
        protected_test_paths=list(protected_test_paths),
    )
    return Issue(metadata=metadata, problem="", directory=None)


def test_parse_ground_truth_pytest_node_id_with_file():
    issue = _issue(["xarray/tests/test_merge.py::TestMergeFunction::test_merge_attrs_override_copy"])
    gt = parse_ground_truth(issue)
    assert gt == GroundTruth(file="xarray/tests/test_merge.py", symbol="TestMergeFunction.test_merge_attrs_override_copy")


def test_parse_ground_truth_pytest_node_id_bare_function():
    issue = _issue(["tests/test_add.py::test_add"])
    gt = parse_ground_truth(issue)
    assert gt == GroundTruth(file="tests/test_add.py", symbol="test_add")


def test_parse_ground_truth_bare_function_name_no_protected_paths():
    issue = _issue(["test_issue_11617"])
    gt = parse_ground_truth(issue)
    assert gt == GroundTruth(file="", symbol="test_issue_11617")


def test_parse_ground_truth_django_dotted_id_uses_protected_paths():
    issue = _issue(
        ["admin_inlines.tests.TestInlinePermissions.test_inline_add_m2m_view_only_perm"],
        protected_test_paths=["tests/admin_inlines/tests.py"],
    )
    gt = parse_ground_truth(issue)
    assert gt.file == "tests/admin_inlines/tests.py"
    assert gt.symbol == "TestInlinePermissions.test_inline_add_m2m_view_only_perm"


def test_parse_ground_truth_returns_none_with_no_ids():
    issue = _issue([])
    assert parse_ground_truth(issue) is None


def _candidate(file, symbol, rank=1):
    return LocalizationCandidate(
        rank=rank, file=file, symbol=symbol, symbol_type="function",
        start_line=1, end_line=2, semantic_score=1.0, lexical_score=1.0,
        relevance_score=1.0, final_score=1.0, matched_terms=[],
    )


def test_is_hit_true_when_top1_matches():
    outcome = LocalizationOutcome(
        issue_id="x", model_identifier="m", index_identifier="i",
        results=[_candidate("mypkg/__init__.py", "add")],
    )
    gt = GroundTruth(file="mypkg/__init__.py", symbol="add")
    assert _is_hit(outcome, gt, top_n=1) is True


def test_is_hit_false_when_correct_result_outside_top_n():
    outcome = LocalizationOutcome(
        issue_id="x", model_identifier="m", index_identifier="i",
        results=[_candidate("wrong.py", "nope", rank=1), _candidate("mypkg/__init__.py", "add", rank=2)],
    )
    gt = GroundTruth(file="mypkg/__init__.py", symbol="add")
    assert _is_hit(outcome, gt, top_n=1) is False
    assert _is_hit(outcome, gt, top_n=3) is True


def test_is_hit_matches_leaf_symbol_across_qualification_depth():
    """A ground truth of 'Class.method' should match a bare 'method'
    candidate (and vice versa) — different runners qualify differently."""
    outcome = LocalizationOutcome(
        issue_id="x", model_identifier="m", index_identifier="i",
        results=[_candidate("tests/test_merge.py", "TestMergeFunction.test_merge_attrs_override_copy")],
    )
    gt = GroundTruth(file="tests/test_merge.py", symbol="test_merge_attrs_override_copy")
    assert _is_hit(outcome, gt, top_n=1) is True


def test_is_hit_no_file_in_ground_truth_falls_back_to_symbol_only():
    outcome = LocalizationOutcome(
        issue_id="x", model_identifier="m", index_identifier="i",
        results=[_candidate("anywhere.py", "test_issue_11617")],
    )
    gt = GroundTruth(file="", symbol="test_issue_11617")
    assert _is_hit(outcome, gt, top_n=1) is True
