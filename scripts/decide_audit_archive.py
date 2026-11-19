#!/usr/bin/env python3
"""Read-only audit of archived Rudra runs for laya decision labels (S2 Task 2).

For each of the four decisions in the catalog, this walks
``$XDG_STATE_HOME/rudra/runs/<project>/<run>/`` and reports whether the archive
already carries the ground-truth LABEL needed to fit laya from history
(`derivable now`), only the input signal but not the outcome
(`needs instrumentation`), or nothing usable (`zero-shot only`).

It NEVER writes, mutates, or imports from a run. Dev tool; not shipped behavior.
Run:  python -I scripts/decide_audit_archive.py  [archive_root]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def archive_root(argv: list[str]) -> Path:
    if len(argv) > 1:
        return Path(argv[1]).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "rudra" / "runs"


def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _iter_jsonl(path: Path):
    try:
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def run_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for project in sorted(root.iterdir()):
        if not project.is_dir():
            continue
        for run in sorted(project.iterdir()):
            if run.is_dir():
                out.append(run)
    return out


def audit(root: Path) -> dict:
    runs = run_dirs(root)
    totals = {
        "runs": len(runs),
        "tasks": 0,
        "done": 0,
        "blocked": 0,
        # #2 clarify
        "runs_with_clarify_ask": 0,
        # #1 failure-class
        "blocked_with_note": 0,
        "blocked_with_cause_signal": 0,
        "unblocks_tasks": 0,
        "unblocks_resolved_done": 0,
        # #3 triage
        "runs_with_tester": 0,
        "tasks_with_attempts_and_seconds": 0,
        "runs_with_stack_fact": 0,
        # #4 mcp/skill
        "mcp_meta_tool_calls": 0,
        "skill_outcome_attribution": 0,
    }
    for run in runs:
        ledger = _load_json(run / "ledger.json") or {}
        tasks = ledger.get("tasks", []) if isinstance(ledger, dict) else []
        by_id = {t.get("id"): t for t in tasks}
        totals["tasks"] += len(tasks)
        for t in tasks:
            st = (t.get("status") or "").lower()
            if st == "done":
                totals["done"] += 1
            if st == "blocked":
                totals["blocked"] += 1
                if (t.get("note") or "").strip():
                    totals["blocked_with_note"] += 1
                # a cause signal: note text, halts, convergence, or dep gates
                if any(
                    t.get(k)
                    for k in ("note", "halts", "convergence", "dependency_gates", "run_errors")
                ):
                    totals["blocked_with_cause_signal"] += 1
            if t.get("unblocks"):
                totals["unblocks_tasks"] += 1
                # recovery outcome: the task(s) this one unblocks ended done
                targets = t.get("unblocks")
                targets = targets if isinstance(targets, list) else [targets]
                if any((by_id.get(x) or {}).get("status", "").lower() == "done" for x in targets):
                    totals["unblocks_resolved_done"] += 1
            if t.get("attempts") is not None and t.get("seconds") is not None:
                totals["tasks_with_attempts_and_seconds"] += 1

        # facts.json: a stack/language/domain fact (feeds #3 domain)
        facts_doc = _load_json(run / "facts.json") or {}
        facts = facts_doc.get("facts", facts_doc) if isinstance(facts_doc, dict) else {}
        if any(
            any(w in str(k).lower() for w in ("stack", "language", "lang", "domain", "framework"))
            for k in (facts or {})
        ):
            totals["runs_with_stack_fact"] += 1

        # debug log: clarify ask_user, tester subagent, mcp meta-tools
        roles_seen_tester = False
        asked = False
        for rec in _iter_jsonl(next(iter(run.glob("debug-*.jsonl")), run / "nope")):
            k = rec.get("kind")
            if k == "tool_call":
                name = rec.get("name")
                if name == "ask_user" and rec.get("role") == "planner":
                    asked = True
                if name in ("list_mcp_tools", "describe_mcp_tool", "call_mcp_tool"):
                    totals["mcp_meta_tool_calls"] += 1
            elif k == "subagent_done" and rec.get("role") == "tester":
                roles_seen_tester = True
        if asked:
            totals["runs_with_clarify_ask"] += 1
        if roles_seen_tester:
            totals["runs_with_tester"] += 1

    return totals


VERDICTS = {
    "#2 needs_clarification": (
        "needs instrumentation",
        "clarify's ask_user IS in the archive (derivable input + action), but whether "
        "asking MATTERED (plan/outcome changed vs. not asking) is not recorded. Ship "
        "zero-shot+abstain; log a decision_label capturing (greenfield, asked, run converged).",
    ),
    "#1 failure_class": (
        "needs instrumentation",
        "the CAUSE signal is rich (34/34 blocked tasks carry note; many carry "
        "halts/convergence/dependency_gates), BUT there is no clean categorical cause-class "
        "label, and the recovery-OUTCOME label the plan requires is absent: 0 of the 8 "
        "unblocks tasks resolve to a done target. Ship zero-shot+abstain; log a decision_label "
        "recording (cause features, chosen recovery, did it converge) so rows accrue.",
    ),
    "#3 triage": (
        "mixed: complexity+domain derivable now, needs_tests needs instrumentation",
        "complexity = attempts+seconds proxy (per task, derivable). domain = stack fact "
        "(derivable when present). needs_tests: subagent_done carries role=tester but NOT a "
        "task id, so 'did the tester run for THIS task' is not cleanly per-task derivable -> "
        "log a decision_label at dispatch.",
    ),
    "#4 mcp_tool / skills_topk": (
        "zero-shot only / needs instrumentation",
        "the trace records which MCP meta-tool was called but not whether the pick HELPED, and "
        "no skill-to-outcome attribution exists at all. No usable label today; ship zero-shot "
        "and add outcome instrumentation before fitting.",
    ),
}


def main() -> int:
    root = archive_root(sys.argv)
    totals = audit(root)
    print(f"archive root: {root}")
    print(f"(exists: {root.is_dir()})\n")
    for k, v in totals.items():
        print(f"  {k:36} {v}")
    print("\n=== per-decision verdict ===")
    for name, (verdict, why) in VERDICTS.items():
        print(f"\n{name}: {verdict}\n  {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
