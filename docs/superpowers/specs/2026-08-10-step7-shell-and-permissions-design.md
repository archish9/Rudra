# Step 7 — Shell Execution and the Permission Layer: Design

**Date:** 2026-08-10
**Ledger rows:** `C3.1`–`C3.4`, `U.7`, `U.17`, closes `A1.16`
**Depends on:** Step 6 (`C2.1`–`C2.5`, `C1.8`, `C0.9` — layered TOML config) — DONE
**Unblocks:** Step 8 (`C3.5` git tools, `C3.6` test-runner), and `C11.2`/`C11.3`, blocked on shell since Step 4

---

## 1. Goal

Give Rudra's agents the ability to run commands, and gate every destructive
action behind a permission layer that ships in the same step.

Section E's hard gate #2 states the constraint plainly: *"`LocalShellBackend`
must never ship without the permission layer in the same step."* This design
treats that as binding. There is no intermediate state in which shell works
and the gate does not.

Four deliverables:

| Row | Deliverable |
|---|---|
| `C3.1` | `CompositeBackend(default=LocalShellBackend(...))` so `execute` works |
| `C3.2` | Oversized command output offloaded to a log, short preview returned |
| `C3.3` | Permission layer: mode from config, allow/deny rules, session grants, audit log |
| `C3.4` | Diff preview before a write or edit is approved — closes `A1.16` |

---

## 2. Findings that changed the plan

Every claim in this section was produced by executing code against the pinned
`deepagents==0.7.4`, not by reading it. Three of them contradict what the
ledger assumed, so they are recorded before the design that follows.

### 2.1 `U.17` — resolved. `CLAUDE.md` §4 was right.

`U.17` asked whether `execute` is merely *registered* on a plain
`FilesystemBackend` or actually *functional*, since Step 1's probe found it in
the default tool stack. It is registered and non-functional. Driven through a
real compiled graph with a scripted model emitting one `execute` call:

```
[FilesystemBackend] status='error'
  "Error: Execution not available. This agent's backend does not support
   command execution (SandboxBackendProtocol). To use the execute tool,
   provide a backend that implements SandboxBackendProtocol."
[LocalShellBackend] status='success'
  'hello-from-execute\n\n[Command succeeded with exit code 0]'
```

Corroborating: `hasattr(FilesystemBackend, "execute")` is `False`, and
`isinstance(FilesystemBackend(root_dir=...), SandboxBackendProtocol)` is
`False` while the `LocalShellBackend` equivalent is `True`.

`C3.1`'s premise stands unchanged. `U.17` closes DONE; the parenthetical in
`CLAUDE.md` §4 that opened it is retracted.

### 2.2 `permissions=` and shell are mutually exclusive — `U.7` is not buildable

`create_deep_agent(permissions=[...], backend=<execute-capable>)` raises at
construction:

```
NotImplementedError: FilesystemMiddleware does not yet support permissions with
backends that provide command execution (SandboxBackendProtocol). Tool-level
permissions for the execute tool are not implemented. Either remove permissions
or use a backend without execution support.
```

Source: `deepagents/middleware/filesystem.py:1667-1672`. The guard is
`_permissions and supports_execution(self.backend) and not
_all_paths_scoped_to_routes(_permissions, self.backend)`.

The escape hatch is real but useless here: `_all_paths_scoped_to_routes`
permits the combination only when **every** permission path sits under a
`CompositeBackend` route prefix. Route prefixes are for mounted subtrees like
`/skills/`; project files live on the composite's `default`, which is exactly
the execute-capable backend the guard is protecting against.

Independently, `FilesystemOperation` is `('read', 'write')` — measured. So
`permissions=` never covered `execute` at all, with or without the guard.

`U.7`'s ledger text says it "replaces most of hand-rolled C3.3". That is false
on 0.7.4. `U.7` becomes **WONTFIX** with this error as evidence, and `C3.3` is
built by Rudra, in full.

### 2.3 What does work, verified end to end

- `CompositeBackend(default=LocalShellBackend(...), routes={...})` — `execute`
  works through the composite, delegating to `default`. Empty `routes={}` also
  constructs, so the `/skills/` route can arrive in Step 11 with no rewiring.
