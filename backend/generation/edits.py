"""SEARCH/REPLACE edit blocks -> unified diff (docs/phase3.md §2).

A small local model reliably reasons about *what* to change but not about
unified-diff bookkeeping: hunk headers whose line counts match the body, and
byte-exact context lines. Asking it for a literal SEARCH/REPLACE block moves
that bookkeeping here, where the diff is derived deterministically from the
file's real contents at base_commit.

The artifact the pipeline persists, validates and applies is still a unified
diff -- nothing in this module writes to a file. Edits are resolved against
source text that was read from a disposable checkout, so a block naming a
file that was never offered as context is rejected rather than guessed at.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from backend.generation.patch import MalformedPatchError

# Tolerant of the marker-length drift small models produce ("<<<<<< SEARCH").
_BLOCK_RE = re.compile(
    r"<{4,}\s*SEARCH\s*\n(.*?)\n?={4,}\s*\n(.*?)\n?>{4,}\s*REPLACE",
    re.DOTALL,
)
_FENCE_LINE_RE = re.compile(r"^\s*`{3,}\w*\s*$")
# Models sometimes echo the format example's placeholder instead of a real
# path; treating it as a filename would attribute the block to the wrong file.
_PLACEHOLDER_PATHS = {"path/to/file.py", "<path>", "file.py"}


@dataclass
class Edit:
    file: str
    search: str
    replace: str


def _path_before(text: str, block_start: int, known_files: list[str]) -> str:
    """The file a block belongs to: the nearest preceding line naming one of
    the files we actually supplied as context.
    """
    for line in reversed(text[:block_start].splitlines()):
        stripped = line.strip().strip("`").strip("<>").strip().rstrip(":").strip()
        if not stripped or _FENCE_LINE_RE.match(line) or stripped in _PLACEHOLDER_PATHS:
            continue
        for path in known_files:
            if stripped == path or stripped.endswith(path):
                return path
    if len(known_files) == 1:
        # Unambiguous: only one file was ever offered, so an unlabelled block
        # can only mean that one.
        return known_files[0]
    raise MalformedPatchError(
        "A SEARCH/REPLACE block is not preceded by one of the provided file paths"
    )


def parse_edits(raw_output: str, known_files: list[str]) -> list[Edit]:
    """Parse every SEARCH/REPLACE block out of raw model output.

    Raises MalformedPatchError if the output contains no block at all, which
    is the signal the caller uses to fall back to plain unified-diff output.
    """
    edits = [
        Edit(file=_path_before(raw_output, m.start(), known_files), search=m.group(1), replace=m.group(2))
        for m in _BLOCK_RE.finditer(raw_output)
    ]
    if not edits:
        raise MalformedPatchError("Model output contains no SEARCH/REPLACE block")
    return edits


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _reindent(lines: list[str], delta: int) -> list[str]:
    if delta == 0:
        return lines
    if delta > 0:
        return [(" " * delta + line) if line.strip() else line for line in lines]
    return [line[min(-delta, _indent_of(line)) :] if line.strip() else line for line in lines]


def _match_ignoring_indent(text_lines: list[str], search_lines: list[str]) -> int | None:
    """Index of the one run of `text_lines` equal to `search_lines` once
    per-line indentation is ignored, or None if there is not exactly one.
    """
    wanted = [line.strip() for line in search_lines]
    span = len(wanted)
    if span == 0:
        return None
    hits = [
        i
        for i in range(len(text_lines) - span + 1)
        if [line.strip() for line in text_lines[i : i + span]] == wanted
    ]
    return hits[0] if len(hits) == 1 else None


def apply_edits_to_text(text: str, edits: list[Edit]) -> str:
    """Resolve edits against one file's text, in order.

    A SEARCH string that is absent, or that matches more than once, is a
    rejection rather than a guess -- applying the wrong occurrence would
    silently produce a patch that verifies against the wrong code.

    Exact matching comes first. Small models routinely re-emit a snippet
    dedented to column zero, which is a formatting slip rather than a wrong
    edit, so a failed exact match falls back to matching the same lines
    ignoring indentation -- still requiring exactly one hit -- and the
    replacement is re-indented to the file's actual column before being
    spliced in.
    """
    for edit in edits:
        occurrences = text.count(edit.search)
        if occurrences > 1:
            raise MalformedPatchError(
                f"SEARCH text is ambiguous in {edit.file}: it matches {occurrences} locations"
            )
        if occurrences == 1:
            text = text.replace(edit.search, edit.replace, 1)
            continue

        text_lines = text.splitlines()
        search_lines = edit.search.splitlines()
        start = _match_ignoring_indent(text_lines, search_lines)
        if start is None:
            head = edit.search.strip().splitlines()[:1]
            raise MalformedPatchError(
                f"SEARCH text not found in {edit.file}: {head[0][:80] if head else '<empty>'!r}"
            )

        delta = _indent_of(text_lines[start]) - _indent_of(search_lines[0])
        replacement = _reindent(edit.replace.splitlines(), delta)
        trailing_newline = text.endswith("\n")
        text_lines[start : start + len(search_lines)] = replacement
        text = "\n".join(text_lines) + ("\n" if trailing_newline else "")
    return text


def edits_to_diff(edits: list[Edit], sources: dict[str, str]) -> str:
    """Build one unified diff covering every edited file."""
    chunks: list[str] = []
    for path in dict.fromkeys(edit.file for edit in edits):
        original = sources.get(path)
        if original is None:
            raise MalformedPatchError(f"Edit targets a file that was not provided as context: {path}")

        updated = apply_edits_to_text(original, [e for e in edits if e.file == path])
        if updated == original:
            raise MalformedPatchError(f"Edits to {path} change nothing")

        chunks.extend(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{path}",
                tofile=f"b/{path}",
                n=3,
            )
        )

    # difflib emits the final line verbatim, so a source file without a
    # trailing newline would yield a diff `git apply` calls truncated.
    diff = "".join(line if line.endswith("\n") else line + "\n" for line in chunks)
    if not diff.strip():
        raise MalformedPatchError("Edits produced an empty diff")
    return diff
