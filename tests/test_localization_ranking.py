from backend.localization.chunking import Chunk
from backend.localization.ranking import (
    combine_scores,
    normalize_scores,
    symbol_file_relevance,
    top_k_unique,
)


def _chunk(symbol_name, file_path="mod.py", chunk_part=0, chunk_parts_total=1, start_line=1, end_line=2):
    return Chunk(
        repo="r",
        commit="c",
        file_path=file_path,
        symbol_type="function",
        symbol_name=symbol_name,
        start_line=start_line,
        end_line=end_line,
        text="",
        chunk_part=chunk_part,
        chunk_parts_total=chunk_parts_total,
    )


def test_normalize_scores_empty():
    assert normalize_scores({}) == {}


def test_normalize_scores_all_zero_stays_zero():
    assert normalize_scores({"a": 0.0, "b": 0.0}) == {"a": 0.0, "b": 0.0}


def test_normalize_scores_single_nonzero_value_is_not_collapsed_to_zero():
    """Regression test: a lone nonzero entry must not be treated as a tie
    with itself and zeroed out — it should end up at the top of [0, 1].
    """
    assert normalize_scores({"a": 5.0}) == {"a": 1.0}


def test_normalize_scores_tied_nonzero_values_go_to_one():
    assert normalize_scores({"a": 3.0, "b": 3.0}) == {"a": 1.0, "b": 1.0}


def test_normalize_scores_min_max():
    result = normalize_scores({"a": 0.0, "b": 5.0, "c": 10.0})
    assert result == {"a": 0.0, "b": 0.5, "c": 1.0}


def test_symbol_file_relevance_rewards_symbol_and_file_matches():
    chunk = _chunk("parse_url", file_path="lib/url_parser.py")
    score = symbol_file_relevance(chunk, ["parse_url", "unrelated_term"])
    assert score > 0.0

    no_match_score = symbol_file_relevance(chunk, ["totally_different"])
    assert no_match_score == 0.0


def test_symbol_file_relevance_no_terms_is_zero():
    chunk = _chunk("foo")
    assert symbol_file_relevance(chunk, []) == 0.0


def test_combine_scores_weights_and_ranks():
    chunks = [_chunk("winner"), _chunk("loser")]
    semantic_raw = {"mod.py::winner:1-2": 1.0, "mod.py::loser:1-2": 0.0}
    lexical_raw = {"mod.py::winner:1-2": 1.0}
    results = combine_scores(chunks, semantic_raw, lexical_raw, query_terms=[], matched_terms_by_chunk={})

    assert results[0].chunk.symbol_name == "winner"
    assert results[0].final_score > results[1].final_score
    # final_score = 0.45*semantic + 0.35*lexical + 0.20*relevance
    assert abs(results[0].final_score - (0.45 * 1.0 + 0.35 * 1.0 + 0.20 * 0.0)) < 1e-9


def test_top_k_unique_deduplicates_split_chunks_of_same_symbol():
    chunks = [
        _chunk("big_func", chunk_part=0, chunk_parts_total=2, start_line=1, end_line=50),
        _chunk("big_func", chunk_part=1, chunk_parts_total=2, start_line=40, end_line=90),
        _chunk("other_func", start_line=1, end_line=2),
    ]
    semantic_raw = {chunks[0].chunk_id: 0.9, chunks[1].chunk_id: 0.5, chunks[2].chunk_id: 0.1}
    results = combine_scores(chunks, semantic_raw, {}, query_terms=[], matched_terms_by_chunk={})

    top = top_k_unique(results, k=5)

    symbol_names = [r.chunk.symbol_name for r in top]
    assert symbol_names.count("big_func") == 1
    # The higher-scoring split part (part 0) is the one kept.
    kept = next(r for r in top if r.chunk.symbol_name == "big_func")
    assert kept.chunk.chunk_part == 0


def test_top_k_unique_respects_k():
    chunks = [_chunk(f"fn_{i}") for i in range(10)]
    semantic_raw = {c.chunk_id: float(i) for i, c in enumerate(chunks)}
    results = combine_scores(chunks, semantic_raw, {}, query_terms=[], matched_terms_by_chunk={})

    top = top_k_unique(results, k=5)

    assert len(top) == 5
    assert top[0].chunk.symbol_name == "fn_9"