- `interrupt_on={...}` coexists with an execute-capable backend. The interrupt
  fires, `astream(subgraphs=True)` drains at it, and the run resumes.
- `AgentMiddleware.wrap_tool_call(request, handler)` exists and can
  short-circuit a call without invoking the handler.

---

## 3. Owner decisions taken during design

Recorded because several of them move or close ledger rows, and a future
session reading only `TODO.md` would otherwise read the deviation as an
oversight.

| # | Decision | Effect on the ledger |
|---|---|---|
| S7.1 | **Rudra owns the gate; `permissions=` is never passed** | `U.7` → WONTFIX (§2.2). `C3.3` is built in full rather than reduced |
| S7.2 | **Rules are flat `tool:pattern` strings using real tool names** | Keeps Step 6's shipped `allow`/`deny` schema unchanged — no migration. Requires correcting `CLAUDE.md` §6's `["Read","Grep","Glob"]` example |
| S7.3 | **Approval offers approve / reject / always** | `always` is `C3.3`'s "per-session grant", in memory only, never written to config — no TOML writer, consistent with S6.1 |
| S7.4 | **`mode = "ask"` without a TTY exits 2 before any model call** | Fails at second zero rather than after a planner pass. Avoids `A1.40`'s failure mode of exiting 0 on a run that did nothing |
| S7.5 | **A deny floor applies in every mode, including `--yolo`** | `--yolo` means "approve my agent's work", not "let it reformat my disk" |
| S7.6 | **`C3.2` becomes configuration, not code** | deepagents already offloads oversized tool results; the work is routing the artifacts root and tuning the threshold. See §5.2 |
| S7.7 | **Shell inherits the environment minus secrets** | `C1.5` keeps API keys out of TOML so they live in the environment. Handing that environment to a model-driven shell would undo it |

---

## 4. Architecture

### 4.1 One pure decision function, two mechanisms

`deny` needs no human; `ask` does. Routing both through one mechanism makes one
of them wrong: an interrupt for a deny pauses a graph only to immediately
un-pause it, and a blocking terminal prompt inside a graph node stalls the
event loop and fights the live trace render.

So the policy lives in one pure function and two different deepagents
mechanisms consume it:

```
                    PermissionEngine.decide(tool, arg)
                     -> Decision(effect, rule, source)
                                  │
              ┌───────────────────┴───────────────────┐
              │                                       │
        effect == "deny"                        effect == "ask"
              │                                       │
   RudraPermissionMiddleware               interrupt_on={tool: {..., when}}
      .wrap_tool_call                       HumanInTheLoopMiddleware
              │                                       │
   returns an error ToolMessage             graph interrupts; Rudra prompts
   without calling handler                  outside the graph and resumes
```

`effect == "allow"` reaches neither: the middleware calls `handler(request)`
and the `when` predicate returns `False`.

The engine is pure — no filesystem, no console, no graph — so its precedence
and matching rules are unit-testable directly.

### 4.2 Module layout

New package `src/rudra/permissions/`. Eight small modules, each with one job.

| Module | Responsibility | Depends on |
|---|---|---|
| `rules.py` | `Rule`, `parse_rule`, `PermissionEngine.decide()` — pure | nothing |
| `floor.py` | built-in deny floor constants | nothing |
| `grants.py` | in-memory session grants | `rules` |
| `middleware.py` | `RudraPermissionMiddleware.wrap_tool_call` — deny only | `rules`, `audit` |
| `interrupts.py` | `build_interrupt_on(engine)` → `when` predicates | `rules` |
| `approval.py` | terminal prompt + the interrupt/resume loop | `rules`, `diff`, `audit`, `grants` |
| `diff.py` | unified diff rendering (`C3.4`) | nothing |
| `audit.py` | JSONL append to `.rudra/run/logs/permissions.jsonl` | nothing |
| `env.py` | `scrubbed_env(cfg)` — the shell environment of §5.3 | nothing |

