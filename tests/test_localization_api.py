import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app, default_embedding_backend
from backend.models.database import get_db
from tests.conftest import FIXTURE_ISSUE_ID


class FakeEmbeddingBackend:
    """Deterministic bag-of-words-ish fake so tests never load a real model.

    Rewards chunks whose text contains "add" for a query that also mentions
    "add" — enough signal to make ranking meaningfully test-able without any
    ML dependency.
    """

    model_id = "fake-test-backend"

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        return np.array([[float(len(t)), float(t.count("add"))] for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return np.array([float(len(text)), float(text.count("add"))])


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[default_embedding_backend] = lambda: FakeEmbeddingBackend()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_create_localization_returns_ranked_results(client):
    response = client.post(f"/api/localize/{FIXTURE_ISSUE_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["issue_id"] == FIXTURE_ISSUE_ID
    assert body["model_identifier"] == "fake-test-backend"
    assert body["index_identifier"]
    assert 1 <= len(body["results"]) <= 5

    top = body["results"][0]
    # The buggy `add()` function is the ground-truth fix location for this
    # fixture issue; the fake backend + real lexical/relevance signal should
    # surface it first.
    assert top["symbol"] == "add"
    assert top["file"] == "mypkg/__init__.py"
    for field in (
        "rank", "file", "symbol", "symbol_type", "start_line", "end_line",
        "semantic_score", "lexical_score", "relevance_score", "final_score", "matched_terms",
    ):
        assert field in top


def test_create_localization_ranks_are_sequential(client):
    response = client.post(f"/api/localize/{FIXTURE_ISSUE_ID}")
    ranks = [r["rank"] for r in response.json()["results"]]
    assert ranks == list(range(1, len(ranks) + 1))


def test_create_localization_unknown_issue_404(client):
    response = client.post("/api/localize/does-not-exist")
    assert response.status_code == 404


def test_get_localization_without_prior_run_404(client):
    response = client.get(f"/api/localize/{FIXTURE_ISSUE_ID}")
    assert response.status_code == 404


def test_get_localization_reads_back_persisted_run(client):
    create_response = client.post(f"/api/localize/{FIXTURE_ISSUE_ID}")
    get_response = client.get(f"/api/localize/{FIXTURE_ISSUE_ID}")

    assert get_response.status_code == 200
    assert get_response.json() == create_response.json()


def test_rerunning_localization_replaces_previous_results(client):
    first = client.post(f"/api/localize/{FIXTURE_ISSUE_ID}").json()
    second = client.post(f"/api/localize/{FIXTURE_ISSUE_ID}").json()

    # Same deterministic pipeline run twice should produce the same ranking,
    # and must not accumulate duplicate rows (rank stays sequential from 1).
    assert first["results"] == second["results"]
    assert [r["rank"] for r in second["results"]] == list(range(1, len(second["results"]) + 1))
