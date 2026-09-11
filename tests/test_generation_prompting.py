from backend.benchmark.loader import load_issue
from backend.generation.prompting import build_prompt
from backend.localization.service import LocalizationCandidate
from tests.conftest import FIXTURE_ISSUE_ID


def _candidate(**overrides):
    defaults = dict(
        rank=1,
        file="mypkg/__init__.py",
        symbol="add",
        symbol_type="function",
        start_line=1,
        end_line=3,
        semantic_score=0.9,
        lexical_score=0.8,
        relevance_score=0.7,
        final_score=0.85,
        matched_terms=["add"],
    )
    defaults.update(overrides)
    return LocalizationCandidate(**defaults)


def test_build_prompt_includes_issue_and_candidate_location():
    issue = load_issue(FIXTURE_ISSUE_ID)
    candidate = _candidate()

    prompt = build_prompt(issue, [candidate], {"mypkg/__init__.py": "1: def add(a, b):"})

    assert issue.metadata.issue_id in prompt
    assert issue.problem.strip() in prompt
    assert "mypkg/__init__.py:1-3" in prompt
    assert "def add(a, b)" in prompt
    assert "<<<<<<< SEARCH" in prompt
    assert ">>>>>>> REPLACE" in prompt


def test_build_prompt_includes_previous_attempt_notes():
    issue = load_issue(FIXTURE_ISSUE_ID)
    candidate = _candidate()

    prompt = build_prompt(issue, [candidate], {}, previous_attempt_notes=["Regression test still fails."])

    assert "Regression test still fails." in prompt
    assert "Try a different approach" in prompt


def test_build_prompt_omits_notes_section_when_no_previous_attempts():
    issue = load_issue(FIXTURE_ISSUE_ID)
    prompt = build_prompt(issue, [_candidate()], {})

    assert "Previous attempts failed" not in prompt


def test_prompt_lists_the_real_editable_paths():
    """The format example carries a placeholder path; the real paths must be
    stated so the model does not echo the placeholder back.
    """
    issue = load_issue(FIXTURE_ISSUE_ID)

    prompt = build_prompt(issue, [_candidate()], {"mypkg/__init__.py": "def add(a, b): ..."})

    assert "## Editable files" in prompt
    assert "- mypkg/__init__.py" in prompt