`__init__.py` exposes one constructor, `build_gate(cfg, project_path)`,
returning a `Gate` that bundles the four things the agent constructor needs:
the engine, the middleware instance, the `interrupt_on` dict, and
`Gate.prompt(interrupts, console)` which turns a batched interrupt into a
decisions list. Nothing outside the package constructs those four separately.

Everything outside the package changes minimally: `main_agent.py` swaps its
backend construction, adds one middleware and one `interrupt_on=` dict, and
routes its two `astream` calls through `approval.run_with_approvals`. `cli.py`
gains a TTY check.

### 4.3 Precedence — fixed, not first-match-wins

`allow` and `deny` are two separate lists, so declaration order cannot
arbitrate between them. deepagents' own first-match-wins model does not apply.
The ladder is explicit and evaluated top down:

```
1. deny floor          -> deny     every mode, including --yolo; not overridable
2. permissions.deny    -> deny
3. session grant       -> allow    this process only, never persisted
4. permissions.allow   -> allow
5. mode default        -> ask:  per-tool table (below)
                          auto: allow
                          plan: deny project mutations
```

Deny beats allow. A config carrying both `allow = ["execute:git*"]` and
`deny = ["execute:git push*"]` gets the safe reading, and the audit line names
which rule fired.

Per-tool default under `mode = "ask"` with empty rule lists — the shipped
configuration:

| Tool | Default |
|---|---|
| `read_file`, `ls`, `glob`, `grep` | allow, silently |
| `write_file`, `edit_file`, `delete` | ask |
| `execute` | ask |
| `task` | allow (subagents, `C6.2`) |
| `update_plan`, `write_task_assignment`, `ask_user` | allow — control plane, §4.6 |

Reads are never gated: a single run performs dozens, and none of them can
destroy anything.

### 4.4 Rule syntax and matching

Matching uses `wcmatch`, already a dependency and already how
`filesystem/tree.py` reads `.gitignore`.

```toml
[permissions]
mode  = "ask"
allow = ["read_file", "ls", "glob", "grep",
         "execute:pytest*", "execute:git status"]
deny  = ["execute:rm -rf *", "write_file:.env", "write_file:**/.git/**"]
```

- A bare tool name matches every call to that tool.
- `tool:pattern` where the pattern starts with `/` is matched against the
  **resolved absolute path**.
- `tool:pattern` otherwise is matched against the **project-relative path**.
  So `write_file:.env` means this project's `.env`, not any file anywhere with
  that name.
- For `execute` the pattern matches the raw command string; there is no path.

**Resolution happens before matching.** `../../etc/hosts` and a symlink
pointing into `/etc` both resolve to their real absolute path before any rule
is consulted, so both hit the floor. Matching the string the model supplied
would be trivially bypassable.

Malformed rules — an unknown tool name, an empty pattern — are rejected by
`C2.4`'s existing config validator with its `difflib` nearest-match
suggestion, at load time, not at first tool call.

### 4.4a The `[tools]` section is un-reserved here

`CLAUDE.md` §6 records `[tools]` as reserved, with writing one a hard error
naming **Step 7** as its implementing step. This is that step, so the reserved
error is removed and the section becomes real:

```toml
[tools]
shell                     = true    # false removes execute from the tool stack
tool_output_limit_tokens  = 4000    # §5.2
```

`shell = false` is a genuine setting, not decoration: it yields the
pre-Step-7 capability set on a backend that still routes artifacts correctly,
which is the honest fallback if `LocalShellBackend` misbehaves on a user's
platform. `[skills]` and `[memory]` stay reserved, still naming Steps 11 and
14.

Three things change in `config/`: `schema.py` gains a `ToolsConfig` dataclass,
its `DEFAULTS` entry, and a `tools` key dropped from `RESERVED_SECTIONS` —
which `loader.py:100-101` reads to raise its reserved-section error, so the
validator needs no edit of its own; and `template.py`'s `rudra init` scaffold
gains a commented `[tools]` block alongside the `[permissions]` one it already
writes. `C2.4`'s "reserved section" test for `[tools]` inverts to a "valid
section" test.

