"""Prompt construction for fix generation (docs/phase3.md §2).

Builds one self-contained prompt from the issue, its localized candidate
locations, and their source context.

The model is asked for SEARCH/REPLACE blocks rather than a unified diff: a
7B-class local model gets the *edit* right far more often than it gets hunk
headers and context lines right, and backend/generation/edits.py turns the
blocks into a real unified diff deterministically. A model that ignores the
instruction and emits a diff anyway is still handled -- the pipeline falls
back to parsing its output as a diff.
"""
from __future__ import annotations

from backend.benchmark.loader import Issue
from backend.localization.service import LocalizationCandidate

_INSTRUCTIONS = """You are fixing a bug in an open-source Python repository.

Reply with ONLY one or more SEARCH/REPLACE blocks, each preceded by the file path:

<one of the file paths listed under "Editable files" below>
<<<<<<< SEARCH
(the exact existing lines to replace, copied character-for-character)
=======
(the replacement lines)
>>>>>>> REPLACE

Rules:
- The SEARCH text must match the file exactly, including indentation. Copy it from the source shown below.
- Keep SEARCH as short as possible while still matching exactly one place in the file.
- Only edit the source files shown below. Never edit test files.
- Keep the change minimal and focused on the described bug.
- If the fix needs a new import, include a second SEARCH/REPLACE block adding it.
- Output nothing else: no explanation, no markdown fences, no commentary.
"""


def build_prompt(
    issue: Issue,
    candidates: list[LocalizationCandidate],
    source_by_file: dict[str, str],
    previous_attempt_notes: list[str] | None = None,
    regression_info: str = "",
) -> str:
    sections = [_INSTRUCTIONS, f"## Issue: {issue.metadata.issue_id}\n\n{issue.problem.strip()}"]

    sections.append("## Likely locations (ranked by an automated localizer)")
    for candidate in candidates:
        sections.append(
            f"- {candidate.file}:{candidate.start_line}-{candidate.end_line} "
            f"({candidate.symbol_type} `{candidate.symbol}`, score={candidate.final_score:.3f})"
        )

    if source_by_file:
        # Naming the real paths here stops the model copying the placeholder
        # out of the format example above.
        sections.append(
            "## Editable files\n" + "\n".join(f"- {path}" for path in source_by_file)
        )

    if regression_info:
        sections.append(f"## Regression test\n{regression_info}")

    for file_path, snippet in source_by_file.items():
        if snippet:
            sections.append(f"## Source: {file_path}\n```\n{snippet}\n```")

    if previous_attempt_notes:
        notes = "\n".join(f"- {note}" for note in previous_attempt_notes)
        sections.append(f"## Previous attempts failed\n{notes}\nTry a different approach.")

    return "\n\n".join(sections)
