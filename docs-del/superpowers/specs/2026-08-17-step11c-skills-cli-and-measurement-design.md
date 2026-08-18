# Step 11c — The `rudra skills` Command, User Skills, and the Selection Measurement (C5.6, C5.1, C5.8, C5.9)

**Date:** 2026-08-17
**Status:** design approved, not implemented
**Ledger rows:** closes `C5.6`, the remaining half of `C5.1`, `C5.8` and `C5.9`.
**Obsoletes `C5.5`** — see §1.
**Depends on:** Step 11b — by code. The cache, the `/skills/` route, `skills=`
wiring and `ROUTE_PREFIXES` all ship there.
**Blocks:** nothing. This closes Step 11 and Stage IV's first row.

---

## 0. What changes for a user

Three things. They can see what skills exist and why one is not loading
(`rudra skills list` / `validate`). They can write their own, in their project
or their home directory. And the shipped default set is decided on measured
evidence rather than on D11's 2026-08-05 estimate.

---

## 1. `C5.5` is obsolete

The row reads: *"Port the superpowers hooks concept (`hooks/session-start`) —
the session-start injection has no Rudra equivalent yet."*

Reading the vendored hook settles it. `hooks/session-start` does exactly one
thing: reads `skills/using-superpowers/SKILL.md`, JSON-escapes it, and emits it
as session context. Its whole body is that plus a three-way branch on
`CURSOR_PLUGIN_ROOT` / `CLAUDE_PLUGIN_ROOT` / `COPILOT_CLI` to pick the field
name each host expects.

**That is precisely what `C5.3` already does**, and by a shorter route:
`planner_agent.bootstrap_text()` reads the *rendered* bootstrap from the cache
and appends it to every planner stage's system prompt (Step 11b).

The hook exists because Claude Code, Cursor and Copilot are **host harnesses
with a hook API and no other way to reach the system prompt**. Rudra builds its
own prompts, so the mechanism has nothing to add. The hook's payload even
instructs the model to "use the 'Skill' tool" — which Rudra does not have, and
which `rudra_tools.md` already reconciles.

This is the same disposition, for the same structural reason, as `C5.4`: a row
describing a *port* whose purpose Rudra achieves natively. Marked OBSOLETE with
this evidence rather than deleted, so the reasoning survives.

**A general hook system — user scripts run at session start, before a task,
after a task — is a different feature** than this row describes: a new
subsystem with its own security surface (arbitrary scripts, permission gating,
failure semantics). It is not in scope here and is not implied by `C5.5`.

---

## 2. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S11c.1** | `C5.5` is OBSOLETE | §1. No hook system in this step |
| **S11c.2** | `C5.8` is measured with **full agent runs**, not an isolated harness | Maximum realism; the cost is small N and confounding. The spec compensates by pre-registering criteria (§5) so the result cannot be read after the fact to suit either answer |
| **S11c.3** | Measured on the **NVIDIA 550B only** | Pragmatic — the Ollama cloud tier hit its quota mid-session and the NVIDIA endpoint is stable. **This cannot answer `C5.8` as written**: D6 sets the floor at 32B, so whatever `C5.9` concludes rests on evidence from a model most users will not run. Recorded as a limitation attached to the answer, not a footnote |
| **S11c.4** | `validate` ships **before** user directories become readable | S11b.3's condition. A writable directory where a typo means a silently skipped skill, with no command to diagnose it, is worse than no directory |

---

## 3. `rudra skills`

A Typer sub-app registered exactly like `models` and `config` (`cli.py:65,68`).

### `rudra skills list`

Every skill Rudra can see, where it came from, and whether it is indexed.

```
  brainstorming              bundled   enabled    Use before any creative work…
  writing-plans              bundled   enabled    …
  using-git-worktrees        bundled   —          …
  deploy-checklist           project   enabled    Use when shipping to prod…
  brainstorming              user      shadowed by project
```

Three columns carry the whole model: **source** answers "where did this come
from", **enabled** answers "is it in the prompt", and `shadowed by` answers the
question that would otherwise generate bug reports — why a skill exists and does
nothing.

### `rudra skills validate [PATH]`

With no argument, validates every user and project skill. With a path,
validates that directory.