### 4.5 The deny floor

Applies in every mode. Not overridable by config, deliberately: a user who
wants `--yolo` wants their agent unblocked, not their machine damaged.

| Rule | Rationale |
|---|---|
| write/delete resolving outside the project root | the agent's remit is the project |
| write/delete under `.git/` | corrupting the repo destroys the undo path |
| `execute` matching `rm -rf /`, `mkfs*`, `dd of=/dev/*` | unrecoverable |

The floor is a small, explicit, reviewable list in `floor.py`. It is not a
general-purpose command sandbox and does not pretend to be one: a determined
model can still write a destructive script and run it. What it buys is that
the obvious catastrophes cannot happen by accident, which is the realistic
failure mode for a 32B model.

### 4.6 Rudra's own tools are control plane

`wrap_tool_call` sees every tool call, including Rudra's own `update_plan`,
`write_task_assignment`, and `ask_user`. Those write only under `.rudra/run/`
and are how the orchestrator functions; gating them would make `mode = "ask"`
prompt for Rudra's own bookkeeping.

They are allowed unconditionally, via a named constant in `rules.py`, not a
check scattered across call sites.

### 4.7 Not per-agent

Rules do not distinguish planner from coder — both share one backend and one
gate. Per-agent policy waits for `C6.2`, when subagents become a designed
feature rather than the unblocked side effect `§0.1`'s accepted-risk note
describes.

---

## 5. Backend wiring (`C3.1`, `C3.2`)

### 5.1 The swap

`main_agent.py`'s current construction:

```python
filesystem_backend = FilesystemBackend(root_dir=str(project_path), virtual_mode=True)
```

becomes:

```python
backend = CompositeBackend(
    default=LocalShellBackend(
        root_dir=str(project_path),
        virtual_mode=True,
        env=scrubbed_env(cfg),          # §5.3
    ),
    routes={
        "/artifacts/": FilesystemBackend(
            root_dir=str(paths.run / "artifacts"), virtual_mode=True,
        ),
    },
    artifacts_root="/artifacts",
)
```

Built as a composite from the start per `D13`: Step 11 adds a `/skills/` route
to `routes` and changes nothing else. Retrofitting the composite later would
rewire every agent constructor.

### 5.2 `C3.2` is configuration, not code

deepagents 0.7.4 already implements what `C3.2` describes.
`FilesystemMiddleware` evicts any tool result exceeding
`tool_token_limit_before_evict` (default 20 000 tokens) to
`<artifacts_root>/large_tool_results/`, leaving a preview and a prompt
instruction telling the model to grep that directory. That is "big output →
log, short preview returned", already built.

Two adjustments make it correct for Rudra:

**Route the artifacts root.** `artifacts_root` defaults to `/` — the backend
root — which for Rudra is the user's project. Left alone, a shell-enabled
Rudra drops `large_tool_results/` and `conversation_history/` into the repo it
is working on. Routing it to `.rudra/run/artifacts/` puts both in `D15`'s
volatile, gitignored subtree. Verified: the prefixes then resolve to
`/artifacts/large_tool_results` and `/artifacts/conversation_history`.

**Lower the threshold.** 20 000 tokens is roughly a third of a 32B context
window for one `pytest` transcript. Rudra passes
`tool_token_limit_before_evict=4000` — about 16 KB, comfortably more than a
passing test run and far less than a failing one — and exposes it as
`[tools] tool_output_limit_tokens` so a user on a larger model can raise it.
4 000 is a considered default, not a measured optimum; implementation confirms
it against a real failing-`pytest` transcript and adjusts once, in this step,
rather than leaving it open.

No hand-rolled offload middleware is written.

### 5.3 The shell environment

`LocalShellBackend` defaults to `inherit_env=False`, which means an **empty**
environment — not a minimal one. Measured:

```
be.execute("echo $PATH; which pytest git ruff")
  -> output='/usr/gnu/bin:/usr/local/bin:/bin:/usr/bin:.\n\nExit code: 1'
```

