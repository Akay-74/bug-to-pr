"""Read-only source context for prompting (docs/phase3.md §2).

Slices the lines Phase 2 localization candidates point at out of a
checked-out workspace. Reading file text is not executing repository code
(docs/phase3.md §1, §11) -- same rule backend/localization/chunking.py
already relies on.

Snippets are returned verbatim, without line-number gutters: the model is
asked for SEARCH/REPLACE blocks whose SEARCH text must match the file
byte-for-byte (backend/generation/edits.py), so any decoration here would
have to be un-decorated by the model.
"""
from __future__ import annotations

from pathlib import Path

from backend.localization.service import LocalizationCandidate

# A little slack around [start_line, end_line] so the model sees the
# surrounding signature/imports, not just the exact chunk boundary.
_CONTEXT_PAD_LINES = 15


def _read_lines(workspace: Path, relative_path: str) -> list[str] | None:
    try:
        return (workspace / relative_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None


def read_candidate_source(workspace: Path, candidate: LocalizationCandidate) -> str:
    """The padded source window for one candidate, or "" if unreadable."""
    lines = _read_lines(workspace, candidate.file)
    if lines is None:
        return ""

    start = max(1, candidate.start_line - _CONTEXT_PAD_LINES)
    end = min(len(lines), candidate.end_line + _CONTEXT_PAD_LINES)
    return "\n".join(lines[start - 1 : end])


def read_candidate_sources(workspace: Path, candidates: list[LocalizationCandidate]) -> dict[str, str]:
    """Source context per file, merging every candidate that lands in the
    same file.

    Localization routinely returns several candidates from one file; keying
    a plain dict on `candidate.file` would keep only the last one's window
    and silently drop the rest of the context.
    """
    windows: dict[str, list[tuple[int, int]]] = {}
    for candidate in candidates:
        lines = _read_lines(workspace, candidate.file)
        if lines is None:
            continue
        start = max(1, candidate.start_line - _CONTEXT_PAD_LINES)
        end = min(len(lines), candidate.end_line + _CONTEXT_PAD_LINES)
        windows.setdefault(candidate.file, []).append((start, end))

    sources: dict[str, str] = {}
    for file_path, ranges in windows.items():
        lines = _read_lines(workspace, file_path) or []
        merged: list[tuple[int, int]] = []
        for start, end in sorted(ranges):
            if merged and start <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        sources[file_path] = "\n\n...\n\n".join("\n".join(lines[s - 1 : e]) for s, e in merged)
    return sources


def read_files(workspace: Path, relative_paths: list[str]) -> dict[str, str]:
    """Full text of each readable file, used to resolve SEARCH/REPLACE edits
    into a unified diff without re-cloning the repository.
    """
    contents: dict[str, str] = {}
    for relative_path in dict.fromkeys(relative_paths):
        try:
            contents[relative_path] = (workspace / relative_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return contents


# The regression test is what the patch will be judged against, so it is a
# prompt input in its own right (docs/phase3.md §2). Only the lines the
# benchmark's test patch *adds* are included: the model must never see this
# as something it may edit, and the protected-test check enforces that
# regardless of what it proposes.
_REGRESSION_EXCERPT_CHARS = 2000


def read_regression_context(issue) -> str:
    """Regression-test information for the prompt: which tests must pass,
    how they are run, and the test body when the benchmark records one.
    """
    metadata = issue.metadata
    parts = []
    if metadata.regression_test_ids:
        parts.append("Tests that must pass after your fix: " + ", ".join(metadata.regression_test_ids))
    if metadata.regression_test_command:
        parts.append(f"They are run with: {metadata.regression_test_command}")

    test_patch = issue.directory / "test_patch.diff"
    try:
        added = [
            line[1:]
            for line in test_patch.read_text(encoding="utf-8", errors="replace").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
    except OSError:
        added = []
    if added:
        parts.append("The test itself (do not edit it, make it pass):\n" + "\n".join(added)[:_REGRESSION_EXCERPT_CHARS])

    return "\n\n".join(parts)
