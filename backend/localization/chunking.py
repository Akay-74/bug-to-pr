"""Repository indexing: deterministic Python symbol extraction and chunking.

Given a repo checked out on disk, walk its Python files, parse each with the
stdlib `ast` module (no LLM, no dependency graph — see docs/phase2.md §1),
and produce retrieval chunks for every function/method/class with enough
local context (imports, enclosing class signature) to be understandable on
its own.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Directories that never contain retrieval-relevant Python source: VCS
# internals, caches, build artifacts, virtualenvs, and "unrelated
# frontend/node directories" (node_modules is the concrete case named in
# docs/phase2.md §1 — a Python repo's JS tooling has no bearing on a Python
# bug's location).
_EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".env",
    "build",
    "dist",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".eggs",
    ".next",
}

# Rough chars-per-token for code (no tokenizer dependency needed just to
# decide when a symbol is "too big" — this only has to be in the right
# ballpark to hit the ~200-800 token target from docs/phase2.md §1).
_APPROX_CHARS_PER_TOKEN = 4
_TARGET_MAX_TOKENS = 800
_CONTEXT_HEADER_LINES = 15


def _is_excluded_dir(dir_name: str) -> bool:
    return dir_name in _EXCLUDED_DIR_NAMES or dir_name.endswith(".egg-info")


def enumerate_python_files(repo_root: Path) -> list[Path]:
    """All indexable .py files under repo_root, sorted for determinism."""
    files = []
    for path in repo_root.rglob("*.py"):
        rel_parts = path.relative_to(repo_root).parts[:-1]
        if any(_is_excluded_dir(part) for part in rel_parts):
            continue
        files.append(path)
    return sorted(files)


@dataclass(frozen=True)
class Chunk:
    repo: str
    commit: str
    file_path: str  # posix-style, relative to repo root
    symbol_type: str  # "function" | "method" | "class"
    symbol_name: str  # "foo" for a function, "ClassName.method" for a method
    start_line: int
    end_line: int
    text: str
    chunk_part: int = 0
    chunk_parts_total: int = 1

    @property
    def chunk_id(self) -> str:
        base = f"{self.file_path}::{self.symbol_name}:{self.start_line}-{self.end_line}"
        if self.chunk_parts_total > 1:
            base += f":part{self.chunk_part}"
        return base


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // _APPROX_CHARS_PER_TOKEN)


def _split_body(
    *,
    repo: str,
    commit: str,
    file_path: str,
    symbol_type: str,
    symbol_name: str,
    start_line: int,
    context: str,
    body_lines: list[str],
) -> list[Chunk]:
    """Turn one symbol's body lines into one or more Chunks.

    Stays under one chunk when the body is small enough; otherwise splits
    into overlapping line windows (~20% overlap) so a fix location near a
    window boundary is never split away from all its surrounding code.
    """
    full_text = context + "".join(body_lines)
    if _approx_tokens(full_text) <= _TARGET_MAX_TOKENS or len(body_lines) <= 1:
        return [
            Chunk(
                repo=repo,
                commit=commit,
                file_path=file_path,
                symbol_type=symbol_type,
                symbol_name=symbol_name,
                start_line=start_line,
                end_line=start_line + len(body_lines) - 1,
                text=full_text,
            )
        ]

    avg_chars_per_line = max(1, len("".join(body_lines)) // len(body_lines))
    window_lines = max(10, (_TARGET_MAX_TOKENS * _APPROX_CHARS_PER_TOKEN) // avg_chars_per_line)
    overlap_lines = max(1, window_lines // 5)

    windows: list[tuple[int, list[str]]] = []
    i = 0
    while i < len(body_lines):
        window = body_lines[i : i + window_lines]
        windows.append((i, window))
        if i + window_lines >= len(body_lines):
            break
        i += window_lines - overlap_lines

    total = len(windows)
    chunks = []
    for part, (offset, window) in enumerate(windows):
        chunks.append(
            Chunk(
                repo=repo,
                commit=commit,
                file_path=file_path,
                symbol_type=symbol_type,
                symbol_name=symbol_name,
                start_line=start_line + offset,
                end_line=start_line + offset + len(window) - 1,
                text=context + "".join(window),
                chunk_part=part,
                chunk_parts_total=total,
            )
        )
    return chunks


def extract_symbols(file_path: Path, repo_root: Path, repo: str, commit: str) -> list[Chunk]:
    """Extract function/method/class chunks from one Python file.

    Files that fail to decode as UTF-8 or fail to parse as Python are
    skipped (generated/binary/syntax-broken files) rather than raising —
    indexing must be able to run over an entire real-world repo.
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    source_lines = source.splitlines(keepends=True)
    rel_path = file_path.relative_to(repo_root).as_posix()

    def leading_context(node_lineno: int) -> str:
        # Capped to the lines strictly before this symbol's own start line,
        # so context never overlaps the body it's attached to (a symbol
        # starting on line 3 gets 2 lines of context, not the file's first
        # 15 — which for a short file would just be the symbol itself again).
        cap = min(_CONTEXT_HEADER_LINES, node_lineno - 1)
        return "".join(source_lines[:cap])

    chunks: list[Chunk] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_lines = source_lines[node.lineno - 1 : node.end_lineno]
            chunks.extend(
                _split_body(
                    repo=repo,
                    commit=commit,
                    file_path=rel_path,
                    symbol_type="function",
                    symbol_name=node.name,
                    start_line=node.lineno,
                    context=leading_context(node.lineno),
                    body_lines=body_lines,
                )
            )
        elif isinstance(node, ast.ClassDef):
            class_body_lines = source_lines[node.lineno - 1 : node.end_lineno]
            chunks.extend(
                _split_body(
                    repo=repo,
                    commit=commit,
                    file_path=rel_path,
                    symbol_type="class",
                    symbol_name=node.name,
                    start_line=node.lineno,
                    context=leading_context(node.lineno),
                    body_lines=class_body_lines,
                )
            )
            class_signature = source_lines[node.lineno - 1]
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_body_lines = source_lines[sub.lineno - 1 : sub.end_lineno]
                    method_context = leading_context(node.lineno) + class_signature
                    chunks.extend(
                        _split_body(
                            repo=repo,
                            commit=commit,
                            file_path=rel_path,
                            symbol_type="method",
                            symbol_name=f"{node.name}.{sub.name}",
                            start_line=sub.lineno,
                            context=method_context,
                            body_lines=method_body_lines,
                        )
                    )
    return chunks


def index_files(files: list[Path], repo_root: Path, repo: str, commit: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for file_path in files:
        chunks.extend(extract_symbols(file_path, repo_root, repo, commit))
    return chunks
