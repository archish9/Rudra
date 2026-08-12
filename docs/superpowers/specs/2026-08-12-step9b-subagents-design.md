# Step 9b — Subagents: coder / tester / reviewer (C6.2, C6.3, C6.4, U.11)

**Date:** 2026-08-12
**Status:** design approved, not implemented
**Ledger rows:** closes `C6.2`, `C6.3`, `C6.4`, `U.11`, `A1.20`. Narrows D4's `task` risk.
**Depends on:** Step 9a (the verification gate) for ordering only — no code dependency.
**Blocks:** Step 9c, which calls these subagents from the fix loop.

---

## 0. Why this is its own spec

TODO.md §E carried Step 9 as one row of eight items. The owner decomposed it on
2026-08-12 into 9a (the deterministic gate), 9b (this), and 9c (the agentic loop,
fix loop, and ledger).

9b replaces the two hand-wired agents with declared subagents. `planner_agent.py:111-112`
already names this step in a comment: git and testing tools were put on the planner
rather than the coder because "the coder's prompt tells it to STOP after one
write_file, and a test runner in the same context window would contradict that.
Step 9 revisits this when subagents become a designed feature (C6.2)."

### The tension this spec resolves

Subagents are normally invoked by a *model* calling the `task` tool. But what decides
the work is still the hardcoded Python loop (`main_agent.py:362-465`), and replacing
that is `C6.1` — explicitly 9c. So "make the coder a subagent" implies a caller that
does not exist yet.

The owner's answer (S9b.1): 9b ships the specs **and a Python-callable runner**, so
9c's loop invokes tester and reviewer deterministically, the same way it invokes
`verify_project`. D9 already requires the loop to be deterministic; if a planner
model decided when to review, an LLM would be back in control of the loop.

---

## 1. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S9b.1** | 9b ships specs **plus** a Python-callable `run_subagent()`, not just specs and not a planner rewiring | 9b has a real acceptance run; 9c gets a deterministic call site |
| **S9b.2** | The gate is injected into **every** spec, and Rudra ships its own `general-purpose` to suppress the auto-added one | No Rudra subagent can exist without the deny middleware |
| **S9b.3** | The reviewer is prevented from editing by **tool absence**, not by prompt or by engine rules | `FilesystemMiddleware(tools=[...])` in its spec; the engine remains a backstop |
| **S9b.4** | Subagents return **plain text** in a `SubagentResult`; no `response_format` | Avoids the structured-output fragility D9 rejected `RubricMiddleware` for |
| **S9b.5** | `BUILTIN_ROLES` gains `tester` and `reviewer`; both inherit `[model.default]` like every other role | One inheritance rule, no named exceptions |

### The two upstream facts S9b.2 rests on

Verified against the installed `deepagents==0.7.4`, not assumed:

1. **`interrupt_on` is inherited by subagents; `middleware=` is not.** Declarative specs
   get `spec.get("interrupt_on", interrupt_on)` (`graph.py:718`), so approval prompts
   reach subagents for free. But each subagent is built a *fresh* stack —
   `[FilesystemMiddleware, summarization, PatchToolCallsMiddleware]` plus only
   `spec["middleware"]` (`graph.py:666-703`). Rudra's top-level `gate.middleware` does
   not propagate. **A subagent spec without it is gated for approvals but not for
   denials** — the worst combination, because it looks protected while `permissions.deny`
   and the deny floor do not apply inside it.

2. **A `general-purpose` subagent is auto-added** unless the caller supplies a spec by
   that name or a harness profile disables it (`graph.py:751`). It inherits the main
   model and the main tool list and gets its own middleware stack — also gate-free.
   That is D4's "`task` is reachable but undesigned" accepted risk, made load-bearing
   the moment anything passes `subagents=`.

Disabling it through a harness profile is the indirect route: it needs a profile
*registered* for whatever provider the user configured, and profile matching for a
pre-built model instance goes through a `provider:identifier` lookup
(`harness_profiles.py:1252-1262`). The direct lever is the name check at `graph.py:751`.

---

## 2. Architecture

```
src/rudra/subagents/
├── __init__.py     run_subagent, SubagentResult, SubagentContext, REGISTRY
├── spec.py         RudraSubagent — frozen dataclass, no behaviour
├── registry.py     the four definitions + their system prompts
├── build.py        _model_for / _tools_for / _middleware_for, build_agent,
│                   to_subagent_spec
└── runner.py       run_subagent() -> SubagentResult, per-invocation guards
```

