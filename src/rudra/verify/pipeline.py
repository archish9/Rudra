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
import re
import shutil
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rudra.permissions.env import scrubbed_env
from rudra.shell.runner import run_gated
from rudra.stacks.detect import (
    _load_package_json,
    resolve_lint_command,
    resolve_typecheck_command,
)
from rudra.stacks.profile import MISSING_TOOL as RESOLUTION_MISSING_TOOL
from rudra.stacks.profile import NOT_APPLICABLE as RESOLUTION_NOT_APPLICABLE
from rudra.stacks.profile import CommandResolution, StackProfile
from rudra.testing.runner import MAX_TAIL_CHARS, run_tests
from rudra.verify.result import (
    COVERED_BY,
    DENIED,
    FAILED,
    MISSING_TOOL,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
)
from rudra.verify.stubs import scan_stubs

# Every missing_tool message ends here, so the error and its fix are one
# hop apart. Composed once rather than at each resolution site.
_DOCS = "Documentation/10-verification.md"
_ANCHORS = {
    "tsc": f"{_DOCS}#typescript",
    "eslint": f"{_DOCS}#eslint",
    "cargo": f"{_DOCS}#rust",
    "node": f"{_DOCS}#node",
    "mypy": f"{_DOCS}#python",
    "ruff": f"{_DOCS}#python",
}

# `file:line: message` -- mypy, ruff, eslint, cargo.
_FINDING = re.compile(r"^(?P<file>[^\s:][^:]*):(?P<line>\d+)(?::\d+)?:\s*(?P<message>.+)$")
# `file(line,col): message` -- tsc.
_TSC_FINDING = re.compile(r"^(?P<file>[^\s(]+)\((?P<line>\d+),\d+\):\s*(?P<message>.+)$")

_MAX_FINDINGS = 50

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

    # Suffix-driven, NOT stack-driven. `detect()` returns nothing until a
    # marker file (pyproject.toml/setup.py/requirements.txt) exists, so on a
    # greenfield tree -- `rudra "build a flask app"`, task 1 -- a .py file
    # used to be routed to the JavaScript checker, come back NOT_APPLICABLE
    # ("no JavaScript files changed"), and pass the whole gate while every
    # other stage abstained for want of a stack. `stubs` could not catch it
    # either: _scan_python swallows SyntaxError precisely because this stage
    # is supposed to have blocked first. A file's language is a property of
    # the file, not of whether the project has been scaffolded yet (CR-E1).
    python_files = [
        relative for relative in changed_files if Path(relative).suffix.lower() in _PYTHON_SUFFIXES
    ]

    if stack != "python" and not python_files:
        return _node_syntax_stage(project_path, changed_files, gate, console, cfg, stack)

    findings = _parse_python_files(Path(project_path), python_files)

    if stack != "python":
        # A non-Python stack with Python files in the diff: both checks are
        # real, so run the node stage too rather than silently dropping it.
        node = _node_syntax_stage(project_path, changed_files, gate, console, cfg, stack)
        combined = tuple(findings) + node.findings
        if combined:
            return StageResult(
                name="syntax",
                outcome=FAILED,
                blocking=True,
                stack=stack,
                findings=combined,
                detail=f"{len(combined)} file(s) do not parse",
            )
        if node.outcome not in (PASSED, NOT_APPLICABLE):
            return node

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
        detail=f"{len(python_files) if stack != 'python' else len(changed_files)} file(s) parsed",
    )


