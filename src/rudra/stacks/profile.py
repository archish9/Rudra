"""The StackProfile record — pure data, no behaviour, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StackProfile:
    """How to recognise one target stack, and how its tests are run.

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
    skip_dirs: frozenset[str] = field(default_factory=frozenset)
    specificity: int = 10
