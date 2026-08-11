"""The permission engine: one pure decision function.

Everything that interprets policy calls `PermissionEngine.decide`. The
middleware acts on a `deny`; `interrupts.build_interrupt_on`'s `when`
predicate acts on an `ask`. Nothing else reads the rule lists.

Purity is the point -- no filesystem writes, no console, no graph -- so
precedence and matching are testable directly, without a model or a
compiled agent.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wcmatch import glob as wcglob

from rudra.permissions.floor import floor_hit

if TYPE_CHECKING:  # pragma: no cover - broken at runtime to avoid a cycle
    from rudra.permissions.grants import SessionGrants

# Rudra's own orchestration tools. They write only under .rudra/run/ and are
# how the loop functions; gating them would make mode="ask" prompt for
# Rudra's own bookkeeping (spec §4.6).
CONTROL_PLANE_TOOLS = frozenset({"update_plan", "write_task_assignment", "ask_user"})

# `read_plan` belongs here, not in the control plane: it only reads
# .rudra/run/PLAN.md. It was in neither set until Step 8, which made it the
# one instance of A1.51 reachable today -- decided "ask", no interrupt
# registered, so it ran unprompted and unaudited.
READ_ONLY_TOOLS = frozenset({"read_file", "ls", "glob", "grep", "read_plan"})
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute"})

# Tools that are a shell command wearing a different name. Their own
# implementation calls decide("execute", ...) with the real command string,
# so deciding them a second time here would prompt twice for one command
# (Step 8 spec S8.2, §6.6).
WRAPPED_EXECUTE_TOOLS = frozenset({"git_diff", "run_tests"})

# `task` is allowed: it spawns a subagent whose own tool calls are gated by
# this same engine, so gating the spawn would double-prompt (spec §4.3).
_OTHER_TOOLS = frozenset({"task"})

ALL_GATED_TOOLS = (
    READ_ONLY_TOOLS | MUTATING_TOOLS | _OTHER_TOOLS | CONTROL_PLANE_TOOLS | WRAPPED_EXECUTE_TOOLS
)

# Which argument carries the thing a pattern matches against.
_ARG_KEYS = {
    "execute": "command",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "read_file": "file_path",
    "ls": "path",
    "glob": "pattern",
    "grep": "pattern",
}

_WCMATCH_FLAGS = wcglob.GLOBSTAR | wcglob.DOTGLOB


@dataclass(frozen=True)
class Rule:
    """One parsed `allow`/`deny` entry."""

    tool: str
    pattern: str | None

    def __str__(self) -> str:
        return self.tool if self.pattern is None else f"{self.tool}:{self.pattern}"


@dataclass(frozen=True)
class Decision:
    """What to do about one tool call, and why.

    `rule` is the rule text that fired -- a user rule verbatim, or
    `<floor:name>` -- so an audit line and an error message can both name it
    without reconstructing anything.
    """

    effect: str  # "allow" | "deny" | "ask"
    rule: str | None
    source: str


def parse_rule(text: str) -> Rule:
    """Parse one `tool` or `tool:pattern` entry.

    Split on the FIRST colon only: `execute:git log --format=%H:%s` is one
    tool and one pattern, not a parse error.
    """
    tool, separator, pattern = text.partition(":")
    tool = tool.strip()
    if tool not in ALL_GATED_TOOLS:
        raise ValueError(
            f"Unknown tool '{tool}' in permission rule {text!r}. "
            f"Valid tools: {', '.join(sorted(ALL_GATED_TOOLS))}."
        )
    if separator and not pattern.strip():
        raise ValueError(f"Permission rule {text!r} has an empty pattern after ':'.")
    return Rule(tool=tool, pattern=pattern if separator else None)


def gated_arg(tool: str, args: dict[str, Any]) -> str | None:
    """The argument a pattern matches against, or None if absent."""
    key = _ARG_KEYS.get(tool)
    if key is None:
        return None
    value = args.get(key)
    return value if isinstance(value, str) else None


def rule_matches(
    rule: Rule, tool: str, absolute: tuple[str, ...], relative: tuple[str, ...]
) -> bool:
    """Does this rule cover this call?

    A patternless rule covers every call to its tool. A pattern starting
    with `/` is matched against the absolute spellings, anything else
    against the project-relative ones. `execute` has no path, so its
    command string is supplied as the only absolute spelling.

    Both spellings of a path are offered -- as the model wrote it and as it
    resolves on disk -- and a match on either counts. Resolving is what
    stops `src/../.env` dodging a deny on `.env`, but resolving *only*
    would break the obvious rule on a platform where the obvious path is a
    symlink: on macOS `/etc` resolves to `/private/etc`, so a deny on
    `/etc/**` would never fire. Offering both can only add matches, so deny
    rules get strictly stronger, and an escape is still caught because the
    resolved spelling is always among the candidates.
    """
    if rule.tool != tool:
        return False
    if rule.pattern is None:
        return True

    # A command is not a path. Glob `*` refuses to cross `/`, so matching a
    # command as one makes `execute:pytest*` fail against
    # `pytest -q tests/x` -- the exact case `always` exists to cover.
    if tool == "execute":
        return any(fnmatch.fnmatchcase(subject, rule.pattern) for subject in absolute)

    candidates = absolute if rule.pattern.startswith("/") else (relative or absolute)
    return any(
        wcglob.globmatch(subject, rule.pattern, flags=_WCMATCH_FLAGS) for subject in candidates
    )


class PermissionEngine:
    """Decides allow / deny / ask for one tool call. Pure."""

    def __init__(
        self,
        mode: str,
        allow: tuple[str, ...],
        deny: tuple[str, ...],
        floor_disable: tuple[str, ...],
        project_root: Path,
        grants: SessionGrants | None = None,
        shell_in_auto: bool = False,
    ) -> None:
        self.mode = mode
        self.shell_in_auto = shell_in_auto
        self.allow = tuple(parse_rule(entry) for entry in allow)
        self.deny = tuple(parse_rule(entry) for entry in deny)
        self.floor_disable = frozenset(floor_disable)
        self.project_root = Path(project_root).resolve()
        self.grants = grants

    def _resolve(
        self, tool: str, arg: str | None
    ) -> tuple[Path | None, tuple[str, ...], tuple[str, ...]]:
        """The resolved path plus every spelling a rule may match against.

        Returns `(resolved, absolute_spellings, relative_spellings)`. The
        floor always uses `resolved` alone -- it must not be foolable. Rules
        see both spellings, for the reason `rule_matches` documents.
        """
        if tool == "execute" or arg is None:
            return None, (), ()

        candidate = Path(arg)
        given = candidate if candidate.is_absolute() else self.project_root / candidate
        resolved = given.resolve()

        absolute = {str(resolved), str(given)}
        relative: set[str] = set()
        for form in (resolved, given):
            try:
                relative.add(str(form.relative_to(self.project_root)))
            except ValueError:
                continue
        return resolved, tuple(sorted(absolute)), tuple(sorted(relative))

    def decide(self, tool: str, args: dict[str, Any]) -> Decision:
        if tool in CONTROL_PLANE_TOOLS:
            return Decision("allow", None, "control-plane")

        # The real decision happens inside the tool, against the command it
        # is about to run. Deciding it here as well would prompt twice.
        if tool in WRAPPED_EXECUTE_TOOLS:
            return Decision("allow", None, "wrapped-execute")

        arg = gated_arg(tool, args)
        resolved, absolute, relative = self._resolve(tool, arg)
        if tool == "execute" and arg is not None:
            absolute = (arg,)

        # 1. floor -- always against the resolved path, never a spelling
        suppressed: str | None = None
        hit = floor_hit(tool, resolved, arg if tool == "execute" else None, self.project_root)
        if hit is not None:
            if hit not in self.floor_disable:
                return Decision("deny", f"<floor:{hit}>", "floor")
            suppressed = hit

        def _final(effect: str, rule: str | None, source: str) -> Decision:
            if suppressed is not None and effect == "allow":
                return Decision(effect, f"<floor:{suppressed}>", "floor-disabled")
            return Decision(effect, rule, source)

        # 2. user deny
        for rule in self.deny:
            if rule_matches(rule, tool, absolute, relative):
                return Decision("deny", str(rule), "deny")

        # 3. session grants
        if self.grants is not None:
            granted = self.grants.matches(tool, absolute, relative)
            if granted is not None:
                return _final("allow", str(granted), "session-grant")

        # 4. user allow
        for rule in self.allow:
            if rule_matches(rule, tool, absolute, relative):
                return _final("allow", str(rule), "allow")

        # 5. mode default
        if tool in READ_ONLY_TOOLS or tool in _OTHER_TOOLS:
            return _final("allow", None, "mode-default")
        if self.mode == "auto":
            # Unattended shell is opt-in. Nobody reads the command before it
            # runs, and a shell command can write anywhere the user can --
            # measured, not theorised (A1.49). Filesystem tools stay allowed
            # because the backend genuinely confines them (A1.50).
            #
            # This sits AFTER the allow rules deliberately: a user who writes
            # `allow = ["execute:pytest*"]` has named exactly what may run,
            # which is opting in for that command.
            if tool == "execute" and not self.shell_in_auto:
                return Decision("deny", "<auto:shell-not-opted-in>", "auto-shell")
            return _final("allow", None, "mode-default")
        if self.mode == "plan":
            return Decision("deny", None, "mode-default")
        return Decision("ask", None, "mode-default")


__all__ = [
    "ALL_GATED_TOOLS",
    "CONTROL_PLANE_TOOLS",
    "MUTATING_TOOLS",
    "READ_ONLY_TOOLS",
    "WRAPPED_EXECUTE_TOOLS",
    "Decision",
    "PermissionEngine",
    "Rule",
    "gated_arg",
    "parse_rule",
    "rule_matches",
]
