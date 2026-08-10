"""Append-only JSONL record of every gated permission decision.

Written in every mode, including `auto`: an unattended run that keeps no
record of what it was allowed to do is exactly the run whose record matters
most.

Silent default-allow reads are deliberately NOT recorded. A run makes
hundreds, and including them stops the log being something a human skims
after a surprising run (spec §6.7).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from rudra.permissions.rules import CONTROL_PLANE_TOOLS, READ_ONLY_TOOLS, Decision

# Noise is reads, not routine decisions. An earlier version silenced by
# SOURCE — anything allowed by the mode default — which emptied the log
# completely in auto mode, where every write is exactly that. That is the
# run whose record matters most, so the rule keys on the TOOL instead:
# reads and Rudra's own bookkeeping are silent, mutations never are.
SILENT_TOOLS = READ_ONLY_TOOLS | CONTROL_PLANE_TOOLS

# Outcomes that only a human can produce.
_HUMAN_OUTCOMES = frozenset({"approve", "reject"})


class AuditLog:
    """One JSONL file per project run tree."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._warned = False

    def _should_record(self, tool: str, outcome: str) -> bool:
        """Silent only for a read that was allowed. Everything else lands."""
        return not (outcome == "allow" and tool in SILENT_TOOLS)

    def record(
        self,
        tool: str,
        arg: str | None,
        decision: Decision,
        *,
        mode: str,
        outcome: str,
    ) -> None:
        """Append one decision.

        `outcome` is what happened; `decision.effect` is what the engine
        asked for. They differ precisely where a human intervened, which is
        the interesting case: effect "ask" with outcome "reject".
        """
        if not self._should_record(tool, outcome):
            return

        source = decision.source
        if source == "mode-default" and outcome in _HUMAN_OUTCOMES:
            source = "prompt"

        entry = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "tool": tool,
            "arg": arg,
            "rule": decision.rule,
            "mode": mode,
            "decision": outcome,
            "source": source,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        except OSError as exc:
            # Never abort a run over a log, and never swallow the failure.
            # Warned once so a broken path does not print on every call.
            if not self._warned:
                self._warned = True
                print(f"warning: could not write the permission audit log: {exc}", file=sys.stderr)


__all__ = ["SILENT_TOOLS", "AuditLog"]
