"""Loading/saving of benchmark candidate metadata from disk."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backend.benchmark.schema import IssueMetadata
from backend.config import settings


class IssueNotFoundError(Exception):
    pass


@dataclass
class Issue:
    metadata: IssueMetadata
    problem: str
    directory: Path


def _issue_dir(issue_id: str, benchmark_path: Path | None = None) -> Path:
    """Resolve an issue id to its directory, refusing to leave the benchmark.

    Issue ids arrive from API path parameters, so this is a trust boundary:
    without the check, an id like "../../etc" would resolve outside the
    benchmark tree, and `save_metadata` would *write* there. An id is one
    path segment, never a path.
    """
    base = benchmark_path or settings.benchmark_path
    if not issue_id or "/" in issue_id or "\\" in issue_id or issue_id in (".", ".."):
        raise IssueNotFoundError(f"Invalid issue id: {issue_id!r}")

    directory = base / issue_id
    # Belt and braces: a resolved path must still sit under the benchmark
    # root, which also catches anything the check above did not anticipate.
    try:
        directory.resolve().relative_to(base.resolve())
    except ValueError:
        raise IssueNotFoundError(f"Invalid issue id: {issue_id!r}") from None
    return directory


def load_issue(issue_id: str, benchmark_path: Path | None = None) -> Issue:
    directory = _issue_dir(issue_id, benchmark_path)
    metadata_path = directory / "metadata.json"
    if not metadata_path.exists():
        raise IssueNotFoundError(f"No metadata.json found for issue '{issue_id}' at {metadata_path}")

    raw = json.loads(metadata_path.read_text())
    metadata = IssueMetadata.model_validate(raw)

    problem_path = directory / "problem.md"
    problem = problem_path.read_text() if problem_path.exists() else ""

    return Issue(metadata=metadata, problem=problem, directory=directory)


def save_metadata(issue_id: str, metadata: IssueMetadata, benchmark_path: Path | None = None) -> None:
    directory = _issue_dir(issue_id, benchmark_path)
    directory.mkdir(parents=True, exist_ok=True)
    metadata_path = directory / "metadata.json"
    metadata_path.write_text(json.dumps(json.loads(metadata.model_dump_json()), indent=2) + "\n")


def list_issue_ids(benchmark_path: Path | None = None) -> list[str]:
    base = benchmark_path or settings.benchmark_path
    if not base.exists():
        return []
    return sorted(
        p.name for p in base.iterdir() if p.is_dir() and (p / "metadata.json").exists()
    )


def load_all_issues(benchmark_path: Path | None = None) -> list[Issue]:
    return [load_issue(issue_id, benchmark_path) for issue_id in list_issue_ids(benchmark_path)]
