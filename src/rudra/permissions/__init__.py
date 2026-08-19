"""Rudra's permission layer (Step 7 — C3.1-C3.4).

deepagents' own `permissions=` cannot be used: it raises NotImplementedError
on any backend that supports command execution, and its FilesystemOperation
is ('read', 'write') only, so it never covered `execute` regardless. See
TODO.md U.7 and A1.46.

Everything outside this package goes through `build_gate`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Console

from rudra.permissions.approval import (
    ApprovalLoopExceeded,
    decide_action_requests,
    run_with_approvals,
)
from rudra.permissions.audit import AuditLog
from rudra.permissions.grants import SessionGrants
from rudra.permissions.interrupts import build_interrupt_on
from rudra.permissions.middleware import RudraPermissionMiddleware
from rudra.permissions.rules import PermissionEngine
from rudra.state.paths import rudra_paths

if TYPE_CHECKING:  # pragma: no cover
    from rudra.config.loader import Config


def _default_reader() -> str:
    """One line from the terminal. Replaced wholesale in tests.

    EOF means the terminal went away mid-run, so the safe answer is reject:
    an approval nobody granted must never be inferred from silence.
    """
    try:
        return input().strip()
    except EOFError:
        return "r"


@dataclass
class Gate:
    """Everything the agent constructor needs, built together.

    The engine and the grants must be the same objects the prompt mutates,
    which is why they are constructed here rather than separately at each
    call site -- an engine holding a different SessionGrants would accept
    `always` and then prompt again on the very next call.
    """

    engine: PermissionEngine
    middleware: RudraPermissionMiddleware
    interrupt_on: dict[str, Any]
    grants: SessionGrants
    audit: AuditLog
    mode: str
    project_root: Path
    reader: Callable[[], str] = field(default=_default_reader)

    def prompt(self, action_requests: list[dict[str, Any]], console: Console) -> list[dict]:
        return decide_action_requests(
            action_requests,
            engine=self.engine,
            grants=self.grants,
            audit=self.audit,
            console=console,
            project_root=self.project_root,
            mode=self.mode,
            reader=self.reader,
        )


def build_gate(cfg: Config, project_path: Path) -> Gate:
    """Construct the permission layer for one project run.

    Malformed rules raise here, at construction, rather than at the first
    tool call -- a run that is going to fail on its rules should fail before
    it spends a planner pass.
    """
    grants = SessionGrants()
    engine = PermissionEngine(
        mode=cfg.permissions.mode,
        allow=cfg.permissions.allow,
        deny=cfg.permissions.deny,
        floor_disable=cfg.permissions.floor_disable,
        project_root=project_path,
        grants=grants,
        shell_in_auto=cfg.tools.shell_in_auto,
        mcp_in_auto=cfg.mcp.mcp_in_auto,
    )
    audit = AuditLog(rudra_paths(project_path).logs / "permissions.jsonl")
    # Built before the middleware, which needs to know which names actually
    # reach a prompt -- an `ask` for a name that does not is denied (A1.53).
    interrupt_on = build_interrupt_on(engine)
    return Gate(
        engine=engine,
        middleware=RudraPermissionMiddleware(
            engine, audit, cfg.permissions.mode, interrupt_tools=frozenset(interrupt_on)
        ),
        interrupt_on=interrupt_on,
        grants=grants,
        audit=audit,
        mode=cfg.permissions.mode,
        project_root=Path(project_path),
    )


def disabled_floor_notice(cfg: Config) -> str | None:
    """One line naming every disabled floor rule, or None.

    Printed at run start so a config edited months ago cannot quietly stay
    off (spec §4.5).
    """
    disabled = cfg.permissions.floor_disable
    if not disabled:
        return None
    return (
        f"Deny-floor rules disabled: {', '.join(disabled)}. "
        f"Calls they would have blocked are still recorded in the audit log."
    )


def stdin_is_interactive() -> bool:
    """Whether an approval prompt can actually reach a human."""
    return bool(getattr(sys.stdin, "isatty", lambda: False)())


__all__ = [
    "ApprovalLoopExceeded",
    "Gate",
    "build_gate",
    "disabled_floor_notice",
    "run_with_approvals",
    "stdin_is_interactive",
]
