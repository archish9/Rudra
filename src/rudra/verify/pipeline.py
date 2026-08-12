"""Stage execution: what runs, in what order, and when to stop.

Nothing here starts a subprocess. Command stages call run_gated
(shell/runner.py:91), which stays Rudra's only subprocess call site, so
every command reaches the permission engine as `execute:<command>` with no
new rule vocabulary (Step 8 spec S8.2).

The pipeline stops at the first blocking failure. A file that does not
parse makes lint, typecheck and test output pure noise, and feeding all of
it to a 32B model consumes the context the fix loop needs to act on the
real failure (D6).
"""

from __future__ import annotations

import ast
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rudra.stacks.profile import StackProfile
from rudra.verify.result import (
    COVERED_BY,
    FAILED,
    PASSED,
    Finding,
    StageResult,
)
from rudra.verify.stubs import scan_stubs

# Stacks whose parse step is subsumed by their type checker. Rust and
# TypeScript have no cheap native parser, and `cargo check` / `tsc --noEmit`
# already answer "does it parse" -- a separate pass would be a second full
# compile for no new information.
_SYNTAX_COVERED_BY_TYPECHECK = frozenset({"rust"})

_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})


def syntax_stage(
    project_path: Path,
    profile: StackProfile | None,
    changed_files: Sequence[str],
    *,
    gate: Any,
    console: Any,
    cfg: Any,
) -> StageResult:
    """Does it parse?

    Python is checked with ast.parse -- native, instant, and ungated. It
    catches exactly the truncated or half-written file that
    main_agent.py:438-439 currently records as a completed task.
    """
    stack = profile.name if profile is not None else None

    if stack in _SYNTAX_COVERED_BY_TYPECHECK or _is_typescript(project_path, profile):
        return StageResult(
            name="syntax",
            outcome=COVERED_BY,
            blocking=True,
            stack=stack,
            detail="covered by typecheck -- this stack has no cheaper parse step",
        )

    if stack != "python":
        return _node_syntax_stage(project_path, changed_files, gate, console, cfg, stack)

    findings: list[Finding] = []
    for relative in changed_files:
        path = Path(project_path) / relative
        if path.suffix.lower() not in _PYTHON_SUFFIXES:
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            findings.append(Finding(relative, exc.lineno, exc.msg or "syntax error"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            findings.append(Finding(relative, None, str(exc)))

    if findings:
        return StageResult(
            name="syntax",
            outcome=FAILED,
            blocking=True,
            stack=stack,
            findings=tuple(findings),
            detail=f"{len(findings)} file(s) do not parse",
        )
    return StageResult(
        name="syntax",
        outcome=PASSED,
        blocking=True,
        stack=stack,
        detail=f"{len(changed_files)} file(s) parsed",
    )


def stubs_stage(project_path: Path, changed_files: Sequence[str]) -> StageResult:
    """Placeholders in the files this run touched.

    The stage that makes C6.6 more than "run the tests": an agent can pass
    a suite and still leave `pass` where an implementation belongs.
    """
    findings = scan_stubs(Path(project_path), changed_files)
    if findings:
        return StageResult(
            name="stubs",
            outcome=FAILED,
            blocking=True,
            findings=findings,
            detail=f"{len(findings)} placeholder(s) in changed files",
        )
    return StageResult(
        name="stubs",
        outcome=PASSED,
        blocking=True,
        detail=f"{len(changed_files)} changed file(s) scanned",
    )


def run_pipeline(
    project_path: Path,
    *,
    profile: StackProfile | None,
    changed_files: Sequence[str],
    gate: Any,
    console: Any,
    cfg: Any,
    _command_override: dict[str, list[str]] | None = None,
) -> tuple[StageResult, ...]:
    """Every stage that ran, in order, stopping at the first blocking failure.

    Stages that never ran are absent from the result rather than carrying a
    "skipped" outcome -- report.py knows STAGE_ORDER and renders the gap.
    That keeps the outcome vocabulary at the six the spec defines.
    """
    project_path = Path(project_path)
    overrides = _command_override or {}
    stages: list[StageResult] = []

    runners = (
        (
            "syntax",
            lambda: syntax_stage(
                project_path, profile, changed_files, gate=gate, console=console, cfg=cfg
            ),
        ),
        (
            "lint",
            lambda: lint_stage(
                project_path,
                profile,
                gate=gate,
                console=console,
                cfg=cfg,
                _override=overrides.get("lint"),
            ),
        ),
        (
            "typecheck",
            lambda: typecheck_stage(
                project_path,
                profile,
                gate=gate,
                console=console,
                cfg=cfg,
                _override=overrides.get("typecheck"),
            ),
        ),
        (
            "test",
            lambda: test_stage(
                project_path,
                gate=gate,
                console=console,
                cfg=cfg,
                _override=overrides.get("test"),
            ),
        ),
        ("stubs", lambda: stubs_stage(project_path, changed_files)),
    )

    for name, runner in runners:
        try:
            result = runner()
        except Exception as exc:  # noqa: BLE001 - containment is the point
            # A bug in Rudra must not be reported as a bug in the user's
            # code, and must not kill the run (A1.39 class). It escalates
            # rather than reaching the fix loop.
            result = StageResult(
                name=name,
                outcome=FAILED,
                blocking=True,
                escalate=True,
                detail=f"internal error in the {name} stage: {exc}",
                output_tail=traceback.format_exc(),
            )
        stages.append(result)
        if result.halts:
            break

    return tuple(stages)


def _is_typescript(project_path: Path, profile: StackProfile | None) -> bool:
    """Replaced in Task 5 by the tsconfig / devDependency check."""
    return False


def _node_syntax_stage(project_path, changed_files, gate, console, cfg, stack) -> StageResult:
    """Replaced in Task 5 by `node --check` per changed file."""
    return StageResult(name="syntax", outcome=PASSED, blocking=True, stack=stack)


def lint_stage(project_path, profile, *, gate, console, cfg, _override=None) -> StageResult:
    """Replaced in Task 5."""
    return StageResult(name="lint", outcome=PASSED, blocking=False)


def typecheck_stage(project_path, profile, *, gate, console, cfg, _override=None) -> StageResult:
    """Replaced in Task 5."""
    return StageResult(name="typecheck", outcome=PASSED, blocking=True)


def test_stage(project_path, *, gate, console, cfg, _override=None) -> StageResult:
    """Replaced in Task 6."""
    return StageResult(name="test", outcome=PASSED, blocking=True)


__all__ = ["run_pipeline", "stubs_stage", "syntax_stage"]