### The spec record

```python
@dataclass(frozen=True)
class RudraSubagent:
    name: str                       # "coder" | "tester" | "reviewer" | "general-purpose"
    description: str                # what a delegating parent reads
    system_prompt: str
    role: str                       # a BUILTIN_ROLES member
    fs_tools: tuple[str, ...]       # deepagents FsToolName values
    rudra_tools: tuple[str, ...]    # Rudra tool names: "run_tests", "git_diff"
```

Pure data, mirroring `stacks/profile.py`. There is deliberately **no `middleware`
field**: middleware is assembled by `build.py`, so no caller can define a subagent
that forgets the gate.

`REGISTRY` is a `dict[str, RudraSubagent]` keyed by `name`, built from four module
constants in `registry.py` — the same shape as `stacks/registry.py`'s `PROFILES`,
where adding an entry is a data change.

`rudra_tools` holds **tool names, not callables**, because the Rudra tool factories
need per-run arguments the spec cannot hold: `create_testing_tools(project_path,
gate=…, console=…, cfg=…)` and `create_git_tools(project_path, gate=…, console=…,
cfg=…)`. `_tools_for(spec, context)` calls each factory once with the context, then
selects by name. A name with no factory is a construction-time `ValueError`, not a
silently missing tool.

### One assembly, two consumers

| Consumer | Call | Used by |
|---|---|---|
| Direct invocation | `build_agent(spec, context)` → `create_deep_agent(...)` | `run_subagent`, i.e. 9c's loop |
| Parent delegation | `to_subagent_spec(spec, context)` → deepagents `SubAgent` dict | whoever later passes `subagents=[...]` |

Both route through the same `_tools_for` and `_middleware_for`. A parity test asserts
that for every registry entry the two paths produce identical tool names and
middleware names, so the delegating path cannot drift from the direct one.

`build_agent` is the same `create_deep_agent` call `create_coder_agent` already makes
(`coder_agent.py:90`), which is why the subagent inherits upstream's full middleware
stack rather than a hand-assembled copy of `graph.py:666-673`.

### Boundaries

- `subagents/` starts no subprocess and interprets no policy. It composes what exists:
  `llm.build_model`, `tools/`, `permissions.Gate`.
- It does **not** import `rudra.agent` — 9c rewrites that module.
- `verify/` and `subagents/` do not know about each other. 9c calls both.
- `create_coder_agent` (`coder_agent.py:61`) becomes one registry entry.
  `planner_agent.py` is untouched in 9b.

---

## 3. The four subagents

| | **coder** | **tester** | **reviewer** | **general-purpose** |
|---|---|---|---|---|
| Model role | `coder` | `tester` | `reviewer` | `default` |
| `fs_tools` | `ls read_file write_file edit_file glob grep` | `ls read_file write_file edit_file glob grep execute` | `ls read_file glob grep` | `ls read_file glob grep` |
| Rudra tools | — | `run_tests` | `git_diff` | — |
| Writes? | yes | yes | **no** | **no** |
| Runs commands? | no | yes | no | no |

Tool names are the exact `FsToolName` literals — `ls`, `read_file`, `write_file`,
`edit_file`, `delete`, `glob`, `grep`, `execute` (`filesystem.py:1321`).

**The coder gains and loses nothing.** It is today's `create_coder_agent` expressed as
a registry entry: filesystem tools only, no shell. `delete` and `execute` are absent
because the current coder has neither and its whole prompt is "write one file, then
stop" (`coder_agent.py:56`).

**The tester is the only new writer.** It needs `write_file` to author test files, and
it carries **both** `execute` and `run_tests` deliberately: `run_tests` resolves the
project's own suite command and caps its output (`testing/runner.py`), which is what a
32B context needs, while `execute` covers the cases it cannot express — running one
test file, or a single reproduction command. Dropping `execute` would push the model to
misuse `run_tests`; dropping `run_tests` would hand it an uncapped test dump. Both go
through the same gate, so this widens what the tester can *say*, not what it may *do*.

That makes the tester the most dangerous of the four, and it is gated identically to
everything else: both reach the engine as `execute:<command>` (Step 8 spec S8.2), so
`--auto` without `--allow-shell` denies them and the tester reports that instead of
silently skipping. No new permission vocabulary.

