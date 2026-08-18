# Step 11b — Wiring the Skill Corpus to the Agents (C5.1, C5.2a, C5.2b, C5.3)

**Date:** 2026-08-17
**Status:** design approved, not implemented
**Ledger rows:** closes `C5.2a` (cache builder), `C5.2b` (composite wiring), `C5.3`
(bootstrap injection), and the vendored-source half of `C5.1`. `C5.9` stays open
by design (S11a.7 defers enablement to 11c).
**Depends on:** Step 11a — by code, not merely by order. `render()`, `BUNDLES`,
`DEFAULT_ENABLED` and `manifest_root_hash()` all ship there.
**Blocks:** Step 11c (`C5.5`, `C5.6`, `C5.8`) — the CLI lists what this builds,
and the live measurement judges what this exposes.

---

## 0. What changes for a user

Today the superpowers corpus ships inside the wheel and no agent can see it.
After this step, the planner and the two writing subagents carry a nine-skill
index in their prompts, and can read any of the fourteen by path.

Nothing else about a run changes: same commands, same approval flow, same gate.

---

## 1. The four links

11a built the corpus and the pure transform that renders it. Nothing calls
`render()`, no backend serves it, no agent is constructed with `skills=`, and
`[skills]` is still a hard error (`config/schema.py:44`). This step supplies
the four missing links, each in exactly one place.

| Link | Where | Today |
|---|---|---|
| Build the cache | new `skills/cache.py` | `render()` is never called |
| Serve it | `agent/main_agent.py:354` (`build_backend`) | routes `/artifacts/` only |
| Index it | `agent/planner_agent.py:296`, `subagents/build.py:153` | `skills=` never passed |
| Let the user steer it | `config/schema.py:44` | `[skills]` reserved |

There are exactly two `create_deep_agent` call sites in Rudra, and one
`build_backend`. That is not luck — `build.py` being the only subagent assembly
path is a Step 9b invariant, and it is what makes this step small.

---

## 2. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S11b.1** | **One shared skill set, given per agent rather than subset per agent.** Planner stages, coder and tester receive it; reviewer and general-purpose do not | No transform change. `SkillsMiddleware` takes *directory paths*, not allowlists, so per-agent subsets would mean per-role rendered trees — a change to 11a's transform, multiplied disk, and a role→skill mapping hard-coded before any evidence says which mapping is right. Revisit only if 11c measures the coder being distracted |
| **S11b.2** | **An unwritable cache falls back to a per-run temp directory**, prints one line, and skills still work | Rendering costs ~20ms for 550KB — measured, not estimated — so the fallback is cheaper than most single tool calls. The property being bought is that **the same command produces the same agent on a locked-down CI box and on a laptop**. An agent that silently reasons differently by environment is the worst of the three options, and refusing to run turns a cosmetic infrastructure problem into an outage over an optional library |
| **S11b.3** | **Vendored sources only. User skill directories defer to 11c** | `C5.1` names `~/.rudra/skills/` and `<project>/.rudra/skills/`. A malformed `SKILL.md` is *silently skipped* by `SkillsMiddleware`, and the command that would diagnose it — `rudra skills validate` — is 11c. Shipping the directory before the diagnostic generates confused bug reports rather than value |
| **S11b.4** | **The bootstrap is injected into the planner stages only** | Not a token-saving choice: `using-superpowers/SKILL.md:5-7` opens with `<SUBAGENT-STOP>` — *"If you were dispatched as a subagent to execute a specific task, ignore this skill."* Injecting it into the coder would spend ~780 tokens instructing it to disregard what it just read. Upstream drew this line; Rudra follows it |
| **S11b.5** | **`[skills]` ships with one key, `enabled`** | It is both the selector and the off switch (`enabled = []`). A separate `enable = false` would be a second way to say the same thing, and §6's config philosophy is one place per decision |

---

## 3. The cache

### Module

`src/rudra/skills/cache.py`, the only module that knows where rendered skills
live. `transform.render()` stays pure and unaware.

```python
@dataclass(frozen=True)
class SkillCache:
    root: Path          # the <key> directory
    active_path: str    # "/skills/active/" -- the backend-relative source
    fell_back: bool     # True when the XDG root was unwritable

def cache_key(bundles: Sequence[Bundle], enabled: frozenset[str]) -> str: ...
def ensure_cache(bundles: Sequence[Bundle], enabled: frozenset[str]) -> SkillCache: ...
```

### The key

```
sha256( rudra.__version__
      + each bundle's manifest_root_hash()   (registry order)
      + TRANSFORM_VERSION
      + sorted(enabled) )
```

Every input is something that must change the rendered output. The corpus hash
is what makes the owner's hand-update (S11a.3) invalidate every user's cache
with no version negotiation anywhere. `TRANSFORM_VERSION` is a constant in
`transform.py`, bumped when the rendering rules change — the one input a hash of
the *inputs* cannot detect.

Location: `${XDG_CACHE_HOME:-~/.cache}/rudra/skills/<key>/`.

