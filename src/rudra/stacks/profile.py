"""Pure data records for the stack layer -- no behaviour, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StackProfile:
    """How to recognise one target stack, and how its checks are run.

    Attributes:
        name: Stable identifier, e.g. "rust".
        markers: Filenames whose presence makes this stack a candidate.
            ANY one is enough.
        requires: Filenames that must ALL also be present. Used where a
            marker alone is ambiguous -- angular.json only means Angular in a
            project that also has package.json.
        dependency: A package.json dependency that must be declared. Used
            where the marker files cannot distinguish, e.g. React vs any
            other Vite project.
        test_command: The command as an argv tuple, or None when the project
            declares its own (Node's package.json scripts.test).
        lint_command: The lint command as an argv tuple, or None when it
            depends on the project's own layout -- a virtualenv, or a
            package.json dependency. resolve_lint_command works it out.
        typecheck_command: Same, for type checking. Rust is the only stack
            whose answer is fixed, because cargo ships with the toolchain.
        skip_dirs: Build-output directories that must never appear in the
            model's view of the project.
        specificity: Higher wins when several profiles match. Angular beats
            plain Node in an Angular workspace.
    """

    name: str
    markers: tuple[str, ...]
    requires: tuple[str, ...] = ()
    dependency: str | None = None
    test_command: tuple[str, ...] | None = None
    lint_command: tuple[str, ...] | None = None
    typecheck_command: tuple[str, ...] | None = None
    skip_dirs: frozenset[str] = field(default_factory=frozenset)
    specificity: int = 10


OK = "ok"
NOT_APPLICABLE = "not_applicable"
MISSING_TOOL = "missing_tool"


@dataclass(frozen=True)
class CommandResolution:
    """The answer to "what command runs this stage here?".

    Three statuses, because two would lose the distinction the gate is
    built on: a plain-JavaScript project has no typechecker and never
    will (`not_applicable`), while a TypeScript project without tsc
    installed has one that is absent (`missing_tool`, which blocks and
    escalates). One status cannot carry both.

    `advisory` says the verdict this command returns is not reproducible on
    another machine, so a failure must be reported and must not fail a task
    (OPEN-38). It rides here rather than on the stage because only the
    resolver knows WHICH tool it picked -- Rudra's bundled mypy, whose
    answer depends on Rudra's own site-packages, or the project's own, whose
    answer does not. It governs the exit-code verdict only: a denied or
    unstartable command still blocks and escalates, because "no model can
    fix this" is a different claim from "this verdict is unreliable".

    `env` is the environment this command needs OVERRIDDEN, as pairs rather
    than a mapping so the record stays hashable and frozen like `argv`. It
    is applied on top of `scrubbed_env`, never in place of it -- that
    function exists because LocalShellBackend's empty-environment default
    made every venv, nvm and rustup toolchain invisible (A1.44), and a
    stage that replaced it would reintroduce exactly that.
    """

    argv: tuple[str, ...] | None
    status: str
    detail: str = ""
    tool: str = ""
    advisory: bool = False
    env: tuple[tuple[str, str], ...] = ()
