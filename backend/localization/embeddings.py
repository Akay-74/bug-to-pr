"""Local code embeddings for semantic retrieval (docs/phase2.md §3).

Two real backends are provided — Jina Code Embeddings and CodeRankEmbed —
both built on sentence-transformers and loaded lazily, so importing this
module (and running the rest of the test suite) never requires
torch/sentence-transformers to be installed. Unit tests inject a small fake
`EmbeddingBackend` instead of loading either real model.

Embeddings are cached to disk per (repo, commit, model) under
backend/cache/<repo>/<commit>/<model>/ so re-indexing the same repository at
the same commit never recomputes them (docs/phase2.md §3, §9).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from backend.config import settings


class EmbeddingBackend(Protocol):
    model_id: str

    def embed_documents(self, texts: list[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class SentenceTransformerBackend:
    """Shared sentence-transformers plumbing for the two evaluated models.

    docs/phase2.md §7 requires the non-embedding retrieval logic to stay
    identical between the Jina and CodeRankEmbed experiments — this class
    *is* that shared logic; only model_id/prefixes differ per subclass.
    """

    model_id: str = ""
    query_prefix: str = ""
    document_prefix: str = ""

    # A repo-sized batch of code chunks encoded in one `.encode()` call with
    # the library's default batch_size (32) can OOM an 8GB consumer GPU:
    # this model's attention-mask materialization scales with
    # batch_size * seq_len^2, and a handful of long chunks in the same batch
    # is enough to blow past available memory. A small batch_size plus a
    # capped max_seq_length keeps peak memory bounded regardless of how many
    # chunks a repository has (docs/phase2.md §9 -- "keep the implementation
    # suitable for the current benchmark size" -- this is what makes that
    # true on modest hardware).
    _ENCODE_BATCH_SIZE = 4
    _MAX_SEQ_LENGTH = 1024

    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise RuntimeError(
                    f"{self.model_id} requires the 'sentence-transformers' package "
                    "(pip install sentence-transformers). It is not needed for "
                    "unit tests, which inject a fake embedding backend."
                ) from exc
            self._model = SentenceTransformer(self.model_id, trust_remote_code=True)
            self._model.max_seq_length = self._MAX_SEQ_LENGTH
        return self._model

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        prefixed = [self.document_prefix + t for t in texts]
        return np.asarray(
            model.encode(prefixed, normalize_embeddings=True, batch_size=self._ENCODE_BATCH_SIZE, show_progress_bar=False)
        )

    def embed_query(self, text: str) -> np.ndarray:
        model = self._load()
        return np.asarray(model.encode([self.query_prefix + text], normalize_embeddings=True))[0]


class JinaCodeEmbeddingsBackend(SentenceTransformerBackend):
    """jinaai/jina-code-embeddings-0.5b in natural-language-to-code retrieval
    mode: queries are natural language (the issue/problem statement),
    documents are code chunks.
    """

    model_id = "jinaai/jina-code-embeddings-0.5b"
    query_prefix = "Represent this query for searching relevant code: "
    document_prefix = ""


class CodeRankEmbedBackend(SentenceTransformerBackend):
    """nomic-ai/CodeRankEmbed — the second model evaluated in docs/phase2.md
    §7, wired through the exact same retrieval pipeline as Jina above.
    """

    model_id = "nomic-ai/CodeRankEmbed"
    query_prefix = "Represent this query for retrieving relevant code: "
    document_prefix = ""


_BACKENDS: dict[str, type[SentenceTransformerBackend]] = {
    "jina": JinaCodeEmbeddingsBackend,
    "coderank": CodeRankEmbedBackend,
}


def get_backend(name: str | None = None) -> EmbeddingBackend:
    name = name or settings.LOCALIZATION_EMBEDDING_MODEL
    try:
        return _BACKENDS[name]()
    except KeyError:
        raise ValueError(f"Unknown embedding backend '{name}'. Options: {sorted(_BACKENDS)}") from None


def cosine_similarity(query: np.ndarray, documents: np.ndarray) -> np.ndarray:
    """Cosine similarity of one query vector against a matrix of document
    vectors. A degenerate (all-zero) embedding gets similarity 0.0 rather
    than raising — it should just rank last, not crash retrieval.
    """
    query = np.asarray(query, dtype=np.float64)
    documents = np.asarray(documents, dtype=np.float64)
    if documents.size == 0:
        return np.zeros(0)
    query_norm = np.linalg.norm(query)
    doc_norms = np.linalg.norm(documents, axis=1)
    dots = documents @ query
    with np.errstate(divide="ignore", invalid="ignore"):
        sims = np.where((doc_norms > 0) & (query_norm > 0), dots / (doc_norms * query_norm), 0.0)
    return sims


def _sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def cache_key(repo: str, commit: str, model_id: str) -> str:
    """Deterministic, filesystem-safe key for one (repo, commit, model)
    embedding set — same inputs always produce the same key.
    """
    raw = f"{repo}@{commit}@{model_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def cache_dir_for(repo: str, commit: str, model_id: str, cache_root: Path | None = None) -> Path:
    root = cache_root or settings.localization_cache_dir
    return root / _sanitize(repo) / commit / _sanitize(model_id)


@dataclass
class CachedEmbeddings:
    embeddings: np.ndarray
    chunk_ids: list[str]
    model_id: str


def load_cached_embeddings(
    repo: str, commit: str, model_id: str, cache_root: Path | None = None
) -> CachedEmbeddings | None:
    directory = cache_dir_for(repo, commit, model_id, cache_root)
    embeddings_path = directory / "embeddings.npy"
    meta_path = directory / "chunk_ids.json"
    config_path = directory / "config.json"
    if not (embeddings_path.exists() and meta_path.exists() and config_path.exists()):
        return None

    config = json.loads(config_path.read_text())
    if config.get("model_id") != model_id:
        return None

    embeddings = np.load(embeddings_path)
    chunk_ids = json.loads(meta_path.read_text())
    if len(chunk_ids) != embeddings.shape[0]:
        return None
    return CachedEmbeddings(embeddings=embeddings, chunk_ids=chunk_ids, model_id=model_id)


def save_embeddings(
    repo: str,
    commit: str,
    model_id: str,
    embeddings: np.ndarray,
    chunk_ids: list[str],
    cache_root: Path | None = None,
) -> Path:
    embeddings = np.asarray(embeddings)
    directory = cache_dir_for(repo, commit, model_id, cache_root)
    directory.mkdir(parents=True, exist_ok=True)
    np.save(directory / "embeddings.npy", embeddings)
    (directory / "chunk_ids.json").write_text(json.dumps(chunk_ids))
    (directory / "config.json").write_text(
        json.dumps(
            {
                "model_id": model_id,
                "repo": repo,
                "commit": commit,
                "embedding_dim": int(embeddings.shape[1]) if embeddings.size else 0,
                "normalized": True,
            },
            indent=2,
        )
    )
    return directory
