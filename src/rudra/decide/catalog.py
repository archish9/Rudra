"""The typed question sets for decisions #1-#4, and a token-budget helper.

Shapes match the real laya schema recorded in
``docs/.../NOTES-output-schema.md`` (not the model card):

- **choice** -> ``criteria`` is a ``{label: description | None}`` dict; laya
  returns the decided label under the answer's ``"choice"`` key.
- **score** -> ``criteria`` is an *ordered* list of ``"N label: desc"`` level
  strings; laya returns a float under ``"score"`` plus a ``legend``.
- **noul** -> NO ``criteria``; laya returns P(true) under ``"noul"``.

Static sets (#1, #2, #3) are module constants. The #4 selections are built at
call time because their options (MCP tool ids / skill names) are only known
then; the *shape* is fixed here. S4-S7 import these; none is wired this session.

laya-mlx has a 512-token total budget shared by questions + state (model card),
so ``fits_budget`` lets a caller refuse to ask when the prompt would overflow.
"""

from __future__ import annotations

from rudra.stacks.registry import PROFILES

# --- #2: clarify gating (planner_agent.py, before the clarify stage) ---------

CLARIFY_NEEDED: dict = {
    "needs_clarification": {
        "type": "noul",
        "instructions": (
            "Is this request too ambiguous to start work without first asking "
            "the user a clarifying question? Answer true only when a wrong "
            "assumption would waste real work."
        ),
    },
}

# --- #1: blocked-task failure classification (loop recovery routing) ---------
# Labels are the RECOVERY ROUTE S5 switches on; keep them stable and the set
# small (<=5) so questions + gate output stay inside laya's 512-token budget.

FAILURE_CLASS: dict = {
    "failure_class": {
        "type": "choice",
        "instructions": (
            "A task's verification gate failed. From the gate output in "
            "`gate_output`, what is the underlying cause?"
        ),
        "criteria": {
            "missing_dependency": "a package or module is not installed or declared",
            "wrong_approach": "the code's logic or design is wrong and must change",
            "flaky_or_environment": "an environment, timing, or setup problem, not the code",
            "needs_decomposition": "the task is too large and must be split into smaller tasks",
        },
    },
}

# --- #3: pre-dispatch task triage (run_task, before coder dispatch) ----------


def _domain_criteria() -> dict[str, str]:
    crit: dict[str, str] = {p.name: f"a {p.name} project" for p in PROFILES}
    crit["other"] = "something else, or no clear stack"
    return crit


TASK_TRIAGE: dict = {
    "needs_tests": {
        "type": "noul",
        "instructions": (
            "Does the task in `task` produce behavior that should be covered by "
            "automated tests? Answer false only for pure config, docs, or scaffolding."
        ),
    },
    "complexity": {
        "type": "score",
        "instructions": "How complex is the task in `task`?",
        "criteria": [
            "0 trivial: a one-line or single-file mechanical change",
            "1 normal: a few files, a clear approach",
            "2 hard: many files or multi-step reasoning to get right",
        ],
    },
    "domain": {
        "type": "choice",
        "instructions": "Which stack does the task in `task` belong to?",
        "criteria": _domain_criteria(),
    },
}

# --- #4: MCP tool + skill pre-selection (subagents/build.py prompt build) -----


def tool_select(tool_ids: list[str]) -> dict:
    """A choice over the available MCP tool ids (options filled at call time)."""
    return {
        "mcp_tool": {
            "type": "choice",
            "instructions": (
                "Which MCP tool, if any, is most likely needed for the task in "
                "`task`? Pick the single best-fitting tool."
            ),
            "criteria": {tid: None for tid in tool_ids},
        },
    }


def skill_select(skill_names: list[str]) -> dict:
    """A choice over the available skill names (options filled at call time)."""
    return {
        "skills_topk": {
            "type": "choice",
            "instructions": (
                "Which skill is most relevant to the task in `task`? Pick the "
                "single most relevant skill."
            ),
            "criteria": {name: None for name in skill_names},
        },
    }


# --- token budget ------------------------------------------------------------

LAYA_TOKEN_LIMIT = 512
# Leave headroom below the hard limit: laya's own framing + truncation margin.
_SAFE_FRACTION = 0.6


def _estimate_tokens(text: str) -> int:
    # No laya tokenizer offline; ~4 chars/token is the standard rough estimate,
    # deliberately conservative (an overestimate refuses early rather than late).
    return (len(text) + 3) // 4


def _question_text(questions: dict) -> str:
    parts: list[str] = []
    for q in questions.values():
        if not isinstance(q, dict):
            continue
        parts.append(str(q.get("instructions", "")))
        crit = q.get("criteria")
        if isinstance(crit, dict):
            for k, v in crit.items():
                parts.append(f"{k}{'' if v is None else v}")
        elif isinstance(crit, list):
            parts.extend(str(x) for x in crit)
    return " ".join(parts)


def _state_text(state: object) -> str:
    if isinstance(state, str):
        return state
    if isinstance(state, dict):
        return " ".join(f"{k}{v}" for k, v in state.items())
    return str(state)


def fits_budget(
    questions: dict,
    state: object,
    *,
    limit: int = LAYA_TOKEN_LIMIT,
    safe_fraction: float = _SAFE_FRACTION,
) -> bool:
    """True iff questions (instructions + options) plus state fit the budget.

    Conservative: estimates tokens and compares against ``safe_fraction * limit``
    so a near-miss refuses rather than silently truncates inside laya.
    """
    total = _estimate_tokens(_question_text(questions)) + _estimate_tokens(_state_text(state))
    return total <= safe_fraction * limit