One install serves many project directories, which is the constraint D13 was
written against (§0.4: `rudra` is installed once, globally, then run in many
projects). Two projects with the same enabled set share one cache; a project
that enables something different gets its own key rather than fighting over one
directory.

### Building it

1. If `<key>/.complete` exists → reuse. Done.
2. Otherwise `render()` into `<key>.tmp-<pid>` beside it, write `.complete` last,
   then `os.replace()` onto `<key>`.
3. On any `OSError` anywhere above → render into `tempfile.mkdtemp()`, set
   `fell_back=True`, print one line.

**The `.complete` sentinel is the load-bearing part.** A run killed mid-render
otherwise leaves a directory that exists, is half-populated, and — because the
key is a pure function of its inputs — is never invalidated by anything. Every
subsequent run would reuse the wreckage. Existence is not completeness, so
completeness is recorded explicitly.

The rename is what makes two concurrent `rudra` invocations safe. Both may
render; the second `os.replace` wins; neither observes a partial directory,
because nothing is ever written into the final path.

---

## 4. Serving it

`build_backend` (`main_agent.py:313-358`) gains one route:

```python
routes={
    "/artifacts/": FilesystemBackend(root_dir=str(paths.artifacts), virtual_mode=True),
    "/skills/":    FilesystemBackend(root_dir=str(cache.root),      virtual_mode=True),
}
```

`virtual_mode=True` is what makes this work: `filesystem.py:132` states its
primary use case is exactly this — a `CompositeBackend` strips the route prefix
and forwards a normalized path.

**`execute` is unaffected.** `composite.py:774` delegates to `default` when it is
a `SandboxBackendProtocol`, which `LocalShellBackend` is, so shell stays rooted
at the user's project. A route is additive. This is asserted rather than assumed
(§7) because adding a route is precisely the change that could break `C3.1`.

The cache is mounted **read-only in effect**: nothing writes to `/skills/`, and
the agents granted skills have no reason to. It is not enforced by the backend,
and does not need to be — the deny floor and the approval gate cover writes
wherever they land.

---

## 5. Indexing it

Both call sites take a new optional argument, defaulting to `None`:

- `planner_agent.create_planner_agent(..., skills_sources=None)`
- `subagents.runner.SubagentContext.skills_sources: tuple[str, ...] | None = None`

`None` means the agent is constructed with no `skills=` at all — not an empty
list. This keeps every existing test constructing agents without a cache, and
is how reviewer and general-purpose stay lean.

Who gets what:

| Agent | `skills=` | Bootstrap in prompt |
|---|:-:|:-:|
| planner: `clarify` | ✅ | ✅ |
| planner: `architect` | ✅ | ✅ |
| planner: `breakdown` | ✅ | ✅ |
| `coder` | ✅ | — |
| `tester` | ✅ | — |
| `reviewer` | — | — |
| `general-purpose` | — | — |

The reviewer reads a diff and reports; the general-purpose agent answers
questions about the codebase. Neither is choosing a method, and both would pay
~916 tokens for descriptions they cannot act on.

Which subagents receive skills is declared on `RudraSubagent` — one boolean
beside `fs_tools` and `rudra_tools` — so it stays a data change in
`registry.py`, matching how every other per-subagent capability is expressed.

### Measured cost

~916 tokens per agent that receives skills: **464** of fixed `SKILLS_SYSTEM_PROMPT`
boilerplate plus **452** for the nine-skill index. The boilerplate is paid per
agent regardless of how many skills are exposed, which is a further argument
against per-role subsetting: subsetting shrinks the smaller half.

`build_agent` runs per invocation (`runner.py:132`), so a subagent pays this on
every dispatch, not once per run.

---

## 6. The bootstrap (C5.3)

The rendered `using-superpowers/SKILL.md` body is appended to each planner
stage's system prompt, **read from the cache**, not imported from the package.

Reading it from the cache is what makes the chain hold together, because the
cached copy carries both of 11a's adaptations:

```
bootstrap in the prompt
  → "If your harness appears here, read its reference file"
  → /skills/library/superpowers/using-superpowers/references/rudra-tools.md
  → "no tool can mark work done; read_file a skill by path"
  → the skill itself
```

A copy imported from `src/` would carry neither the rewritten cross-references
nor the Rudra platform line, and would send the model looking for a plugin
namespace that does not exist.

Cost: ~780 tokens (63 lines, 3108 chars) on each of three planner stages.

### The risk, stated now rather than discovered later

The bootstrap is emphatic by design — *"If you think there is even a 1% chance a
skill might apply, you ABSOLUTELY MUST invoke the skill"*, *"This is not
negotiable. You cannot rationalize your way out of this."* It was written for a
frontier model that can weigh it.

On a 32B local model the plausible failure is **over-triggering**: the planner
reading `brainstorming`'s 15KB body before answering a one-line request,
spending context and turns on ceremony the task did not need.

This is `C5.8`'s measurement in 11c. Two things follow:

1. `enabled = []` is a first-class escape hatch, not an afterthought.
2. If 11c measures over-triggering, the fix is a Rudra-authored primer replacing
   the verbatim bootstrap — one function, already isolated for that reason. It is
   not written pre-emptively, because writing it now would mean guessing at a
   failure that may not occur.

