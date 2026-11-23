# S7 / OPEN-179 — Design Amendment (pre-implementation)

> Amends `07-mcp-skill-preselect.md` after checking its premises against the tree
> (CLAUDE.md lessons 2 & 3: a plan's premise is not evidence; verify against the
> code before executing). Read `00-master.md` §3 §4 and `07-*.md` first; this file
> only records what changed and the calls the owner settled on 2026-11-23.
> Force-added to the branch like the other plan notes (plan dir is gitignored).

## Why this amendment exists

Plan 07's **skill-half premise is inaccurate vs the current tree.**

- Plan says: *"OPEN-168 inlines **all** skills into coder/tester/reviewer/planner
  prompts — a fat fixed cost every turn; laya top-k trims it."*
- Reality (verified): OPEN-168 (merge `150891f`) inlined **static, hand-authored
  per-role methodology** — `_CODER_METHOD`, tester red-green, reviewer rubric — in
  `subagents/registry.py:286+` and `agent/planner_agent.py`. That text is **not a
  corpus and not laya-selectable.**
- The actual superpowers corpus (**14 skills**, `skills/registry.py::BUNDLES`,
  `bundle.skill_names()`) still rides on deepagents' `SkillsMiddleware`
  (`.venv/.../deepagents/middleware/skills.py`) as a **progressive-disclosure
  index**: skill *descriptions* (~1 line each) in the system prompt, bodies pulled
  on demand via `read_file`. There is **no fat inlined skill blob to prune.**
- So the skill-half's upside is **modest** (the index is descriptions, not bodies;
  OPEN-168's own note — memory `open-168-skills-inline-per-stage` — says
  progressive disclosure left skills looking *unused*, i.e. the model rarely pulls
  bodies). The MCP-half is the real win.

The **MCP-half matches the tree exactly** and is high-value: the agent today burns
`list_mcp_tools → describe_mcp_tool → call_mcp_tool` (`mcp/tools.py`, 2–3 Vaayu
turns). A pre-picked tool id + its schema injected into the prompt collapses that.

## Owner decisions (2026-11-23)

1. **Scope: build BOTH halves, amended.** MCP hint + skill top-k. Both
   live-proven to close OPEN-179.
2. **Skill floor = the superpowers process core**, which laya may never drop:
   `using-superpowers`, `test-driven-development`, `systematic-debugging`,
   `verification-before-completion`. laya only **adds/reorders** on top. Abstain /
   review / unavailable → **no hint** (today's behaviour, all 14 indexed equally).
3. **MCP live-verification = configure `mempalace`** (the shipped default, goal #6)
   in `.mcp.json` for the live run; prove from `.rudra/run/logs/` that the agent
   used the hinted tool **without** the list→describe exploration turns.

## Mechanism corrections (supersede plan 07 Tasks 2 & 3 wording)

### Skill top-k is a PROMPT HINT, not a filter of the inlined set

Because the corpus is progressive-disclosure (nothing to "un-inline"), `skill_topk`
does **not** remove skills from `SkillsMiddleware`'s index. It produces an **ordered
hint** that `_prompt_for` renders as a short block, e.g.:

> *Most relevant skills for this task (read these SKILL.md first): `<topk>`.*

- This keeps **OPEN-168's invariant literally**: Rudra injects a hint, removes **no
  capability** — every skill stays reachable in the index. "Hints only" (plan §3).
- The **floor** is simply which skill names are always present in the hint (the
  process core above); laya reorders/adds. A wrong laya pick therefore **cannot
  drop** a skill — it only fails to *promote* one, which degrades to today.
- Source of candidate names: `bundle.skill_names()` ∩ `cfg.skills.enabled`
  (`skills/registry.py`, `config/schema.py::SkillsConfig`). `catalog.skill_select`
  already builds the `choice`; top-k is derived by ranking its `probabilities`
  (`NOTES-output-schema.md`) and taking the mass/-count above the floor.
- `_skills_for` and the `skills=` wiring are **untouched** → the OPEN-168 "skills
  inlined/indexed, not pulled-by-removal" invariant test stays green unchanged.

### Both hints are computed at DISPATCH, injected into the prompt

The `DecisionEngine` lives in `LoopContext` (`loop/engine.py:143`), **not**
`SubagentContext`; MCP listing (`mcp/tools.py`) is **async** and `_prompt_for` is
sync. So — exactly like S6 triage (`loop/engine.py:1107`, `asyncio.to_thread`) —
compute both hints in `run_task` **before dispatch** and pass them into the coder/
tester prompt build. No engine is threaded into sync `build.py`; no async MCP call
is made from build time. (Plan 07 Task 3 explicitly allowed "compute at dispatch
and pass in"; this chooses that branch and drops the "thread engine into
build_agent context" branch.)

### MCP hint respects the subagent's visibility

`mcp_hint` builds its `choice` options only from tools **visible to this subagent**
(`mcp/tools.py::_visible`, filtered by the spec's `allow`/`deny`), and returns
`None` if laya's pick is not visible, or on abstain/no-servers — never hints a tool
the agent cannot call (plan 07 Review Focus).

## Net-cost gate (unchanged, binding)

A hint that does not cut exploration is a net loss (laya call + bigger prompt). The
live run **must** show fewer MCP exploration turns (and/or the skill hint changing
which bodies are pulled) on the same request. A non-reduction = re-tune or revert
the offending half, **not** ship. `backend="off"` → identical to today.

## Task map (as amended)

| Task | File | Change from plan 07 |
|---|---|---|
| 1. `mcp_hint` | `decide/gates.py` + `tests/decide/test_gate_mcp.py` | As planned. Options from `_visible` tool ids; fetch chosen tool schema to inline; `None` on abstain / not-visible / no-servers. |
| 2. `skill_topk` | `decide/gates.py` + `tests/decide/test_gate_skills.py` | **Returns an ordered hint list = floor ∪ laya top-k (nothing removed).** Abstain/unavailable → `[]` (no hint). Floor always present. |
| 3. Wire at dispatch | `loop/engine.py::run_task`, `subagents/build.py::_prompt_for` + `tests/decide/test_preselect_wiring.py` | Compute both hints in `run_task` (`asyncio.to_thread`), pass into `_prompt_for` as optional blocks. **Do not** thread engine into build.py. OPEN-168 invariant test stays green. |
| 4. Live verify | `mempalace` in `.mcp.json`; a task needing one MCP tool | Prove hinted tool used w/o list→describe; record shrink; `backend="off"` == today. New "Live verification" note. |

## Done criteria (unchanged from plan 07 §Done, with the amendment)

- MCP hint only names visible tools; skill hint never drops the floor; abstain →
  today's behaviour, no hint.
- OPEN-168 "skills inlined not pulled-by-removal" invariant preserved + tested.
- Measured turn/prompt reduction on at least one real request (MCP half).
- `ruff`/`pytest` green; `TODO.md` updated; committed to branch, **not merged**.
