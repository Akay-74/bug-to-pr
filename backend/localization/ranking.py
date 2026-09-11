"""Hybrid ranking: merge lexical and semantic candidates (docs/phase2.md §4)."""
from __future__ import annotations

from dataclasses import dataclass

from backend.localization.chunking import Chunk

SEMANTIC_WEIGHT = 0.45
LEXICAL_WEIGHT = 0.35
RELEVANCE_WEIGHT = 0.20

TOP_K = 5


def normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalize a {id: raw_score} mapping into [0, 1].

    The input must already be dense over the full id universe (a zero for
    every id with no raw signal, not merely absent) — see `combine_scores`,
    which builds that before calling this. Without the implicit zeros, a
    single chunk with the only lexical hit would be the sole entry, its
    value would equal both the min and max, and it would wrongly normalize
    to the "no signal" case below instead of ranking above everything else.

    An empty input maps to empty output. When every value is equal:
    - all zero (no signal anywhere) normalizes to 0.0 for every id, so it
      doesn't contribute to the final score.
    - all equal but nonzero (a genuine tie) normalizes to 1.0 for every id,
      since that shared value *is* real, undifferentiated signal.
    """
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: (1.0 if hi > 0 else 0.0) for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def symbol_file_relevance(chunk: Chunk, query_terms: list[str]) -> float:
    """Reward query terms appearing in the symbol name or file path — a
    cheap signal independent of both lexical line-hits and embedding
    similarity (docs/phase2.md §4).
    """
    if not query_terms:
        return 0.0
    symbol_lower = chunk.symbol_name.lower()
    file_lower = chunk.file_path.lower()
    symbol_hits = sum(1 for t in query_terms if t.lower() in symbol_lower)
    file_hits = sum(1 for t in query_terms if t.lower() in file_lower)
    score = 0.6 * (symbol_hits / len(query_terms)) + 0.4 * (file_hits / len(query_terms))
    return min(1.0, score)


@dataclass
class RankedResult:
    chunk: Chunk
    semantic_score: float
    lexical_score: float
    relevance_score: float
    final_score: float
    matched_terms: list[str]


def combine_scores(
    chunks: list[Chunk],
    semantic_raw: dict[str, float],
    lexical_raw: dict[str, float],
    query_terms: list[str],
    matched_terms_by_chunk: dict[str, list[str]],
) -> list[RankedResult]:
    """final_score = 0.45*semantic + 0.35*lexical + 0.20*symbol_file_relevance,
    per docs/phase2.md §4, with each component min-max normalized first.
    """
    chunk_ids = [c.chunk_id for c in chunks]
    # Dense over every chunk (0.0 where a chunk has no raw score at all) —
    # normalize_scores relies on this to tell "the one chunk with a hit"
    # apart from "no signal anywhere".
    dense_semantic = {cid: semantic_raw.get(cid, 0.0) for cid in chunk_ids}
    dense_lexical = {cid: lexical_raw.get(cid, 0.0) for cid in chunk_ids}
    semantic_norm = normalize_scores(dense_semantic)
    lexical_norm = normalize_scores(dense_lexical)

    results = []
    for chunk in chunks:
        semantic = semantic_norm.get(chunk.chunk_id, 0.0)
        lexical = lexical_norm.get(chunk.chunk_id, 0.0)
        relevance = symbol_file_relevance(chunk, query_terms)
        final = SEMANTIC_WEIGHT * semantic + LEXICAL_WEIGHT * lexical + RELEVANCE_WEIGHT * relevance
        results.append(
            RankedResult(
                chunk=chunk,
                semantic_score=semantic,
                lexical_score=lexical,
                relevance_score=relevance,
                final_score=final,
                matched_terms=matched_terms_by_chunk.get(chunk.chunk_id, []),
            )
        )
    results.sort(key=lambda r: r.final_score, reverse=True)
    return results


def top_k_unique(results: list[RankedResult], k: int = TOP_K) -> list[RankedResult]:
    """Top-k deduplicated by (file, symbol): keep each symbol's single
    best-scoring chunk so the Top-5 gives coverage across distinct locations
    rather than several split-chunks of one symbol (docs/phase2.md §4).
    """
    best_by_symbol: dict[tuple[str, str], RankedResult] = {}
    for result in results:
        key = (result.chunk.file_path, result.chunk.symbol_name)
        existing = best_by_symbol.get(key)
        if existing is None or result.final_score > existing.final_score:
            best_by_symbol[key] = result

    return sorted(best_by_symbol.values(), key=lambda r: r.final_score, reverse=True)[:k]