No `pytest`, no `git`, no `ruff`. Every venv, `nvm`, `rustup`, and `pyenv`
toolchain is invisible. Shipping the default would make Step 8's test-runner
and git tools fail on their first call.

Rudra passes an explicit environment: `os.environ` minus secrets.

```python
_SECRET_RE = re.compile(r"(_KEY|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)$|^AWS_")

env = {
    k: v for k, v in os.environ.items()
    if k not in configured_api_key_env_names and not _SECRET_RE.search(k)
}
```

`configured_api_key_env_names` is every role's `api_key_env` from the resolved
config, so Rudra's own provider key is removed by name regardless of what it
is called.

The reasoning is `C1.5`'s, applied one layer further out. `C1.5` keeps API
keys out of TOML specifically so they live in the environment; handing that
environment to a shell the model drives would undo it, since anything the
agent prints goes into the transcript and straight to the model provider.

This is a real-toolchains-work default, not a sandbox. A command that reads
`~/.aws/credentials` from disk is unaffected — that is the filesystem gate's
job, not the environment's.

---

## 6. The ask path (`C3.3`, `C3.4`)

### 6.1 Interrupts are batched

`HumanInTheLoopMiddleware` interrupts once per AI message, carrying an
`action_requests` list covering all tool calls in that message — not one
interrupt per call. Resume takes a matching list of decisions:

```python
Command(resume={"decisions": [{"type": "approve"},
                              {"type": "reject", "message": "..."}]})
```

Verified: `approve` ran the command; `reject` placed its message into the
`ToolMessage` content. The wrong shape — `resume=[...]` instead of
`resume={"decisions": [...]}` — raises `TypeError: list indices must be
integers or slices, not str` from inside the middleware. A test pins the
correct shape so nobody rediscovers this.

### 6.2 Collecting the interrupt without changing stream mode

`_stream_planner` and `_stream_coder` both stream `stream_mode="values"`, and
`__interrupt__` does not appear in `values` chunks. Changing the stream mode
changes the chunk shape and would force a rewrite of both parse loops — the
same two functions carrying `A1.20`'s unfixed shared-counter hole. So the
interrupt is read from state after the stream drains instead:

```python
async def run_with_approvals(agent, inputs, config, gate, console):
    payload = inputs
    for _ in range(MAX_APPROVAL_ROUNDS):
        async for chunk in agent.astream(payload, config,
                                         stream_mode="values", subgraphs=True):
            yield chunk                       # existing parse loops unchanged
        state = agent.get_state(config)
        if not state.interrupts:
            return
        payload = Command(resume={"decisions": gate.prompt(state.interrupts, console)})
    raise ApprovalLoopExceeded(...)
```

Verified against a real graph: after the stream drains at an interrupt,
`state.next == ('HumanInTheLoopMiddleware.after_model',)` and
`state.interrupts` holds one entry.

`_stream_planner` and `_stream_coder` each change by exactly one line — the
`astream(...)` call becomes `run_with_approvals(...)`. Their message-parsing
bodies, and `A1.20`'s hole, are untouched. `A1.20` stays PENDING and is not
made worse.

### 6.3 The prompt

```
╭─ approval required ──────────────────────────────╮
│ write_file  src/app.py        +12 -3  (overwrite) │
╰──────────────────────────────────────────────────╯
  @@ -8,6 +8,15 @@
  - def run(argv):
  -     pass
  + def run(argv):
  +     args = parse(argv)
  … 8 more changed lines

[a]pprove  [r]eject  [A]lways (write_file:src/app.py)  [d]iff (full)
```

`always` records a session grant for that tool and pattern, held in memory for
the life of the process and never written to config. A fix loop that runs the
test suite twelve times prompts once. A run can never silently widen the
user's persistent rules.

### 6.4 Diff rendering (`C3.4`)

| Tool | Shown |
|---|---|
| `write_file`, existing target | unified diff, disk vs new content, ≤20 changed lines, `+N/-M` header |
| `write_file`, new target | `(new file)`, line and byte count, first 10 lines |
| `edit_file` | unified diff of `old_string` → `new_string` |
| `delete` | path and current line count |
| `execute` | command and cwd — no diff |

