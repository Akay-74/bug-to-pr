from pathlib import Path

from backend.localization import chunking, lexical


def test_extract_query_terms_picks_up_exception_and_quoted_phrase():
    problem = 'Calling `add(a, b)` raises a ValueError with message "unexpected sign".'

    terms = lexical.extract_query_terms(problem)

    assert "add(a, b)" in terms
    assert "ValueError" in terms
    assert "unexpected sign" in terms


def test_extract_query_terms_filters_common_words():
    problem = "This is a bug that we found when the tests were run for the feature."

    terms = lexical.extract_query_terms(problem)

    for stopword in ("this", "that", "when", "were", "for", "the"):
        assert stopword not in [t.lower() for t in terms]


def test_extract_query_terms_deduplicates_case_insensitively():
    problem = "FooBar breaks. FooBar also breaks elsewhere."

    terms = lexical.extract_query_terms(problem)

    assert terms.count("FooBar") == 1


def test_search_terms_and_score_chunks_against_real_repo(tmp_path):
    (tmp_path / "mod.py").write_text("def add(a, b):\n    return a - b\n")
    (tmp_path / "other.py").write_text("def unrelated():\n    return None\n")

    files = chunking.enumerate_python_files(tmp_path)
    chunks = chunking.index_files(files, tmp_path, "repo", "commit")

    hits = lexical.search_terms(tmp_path, ["add", "unrelated"])
    scores = lexical.score_chunks(chunks, hits, total_terms=2)

    add_chunk = next(c for c in chunks if c.symbol_name == "add")
    unrelated_chunk = next(c for c in chunks if c.symbol_name == "unrelated")

    assert scores[add_chunk.chunk_id] == 0.5  # 1 of 2 query terms hits this chunk
    assert scores[unrelated_chunk.chunk_id] == 0.5


def test_score_chunks_returns_empty_when_no_terms():
    assert lexical.score_chunks([], [], total_terms=0) == {}


def test_matched_terms_for_chunk_is_deduplicated_and_scoped_to_file(tmp_path: Path):
    chunk = chunking.Chunk(
        repo="r", commit="c", file_path="mod.py", symbol_type="function",
        symbol_name="add", start_line=1, end_line=2, text="",
    )
    hits = [
        lexical.LexicalHit(file_path="mod.py", line=1, term="add"),
        lexical.LexicalHit(file_path="mod.py", line=1, term="ADD"),  # dup, different case
        lexical.LexicalHit(file_path="mod.py", line=99, term="add"),  # outside chunk range
        lexical.LexicalHit(file_path="other.py", line=1, term="add"),  # different file
    ]

    matched = lexical.matched_terms_for_chunk(chunk, hits)

    assert matched == ["add"]
