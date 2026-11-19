"""Dev-only probe: run laya-mlx for real and record its true output shape.

NOT shipped. Investigation scaffolding for S1 Task 3 — the laya model card does
not document the output dict, so we run both the raw `Agent.predict` path and the
`decide(..., return_details=True)` path and dump the verbatim keys/shapes for one
`choice`, one `score`, and one `noul` question. The recorded shape drives
`src/rudra/decide/normalize.py` (Task 4).

Usage:
    uv run python scripts/decide_probe.py              # default repo convaiinnovations/laya
    uv run python scripts/decide_probe.py REPO_ID      # try an alternate repo
"""

from __future__ import annotations

import dataclasses
import json
import sys
from typing import Any


def _show(label: str, obj: Any) -> None:
    print(f"\n### {label}")
    print(f"  python type: {type(obj)!r}")
    if isinstance(obj, dict):
        print(f"  keys: {sorted(obj.keys())}")
    try:
        print("  value: " + json.dumps(obj, indent=1, default=str)[:2000])
    except Exception as exc:  # noqa: BLE001 - probe, show anything
        print(f"  (not json-able: {exc}) repr: {obj!r}"[:2000])


def main() -> int:
    import laya_mlx

    repo = sys.argv[1] if len(sys.argv) > 1 else "convaiinnovations/laya"
    print(f"laya_mlx {laya_mlx.__version__}; loading {repo!r} ...")
    agent = laya_mlx.load(repo)
    print(f"loaded. agent type: {type(agent)!r}")

    # One question of each kind, using the documented question-dict format.
    state = {
        "request": "Add a --json flag to the status command and update the docs.",
    }
    questions = {
        "domain": {
            "type": "choice",
            "instructions": "What domain does `request` belong to?",
            "criteria": {
                "code": "software engineering, programming",
                "writing": "prose, docs, essays",
                "other": "none of the above",
            },
        },
        "difficulty": {
            "type": "score",
            "instructions": "How hard is `request` for a language model?",
            "criteria": [
                "trivial: a one-liner",
                "easy: short answer",
                "moderate: several steps",
                "hard: long multi-step reasoning",
            ],
        },
        "needs_clarification": {
            "type": "noul",
            "instructions": "Is `request` too ambiguous to start work without asking a question?",
        },
    }

    # --- Path 1: raw Agent.predict ---
    raw = agent.predict(state, questions)
    _show("Agent.predict(state, questions) -> top-level", raw)
    # Answers live under raw["answers"][qid], NOT at the top level.
    raw_answers = raw.get("answers", {}) if isinstance(raw, dict) else {}
    for qid in questions:
        _show(f"raw['answers'][{qid!r}]  (qtype={questions[qid]['type']})", raw_answers.get(qid))

    # --- Path 2: decide(..., return_details=True) -> DecisionResult ---
    details = laya_mlx.decide(agent, state, questions=questions, return_details=True)
    print(f"\n### decide(return_details=True) -> {type(details)!r}")
    if dataclasses.is_dataclass(details):
        for f in dataclasses.fields(details):
            _show(f"DecisionResult.{f.name}", getattr(details, f.name))

    # --- Path 3: same, with a min_confidence gate (the abstain mechanism) ---
    gated = laya_mlx.decide(
        agent, state, questions=questions, return_details=True, min_confidence=0.75
    )
    print("\n### decide(min_confidence=0.75) -> routing / gate under a threshold")
    if dataclasses.is_dataclass(gated):
        _show("gated.routing", getattr(gated, "routing", None))
        _show("gated.answer_confidence", getattr(gated, "answer_confidence", None))
        _show("gated.values", getattr(gated, "values", None))

    print("\nDONE. Copy the shapes above into NOTES-output-schema.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
