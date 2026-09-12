"""Benchmark evaluation CLI (docs/phase5.md "Final Benchmark Evaluation").

    python -m backend.evaluation run --limit 3
    python -m backend.evaluation run --issues pylint-dev__pylint-4604
    python -m backend.evaluation report

`run` is resumable and bounded: it appends each issue's result to the report
file as it finishes and, with --resume, skips issues already recorded. A full
evaluation over real benchmark repositories takes hours, so it has to survive
being interrupted.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.config import REPO_ROOT
from backend.evaluation.harness import (
    evaluate_issue,
    load_report,
    save_result,
    validated_issue_ids,
)
from backend.models.database import SessionLocal

DEFAULT_REPORT = REPO_ROOT / "backend" / "evaluation" / "report.json"


def _cmd_run(args) -> int:
    path = Path(args.output)
    report = load_report(path)
    # Record the limit this run actually used, not the configured default --
    # the report is the evidence for the numbers, so it must describe the
    # conditions they were measured under.
    if args.candidate_limit:
        report.candidate_limit = args.candidate_limit
    done = {i.issue_id for i in report.issues} if args.resume else set()

    issue_ids = args.issues.split(",") if args.issues else validated_issue_ids()
    pending = [i for i in issue_ids if i not in done]
    if args.limit:
        pending = pending[: args.limit]

    if not pending:
        print("Nothing to do: every requested issue is already in the report.")
        return 0

    print(f"Evaluating {len(pending)} issue(s); report -> {path}")
    db = SessionLocal()
    try:
        for index, issue_id in enumerate(pending, start=1):
            print(f"[{index}/{len(pending)}] {issue_id} ... ", end="", flush=True)
            result = evaluate_issue(
                issue_id, db, candidate_limit=args.candidate_limit, create_pr=args.create_pr
            )
            report = save_result(path, report, result)
            print(
                f"{result.outcome}"
                f" (attempts={result.attempts},"
                f" gen={result.generation_ms}ms,"
                f" val={result.validation_ms}ms,"
                f" wall={result.wall_ms}ms)"
            )
    finally:
        db.close()

    print(json.dumps(report.summary(), indent=2))
    return 0


def _cmd_report(args) -> int:
    path = Path(args.output)
    if not path.exists():
        print(f"No report at {path}. Run `python -m backend.evaluation run` first.")
        return 1
    report = load_report(path)
    print(json.dumps(report.summary(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.evaluation")
    parser.add_argument("--output", default=str(DEFAULT_REPORT), help="Report JSON path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Evaluate benchmark issues end to end")
    run_parser.add_argument("--issues", help="Comma-separated issue ids (default: all validated)")
    run_parser.add_argument("--limit", type=int, help="Evaluate at most this many issues")
    run_parser.add_argument("--candidate-limit", type=int, default=None)
    run_parser.add_argument("--create-pr", action="store_true", help="Also open draft PRs")
    run_parser.add_argument(
        "--no-resume", dest="resume", action="store_false", help="Re-run issues already in the report"
    )
    run_parser.set_defaults(func=_cmd_run, resume=True)

    report_parser = subparsers.add_parser("report", help="Print the summary of an existing report")
    report_parser.set_defaults(func=_cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