**The reviewer cannot edit because the tools do not exist.**
`FilesystemMiddleware(backend=..., tools=["ls","read_file","glob","grep"])` in its
spec replaces the default filesystem middleware **in place by name**
(`graph.py:221-228`), so `write_file`, `edit_file`, `delete` and `execute` are never
registered. Its prompt says it reports rather than fixes; that is a hint, not the
enforcement. `U.17` measured that a registered-but-unsupported tool still gets called
and returns an error — not registering it is the only structural answer. It reads
changes through `git_diff`, which bounds its output; an uncapped diff is what raw
`execute` does badly in a 32B window (`C3.5`).

**`general-purpose` is not a feature.** Rudra ships a spec under that exact name to
suppress the auto-added one. Same read-only tool set as the reviewer, same gate
middleware, and a description scoping it to open-ended reading and search. Without it,
the moment anything passes `subagents=`, the framework adds an agent carrying the main
tool list and no deny middleware.

### Gate wiring

`_middleware_for` always returns `[gate.middleware, FixWriteParamsMiddleware(...), *compat]`,
and `to_subagent_spec` always sets `interrupt_on=gate.interrupt_on`. There is no code
path that builds a Rudra subagent without the gate.

**One ordering caveat, to be measured rather than assumed.** Through `subagents=`,
custom middleware lands **after** the core stack (`graph.py:229-231`), not first — while
Rudra's convention is `middleware.insert(0, gate.middleware)` precisely so a denial
stops a call before anything rewrites its arguments (`planner_agent.py:126-128`). So a
*delegated* subagent's gate sits inside `FilesystemMiddleware` rather than outside it,
whereas `build_agent` keeps Rudra's ordering. The implementation records what each path
actually produces in a contract test. **If the orders differ in a way that changes
behaviour, that is a finding to log in TODO.md before anything is changed** — not
something to paper over.

---

## 4. Data flow

### One context, built once per run

```python
@dataclass(frozen=True)
class SubagentContext:
    project_path: Path
    backend: Any            # the CompositeBackend from build_backend()
    gate: Any
    console: Console
    cfg: Any
    checkpointer: Any = None
    session_id: str = ""
```

Bundled for the reason `Gate` itself is a bundle (`permissions/__init__.py:50-56`):
these must be the *same* objects across calls, the gate and its session grants
especially. A second `Gate` would re-prompt for something the user already answered
`always` to.

### The call

```python
async def run_subagent(
    name: str,
    prompt: str,
    *,
    context: SubagentContext,
    thread_id: str | None = None,
) -> SubagentResult
```

```python
@dataclass(frozen=True)
class SubagentResult:
    name: str
    text: str                    # the final assistant message
    ok: bool
    halted_reason: str | None    # a guard stopped it
    error: str | None            # the invocation itself failed
```

`text` is the final assistant message because that is all a delegating parent would
have seen either (`subagents.py:117`) — the two call styles return the same thing.

`ok=False` with `halted_reason` means the subagent misbehaved; with `error` means it
never ran. 9c needs to tell those apart for the same reason 9a's report splits
`escalate` from a plain failure.

### Flow

```
run_subagent("reviewer", prompt, context=ctx)
  → REGISTRY["reviewer"]                          the frozen spec
  → build_agent(spec, ctx)                        model + tools + middleware
  → run_with_approvals(agent, …, gate, console)   permissions/__init__ — the same
                                                  helper the orchestrator uses, so
                                                  `ask` mode still prompts
  → stream, applying the per-invocation guards
  → SubagentResult
```

`run_subagent` is `async` because `run_with_approvals` yields from `astream`
(`permissions/__init__.py`), which is also what the current orchestrator awaits
(`main_agent.py:170`). 9c's loop is already async, so no sync wrapper is shipped —
an unused one would be dead code.

A fresh thread per invocation, defaulting to
`f"{session_id}-{name}-{uuid4().hex[:8]}"`, matching what the orchestrator does for
retries today (`main_agent.py:351`) so a re-run starts on clean context. This does
**not** fix `A1.2` — checkpoints are still written and never resumed.

### Guards, and why the split is deliberate

`_stream_coder` carries two guards today (`main_agent.py:241-243`): three consecutive
tool errors, and the same tool-plus-argument three times. These are *per-invocation*
guards; they move into `runner.py`, because 9c deletes the function they live in and
they would otherwise be lost silently.

