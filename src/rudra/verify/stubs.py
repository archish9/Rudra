"""Placeholder detection -- the stage a test-only gate cannot have.

C6.6 exists as a separate item from "run the tests" because an agent can
pass a suite and still leave placeholders behind. Nothing here executes
anything or starts a subprocess.

Python is scanned with `ast` and `tokenize`, not regex, because a bare
`pass` is legitimate in an `except:` block, in `class Foo: pass`, and in
`while True: pass` -- a line matcher cannot tell those from a stubbed
function body. Rust and JS/TS get line regex, which is shallower, and the
report says so rather than implying otherwise.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Sequence
from pathlib import Path

from rudra.stacks.registry import ALL_SKIP_DIRS
from rudra.verify.result import Finding

_MARKER = re.compile(r"\b(TODO|FIXME)\b")
_RUST_STUB = re.compile(r"\b(todo!|unimplemented!)\s*[(\[{]")
_JS_STUB = re.compile(r"""throw\s+new\s+Error\s*\(\s*['"`][^'"`]*not\s+implemented""", re.I)

_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
_RUST_SUFFIXES = frozenset({".rs"})
_JS_SUFFIXES = frozenset({".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"})
_SCANNED_SUFFIXES = _PYTHON_SUFFIXES | _RUST_SUFFIXES | _JS_SUFFIXES

# Directories nothing is ever scanned in, on top of every stack's own
# build-output dirs. ALL_SKIP_DIRS is the registry's union (registry.py:71).
_EXTRA_SKIP_DIRS = frozenset({".git", ".rudra", ".idea", ".vscode"})
_SKIP_DIRS = ALL_SKIP_DIRS | _EXTRA_SKIP_DIRS


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _stub_reason(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Why this function is a placeholder, or None if it has a real body."""
    body = list(node.body)
    docstrings = [
        statement
        for statement in body
        if isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    ]
    remaining = [statement for statement in body if statement not in docstrings]

    if not remaining:
        return "function body is only a docstring"
    if len(remaining) != 1:
        return None

    only = remaining[0]
    if isinstance(only, ast.Pass):
        return "function body is a bare `pass`"
    if (
        isinstance(only, ast.Expr)
        and isinstance(only.value, ast.Constant)
        and only.value.value is Ellipsis
    ):
        return "function body is `...`"
    if isinstance(only, ast.Raise):
        raised = only.exc
        if isinstance(raised, ast.Call):
            raised = raised.func
        if isinstance(raised, ast.Name) and raised.id == "NotImplementedError":
            return "function raises NotImplementedError"
    return None


def _scan_python(relative: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # The syntax stage blocks before this one ever runs, so an
        # unparseable file here means the caller skipped ahead. Report
        # nothing rather than guessing.
        return findings

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            reason = _stub_reason(node)
            if reason is not None:
                findings.append(Finding(relative, node.lineno, f"{node.name}: {reason}"))

    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT and _MARKER.search(token.string):
                findings.append(
                    Finding(relative, token.start[0], f"marker comment: {token.string.strip()}")
                )
    except (tokenize.TokenError, IndentationError):
        pass

    return sorted(findings, key=lambda finding: (finding.line or 0, finding.message))


def _scan_lines(relative: str, text: str, pattern: re.Pattern[str]) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            findings.append(Finding(relative, number, f"placeholder: {line.strip()}"))
        elif _MARKER.search(line):
            findings.append(Finding(relative, number, f"marker: {line.strip()}"))
    return findings


def scan_stubs(project_path: Path, files: Sequence[str]) -> tuple[Finding, ...]:
    """Placeholders in `files`, which are project-relative paths.

    Changed files only, per spec S9a.5: a pre-existing TODO in code Rudra
    never touched is not Rudra's defect, and failing on it would make the
    gate unpassable in any real repository.
    """
    project_path = Path(project_path)
    findings: list[Finding] = []

    for relative in files:
        path = project_path / relative
        suffix = path.suffix.lower()
        if suffix not in _SCANNED_SUFFIXES:
            continue
        text = _read(path)
        if text is None:
            continue

        if suffix in _PYTHON_SUFFIXES:
            findings.extend(_scan_python(relative, text))
        elif suffix in _RUST_SUFFIXES:
            findings.extend(_scan_lines(relative, text, _RUST_STUB))
        else:
            findings.extend(_scan_lines(relative, text, _JS_STUB))

    return tuple(findings)


def source_files(project_path: Path) -> tuple[str, ...]:
    """Every scannable source file in the project, build output pruned.

    Backs `rudra verify --all`. Sorted so the report is stable between runs.
    """
    project_path = Path(project_path)
    found: list[str] = []

    for path in project_path.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SCANNED_SUFFIXES:
            continue
        relative = path.relative_to(project_path)
        if any(part in _SKIP_DIRS for part in relative.parts[:-1]):
            continue
        found.append(str(relative))

    return tuple(sorted(found))


__all__ = ["scan_stubs", "source_files"]
