"""Localization service: the public interface for Phase 2 (docs/phase2.md §5).

Orchestrates repository indexing, lexical + semantic retrieval, and hybrid
ranking for one benchmark issue, and persists/reads the Top-5 result.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete
from sqlalchemy.orm import Session

from backend.benchmark.allowlist import is_repo_allowed
from backend.benchmark.loader import Issue, load_issue
from backend.localization import embeddings as embeddings_mod
from backend.localization import lexical, ranking
from backend.localization.chunking import Chunk
from backend.localization.embeddings import EmbeddingBackend
from backend.localization.indexer import checked_out_workspace, index_workspace
from backend.models.localization_result import LocalizationResult


class RepositoryNotAllowedError(Exception):
    pass


@dataclass
class LocalizationCandidate:
    rank: int
    file: str
    symbol: str
    symbol_type: str
    start_line: int
    end_line: int
    semantic_score: float
    lexical_score: float
    relevance_score: float
    final_score: float
    matched_terms: list[str]


@dataclass
class LocalizationOutcome:
    issue_id: str
    model_identifier: str
    index_identifier: str
    results: list[LocalizationCandidate]


def _semantic_scores(issue: Issue, chunks: list[Chunk], backend: EmbeddingBackend) -> tuple[dict[str, float], str]:
    """Embedding scores for every chunk against the issue's problem
    statement, using the on-disk cache when this repo+commit+model triple
    was already indexed (docs/phase2.md §3, §9).
    """
    repo = issue.metadata.repo
    commit = issue.metadata.base_commit
    model_id = backend.model_id
    index_id = embeddings_mod.cache_key(repo, commit, model_id)

    if not chunks:
        return {}, index_id

    chunk_ids = [c.chunk_id for c in chunks]
    cached = embeddings_mod.load_cached_embeddings(repo, commit, model_id)
    if cached is not None and cached.chunk_ids == chunk_ids:
        doc_embeddings = cached.embeddings
    else:
        doc_embeddings = backend.embed_documents([c.text for c in chunks])
        embeddings_mod.save_embeddings(repo, commit, model_id, doc_embeddings, chunk_ids)

    query_embedding = backend.embed_query(issue.problem)
    similarities = embeddings_mod.cosine_similarity(query_embedding, doc_embeddings)
    return {chunk_id: float(score) for chunk_id, score in zip(chunk_ids, similarities)}, index_id


def run_localization(issue: Issue, backend: EmbeddingBackend) -> LocalizationOutcome:
    """Run indexing + lexical/semantic retrieval + hybrid ranking for one
    issue, without touching persistence. Split out from `localize_issue` so
    the evaluation command (docs/phase2.md §7) can call it directly without
    a database session.
    """
    if not is_repo_allowed(issue.metadata.repo):
        raise RepositoryNotAllowedError(f"Repository '{issue.metadata.repo}' is not allowlisted")

    query_terms = lexical.extract_query_terms(issue.problem)

    with checked_out_workspace(issue.metadata.repo, issue.metadata.base_commit) as workspace:
        repo_index = index_workspace(workspace, issue.metadata.repo, issue.metadata.base_commit)
        chunks = repo_index.chunks
        lexical_hits = lexical.search_terms(workspace, query_terms)

    semantic_raw, index_id = _semantic_scores(issue, chunks, backend)
    lexical_raw = lexical.score_chunks(chunks, lexical_hits, total_terms=len(query_terms))
    matched_terms_by_chunk = {chunk.chunk_id: lexical.matched_terms_for_chunk(chunk, lexical_hits) for chunk in chunks}

    combined = ranking.combine_scores(chunks, semantic_raw, lexical_raw, query_terms, matched_terms_by_chunk)
    top = ranking.top_k_unique(combined, ranking.TOP_K)

    candidates = [
        LocalizationCandidate(
            rank=i + 1,
            file=r.chunk.file_path,
            symbol=r.chunk.symbol_name,
            symbol_type=r.chunk.symbol_type,
            start_line=r.chunk.start_line,
            end_line=r.chunk.end_line,
            semantic_score=r.semantic_score,
            lexical_score=r.lexical_score,
            relevance_score=r.relevance_score,
            final_score=r.final_score,
            matched_terms=r.matched_terms,
        )
        for i, r in enumerate(top)
    ]
    return LocalizationOutcome(
        issue_id=issue.metadata.issue_id,
        model_identifier=backend.model_id,
        index_identifier=index_id,
        results=candidates,
    )


def _persist(db: Session, outcome: LocalizationOutcome) -> None:
    # Replace-on-rerun, not append: Phase 2 only needs "the current best
    # answer" for an issue, not a history of every localization run.
    db.execute(delete(LocalizationResult).where(LocalizationResult.issue_id == outcome.issue_id))
    for candidate in outcome.results:
        db.add(
            LocalizationResult(
                issue_id=outcome.issue_id,
                rank=candidate.rank,
                file=candidate.file,
                symbol=candidate.symbol,
                symbol_type=candidate.symbol_type,
                start_line=candidate.start_line,
                end_line=candidate.end_line,
                semantic_score=candidate.semantic_score,
                lexical_score=candidate.lexical_score,
                relevance_score=candidate.relevance_score,
                final_score=candidate.final_score,
                matched_terms=candidate.matched_terms,
                model_identifier=outcome.model_identifier,
                index_identifier=outcome.index_identifier,
            )
        )
    db.commit()


def localize_issue(issue_id: str, db: Session, backend: EmbeddingBackend | None = None) -> LocalizationOutcome:
    """Run the full localization pipeline for `issue_id` and persist the
    result, replacing any previous run for the same issue.
    """
    issue = load_issue(issue_id)
    backend = backend or embeddings_mod.get_backend()
    outcome = run_localization(issue, backend)
    _persist(db, outcome)
    return outcome


def get_localization(issue_id: str, db: Session) -> LocalizationOutcome | None:
    """Read back a previously persisted localization run, if any."""
    rows = (
        db.query(LocalizationResult)
        .filter(LocalizationResult.issue_id == issue_id)
        .order_by(LocalizationResult.rank)
        .all()
    )
    if not rows:
        return None

    candidates = [
        LocalizationCandidate(
            rank=row.rank,
            file=row.file,
            symbol=row.symbol,
            symbol_type=row.symbol_type,
            start_line=row.start_line,
            end_line=row.end_line,
            semantic_score=row.semantic_score,
            lexical_score=row.lexical_score,
            relevance_score=row.relevance_score,
            final_score=row.final_score,
            matched_terms=row.matched_terms,
        )
        for row in rows
    ]
    return LocalizationOutcome(
        issue_id=issue_id,
        model_identifier=rows[0].model_identifier,
        index_identifier=rows[0].index_identifier,
        results=candidates,
    )