`d` prints the full diff. Content that is binary or over ~1 MB reports its
size and says so rather than rendering.

This closes `A1.16` — "files silently overwritten, no diff, no backup, no
confirm" — whose evidence pointer (`compat/overwrite_backend.py:50-65`) has
pointed at a deleted file since `U.3`.

### 6.5 Non-TTY

`cli.py` checks `sys.stdin.isatty()` after config resolution and before agent
construction. `mode == "ask"` without a TTY exits 2:

```
Error: permissions.mode = "ask" needs an interactive terminal,
but stdin is not a TTY.

  --auto                      approve everything (unattended)
  permissions.mode = "auto"   same, persisted in .rudra/config.toml
```

Zero model calls are made. In ask mode every write is gated and writing files
is Rudra's entire job, so a non-TTY ask run would hit a prompt within seconds;
failing at second zero is the honest version of failing at second thirty.

`approval.py` keeps an independent backstop for a TTY that disappears
mid-run: it denies, records `source: "no-tty"` in the audit log, and does not
block.

### 6.6 `--plan` becomes real

Plan mode denies `write_file`/`edit_file`/`delete`/`execute` against the
project while leaving the control-plane tools of §4.6 alive. The planner runs,
`.rudra/run/PLAN.md` is written, nothing else is touched.

Noted for the ledger, not built here: this makes `--dry-run` (`A1.40`, a no-op
that exits 0 while claiming to preview) redundant rather than merely broken.
`A1.40` stays PENDING and out of scope.

### 6.7 Audit log

`.rudra/run/logs/permissions.jsonl`, one object per decision, written in every
mode including `auto`.

```json
{"ts":"2026-08-10T14:22:01Z","tool":"execute","arg":"pytest -q",
 "rule":null,"mode":"ask","decision":"approve","source":"prompt"}
{"ts":"...","tool":"execute","arg":"pytest -q tests/x",
 "rule":"execute:pytest*","mode":"ask","decision":"approve","source":"session-grant"}
{"ts":"...","tool":"write_file","arg":"/etc/hosts",
 "rule":"<floor:outside-root>","mode":"auto","decision":"deny","source":"floor"}
```

Recorded: deny, ask→approve, ask→reject, session grants, floor denials.
**Not** recorded: silent default-allow reads, which would bury the signal
under hundreds of `read_file` lines and stop the log being something a human
skims after a surprising run.

Machine-readable so `C6.6`'s completion gate and any later `rudra audit` can
consume it without a parser. Lives in `run/logs/`, which `D15` places in the
volatile, gitignored subtree.

---

## 7. Error handling

| Condition | Behavior |
|---|---|
| Approval rounds exceed `MAX_APPROVAL_ROUNDS` (50) | `ApprovalLoopExceeded` halts that agent invocation. Prevents an unbounded prompt loop. 50 is far above any real run — a coder writing one file interrupts a handful of times — so tripping it means something is wrong, not that a user was busy |
| Malformed rule string | Rejected at config-load time by `C2.4`'s validator with a nearest-match suggestion — not at first tool call |
| Audit log unwritable | Console warning, run continues. An unwritable log must never abort a run, and must never be swallowed silently either |
| TTY lost mid-run | Deny, log `source: "no-tty"`, continue. Never block |
| Denied call | Error `ToolMessage` naming the rule that fired, so the model can adapt rather than retry blindly |

---

## 8. Testing

Current bar: `ruff check src/ tests/` clean, `ruff format --check` clean,
`pytest -q` at or above **286 passed, 2 skipped**. Nothing below needs a live
model or network — the `ScriptedToolModel` pattern in
`tests/test_deepagents_contract.py` drives real compiled graphs offline.

