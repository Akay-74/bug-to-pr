"""Deterministic localization evaluation (docs/phase2.md §7).

For each validated benchmark issue, runs localization against its exact
base_commit and checks whether the known regression-test file/symbol shows
up in the Top-1 / Top-3 results.

Ground truth is derived from existing metadata (`regression_test_ids` +
`protected_test_paths`) rather than a new hand-authored field, since test-id
formats already vary across the curated benchmark's test runners (pytest
node ids, bare pytest function names, Django's dotted module.Class.method
strings) — see `parse_ground_truth` below for exactly how each is handled.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass

from backend.benchmark.loader import Issue, list_issue_ids, load_issue
from backend.benchmark.schema import ValidationStatus
from backend.localization.embeddings import EmbeddingBackend, get_backend
from backend.localization.service import LocalizationOutcome, RepositoryNotAllowedError, run_localization


@dataclass
class GroundTruth:
    file: str
    symbol: str


def parse_ground_truth(issue: Issue) -> GroundTruth | None:
    """Best-effort (file, symbol) location of the regression test itself,
    from whichever of the three id shapes this issue's test runner uses.
    Returns None when neither `regression_test_ids` nor
    `protected_test_paths` gives enough to work with.
    """
    ids = issue.metadata.regression_test_ids
    protected = issue.metadata.protected_test_paths
    if not ids:
        return None
    test_id = ids[0]

    if "::" in test_id:
        # pytest node id: "path/to/test_file.py::Class::method" or
        # "path/to/test_file.py::function".
        parts = test_id.split("::")
        file = parts[0]
        symbol = ".".join(parts[1:])
        return GroundTruth(file=file, symbol=symbol)

    if "." in test_id and not protected:
        # Dotted module path with no protected_test_paths to anchor a file
        # to (e.g. a runner we don't otherwise recognize) — not enough to
        # locate a file confidently.
        segments = test_id.split(".")
        symbol = ".".join(segments[-2:]) if len(segments) >= 2 else segments[-1]
        return GroundTruth(file="", symbol=symbol)

    if not protected:
        # Bare pytest function name (e.g. sympy's `test_issue_11617`) with no
        # protected_test_paths to anchor a file to.
        return GroundTruth(file="", symbol=test_id)

    # Django-style dotted "app.module.Class.method" (or a bare function name)
    # plus one or more known test files in protected_test_paths. Pick the
    # protected path whose stem appears in the dotted id; fall back to the
    # first protected path if none obviously match.
    segments = test_id.split(".")
    symbol = ".".join(segments[-2:]) if len(segments) >= 2 else segments[-1]
    file = protected[0]
    for candidate_path in protected:
        stem = candidate_path.rsplit("/", 1)[-1].removesuffix(".py")
        if stem in segments:
            file = candidate_path
            break
    return GroundTruth(file=file, symbol=symbol)


def _file_matches(candidate_file: str, gt_file: str) -> bool:
    if not gt_file:
        # No file to check against — only the symbol name can be compared.
        return True
    if candidate_file == gt_file:
        return True
    return candidate_file.endswith("/" + gt_file) or gt_file.endswith("/" + candidate_file)


def _symbol_matches(candidate_symbol: str, gt_symbol: str) -> bool:
    # Compare leaf names case-insensitively: our AST-derived "Class.method"
    # form and a test runner's "Class::method"-turned-"Class.method" form
    # should match even if qualification depth differs.
    return candidate_symbol.split(".")[-1].lower() == gt_symbol.split(".")[-1].lower()


def _is_hit(outcome: LocalizationOutcome, gt: GroundTruth, top_n: int) -> bool:
    for candidate in outcome.results[:top_n]:
        if _file_matches(candidate.file, gt.file) and _symbol_matches(candidate.symbol, gt.symbol):
            return True
    return False


@dataclass
class IssueEvalResult:
    issue_id: str
    ground_truth: GroundTruth | None
    top1_hit: bool | None
    top3_hit: bool | None
    returned_locations: list[str]
    model_identifier: str | None
    index_identifier: str | None
    error: str | None = None


def evaluate_issue(issue_id: str, backend: EmbeddingBackend) -> IssueEvalResult:
    issue = load_issue(issue_id)
    gt = parse_ground_truth(issue)
    if gt is None:
        return IssueEvalResult(
            issue_id=issue_id,
            ground_truth=None,
            top1_hit=None,
            top3_hit=None,
            returned_locations=[],
            model_identifier=None,
            index_identifier=None,
            error="Could not derive ground truth from metadata",
        )

    try:
        outcome = run_localization(issue, backend)
    except RepositoryNotAllowedError as exc:
        return IssueEvalResult(
            issue_id=issue_id,
            ground_truth=gt,
            top1_hit=None,
            top3_hit=None,
            returned_locations=[],
            model_identifier=backend.model_id,
            index_identifier=None,
            error=str(exc),
        )

    return IssueEvalResult(
        issue_id=issue_id,
        ground_truth=gt,
        top1_hit=_is_hit(outcome, gt, top_n=1),
        top3_hit=_is_hit(outcome, gt, top_n=3),
        returned_locations=[f"{r.file}:{r.symbol}" for r in outcome.results],
        model_identifier=outcome.model_identifier,
        index_identifier=outcome.index_identifier,
    )


@dataclass
class EvaluationReport:
    model_identifier: str
    total_evaluated: int
    top1_hits: int
    top3_hits: int
    top1_accuracy: float
    top3_accuracy: float
    issues: list[IssueEvalResult]


def evaluate_validated_issues(backend: EmbeddingBackend) -> EvaluationReport:
    """Run evaluate_issue over every `valid` benchmark candidate — the
    validated set, per docs/phase2.md §7 ("For each validated benchmark
    issue"). Issues that errored or whose ground truth couldn't be derived
    still appear in the report but are excluded from the accuracy
    denominator.
    """
    results = []
    for issue_id in list_issue_ids():
        issue = load_issue(issue_id)
        if issue.metadata.validation_status != ValidationStatus.VALID:
            continue
        results.append(evaluate_issue(issue_id, backend))

    scorable = [r for r in results if r.error is None]
    top1_hits = sum(1 for r in scorable if r.top1_hit)
    top3_hits = sum(1 for r in scorable if r.top3_hit)
    total = len(scorable)

    return EvaluationReport(
        model_identifier=backend.model_id,
        total_evaluated=total,
        top1_hits=top1_hits,
        top3_hits=top3_hits,
        top1_accuracy=(top1_hits / total) if total else 0.0,
        top3_accuracy=(top3_hits / total) if total else 0.0,
        issues=results,
    )


def _report_to_dict(report: EvaluationReport) -> dict:
    data = asdict(report)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.localization.evaluate")
    parser.add_argument("--model", choices=["jina", "coderank"], default="jina")
    args = parser.parse_args(argv)

    backend = get_backend(args.model)
    report = evaluate_validated_issues(backend)
    print(json.dumps(_report_to_dict(report), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
