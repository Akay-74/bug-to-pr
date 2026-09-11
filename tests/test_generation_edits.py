import pytest

from backend.generation.edits import Edit, apply_edits_to_text, edits_to_diff, parse_edits
from backend.generation.patch import MalformedPatchError

_SOURCE = 'def add(a, b):\n    """Deliberately wrong."""\n    return a - b\n'
_SOURCES = {"mypkg/__init__.py": _SOURCE}

_BLOCK = """mypkg/__init__.py
<<<<<<< SEARCH
    return a - b
=======
    return a + b
>>>>>>> REPLACE
"""


def test_parse_edits_extracts_file_search_and_replace():
    edits = parse_edits(_BLOCK, ["mypkg/__init__.py"])

    assert len(edits) == 1
    assert edits[0].file == "mypkg/__init__.py"
    assert edits[0].search == "    return a - b"
    assert edits[0].replace == "    return a + b"


def test_parse_edits_tolerates_commentary_and_short_markers():
    raw = """Here is the fix.

mypkg/__init__.py
<<<<<< SEARCH
    return a - b
======
    return a + b
>>>>>> REPLACE

Hope that helps!
"""
    edits = parse_edits(raw, ["mypkg/__init__.py"])

    assert edits[0].replace == "    return a + b"


def test_parse_edits_strips_angle_brackets_around_the_path():
    # Real local-model output (docs/phase3.md end-to-end run): the path is
    # wrapped in angle brackets instead of given bare.
    raw = """<mypkg/__init__.py>
<<<<<<< SEARCH
    return a - b
=======
    return a + b
>>>>>>> REPLACE
"""
    edits = parse_edits(raw, ["mypkg/__init__.py"])

    assert edits[0].file == "mypkg/__init__.py"
    assert edits[0].replace == "    return a + b"


def test_parse_edits_handles_multiple_blocks_in_one_file():
    raw = _BLOCK + """
mypkg/__init__.py
<<<<<<< SEARCH
def add(a, b):
=======
def add(a, b=0):
>>>>>>> REPLACE
"""
    edits = parse_edits(raw, ["mypkg/__init__.py"])

    assert len(edits) == 2
    assert all(e.file == "mypkg/__init__.py" for e in edits)


def test_parse_edits_infers_the_only_known_file_when_block_is_unlabelled():
    raw = "<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE\n"

    assert parse_edits(raw, ["mypkg/__init__.py"])[0].file == "mypkg/__init__.py"


def test_parse_edits_rejects_unlabelled_block_when_several_files_are_offered():
    raw = "<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n"

    with pytest.raises(MalformedPatchError, match="not preceded by"):
        parse_edits(raw, ["one.py", "two.py"])


def test_parse_edits_raises_when_output_contains_no_block():
    with pytest.raises(MalformedPatchError, match="no SEARCH/REPLACE block"):
        parse_edits("I would rather not, thank you.", ["mypkg/__init__.py"])


def test_edits_to_diff_produces_an_appliable_unified_diff():
    diff = edits_to_diff(parse_edits(_BLOCK, list(_SOURCES)), _SOURCES)

    assert diff.startswith("--- a/mypkg/__init__.py")
    assert "+++ b/mypkg/__init__.py" in diff
    assert "-    return a - b" in diff
    assert "+    return a + b" in diff
    assert diff.endswith("\n")


def test_edits_to_diff_rejects_a_file_that_was_not_provided_as_context():
    edits = [Edit(file="secrets/creds.py", search="a", replace="b")]

    with pytest.raises(MalformedPatchError, match="not provided as context"):
        edits_to_diff(edits, _SOURCES)


def test_apply_edits_rejects_search_text_that_is_absent():
    edits = [Edit(file="mypkg/__init__.py", search="    return a * b", replace="    return a + b")]

    with pytest.raises(MalformedPatchError, match="not found"):
        apply_edits_to_text(_SOURCE, edits)


def test_apply_edits_rejects_ambiguous_search_text():
    source = "x = 1\ny = 2\nx = 1\n"
    edits = [Edit(file="m.py", search="x = 1", replace="x = 3")]

    with pytest.raises(MalformedPatchError, match="ambiguous"):
        apply_edits_to_text(source, edits)


def test_edits_to_diff_rejects_a_no_op_edit():
    edits = [Edit(file="mypkg/__init__.py", search="    return a - b", replace="    return a - b")]

    with pytest.raises(MalformedPatchError, match="change nothing"):
        edits_to_diff(edits, _SOURCES)


def test_edits_to_diff_handles_a_source_file_without_a_trailing_newline():
    sources = {"m.py": "value = 1"}
    edits = [Edit(file="m.py", search="value = 1", replace="value = 2")]

    diff = edits_to_diff(edits, sources)

    assert all(line.endswith("\n") for line in diff.splitlines(keepends=True))


_INDENTED_SOURCE = '''class Point:
    def distance(self, p):
        return sqrt(sum([(a - b)**2 for a, b in zip(
            self.args, p.args)]))
'''


def test_dedented_search_block_still_matches_and_is_reindented():
    """Small models routinely re-emit a snippet flush to column zero. That is
    a formatting slip, not a different edit -- the replacement must land back
    at the file's real indentation.
    """
    edits = [
        Edit(
            file="point.py",
            search="return sqrt(sum([(a - b)**2 for a, b in zip(\n    self.args, p.args)]))",
            replace="return sqrt(sum([(a - b)**2 for a, b in zip_longest(\n    self.args, p.args, fillvalue=0)]))",
        )
    ]

    updated = apply_edits_to_text(_INDENTED_SOURCE, edits)

    assert "        return sqrt(sum([(a - b)**2 for a, b in zip_longest(" in updated
    assert "            self.args, p.args, fillvalue=0)]))" in updated
    assert updated.endswith("\n")


def test_exactly_repeated_search_text_is_reported_as_ambiguous():
    source = "def a():\n    x = 1\ndef b():\n    x = 1\n"
    edits = [Edit(file="m.py", search="x = 1", replace="x = 2")]

    with pytest.raises(MalformedPatchError, match="ambiguous"):
        apply_edits_to_text(source, edits)


def test_dedented_search_matching_two_places_is_rejected_not_guessed():
    """The indentation fallback must not pick one of several candidates."""
    source = "def a():\n    y = 0\n    x = 1\ndef b():\n        y = 0\n        x = 1\n"
    edits = [Edit(file="m.py", search="y = 0\nx = 1", replace="y = 0\nx = 2")]

    with pytest.raises(MalformedPatchError, match="not found"):
        apply_edits_to_text(source, edits)


def test_dedent_fallback_does_not_mask_a_genuinely_absent_search():
    edits = [Edit(file="point.py", search="totally unrelated line", replace="x")]

    with pytest.raises(MalformedPatchError, match="not found"):
        apply_edits_to_text(_INDENTED_SOURCE, edits)


def test_placeholder_path_from_the_prompt_example_is_not_treated_as_a_file():
    raw = "path/to/file.py\n<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE\n"

    # Falls through the placeholder to the single real file we offered.
    assert parse_edits(raw, ["mypkg/__init__.py"])[0].file == "mypkg/__init__.py"