They close **`A1.20`** on the way. That row is about the guard not watching `task` —
the watched set already lists it (`main_agent.py:273`), but the coder had no `task`
tool to call. With a `general-purpose` subagent registered, one exists.

9b does **not** take `C6.5a`'s *across-invocation* bounds — max fix attempts,
no-progress detection. One subagent looping on itself and a fix loop looping on a bug
it cannot fix are different failures with different remedies. The second is 9c's.

---

## 5. Error handling

`run_subagent` never raises at its caller for a runtime failure. 9c's loop must act on
a bad subagent run the way it acts on a failed gate, and an exception escaping into the
loop is `A1.39`'s shape — one transient provider error killing a run and discarding
completed work.

| Case | Result |
|---|---|
| Unknown subagent name | **Raises** `ValueError` listing the registry names — a programming error, not a runtime one |
| Model construction fails | `ok=False`, `error=` the factory's message |
| Provider error mid-stream | `ok=False`, `error=`. No retry; that is `A1.39`, still open and not claimed here |
| A guard fires | `ok=False`, `halted_reason=` |
| The gate denies a tool inside | The subagent sees the denial as a tool result and continues. If it then gives up, that is ordinary `ok=True` with text saying so — a denial is not a runner failure |
| `ask` mode with no TTY | Unchanged: `run_with_approvals` rejects and the subagent sees it |

---

## 6. Configuration

Two mechanical additions.

**`BUILTIN_ROLES`** becomes `("default", "planner", "coder", "tester", "reviewer")`
(`config/schema.py:12`). Unset roles inherit `[model.default]` through the existing
post-merge inheritance — no new precedence logic, which is the point of S9b.5.
CLAUDE.md §7 is explicit that precedence lives in exactly one function and warns
against per-field special-casing; "roles inherit default, except two of them" is that
special-casing. `rudra config list` picks the new roles up for free, because `_flatten`
iterates `cfg.models` rather than hand-listing since `A1.54`.

**`ROLES_TO_PROBE`** is currently `("planner", "coder")` (`llm/probe.py:18`). Naively
adding two more makes `rudra models test` fire five network probes where a
single-model setup has one distinct endpoint. Instead it probes the **distinct
resolved identities** — deduped on `(provider, base_url, model)` — and the table gains
a `Roles` column listing which roles map to each. A user who configures
`[model.reviewer]` separately gets it verified; a user on one model still sees one row.

**No new `[tools]` or `[agent]` keys.** Nothing about a subagent is configurable in
9b beyond its model, and an inert config key is worse than no key (CLAUDE.md §6).

---

## 7. Prompts

System prompts live in `registry.py` as module constants beside their specs, the same
shape as `_CODER_SYSTEM_PROMPT` (`coder_agent.py:33`).

Each states its tools, its stop condition, and its output contract — the output
contract because only the final message reaches a caller. The reviewer's says: report
findings with `file:line`, never edit, and say plainly when the diff is clean. The
tester's says: write tests, run them, report what failed, and do not retry a denied
`run_tests` — wording `testing_tools.py:33-37` already uses for the tool itself.

**Subagents get no `memory=`.** `.rudra/AGENTS.md` reaches the planner only today
(`planner_agent.py:139`). It is created once and never updated (`A1.9`), so passing it
to four more agents would add tokens without adding truth. That is a 9c / Step 12
question.

---

## 8. Testing

### Unit layer, no network

| File | Asserts |
|---|---|
| `tests/test_subagents_registry.py` | Four entries; every `role` is a `BUILTIN_ROLES` member; reviewer and general-purpose carry no write tool; tester is the only entry with `execute` |
| `tests/test_subagents_build.py` | **The invariant:** every registry entry, built either way, includes `gate.middleware`. Parametrized over the registry so a fifth subagent cannot skip it |
| `tests/test_subagents_parity.py` | `build_agent` and `to_subagent_spec` agree on tool names and middleware names for every entry |
| `tests/test_subagents_runner.py` | `SubagentResult` mapping: clean run, guard halt, construction error, provider error |
| `tests/test_subagents_reviewer.py` | The reviewer's **compiled graph** has no `write_file`/`edit_file`/`delete`/`execute`. Read off the compiled agent, not the spec — the spec is the input, the graph is the claim |
| `tests/test_config_roles.py` | `tester`/`reviewer` inherit `[model.default]`; explicit sections override; `config list` shows them; `models test` dedupes identical endpoints |