**It checks against deepagents' own parser**, not a reimplementation:
`_parse_skill_metadata` and `_validate_skill_name` from
`deepagents.middleware.skills`, the same technique 11a's contract test uses.
A hand-rolled check would drift from the real contract, and the failure mode of
drift here is the exact thing this command exists to prevent.

What that covers, with upstream's own limits:

| Check | Limit |
|---|---|
| `SKILL.md` exists and is UTF-8 | — |
| YAML frontmatter parses | — |
| `name` present, matches the parent directory | ≤ 64 chars (`skills.py:145`) |
| `description` present and non-empty | ≤ 1024 chars (`skills.py:146`) |
| File size | ≤ 10 MB (`skills.py:139`) |

Exit **0** when everything would load, **1** when anything would be silently
skipped — naming each one and why. This is the only place a user can find out
that their skill is invisible, because `SkillsMiddleware` logs a warning and
carries on.

### `rudra skills rebuild`

Forces a cache render, prints the path, and prunes stale keys — 11a's
deliberately deferred garbage collection, which now has a command to live on.

`rebuild` earns its place in this step specifically: user directories change
without the cache key changing, because the key hashes *vendored* manifests
(11a §3). Nothing else would ever force a refresh.

**There is no `add` / `remove` / `update`.** `add`/`remove` are `[skills]
enabled` edits; `update` was dropped in S11a.3 because the corpus is frozen.

---

## 4. User skills

### Layering

`SkillsMiddleware` takes sources in order and **later sources win**
(`skills.py:955`). Rudra passes:

```
1. bundled            lowest precedence
2. ~/.rudra/skills/
3. <project>/.rudra/skills/   highest
```

So a project skill named `brainstorming` shadows the bundled one. That is
deliberate and is upstream's own mechanism for exactly this.

**Note this is the opposite rule to 11a's bundle collision, and the difference
is intent.** Two vendored corpora colliding inside one rendered tree is an
accident nobody asked for, so `render()` raises (S11a.6). A user putting a file
in their own project is an instruction. `rudra skills list` makes the shadowing
visible either way.

### Mounting

User skills are **not** rendered into the keyed cache. Their content changes
freely, and the cache key hashes vendored manifests only, so a copy would go
stale the moment someone edited a file. They mount directly at their real
directories.

That means more routes, and it raises one mechanism question this spec
deliberately does not assume the answer to: **does `CompositeBackend` resolve
`/skills/` and `/skills/user/` unambiguously, or does the shorter prefix
swallow the longer?** If prefixes must be disjoint, the layout becomes
`/skills/`, `/user-skills/`, `/project-skills/` — less tidy, unambiguous.

**The plan's first task settles this against the real backend before any
production code is written**, exactly as 11b's Task 1 did, and stops if the
mechanism does not hold. That check is why `A1.79` was 11b's only route
surprise rather than its second.

One consequence worth stating: `ROUTE_PREFIXES` grows, and 11b's guard test —
asserting the mounted routes equal the prefixes the path normalizer knows —
fails loudly if a route is added without telling the normalizer. **`A1.79`
cannot recur silently.**

---

## 5. The `C5.8` measurement

### Why the criteria are fixed first

N is 8. The model has already behaved inconsistently — one spontaneous skill
read across five runs in Step 11b, and `A1.80`'s `<SUBAGENT-STOP>` echo in
another. With a sample that small and a signal that noisy, logs can be read
after the fact to support either conclusion. So the criteria below are part of
the spec, committed before any run.

### Matrix

**4 tasks × 2 arms × NVIDIA 550B = 8 runs.** Identical tasks in both arms.

| Arm | `[skills] enabled` |
|---|---|
| A | D11's 9 (the shipped default) |
| B | 13 — all but `writing-skills` |

Tasks are chosen so exactly one skill clearly applies, and so that between them
they cover the skills the *skill-carrying* agents hold — the planner stages, the
coder and the tester. The reviewer and general-purpose agent get no index
(S11b.1), so no task targets them.

| Task shape | Skill that should apply |
|---|---|
| Diagnose a failing test in existing code | `systematic-debugging` |
| Implement a small feature with tests | `test-driven-development` |
| Build something under-specified | `brainstorming` / `writing-plans` |
| Act on review feedback | `receiving-code-review` |

