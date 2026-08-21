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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wcmatch import glob as wcglob

from rudra.permissions.floor import floor_hit

if TYPE_CHECKING:  # pragma: no cover - broken at runtime to avoid a cycle
    from rudra.permissions.grants import SessionGrants

# Rudra's own orchestration tools. They write only under .rudra/ and are
# how the loop functions; gating them would make mode="ask" prompt for
# Rudra's own bookkeeping (spec §4.6).
#
# `record_fact` joined late and the omission had teeth (A1.75): denied in
# `plan` mode it left `--plan` presenting a plan with no facts, which is
# the one thing that flag exists to show, and in `ask` mode it fell to a
# bare "ask" that no interrupt could deliver -- A1.53's shape, since
# build_interrupt_on covers MUTATING_TOOLS only.
# `compact_conversation` is control plane for the same reason: it writes no
# file and runs no command, it shrinks the agent's own context. Outside this
# set it would be decided `ask` with no interrupt registered, which the
# middleware denies rather than runs (tests/test_permissions_unknown_tool.py)
# -- so the tool would be offered to the model and refused every time. That
# is A1.75's shape, found the same way.
# `remember` joins for exactly `record_fact`'s reason, and A1.75 is why this
# was not left to look obvious: it writes only under .rudra/, so gating it
# would prompt for Rudra's own bookkeeping -- and leaving it out would have
# it decided `ask` with no interrupt registered, which the middleware denies
# rather than runs. Denied on every call, in every mode, silently.
CONTROL_PLANE_TOOLS = frozenset(
    {"add_tasks", "drop_task", "ask_user", "record_fact", "compact_conversation", "remember"}
)

# `read_ledger` belongs here, not in the control plane: it only reads
# .rudra/run/ledger.json. Its predecessor `read_plan` was in neither set
# until Step 8, which made it the one instance of A1.53 reachable today --
# decided "ask", no interrupt registered, so it ran unprompted and
# unaudited.
# `list_mcp_tools` and `describe_mcp_tool` only read a server's own
# advertisement of itself. `call_mcp_tool` is where anything happens, so it
# is the one that is gated -- one entry rather than one per MCP tool, which
# is what makes the meta-tool design gateable at all (Step 13, §0.8).
# `search_memory` belongs here beside `read_ledger`: it reads .rudra/memory/
# and nothing else, and reads are never gated.
READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "ls",
        "glob",
        "grep",
        "read_ledger",
        "list_mcp_tools",
        "describe_mcp_tool",
        "search_memory",
    }
)
MUTATING_TOOLS = frozenset({"write_file", "edit_file", "delete", "execute", "call_mcp_tool"})

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

# The names a permission rule may actually name. `decide` returns for the
# control-plane and wrapped-execute sets BEFORE the user deny/allow loops,
# so a rule naming one of them can never fire -- and accepting it silently
# meant `deny = ["remember"]` validated, printed no warning, and did
# nothing. Rejecting them here is what turns that into an error the user
# can act on, and it is the set `parse_rule` reports as valid (CR-B5).
RULEABLE_TOOLS = ALL_GATED_TOOLS - CONTROL_PLANE_TOOLS - WRAPPED_EXECUTE_TOOLS

# Tools whose gated argument is not a path. `execute` carries a command,
# `call_mcp_tool` a `server__tool` id; resolving either against the project
# root would fabricate a spelling that a rule could match by accident.
_UNRESOLVED_TOOLS = frozenset({"execute", "call_mcp_tool", "describe_mcp_tool"})

# Which argument carries the thing a pattern matches against.
_ARG_KEYS = {
    "execute": "command",
    "call_mcp_tool": "tool_id",
    "describe_mcp_tool": "tool_id",
    "write_file": "file_path",
    "edit_file": "file_path",
    "delete": "file_path",
    "read_file": "file_path",
    "ls": "path",
    "glob": "pattern",
    "grep": "pattern",
}