### `--auto` and the missing human

Several skills instruct the model to consult its "human partner"; under `--auto`
there is not one. `rudra-tools.md` already states that `ask_user` is budgeted and
may be absent entirely, so the reconciliation is written. Whether the model
honours it is an 11c observation, not an 11b change.

---

## 7. Testing

**The test that matters most is the integration one**, because everything rests
on an assumption 11a argued from source but never executed: that
`SkillsMiddleware` can read through a `CompositeBackend` route with the prefix
stripped.

So the first test builds the **real** composite — `LocalShellBackend` default,
both routes — hands it to a real `SkillsMiddleware(sources=["/skills/active/"])`,
and asserts nine skills load with the right names. No model required. If that
fails, the design is wrong and nothing downstream matters.

| Area | Test |
|---|---|
| **Route integration** | A real `SkillsMiddleware` over the real composite lists the 9 enabled skills |
| **`execute` regression** | Shell still reaches the project root through the composite with `/skills/` mounted (`C3.1`) |
| Cache key | Deterministic; changes with `enabled`; changes with a bundle manifest; stable across processes |
| Reuse | A complete cache is reused rather than re-rendered |
| Completeness | A directory without `.complete` is rebuilt, not trusted |
| Atomicity | Rendering targets a temp sibling; the final path never holds a partial tree |
| Fallback | `OSError` on the cache root falls back to temp, sets `fell_back`, and **skills still load** — asserted through the middleware, not by reading the flag |
| Wiring | `skills=` present for 3 planner stages + coder + tester; **absent** for reviewer and general-purpose |
| Config | `[skills]` accepted; unknown name errors naming the valid ones; `enabled = []` gives no agent skills and builds no cache |
| Bootstrap | Present in every planner prompt, absent from every subagent prompt; the injected text contains the `rudra-tools.md` line and **no** surviving `superpowers:` token |

Gates unchanged: `ruff check src/ tests/` → `All checks passed!`, format clean,
`uv run pytest -q` never goes down.

---

## 8. Acceptance

The first sub-step of Step 11 that needs a live model. Four runs, from a
`mktemp -d` outside the repo under `env -i`, configured only by
`.rudra/config.toml` — the shape Steps 7 through 10 were accepted in.

1. **Default run.** Evidence is a log line showing the model calling `read_file`
   on a `/skills/…/SKILL.md` path of its own accord. That one line is the whole
   point of 11b: it proves index → bootstrap → read closes on a real model.
2. **`enabled = []`.** The control: same task, no skills in any prompt, run still
   completes normally.
3. **Unwritable cache.** `XDG_CACHE_HOME` pointed at a read-only path. The run
   prints the fallback line and otherwise behaves like (1) — same skills, same
   ability to read them.
4. **Regression.** Step 10c's plan approval still presents, revises and cancels.
   The planner prompt just grew by ~780 tokens, which is exactly the kind of
   change that quietly breaks a prompt-sensitive flow.

**If run (1) shows the model never reading a skill, that is a finding to record,
not a failure to hide.** It would mean a 32B model needs more than upstream's
bootstrap, and it belongs in the ledger against `C5.8` — not papered over by
tweaking the prompt until the run looks right.

---

## 9. Out of scope

**Deferred to 11c:** user skill directories (`~/.rudra/skills/`,
`<project>/.rudra/skills/` — the rest of `C5.1`), the `rudra skills` command
(`C5.6`), the session-start hook (`C5.5`), the live selection-quality
measurement (`C5.8`), and any change to the enabled set (`C5.9` / `S11a.7`).

**Rejected in this step:** per-role skill subsets (S11b.1). Revisit only if 11c
produces evidence of the coder being distracted.

**Deliberately not built: cache garbage collection.** Old `<key>` directories are
never pruned. Each is ~550KB, and a new one appears only when Rudra's version,
the corpus, or the user's `enabled` list changes — a handful over a year.
Pruning belongs with `rudra skills rebuild` in 11c, where there is a command to
hang it on. Recorded here so it is a decision rather than an oversight.

---

## 10. Ledger impact

| Row | Disposition |
|---|---|
| `C5.2a` | Closed — `skills/cache.py`, keyed and atomic, with the temp-dir fallback of S11b.2 |
| `C5.2b` | Closed — `/skills/` route on the existing composite; `execute` delegation asserted intact |
| `C5.3` | Closed — the rendered bootstrap injected into the three planner stages, per S11b.4 |
| `C5.1` | **Half closed** — vendored sources are wired; `~/.rudra/skills/` and `<project>/.rudra/skills/` move to 11c (S11b.3) |
| `C5.9` | Unchanged, still 11c — S11a.7 defers enablement until `C5.8` measures it |
| `[skills]` reserved | Un-reserved in `config/schema.py:44`; the reserved-section message and `Documentation/02-configuration.md`'s quote of it both need updating |
| `Documentation/08-project-status.md` | "Skills ship but aren't connected" becomes untrue and must move to *What works* |
| `Documentation/12-skills.md` | Its "vendored, not yet active" banner comes down |
