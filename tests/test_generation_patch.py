import pytest

from backend.generation.patch import (
    MalformedPatchError,
    apply_patch,
    extract_diff,
    validate_patch_paths,
    validate_patch_size,
)

_GOOD_DIFF = """diff --git a/mypkg/__init__.py b/mypkg/__init__.py
--- a/mypkg/__init__.py
+++ b/mypkg/__init__.py
@@ -1,3 +1,3 @@
 def add(a, b):
     \"\"\"Deliberately wrong: subtracts instead of adding, for the fixture bug.\"\"\"
-    return a - b
+    return a + b
"""


def test_extract_diff_passes_through_clean_diff():
    assert extract_diff(_GOOD_DIFF) == _GOOD_DIFF.rstrip() + "\n"


def test_extract_diff_strips_markdown_fence():
    wrapped = f"Here is the fix:\n```diff\n{_GOOD_DIFF}```\nThat should do it."
    assert extract_diff(wrapped).startswith("diff --git")


def test_extract_diff_drops_leading_prose_without_fence():
    text = f"I looked at the code and here's the patch:\n\n{_GOOD_DIFF}"
    assert extract_diff(text).startswith("diff --git")


def test_extract_diff_raises_on_empty_output():
    with pytest.raises(MalformedPatchError):
        extract_diff("")


def test_extract_diff_raises_when_no_diff_header_present():
    with pytest.raises(MalformedPatchError):
        extract_diff("I think the bug is in add(), but here's no diff.")


def test_validate_patch_size_accepts_small_patch():
    validate_patch_size(_GOOD_DIFF, max_lines=100)


def test_validate_patch_size_rejects_oversized_patch():
    huge = _GOOD_DIFF + ("+extra line\n" * 500)
    with pytest.raises(MalformedPatchError):
        validate_patch_size(huge, max_lines=100)


def test_validate_patch_paths_accepts_normal_path():
    validate_patch_paths(_GOOD_DIFF, protected_test_paths=["tests/test_add.py"])


def test_validate_patch_paths_rejects_parent_traversal():
    evil = _GOOD_DIFF.replace("mypkg/__init__.py", "../../etc/passwd")
    with pytest.raises(MalformedPatchError):
        validate_patch_paths(evil, protected_test_paths=[])


def test_validate_patch_paths_rejects_absolute_path():
    evil = _GOOD_DIFF.replace("a/mypkg/__init__.py", "a//etc/passwd").replace(
        "b/mypkg/__init__.py", "b//etc/passwd"
    )
    with pytest.raises(MalformedPatchError):
        validate_patch_paths(evil, protected_test_paths=[])


def test_validate_patch_paths_rejects_protected_test_file():
    protected_diff = _GOOD_DIFF.replace("mypkg/__init__.py", "tests/test_add.py")
    with pytest.raises(MalformedPatchError):
        validate_patch_paths(protected_diff, protected_test_paths=["tests/test_add.py"])


def test_validate_patch_paths_rejects_diff_with_no_headers():
    with pytest.raises(MalformedPatchError):
        validate_patch_paths("not actually a diff body", protected_test_paths=[])


def test_apply_patch_succeeds_and_modifies_only_target_file(fixture_worktree):
    result = apply_patch(fixture_worktree, _GOOD_DIFF)

    assert result.success
    content = (fixture_worktree / "mypkg" / "__init__.py").read_text()
    assert "return a + b" in content


def test_apply_patch_rejects_malformed_diff_cleanly_without_modifying_workspace(fixture_worktree):
    original = (fixture_worktree / "mypkg" / "__init__.py").read_text()
    bogus = _GOOD_DIFF.replace("return a - b", "this context line does not exist")

    result = apply_patch(fixture_worktree, bogus)

    assert not result.success
    assert result.error
    assert (fixture_worktree / "mypkg" / "__init__.py").read_text() == original


def test_apply_patch_rejects_diff_targeting_nonexistent_file(fixture_worktree):
    missing_file_diff = _GOOD_DIFF.replace("mypkg/__init__.py", "mypkg/does_not_exist.py")

    result = apply_patch(fixture_worktree, missing_file_diff)

    assert not result.success
