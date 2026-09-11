import numpy as np

from backend.localization.embeddings import (
    cache_dir_for,
    cache_key,
    cosine_similarity,
    load_cached_embeddings,
    save_embeddings,
)


def test_cache_key_is_deterministic():
    a = cache_key("django/django", "abc123", "jinaai/jina-code-embeddings-0.5b")
    b = cache_key("django/django", "abc123", "jinaai/jina-code-embeddings-0.5b")
    assert a == b


def test_cache_key_differs_by_repo_commit_or_model():
    base = cache_key("django/django", "abc123", "jina")
    assert base != cache_key("django/other", "abc123", "jina")
    assert base != cache_key("django/django", "def456", "jina")
    assert base != cache_key("django/django", "abc123", "coderank")


def test_cache_dir_for_is_stable_and_filesystem_safe(tmp_path):
    directory = cache_dir_for("django/django", "abc123", "jinaai/jina-code-embeddings-0.5b", cache_root=tmp_path)
    assert directory == cache_dir_for(
        "django/django", "abc123", "jinaai/jina-code-embeddings-0.5b", cache_root=tmp_path
    )
    # No raw "/" from the repo/model id leaking into intermediate path
    # segments (that would create unwanted nested directories).
    assert directory.relative_to(tmp_path).parts[0] != "django"


def test_embedding_cache_round_trip(tmp_path):
    embeddings = np.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]])
    chunk_ids = ["a", "b", "c"]

    save_embeddings("repo", "commit", "model", embeddings, chunk_ids, cache_root=tmp_path)
    cached = load_cached_embeddings("repo", "commit", "model", cache_root=tmp_path)

    assert cached is not None
    assert cached.chunk_ids == chunk_ids
    assert cached.model_id == "model"
    np.testing.assert_array_equal(cached.embeddings, embeddings)


def test_embedding_cache_miss_when_absent(tmp_path):
    assert load_cached_embeddings("repo", "commit", "model", cache_root=tmp_path) is None


def test_embedding_cache_miss_when_model_differs(tmp_path):
    save_embeddings("repo", "commit", "model-a", np.array([[1.0]]), ["a"], cache_root=tmp_path)

    # A different model has its own cache_dir_for path, so this simulates
    # someone pointing load at the wrong directory / a stale config file.
    directory = cache_dir_for("repo", "commit", "model-a", cache_root=tmp_path)
    config_path = directory / "config.json"
    config = config_path.read_text().replace("model-a", "model-b")
    config_path.write_text(config)

    assert load_cached_embeddings("repo", "commit", "model-a", cache_root=tmp_path) is None


def test_cosine_similarity_identical_vectors_score_one():
    query = np.array([1.0, 0.0])
    documents = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])

    sims = cosine_similarity(query, documents)

    np.testing.assert_allclose(sims, [1.0, 0.0, -1.0], atol=1e-8)


def test_cosine_similarity_handles_zero_vector_without_raising():
    query = np.array([1.0, 0.0])
    documents = np.array([[0.0, 0.0], [1.0, 0.0]])

    sims = cosine_similarity(query, documents)

    assert sims[0] == 0.0
    assert sims[1] == 1.0


def test_cosine_similarity_empty_documents():
    query = np.array([1.0, 0.0])
    documents = np.zeros((0, 2))

    sims = cosine_similarity(query, documents)

    assert sims.shape == (0,)