### Recorded per run

Whether any `/skills/…/SKILL.md` was read; which; whether it was the apt one;
tasks done/blocked; and whether the `A1.80` echo appeared.

**Read counting must not use a naive line regex.** Rich wraps long lines, so
`grep "CALL read_file.*'/skills/"` under-reports — measured during 11b, where
that pattern returned 0 for a run that demonstrably read a skill. Match on the
path alone.

### Decision rule for `C5.9`

| Outcome | Action |
|---|---|
| Arm B reads apt skills as often as arm A, with no additional blocked tasks | Enable the four; `writing-skills` stays off |
| Arm B reads fewer apt skills, or blocks more tasks | Stay at 9, recording the degradation as the reason |
| **Both arms near-zero reads** | Stay at 9 — and the finding is that the **bootstrap** fails at this scale, not that the index is too long. Reopens `A1.80`'s primer question rather than the count |

The third row is the expected one on current evidence. Naming it in advance is
the point: if it happens it is a result, not a disappointment to be explained
away by adjusting the prompt until a run looks better.

### `A1.80`

Counted across all 8 runs rather than fixed on one observation. If it recurs,
the fix is the three-line strip of `<SUBAGENT-STOP>…</SUBAGENT-STOP>` in
`bootstrap_text`, beside the frontmatter strip that is already there. If it does
not recur, the row closes as a one-off.

---

## 6. Testing

None of it live.

| Area | Test |
|---|---|
| **Route mechanism** | Multiple routes resolved unambiguously by the real `CompositeBackend`; written first, no production code |
| `ROUTE_PREFIXES` guard | Still passes with the new routes — the 11b test that makes `A1.79` unrepeatable |
| `list` | Bundled, user and project skills each appear with the right source; a shadowed skill is labelled |
| `validate` | A deliberately malformed skill — bad frontmatter, name/directory mismatch, empty description — exits 1 and names the reason; a good tree exits 0 |
| `rebuild` | Renders, reports the path, removes stale keys, keeps the current one |
| Precedence | Project shadows user shadows bundled, asserted through a real `SkillsMiddleware` |

Gates unchanged: `ruff check src/ tests/` → `All checks passed!`, format clean,
`uv run pytest -q` never goes down from **1150 passed, 2 skipped**.

---

## 7. Acceptance

**The 8 measurement runs of §5**, plus one that measures nothing and proves
something the runs cannot:

**A user-authored skill in `<project>/.rudra/skills/` is read by the model
during a real run.** Mounted-and-ignored looks identical to mounted-and-working
in every test that does not involve a model choosing. This is the only evidence
the new directories function end to end.

Both use `docs/superpowers/pty_drive.py` where a terminal is needed.

---

## 8. Out of scope

- **Hooks** — `C5.5` is obsolete (§1); a general hook system is a separate step.
- **`extra_body` / `top_p`** for NVIDIA reasoning mode. Documented as absent in
  `03-providers.md`; A1.36 warns blanket kwarg passthrough silently drops values
  on providers that do not accept them, so it needs per-provider validation and
  a step of its own.
- **Any change to the vendored corpus** — frozen (S11a.3), hash-verified.
- **A 32B re-measurement.** Not in this step (S11c.3), and the row will say so.

---

## 9. Ledger impact

| Row | Disposition |
|---|---|
| `C5.6` | Closed — `list` / `validate` / `rebuild`; no `add`/`remove`/`update` |
| `C5.1` | Fully closed — user and project sources mounted and layered |
| `C5.8` | Closed **with its limitation stated**: measured at 550B, not at D6's 32B floor |
| `C5.9` | Decided by §5's pre-registered rule |
| `C5.5` | **OBSOLETE** — §1 |
| `A1.80` | Closed or confirmed by the 8-run count |
| `Documentation/12-skills.md` | "Adding your own — not yet supported" becomes untrue |
| `Documentation/08-project-status.md` | "You cannot add your own skills" moves out of *What doesn't work yet* |
| `Documentation/04-cli-reference.md` | Gains `rudra skills` |
