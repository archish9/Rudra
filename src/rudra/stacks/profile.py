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
    """

    argv: tuple[str, ...] | None
    status: str
    detail: str = ""
    tool: str = ""