| Layer | Test |
|---|---|
| Precedence | Table-driven: floor beats deny, deny beats allow, grant beats allow, mode default is last |
| Matching | `..` and symlink resolve before matching; `/`-anchored vs project-relative patterns; `execute` matches the command string |
| Deny short-circuit | Scripted model emits a denied write; assert an error `ToolMessage` **and** that the file was not created |
| Interrupt round trip | Scripted model + `InMemorySaver`; approve runs the call, reject returns its message, resume payload shape is `{"decisions": [...]}` |
| Floor under `--yolo` | `mode="auto"` still denies a write outside the root and an `rm -rf /` |
| Secret scrub | A `FOO_API_KEY` present in `os.environ` is absent from the constructed backend env |
| Artifacts routing | `_large_tool_results_prefix` resolves under `.rudra/run/artifacts/`, not the project root |
| Control-plane exemption | `update_plan` is never gated in `ask` mode |
| Non-TTY | Monkeypatched `isatty` → `False` exits 2, and no model is constructed |
| Contract guard | `create_deep_agent(permissions=[...], backend=<execute-capable>)` still raises `NotImplementedError` |

The contract guard is the row that matters most long-term: it is how a future
session learns that deepagents has lifted the restriction and `U.7` has become
possible again.

---

## 9. Scope

**In:** `C3.1`, `C3.2`, `C3.3`, `C3.4`, `U.7` (as WONTFIX), `U.17` (as DONE),
`A1.16` (closed by `C3.4`).

**Out, deliberately:**

| Row | Why |
|---|---|
| `A1.20` | Per-namespace stream counters belong with `C6.2`/`C9.1`. §6.2 is built so as not to touch it |
| `A1.39` | Provider-error retry belongs with the Step 9 loop rewrite |
| `A1.40` | `--dry-run` is its own row; §6.6 only observes that `--plan` supersedes it |
| `C3.5`, `C3.6` | Git tools and test-runner are Step 8, and both depend on this step's shell |
| MCP permission gating | `§0.8`'s open question is Step 13's. §2.2's finding — `permissions=` never covered non-filesystem tools — is the input it needs |

---

## 10. Ledger deltas

Per session rule 2, all of these are written to `TODO.md` as `PENDING` **before
any code is written**.

| Row | Action |
|---|---|
| `U.17` | → DONE. Probe output recorded; `CLAUDE.md` §4's parenthetical retracted |
| `U.7` | → **WONTFIX**, with the `NotImplementedError` as evidence. Its "replaces most of hand-rolled C3.3" text corrected in place, the way `D9` records why `RubricMiddleware` was rejected |
| new | `LocalShellBackend` defaults to an empty environment; shipping the default breaks every real toolchain (§5.3) |
| new | `artifacts_root` defaults to the backend root, so a shell-enabled agent writes `large_tool_results/` and `conversation_history/` into the user's project (§5.2) |
| new | `FilesystemOperation` is `('read','write')` only — `permissions=` never covered `execute`, which `§0.8`'s open MCP question assumed it might |
| `CLAUDE.md` §4 | `execute` row corrected — registered but non-functional, with the measured error text |
| `CLAUDE.md` §6 | `[permissions]` example corrected from `["Read","Grep","Glob"]` to real tool names |
| `CLAUDE.md` §3 | Architecture table gains `permissions/`; backend line updated to the composite |

---

## 11. Acceptance

Matching the bar Step 6 set — `env -i`, no `RUDRA_*`/`OLLAMA_*` set, configured
only by `.rudra/config.toml`, run from a `mktemp -d` outside the repo.

1. **Interactive.** A task that writes a file and runs a command under
   `mode = "ask"`. Approve the write after reading its diff; approve the
   command; use `always` on a repeated command and confirm the second
   occurrence does not prompt. Run exits 0, file is correct, command output is
   in the transcript.
2. **Unattended.** The same task under `--auto`. No prompts, exits 0, and
   `permissions.jsonl` records the auto-allowed decisions.
3. **Floor holds under `--auto`.** A task instructed to write outside the
   project root is denied, and the denial is in the audit log.
4. **Non-TTY.** The same command with `< /dev/null` under `mode = "ask"` exits
   2 having made no model call.

Evidence — exact commands, decisions taken, resulting files, `git log` where
relevant, exit codes — goes into the ledger row itself, not a scratch file.
`U.12`'s `/tmp` smoke logs no longer resolve; that is the lesson being applied.
