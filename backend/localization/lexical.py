"""Lexical retrieval via ripgrep — no LLM involved (docs/phase2.md §2)."""
from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from backend.localization.chunking import Chunk

# A short, generic stopword list so common English words in a problem
# statement don't get treated as distinctive search terms and drown out the
# identifiers/exception names/phrases that actually help ripgrep find code.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "being", "this", "that", "these", "those", "it", "its", "to",
    "of", "in", "on", "for", "with", "as", "by", "at", "from", "into",
    "when", "then", "than", "not", "no", "if", "so", "do", "does", "did",
    "has", "have", "had", "will", "would", "should", "could", "can", "may",
    "we", "you", "they", "i", "he", "she", "which", "what", "who",
    "there", "here", "also", "such", "some", "any", "all", "each",
}

_EXCEPTION_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)\b")
_QUOTED_RE = re.compile(r'"([^"\n]{3,80})"|\'([^\'\n]{3,80})\'|`([^`\n]{2,80})`')
_IDENTIFIER_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")

_MAX_TERMS = 25


def extract_query_terms(problem_statement: str) -> list[str]:
    """Distinctive terms to search for: exception names, quoted error
    messages/phrases, and identifiers (function/class names, other symbols)
    — the categories docs/phase2.md §2 lists. Deduplicated, common English
    words filtered out, capped so a long problem statement doesn't turn into
    an expensive ripgrep-per-term fan-out.
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        term = term.strip()
        if not term or term.lower() in seen:
            return
        seen.add(term.lower())
        terms.append(term)

    for match in _EXCEPTION_RE.finditer(problem_statement):
        add(match.group(0))

    for match in _QUOTED_RE.finditer(problem_statement):
        quoted = next(g for g in match.groups() if g)
        add(quoted)

    for match in _IDENTIFIER_RE.finditer(problem_statement):
        word = match.group(0)
        if word.lower() in _STOPWORDS:
            continue
        if word.islower() and "_" not in word and len(word) < 5:
            # Short, all-lowercase, no-underscore words are unlikely to be
            # identifiers/distinctive phrases — skip prose noise like "line".
            continue
        add(word)

    return terms[:_MAX_TERMS]


@dataclass
class LexicalHit:
    file_path: str
    line: int
    term: str


def _ripgrep_hits(workspace: Path, term: str) -> list[LexicalHit]:
    result = subprocess.run(
        ["rg", "--no-heading", "--line-number", "--fixed-strings", "--", term, "."],
        cwd=workspace,
        capture_output=True,
        text=True,
    )
    # rg exits 1 for "no matches" (not an error) and 2 for real errors.
    if result.returncode not in (0, 1):
        return []

    hits = []
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 2:
            continue
        file_part, line_part = parts[0], parts[1]
        if not line_part.isdigit():
            continue
        file_path = Path(file_part).as_posix()
        if file_path.startswith("./"):
            file_path = file_path[2:]
        hits.append(LexicalHit(file_path=file_path, line=int(line_part), term=term))
    return hits


def search_terms(workspace: Path, terms: list[str]) -> list[LexicalHit]:
    hits: list[LexicalHit] = []
    for term in terms:
        hits.extend(_ripgrep_hits(workspace, term))
    return hits


def score_chunks(chunks: list[Chunk], hits: list[LexicalHit], total_terms: int) -> dict[str, float]:
    """Lexical score per chunk_id: fraction of distinct query terms that hit
    a line inside that chunk's [start_line, end_line] range in its file.
    """
    if total_terms == 0 or not chunks:
        return {}

    hits_by_file: dict[str, list[LexicalHit]] = defaultdict(list)
    for hit in hits:
        hits_by_file[hit.file_path].append(hit)

    scores: dict[str, float] = {}
    for chunk in chunks:
        matched_terms: set[str] = set()
        for hit in hits_by_file.get(chunk.file_path, []):
            if chunk.start_line <= hit.line <= chunk.end_line:
                matched_terms.add(hit.term.lower())
        if matched_terms:
            scores[chunk.chunk_id] = len(matched_terms) / total_terms
    return scores


def matched_terms_for_chunk(chunk: Chunk, hits: list[LexicalHit]) -> list[str]:
    matched: list[str] = []
    seen: set[str] = set()
    for hit in hits:
        if hit.file_path != chunk.file_path:
            continue
        if not (chunk.start_line <= hit.line <= chunk.end_line):
            continue
        if hit.term.lower() in seen:
            continue
        seen.add(hit.term.lower())
        matched.append(hit.term)
    return matched