# Alternate spellings of the path argument that FixWriteParamsMiddleware
# renames to `file_path` before the tool runs. The gate decides before that
# rename, so it must recognise them too -- see gated_arg. `delete` is listed
# even though the middleware does not repair it: an unrecognised spelling
# there must fail closed, not fall through to the mode default.
_ARG_ALIASES = {
    "write_file": ("filename", "path"),
    "edit_file": ("filename", "path"),
    "delete": ("filename", "path"),
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
            f"Valid tools: {', '.join(sorted(RULEABLE_TOOLS))}."
        )
    if separator and not pattern.strip():
        raise ValueError(f"Permission rule {text!r} has an empty pattern after ':'.")
    # Refuse the names a rule cannot affect, rather than accepting them and
    # doing nothing. `decide` returns for CONTROL_PLANE_TOOLS and
    # WRAPPED_EXECUTE_TOOLS before the user-deny loop, but parse_rule
    # accepted them because ALL_GATED_TOOLS includes both sets -- so
    # `deny = ["remember"]`, written to stop the agent writing to the memory
    # palace, validated, printed no warning, and had no effect (CR-B5).
    if tool in CONTROL_PLANE_TOOLS:
        raise ValueError(
            f"'{tool}' in permission rule {text!r} is control plane -- it writes no file "
            f"and runs no command, so it is never gated and a rule naming it does nothing."
        )
    if tool in WRAPPED_EXECUTE_TOOLS:
        raise ValueError(
            f"'{tool}' in permission rule {text!r} is gated as the command it runs. "
            f"Write an `execute:` rule instead, e.g. 'execute:git diff*'."
        )
    return Rule(tool=tool, pattern=pattern if separator else None)


def gated_arg(tool: str, args: dict[str, Any]) -> str | None:
    """The argument a pattern matches against, or None if absent.

    The file tools are checked under every spelling
    `FixWriteParamsMiddleware` will accept, in the order it tries them
    (`middleware/fix_write_params.py:77-80`). This is not defensive
    programming: the gate is installed as the OUTERMOST middleware
    (`subagents/build.py:219`, `agent/planner_agent.py:386`, both
    `insert(0, ...)`), so it decides on the model's raw spelling and the
    rename happens *afterwards*, on the way to the tool. Reading only
    `file_path` here meant `write_file(path=".git/config")` resolved to no
    path at all, so the floor short-circuited on `path is not None`
    (`floor.py:67`) and every patterned deny rule matched nothing -- while
    the middleware renamed the key and the write went through (CR-B2).
    """
    key = _ARG_KEYS.get(tool)
    if key is None:
        return None
    for candidate in (key, *_ARG_ALIASES.get(tool, ())):
        value = args.get(candidate)
        if isinstance(value, str):
            return value
    return None


# Shell metacharacters that chain a second command onto the first. `fnmatch`
# `*` matches every one of them, so `execute:pytest*` matched
# `pytest -q; rm -rf ~` -- and the backend runs the command through
# `/bin/sh -c` (deepagents backends/local_shell.py:306, shell=True), so the
# tail really executes. Splitting is deliberately naive: a separator inside
# quotes over-splits, which can only make a permissive rule match LESS and a
# deny rule match MORE. Both errors point the safe way (CR-B1).
_SEGMENT_SPLIT = re.compile(r"(?:\|\||&&|[;\n|&])")

# Command substitution smuggles a whole command into a segment that otherwise
# looks innocent (`pytest $(rm -rf ~)`), so no permissive rule may match a
# segment containing one.
SUBSTITUTION_MARKS = ("$(", "${", "`")


def command_segments(command: str) -> tuple[str, ...]:
    """The individual commands a shell would run for this command string."""
    return tuple(part.strip() for part in _SEGMENT_SPLIT.split(command) if part.strip())


def _execute_matches(pattern: str, commands: tuple[str, ...], *, permissive: bool) -> bool:
    """Match an `execute` pattern against every command that would run.

    A permissive rule (`allow`, or a session grant) must cover EVERY
    segment: consenting to `pytest` is not consenting to whatever was
    chained after it. A deny rule needs only ONE segment, so chaining
    cannot smuggle a denied command past it.
    """
    for command in commands:
        segments = command_segments(command)
        if not segments:
            continue
        if permissive:
            if any(mark in command for mark in SUBSTITUTION_MARKS):
                continue
            if all(fnmatch.fnmatchcase(segment, pattern) for segment in segments):
                return True
        elif any(fnmatch.fnmatchcase(segment, pattern) for segment in segments):
            return True
        # A deny rule also fires on the command as written, so a pattern
        # containing a separator itself (`execute:foo && bar`) still works.
        if not permissive and fnmatch.fnmatchcase(command, pattern):
            return True
    return False