def _parse_python_files(project_path: Path, relatives: Sequence[str]) -> list[Finding]:
    """ast.parse every named file. A file that is gone is not a failure.

    A deleted or renamed path reaches here by design -- changed_since keeps
    deletions (loop/engine.py:92-93, :254) and `git status` reports them as
    ` D path`. Reading one raises OSError, which used to be recorded as
    "does not parse": a blocking, non-escalating failure whose message was
    an absolute host path, repeated identically until the task went BLOCKED.
    stubs._read already skips missing files, and the two native stages must
    not disagree about what a deletion means (CR-E2).
    """
    findings: list[Finding] = []
    for relative in relatives:
        path = project_path / relative
        if not path.is_file():
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            findings.append(Finding(relative, exc.lineno, exc.msg or "syntax error"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            findings.append(Finding(relative, None, str(exc)))
    return findings


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


def _tail(text: str, limit: int = MAX_TAIL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"[... {len(text) - limit} earlier characters omitted ...]\n{text[-limit:]}"


def _parse_findings(text: str) -> tuple[Finding, ...]:
    """Located problems from tool output, capped.

    Deliberately shallow, for the same reason the test parser is
    (testing/parse.py): a per-tool structured format needs a plugin or a
    JSON mode Rudra cannot assume is installed. The full output is on disk
    either way.
    """
    findings: list[Finding] = []
    for line in text.splitlines():
        match = _FINDING.match(line) or _TSC_FINDING.match(line)
        if match is None:
            continue
        findings.append(
            Finding(match.group("file"), int(match.group("line")), match.group("message").strip())
        )
        if len(findings) >= _MAX_FINDINGS:
            break
    return tuple(findings)


def _run_command_stage(
    name: str,
    resolution: CommandResolution,
    *,
    blocking: bool,
    project_path: Path,
    gate: Any,
    console: Any,
    cfg: Any,
    stack: str | None,
    override: list[str] | None = None,
) -> StageResult:
    """One command stage, from resolution to StageResult.

    Every branch that cannot succeed on a retry says so, and every one that
    needs a human rather than a model sets escalate.
    """
    if override is None:
        if resolution.status == RESOLUTION_NOT_APPLICABLE:
            return StageResult(
                name=name,
                outcome=NOT_APPLICABLE,
                blocking=blocking,
                stack=stack,
                detail=resolution.detail,
            )
        if resolution.status == RESOLUTION_MISSING_TOOL:
            return StageResult(
                name=name,
                outcome=MISSING_TOOL,
                blocking=blocking,
                escalate=True,
                stack=stack,
                detail=resolution.detail,
                docs_anchor=_ANCHORS.get(resolution.tool, _DOCS),
            )
        argv = list(resolution.argv or ())
    else:
        argv = list(override)

    result = run_gated(
        argv,
        cwd=project_path,
        gate=gate,
        console=console,
        timeout=cfg.tools.test_timeout,
        env=scrubbed_env(cfg),
    )
    combined = f"{result.stdout}\n{result.stderr}".strip()

    if result.denied:
        return StageResult(
            name=name,
            outcome=DENIED,
            blocking=blocking,
            escalate=True,
            command=result.argv,
            stack=stack,
            detail=result.denial_reason or "denied by the permission gate",
        )
    if result.timed_out:
        return StageResult(
            name=name,
            outcome=FAILED,
            blocking=blocking,
            command=result.argv,
            stack=stack,
            detail=f"timed out after {cfg.tools.test_timeout}s -- raise [tools] test_timeout "
            "if this stage is genuinely slow",
            output_tail=_tail(combined),
        )
    if result.exit_code is None:
        return StageResult(
            name=name,
            outcome=MISSING_TOOL,
            blocking=blocking,
            escalate=True,
            command=result.argv,
            stack=stack,
            detail=result.stderr.strip() or "the command could not be started",
            docs_anchor=_ANCHORS.get(resolution.tool, _DOCS),
        )
    if result.exit_code == 0:
        return StageResult(
            name=name, outcome=PASSED, blocking=blocking, command=result.argv, stack=stack
        )
    return StageResult(
        name=name,
        outcome=FAILED,
        blocking=blocking,
        command=result.argv,
        stack=stack,
        findings=_parse_findings(combined),
        output_tail=_tail(combined),
        detail=resolution.detail,
    )


def _is_typescript(project_path: Path, profile: StackProfile | None) -> bool:
    """Does this project typecheck through tsc?

    Used by the syntax stage: where tsc runs, `tsc --noEmit` is already the
    parse step and a separate syntax pass buys nothing.
    """
    if profile is None or profile.name not in {"node", "react", "angular"}:
        return False
    package_json = _load_package_json(Path(project_path))
    declared = any(
        "typescript" in (package_json.get(section) or {})
        for section in ("dependencies", "devDependencies", "peerDependencies")
    )
    return declared or (Path(project_path) / "tsconfig.json").is_file()


def _node_syntax_stage(
    project_path: Path,
    changed_files: Sequence[str],
    gate: Any,
    console: Any,
    cfg: Any,
    stack: str | None,
) -> StageResult:
    """`node --check` per changed JavaScript file.

    Plain JavaScript has no type checker to subsume the parse step, so this
    is the only place the check can happen.
    """
    javascript = [
        relative
        for relative in changed_files
        if Path(relative).suffix.lower() in {".js", ".jsx", ".mjs", ".cjs"}
    ]
    if not javascript:
        return StageResult(
            name="syntax",
            outcome=NOT_APPLICABLE,
            blocking=True,
            stack=stack,
            detail="no JavaScript files changed",
        )
    if shutil.which("node") is None:
        return StageResult(
            name="syntax",
            outcome=MISSING_TOOL,
            blocking=True,
            escalate=True,
            stack=stack,
            detail="node is not on PATH, so JavaScript cannot be parse-checked",
            docs_anchor=_ANCHORS["node"],
        )

    findings: list[Finding] = []
    for relative in javascript:
        result = run_gated(
            ["node", "--check", relative],
            cwd=Path(project_path),
            gate=gate,
            console=console,
            timeout=cfg.tools.test_timeout,
            env=scrubbed_env(cfg),
        )
        if result.denied:
            return StageResult(
                name="syntax",
                outcome=DENIED,
                blocking=True,
                escalate=True,
                stack=stack,
                detail=result.denial_reason or "denied by the permission gate",
            )
        if result.exit_code is None:
            # No verdict: the command timed out, or node could not be
            # started between shutil.which() above and here. "Never ran" is
            # not "parsed clean" -- excluding None from the check below made
            # a killed `node --check` return PASSED "N file(s) parsed" over
            # a file nobody checked, and the loop marked the task DONE. The
            # command-stage path already treats this as MISSING_TOOL with
            # escalate=True; this stage must agree (CR-E6).
            return StageResult(
                name="syntax",
                outcome=MISSING_TOOL,
                blocking=True,
                escalate=True,
                stack=stack,
                detail=(
                    f"`node --check` produced no verdict for {relative}: "
                    f"{result.stderr.strip() or 'timed out'}"
                ),
            )
        if result.exit_code != 0:
            complaint = result.stderr.strip().splitlines()
            findings.append(Finding(relative, None, complaint[0] if complaint else "parse error"))

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
        detail=f"{len(javascript)} file(s) parsed",
    )


def lint_stage(
    project_path: Path,
    profile: StackProfile | None,
    *,
    gate: Any,
    console: Any,
    cfg: Any,
    _override: list[str] | None = None,
) -> StageResult:
    """Advisory. It reports fully and never fails a task (spec S9a.2)."""
    if profile is None:
        return StageResult(
            name="lint", outcome=NOT_APPLICABLE, blocking=False, detail="no stack detected"
        )
    return _run_command_stage(
        "lint",
        resolve_lint_command(project_path, profile),
        blocking=False,
        project_path=Path(project_path),
        gate=gate,
        console=console,
        cfg=cfg,
        stack=profile.name,
        override=_override,
    )


def typecheck_stage(
    project_path: Path,
    profile: StackProfile | None,
    *,
    gate: Any,
    console: Any,
    cfg: Any,
    _override: list[str] | None = None,
) -> StageResult:
    """Blocking. A type error is a genuine defect (spec S9a.2)."""
    if profile is None:
        return StageResult(
            name="typecheck", outcome=NOT_APPLICABLE, blocking=True, detail="no stack detected"
        )
    return _run_command_stage(
        "typecheck",
        resolve_typecheck_command(project_path, profile),
        blocking=True,
        project_path=Path(project_path),
        gate=gate,
        console=console,
        cfg=cfg,
        stack=profile.name,
        override=_override,
    )


def test_stage(
    project_path: Path,
    *,
    gate: Any,
    console: Any,
    cfg: Any,
    _override: list[str] | None = None,
) -> StageResult:
    """The project's own test suite, via run_tests (C3.6, Step 8).

    Reused whole rather than reimplemented: TestResult already keeps
    available / denied / timed_out / launch_error / no_tests_collected
    distinct precisely so a completion gate can act on them
    (testing/runner.py:39-43), and duplicating that logic here would give
    the two callers two different answers.
    """
    result = run_tests(
        Path(project_path), gate=gate, console=console, cfg=cfg, _command_override=_override
    )

    if not result.available:
        return StageResult(
            name="test",
            outcome=NOT_APPLICABLE,
            blocking=True,
            stack=result.stack,
            detail="this project declares no test command",
        )
    if result.denied:
        return StageResult(
            name="test",
            outcome=DENIED,
            blocking=True,
            escalate=True,
            command=result.command,
            stack=result.stack,
            detail=result.output_tail or "denied by the permission gate",
        )
    if result.launch_error is not None:
        return StageResult(
            name="test",
            outcome=MISSING_TOOL,
            blocking=True,
            escalate=True,
            command=result.command,
            stack=result.stack,
            detail=result.launch_error,
            docs_anchor=_DOCS,
        )
    if result.timed_out:
        return StageResult(
            name="test",
            outcome=FAILED,
            blocking=True,
            command=result.command,
            stack=result.stack,
            detail=f"the suite timed out after {cfg.tools.test_timeout}s -- raise "
            "[tools] test_timeout if it is genuinely slow",
            output_tail=result.output_tail,
        )
    if result.no_tests_collected:
        # Neither a pass nor a failure: the code was never exercised. Calling
        # it a failure sends the fix loop to repair working code (A1.57).
        return StageResult(
            name="test",
            outcome=NOT_APPLICABLE,
            blocking=True,
            command=result.command,
            stack=result.stack,
            detail="no tests were collected -- the suite ran but found nothing to execute",
        )
    if result.passed:
        return StageResult(
            name="test",
            outcome=PASSED,
            blocking=True,
            command=result.command,
            stack=result.stack,
            detail="" if result.total is None else f"{result.total} run",
        )
    counted = (
        ""
        if result.total is None
        else f"{result.total} run, {result.failed} failed, {result.skipped} skipped"
    )
    return StageResult(
        name="test",
        outcome=FAILED,
        blocking=True,
        command=result.command,
        stack=result.stack,
        findings=_parse_findings(result.output_tail),
        output_tail=result.output_tail,
        detail=counted,
    )


__all__ = ["run_pipeline", "stubs_stage", "syntax_stage"]
