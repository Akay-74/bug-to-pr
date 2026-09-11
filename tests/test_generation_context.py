from backend.generation.context import read_candidate_source, read_candidate_sources, read_files
from backend.localization.service import LocalizationCandidate
from tests.conftest import FIXTURE_ISSUE_ID


def _candidate(**overrides):
    defaults = dict(
        rank=1,
        file="mypkg/__init__.py",
        symbol="add",
        symbol_type="function",
        start_line=2,
        end_line=2,
        semantic_score=0.0,
        lexical_score=0.0,
        relevance_score=0.0,
        final_score=0.0,
        matched_terms=[],
    )
    defaults.update(overrides)
    return LocalizationCandidate(**defaults)


def test_read_candidate_source_returns_verbatim_padded_lines(fixture_worktree):
    snippet = read_candidate_source(fixture_worktree, _candidate(start_line=2, end_line=2))

    # Verbatim, so a SEARCH string copied out of it matches the file exactly.
    assert "def add(a, b):" in snippet
    assert "return a - b" in snippet
    assert "1: def add" not in snippet


def test_read_candidate_source_returns_empty_string_for_missing_file(fixture_worktree):
    assert read_candidate_source(fixture_worktree, _candidate(file="mypkg/does_not_exist.py")) == ""


def test_read_candidate_sources_merges_candidates_from_the_same_file(fixture_worktree):
    candidates = [
        _candidate(rank=1, start_line=1, end_line=1),
        _candidate(rank=2, start_line=3, end_line=3),
    ]

    sources = read_candidate_sources(fixture_worktree, candidates)

    assert list(sources) == ["mypkg/__init__.py"]
    # Both candidates' context survives; the second must not overwrite the first.
    assert "def add(a, b):" in sources["mypkg/__init__.py"]
    assert "return a - b" in sources["mypkg/__init__.py"]


def test_read_candidate_sources_skips_unreadable_files(fixture_worktree):
    sources = read_candidate_sources(fixture_worktree, [_candidate(file="nope.py")])

    assert sources == {}


def test_read_files_returns_full_text_and_skips_missing_files(fixture_worktree):
    contents = read_files(fixture_worktree, ["mypkg/__init__.py", "nope.py"])

    assert list(contents) == ["mypkg/__init__.py"]
    assert contents["mypkg/__init__.py"].startswith("def add(a, b):")


def test_read_regression_context_includes_ids_command_and_test_body(tmp_path):
    from backend.benchmark.loader import Issue, load_issue
    from backend.generation.context import read_regression_context

    (tmp_path / "test_patch.diff").write_text(
        "--- a/tests/test_add.py\n+++ b/tests/test_add.py\n@@ -1,0 +1,2 @@\n"
        "+def test_issue_1():\n+    assert add(1, 2) == 3\n"
    )
    base = load_issue(FIXTURE_ISSUE_ID)
    issue = Issue(metadata=base.metadata, problem=base.problem, directory=tmp_path)

    context = read_regression_context(issue)

    assert base.metadata.regression_test_ids[0] in context
    assert base.metadata.regression_test_command in context
    assert "assert add(1, 2) == 3" in context
    # Only added lines: the diff's own headers must not leak in.
    assert "+++" not in context


def test_read_regression_context_survives_a_missing_test_patch(tmp_path):
    from backend.benchmark.loader import Issue, load_issue
    from backend.generation.context import read_regression_context

    base = load_issue(FIXTURE_ISSUE_ID)
    context = read_regression_context(Issue(metadata=base.metadata, problem=base.problem, directory=tmp_path))

    assert base.metadata.regression_test_command in context