def rule_matches(
    rule: Rule,
    tool: str,
    absolute: tuple[str, ...],
    relative: tuple[str, ...],
    *,
    permissive: bool = False,
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
        return _execute_matches(rule.pattern, absolute, permissive=permissive)

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
        mcp_in_auto: bool = False,
    ) -> None:
        self.mode = mode
        self.shell_in_auto = shell_in_auto
        self.mcp_in_auto = mcp_in_auto
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
        if tool in _UNRESOLVED_TOOLS or arg is None:
            return None, (), ()

        candidate = Path(arg)
        given = candidate if candidate.is_absolute() else self.project_root / candidate
        try:
            resolved = given.resolve()
        except (ValueError, OSError):
            # A NUL byte raises `ValueError: embedded null character`, and
            # nothing up the stack catches it -- not decide, not
            # RudraPermissionMiddleware._check, not interrupts._predicate --
            # so a malformed tool call killed the graph run instead of being
            # denied. grep/glob PATTERNS come through here as if they were
            # paths, so arbitrary model-authored search text reached
            # Path.resolve() (CR-B7).
            return None, (), ()

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
        # Fail closed. A mutating tool whose path argument is missing or is
        # not a string cannot be matched against any rule, so the floor and
        # every deny rule silently abstain and the call falls through to the
        # mode default -- which under --auto is "allow". Denying instead
        # costs a malformed call; allowing costs the whole policy (CR-B2).
        if tool in MUTATING_TOOLS and tool not in _UNRESOLVED_TOOLS and arg is None:
            return Decision("deny", "<unresolvable-path>", "fail-closed")
        resolved, absolute, relative = self._resolve(tool, arg)
        # Same rule one step later: a path that cannot be resolved at all
        # (a NUL byte, an OSError) leaves the floor and every deny rule with
        # nothing to match, which is the CR-B2 shape. Deny rather than fall
        # through to the mode default (CR-B7).
        if tool in MUTATING_TOOLS and tool not in _UNRESOLVED_TOOLS and resolved is None:
            return Decision("deny", "<unresolvable-path>", "fail-closed")
        if tool in _UNRESOLVED_TOOLS and arg is not None:
            absolute = (arg,)

        # 1. floor -- always against the resolved path, never a spelling
        suppressed: str | None = None
        hit = floor_hit(tool, resolved, arg if tool == "execute" else None, self.project_root)
        if hit is not None:
            if hit not in self.floor_disable:
                return Decision("deny", f"<floor:{hit}>", "floor")
            suppressed = hit

        def _final(effect: str, rule: str | None, source: str) -> Decision:
            # Every effect, not just allow. `disabled_floor_notice` promises
            # "Calls they would have blocked are still recorded in the audit
            # log", but the plan and ask branches returned without this
            # wrapper -- and the middleware records nothing for an `ask`, so
            # in ask mode the log held no trace that a floor rule had been
            # violated and suppressed. Only the auto path was tested
            # (CR-B6).
            if suppressed is not None:
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
            if rule_matches(rule, tool, absolute, relative, permissive=True):
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
            # Same measured argument, one layer out: an MCP server is a
            # separate process Rudra does not confine, and under --auto
            # nobody reads the call before it runs. After the allow rules
            # for the same reason -- naming an id there is consent for it.
            if tool == "call_mcp_tool" and not self.mcp_in_auto:
                return Decision("deny", "<auto:mcp-not-opted-in>", "auto-mcp")
            return _final("allow", None, "mode-default")
        if self.mode == "plan":
            return _final("deny", None, "mode-default")
        return _final("ask", None, "mode-default")


__all__ = [
    "ALL_GATED_TOOLS",
    "CONTROL_PLANE_TOOLS",
    "MUTATING_TOOLS",
    "READ_ONLY_TOOLS",
    "RULEABLE_TOOLS",
    "SUBSTITUTION_MARKS",
    "WRAPPED_EXECUTE_TOOLS",
    "Decision",
    "PermissionEngine",
    "Rule",
    "command_segments",
    "gated_arg",
    "parse_rule",
    "rule_matches",
]
