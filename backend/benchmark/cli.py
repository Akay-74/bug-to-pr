"""Benchmark CLI.

    python -m backend.benchmark validate <issue_id>
    python -m backend.benchmark validate-all
    python -m backend.benchmark status
"""
from __future__ import annotations

import argparse
import sys

from backend.benchmark.loader import list_issue_ids, load_issue
from backend.benchmark.validator import validate_issue


def _cmd_validate(issue_id: str) -> int:
    report = validate_issue(issue_id)
    print(f"{issue_id}: {report.status.value}")
    print(f"  {report.message}")
    for step in report.steps:
        if step.result is None:
            print(f"  [{step.name}] error: {step.error}")
        else:
            print(
                f"  [{step.name}] exit={step.result.exit_code} "
                f"duration={step.result.duration:.1f}s timed_out={step.result.timed_out}"
            )
    return 0 if report.status.value in ("valid",) else 1


def _cmd_validate_all() -> int:
    issue_ids = list_issue_ids()
    if not issue_ids:
        print("No benchmark candidates found.")
        return 1
    exit_code = 0
    for issue_id in issue_ids:
        rc = _cmd_validate(issue_id)
        exit_code = exit_code or rc
    return exit_code


def _cmd_status() -> int:
    issue_ids = list_issue_ids()
    if not issue_ids:
        print("No benchmark candidates found.")
        return 1
    for issue_id in issue_ids:
        issue = load_issue(issue_id)
        print(f"{issue_id}: {issue.metadata.validation_status.value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a single benchmark candidate")
    validate_parser.add_argument("issue_id")

    subparsers.add_parser("validate-all", help="Validate every benchmark candidate")
    subparsers.add_parser("status", help="Show validation status for every candidate")

    args = parser.parse_args(argv)

    if args.command == "validate":
        return _cmd_validate(args.issue_id)
    if args.command == "validate-all":
        return _cmd_validate_all()
    if args.command == "status":
        return _cmd_status()

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