Driven by `ScriptedToolModel` (`tests/test_deepagents_contract.py:56`), extended to
emit a scripted *sequence* rather than a single call. It drives the real compiled
graph — real ToolNode, real backend — with no network.

### Contract tests

Added to `tests/test_deepagents_contract.py`, the file that exists to turn assumptions
about deepagents into executable assertions (`U.18`). Two facts this design rests on:

1. A spec named `general-purpose` suppresses the auto-added one.
2. Top-level `middleware=` does **not** reach subagents, while top-level `interrupt_on`
   **does**.

If upstream changes either, a test fails and names the decision that depended on it —
the role `test_permissions_still_rejected_with_execute_backend` already plays for `U.7`.

### Acceptance — three live runs

Against a real model, from a `mktemp -d` outside the repo under `env -i`, configured
only by `.rudra/config.toml`, matching how Steps 5–8 were accepted.

1. **Reviewer on a real diff.** Write a file with an obvious defect, commit, modify it,
   then invoke the reviewer. It must call `git_diff`, report the issue with a
   `file:line`, and leave the file byte-identical — checked by hash, not by reading its
   prose.
2. **Tester end to end.** With shell opted in: it writes a test file, runs it, reports
   the failure. Then the same run *without* the opt-in: `run_tests` denied, `ok=True`,
   text saying it could not run them, and `permissions.jsonl` showing
   `source: "auto-shell"`.
3. **The reviewer tries to edit.** Prompt it explicitly to fix what it found. It cannot
   — the tool is not registered — and the file is unchanged at the end. This run proves
   §3's claim rather than asserting it.

### Gates, unchanged

```
uv run ruff check src/ tests/      → All checks passed!
uv run ruff format --check src/ tests/
uv run pytest -q                   → never below 742 passed, 2 skipped
```

---

## 9. Ledger impact

### Closed

- **`C6.2`** — subagents for coder / tester / reviewer, each with its own model role.
- **`C6.3`** — reviewer reads the diff, reports, does not edit. Enforced by tool absence.
- **`C6.4`** — tester writes and runs tests and feeds failures back. "Feeds back" means
  it *returns* them; acting on them is 9c.
- **`U.11`** — record-only, and now recorded: `AsyncSubAgentMiddleware` / `AsyncSubAgent`
  are `graph_id`-keyed and route to LangSmith deployments (`graph.py:647-650`), i.e.
  remote execution. Not adopted — it is the opposite of local-first — and the row says
  why so it stops resurfacing.
- **`A1.20`** — the repeat-tool-call guard moves into `runner.py` with `task` genuinely
  reachable.

### Open by design

- **`A1.8`**, **`A1.25`** — the hardcoded loop still owns success. 9c.
- **`A1.39`** — no retry around model invocation. `run_subagent` reporting the error
  instead of crashing is containment, not a fix.
- **`A1.2` / `A1.3`** — checkpoint continuity. Fresh thread per invocation, as today.
- **`A1.9`** — AGENTS.md never updated; the reason subagents get no `memory=`.
- **D4's `task` risk narrows rather than closes.** A gated `general-purpose` replaces the
  ungated auto-added one, but `task` remains reachable from any agent given subagents.
  The exposure is now designed rather than incidental, and D4's row should say so.

### A ledger correction to verify, not assume

`A1.35` states that built-in harness profiles "can never match a Rudra model", because
`graph.py:584` sets `_model_spec = None` for a pre-built instance. But
`_harness_profile_for_model` (`harness_profiles.py:1252-1262`) falls back to a
`provider:identifier` lookup built from `model_dump` and `_get_ls_params`, so a profile
registered under `ollama:...` may in fact match.

The implementation writes a test that settles it. If profiles do match, `A1.35` is
wrong and `U.10` becomes reachable; if they do not, the row is right and gains that
evidence. **Either way the finding is logged in TODO.md before anything is changed**,
per session rule 2.

---

## 10. Out of scope

Stated so 9c inherits cleanly:

- `src/rudra/agent/main_agent.py` is not modified. No loop change, no `RudraAgent` edits.
- `src/rudra/agent/planner_agent.py` is not modified — the planner is not yet handed
  `subagents=`, because the parent that should delegate is 9c's.
- No fix loop, no attempt bounds, no progress ledger (`C6.5`, `C6.5a`, `C6.10`).
- No `response_format`, no async subagents, no `memory=` on subagents.
- No retry policy (`A1.39`), no checkpoint resumption (`A1.2`).
