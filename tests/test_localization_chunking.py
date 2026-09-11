from pathlib import Path

from backend.localization.chunking import (
    _approx_tokens,
    enumerate_python_files,
    extract_symbols,
)


def _write(tmp_path: Path, rel_path: str, content: str) -> Path:
    path = tmp_path / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_extract_symbols_finds_function(tmp_path):
    _write(tmp_path, "mod.py", "def foo(x):\n    return x + 1\n")
    file_path = tmp_path / "mod.py"

    chunks = extract_symbols(file_path, tmp_path, "repo", "commit")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.symbol_type == "function"
    assert chunk.symbol_name == "foo"
    assert chunk.file_path == "mod.py"
    assert chunk.start_line == 1
    assert chunk.end_line == 2
    assert "return x + 1" in chunk.text


def test_extract_symbols_finds_class_and_methods(tmp_path):
    source = (
        "import os\n"
        "\n"
        "class Foo:\n"
        "    def bar(self, x):\n"
        "        return x + 1\n"
        "\n"
        "    def baz(self):\n"
        "        return self.bar(1)\n"
    )
    file_path = _write(tmp_path, "mod.py", source)

    chunks = extract_symbols(file_path, tmp_path, "repo", "commit")
    by_name = {c.symbol_name: c for c in chunks}

    assert set(by_name) == {"Foo", "Foo.bar", "Foo.baz"}
    assert by_name["Foo"].symbol_type == "class"
    assert by_name["Foo.bar"].symbol_type == "method"
    assert by_name["Foo.bar"].start_line == 4
    assert by_name["Foo.bar"].end_line == 5
    # Method chunks carry the class signature as context, without duplicating it.
    assert by_name["Foo.bar"].text.count("class Foo:") == 1


def test_extract_symbols_context_never_duplicates_body(tmp_path):
    """A symbol near the top of a short file must not get the file's first
    N lines as context when those lines ARE the symbol body — the context
    window is capped to lines strictly before the symbol starts.
    """
    file_path = _write(tmp_path, "mod.py", "def add(a, b):\n    return a + b\n")

    chunks = extract_symbols(file_path, tmp_path, "repo", "commit")

    assert len(chunks) == 1
    assert chunks[0].text.count("def add(a, b):") == 1


def test_extract_symbols_skips_syntax_error_file(tmp_path):
    file_path = _write(tmp_path, "broken.py", "def bad(:\n")

    chunks = extract_symbols(file_path, tmp_path, "repo", "commit")

    assert chunks == []


def test_extract_symbols_skips_undecodable_file(tmp_path):
    file_path = tmp_path / "binary.py"
    file_path.write_bytes(b"\xff\xfe\x00\x01binary garbage")

    chunks = extract_symbols(file_path, tmp_path, "repo", "commit")

    assert chunks == []


def test_split_body_splits_oversized_symbol(tmp_path):
    # ~2000 lines of trivial statements comfortably exceeds the 800-token
    # target, forcing the splitter to kick in.
    body_lines = ["def big():\n"] + [f"    x_{i} = {i}\n" for i in range(2000)]
    file_path = _write(tmp_path, "mod.py", "".join(body_lines))

    chunks = extract_symbols(file_path, tmp_path, "repo", "commit")

    assert len(chunks) > 1
    assert all(c.symbol_name == "big" for c in chunks)
    assert all(c.chunk_parts_total == len(chunks) for c in chunks)
    # Parts are numbered contiguously from 0.
    assert sorted(c.chunk_part for c in chunks) == list(range(len(chunks)))
    # Each part stays within the approx-token target.
    assert all(_approx_tokens(c.text) <= 900 for c in chunks)
    # Consecutive parts overlap: the tail of one part's line range should
    # reach into (or touch) the next part's start.
    ordered = sorted(chunks, key=lambda c: c.chunk_part)
    for prev, nxt in zip(ordered, ordered[1:]):
        assert nxt.start_line <= prev.end_line


def test_enumerate_python_files_excludes_vcs_and_build_dirs(tmp_path):
    _write(tmp_path, "pkg/real.py", "x = 1\n")
    _write(tmp_path, ".git/objects/fake.py", "x = 1\n")
    _write(tmp_path, "__pycache__/fake.py", "x = 1\n")
    _write(tmp_path, "node_modules/some_pkg/fake.py", "x = 1\n")
    _write(tmp_path, "build/fake.py", "x = 1\n")
    _write(tmp_path, "thing.egg-info/fake.py", "x = 1\n")
    _write(tmp_path, ".venv/lib/fake.py", "x = 1\n")

    files = enumerate_python_files(tmp_path)

    assert [f.relative_to(tmp_path).as_posix() for f in files] == ["pkg/real.py"]
