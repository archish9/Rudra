# Design — deepagents 0.4.12 → 0.7.4 Upgrade

**Date:** 2026-08-05
**TODO.md item:** Section E, Stage I, **Step 1** — `U.1–U.6, U.12`
**Branch:** `upgrade/deepagents-0.7.4`
**Baseline commit:** `b322978`

---

## 1. Goal

Move Rudra from `deepagents 0.4.12` to `deepagents 0.7.4` **without changing a single observable behavior.**

Step 1's success condition is *"Rudra behaves exactly as it does today, but on 0.7.4."* Any behavior change is a bug, not a feature. Every later phase in `TODO.md` builds on 0.7.4 APIs, so doing this after new code means porting twice — which is why it is the first step in the execution order.

---

## 2. Scope

### In scope

| Item | Summary |
|---|---|
| U.1 | Bump `deepagents` to `0.7.4`; forced `langchain` / `langchain-core` / `langchain-ollama` bumps |
| U.2 | Declare new transitive hard deps intentionally |
| U.3 | Delete `compat/overwrite_backend.py` |
| U.4 | Re-verify + guard the `validate_path` monkeypatch |
| U.5 | Handle `TodoListMiddleware` no longer being auto-added |
| U.6 | Verify `backend` param accepts an instance (`BackendProtocol` only) |
| U.12 | End-to-end smoke test on 0.7.4 |
| **F1** | *(new)* Fence-stripping already lives in the middleware, so U.3 shrinks — but exposes two gaps: **F1a** planner lacks `FixWriteParamsMiddleware`, **F1b** the middleware regex is narrower than the backend's |
| **F2** | *(new)* `task_anchor.py` imports a deepagents **private** API |

### Out of scope — deliberately deferred

These are 0.7.4 features, but adopting them here means testing two changes at once.

| Deferred | Belongs to |
|---|---|
| U.7 `permissions=` | Step 7 |
| U.9 provider profiles | Step 5 |
| U.10 harness profiles | Step 5 or later |
| U.11 `AsyncSubAgentMiddleware` | Step 9 |
| U.8 `RubricMiddleware` | WONTFIX per D9 / §0.2 |
| D4 middleware deletions | Step 2 (C0.2) |
| D7 VFS deletion | Step 2 |
| A1.1 version string, C0.4 ruff, C0.7 dev extras | Step 3 |
| C0.5 full pytest suite, C0.6 CI | Step 4 |

---

## 3. Verified starting state

Measured 2026-08-05 against `.venv`:

| Package | Installed | Required by 0.7.4 |
|---|---|---|
| `deepagents` | 0.4.12 | **0.7.4** |
| `langchain` | 1.2.13 | **>=1.3.14** |
| `langchain-core` | 1.2.23 | **>=1.5.0** |
| `langchain-ollama` | 1.0.1 | **>=1.1.0** |
| `langchain-anthropic` | 1.4.0 | **>=1.5.3** (now hard dep) |
| `langchain-google-genai` | 4.2.1 | **>=4.3.1** (now hard dep) |
| `langsmith` | 0.7.22 | **>=0.10.9** |
| `wcmatch` | 10.1 | **>=11.0** |
| `packaging` | 26.0 | >=23.2 — satisfied |
| `aiosqlite` | 0.22.1 | installed, **undeclared** (A1.12) |
| `langchain-openai` | not installed | not needed until Step 5 |

Repo state: Python 3.12.3, `uv` available, `tests/` empty, no `plans/` or `docs/` directory, working tree clean except unrelated `TODO.md` edits (D16, C9.8, C9.9).

Available Ollama models: `qwen3-coder-next:cloud`, `gemma4:31b-cloud` (pulled 2026-08-05). Ollama cloud auth is **not** signed in — `/api/chat` returns `{"error":"unauthorized"}`. Signin is a prerequisite for phase 6 only.

---

## 4. Findings that change the plan

### F1 — U.3 is smaller than TODO.md §F states

`TODO.md` U.3 says the markdown-fence stripping in `OverwriteFilesystemBackend` must "move to `FixWriteParamsMiddleware`". **It is already there.**

- `src/rudra/middleware/fix_write_params.py:19` — `_FENCE_RE`
- `src/rudra/middleware/fix_write_params.py:31-33` — `_strip_fences`
- `src/rudra/middleware/fix_write_params.py:63-64` — applied to `content` for `write_file` / `edit_file`

Nothing needs porting. U.3 reduces to a delete plus a constructor swap.

Two real deltas remain:

**F1a — the planner loses fence-stripping.** `FixWriteParamsMiddleware` is wired into the coder only (`coder_agent.py:70`), not the planner (`planner_agent.py:92-95`, which carries `TaskAnchorMiddleware` + `BlockPrematureAskMiddleware`). Today the backend covered the planner. Post-deletion nothing does.

**F1b — the two fence regexes differ.**

```
backend    (compat/overwrite_backend.py:43)  ^```[^\n]*\n(.*)\n```$              greedy
middleware (middleware/fix_write_params.py:19) ^```[a-zA-Z0-9_\-]*\n?(.*?)```\s*$  non-greedy
```

The middleware's `[a-zA-Z0-9_\-]*` will not match an info string like ` ```py title="x" `, which the backend's `[^\n]*` did. Narrowing coverage during a no-behavior-change step is not acceptable.

### F2 — private-API import, not recorded in TODO.md

`src/rudra/middleware/task_anchor.py:20`:

```python
from deepagents.middleware._utils import append_to_system_message
```

`TaskAnchorMiddleware` is **KEEP, opt-in** per D4 and is currently wired into *both* agents (`planner_agent.py:93`, `coder_agent.py:71`). A leading-underscore module carries no stability guarantee. This is a third monkeypatch-class breakage risk alongside U.4, and it is not in `TODO.md`.

### U.5 — no action required

Rudra deliberately replaced `write_todos` with filesystem-backed planning tools (`src/rudra/tools/planning_tools.py:3`). Grep confirms no source depends on `write_todos`. Losing the auto-added `TodoListMiddleware` removes a redundant tool that both system prompts already steer the model away from. Net win. Documented by a contract test, no code change.

### U.6 — no action required

`main_agent.py:554` already passes a backend **instance**, never a factory. `create_deep_agent` calls at `planner_agent.py:85` and `coder_agent.py:77` both forward that instance. Locked by a contract test.

---

## 5. Approach

### Isolation

Branch `upgrade/deepagents-0.7.4`. A **throwaway probe venv** carries 0.7.4 while the real `.venv` stays on 0.4.12 and working through phases 1–5.

Rejected alternatives:

- **Upgrade `.venv` in place, fix fallout.** Conflates three independent failure modes — resolver conflict, API change, and our own edits — into one surface. On the repo's riskiest change with zero existing tests, that is the wrong trade.
- **Git worktree with its own venv.** Cleanest separation, but the thing at risk is the venv, not the source. Duplicating a multi-GB venv buys nothing the probe venv does not.

Rollback at any phase: `git checkout main && uv sync`. The probe venv is disposable.

### Phases

Each phase is a checkpoint. Do not start a phase until the previous one passes.

| # | Phase | Detail |
|---|---|---|
| 1 | **Branch** | `upgrade/deepagents-0.7.4`. `.venv` untouched |
| 2 | **Probe venv** | `uv venv .venv-probe`, then `uv pip install --dry-run` the full 0.7.4 stack to surface resolver conflicts before any download; then install for real |
| 3 | **Contract tests** | Write `tests/test_deepagents_contract.py`, run under the probe venv. This is what *verifies* `TODO.md` §F rather than trusting it |
| 4 | **Source edits** | U.3, F1, F2 guards. Still on 0.4.12 in `.venv`. Phase-3 results decide anything version-conditional |
| 5 | **Migrate `.venv`** | Update `pyproject.toml`, `uv sync`, delete `requirements.txt`. Contract tests must pass under the real venv |
| 6 | **Smoke run** | `gemma4:31b-cloud`, temp dir, real `rudra` invocation. Output recorded in `TODO.md` as U.12 evidence |

Phase 3 before phase 4 is the load-bearing ordering choice: anything `TODO.md` §F got wrong surfaces as a failing assertion before a single source line changes.

---

## 6. Detailed changes

### 6.1 Contract tests — `tests/test_deepagents_contract.py`

Each assertion locks one §F claim so a future deepagents bump fails loudly and specifically.

| # | Assertion | Locks |
|---|---|---|
| 1 | `importlib.metadata.version("deepagents") == "0.7.4"` | C0.8 exact pin |
| 2 | `deepagents.backends.utils.validate_path` exists and is callable | U.4 |
| 3 | `deepagents.middleware.filesystem.validate_path` exists **and `is` the same object** as #2 before patching | U.4 — proves the two-target patch is still both necessary and sufficient |
| 4 | `deepagents.middleware._utils.append_to_system_message` exists and accepts `(system_message, str)` | F2 |
| 5 | `langchain.agents.middleware.types.AgentMiddleware` is importable | all 6 Rudra middlewares (`fix_write_params.py:14`) |
| 6 | `FilesystemBackend(root_dir=tmp)`: `write(p,"a")` then `write(p,"b")` → no error, content is `"b"` | U.3's core claim |
| 7 | That same backend does **not** strip ` ``` ` fences | proves `FixWriteParamsMiddleware` remains load-bearing |
| 8 | `FilesystemBackend(root_dir=..., virtual_mode=True)` is accepted | `main_agent.py:556` |
| 9 | `create_deep_agent(backend=<instance>)` accepts an instance; kwargs `model`, `tools`, `system_prompt`, `backend`, `checkpointer`, `middleware`, `memory` all still accepted | U.6 |
| 10 | An agent built without explicit todo middleware exposes **no** `write_todos` tool | U.5 — documents the delta |

Plus a pure-unit test for `_strip_fences` covering: ` ```python\ncode\n``` `, ` ```\ncode\n``` `, unfenced content, fenced-with-trailing-newline, an inner ` ``` ` inside the code body, and an info string with attributes (` ```py title="x" `).

Tests 1–10 require no model and no network.

### 6.2 Source changes

| Change | File:line | Detail |
|---|---|---|
| U.3a | `agent/main_agent.py:550`, `:554` | Drop the `OverwriteFilesystemBackend` import; construct `FilesystemBackend(root_dir=str(project_path), virtual_mode=True)` |
| U.3b | `compat/overwrite_backend.py` | **Delete** — 65 lines. Sole instantiation is `main_agent.py:554` |
| U.4 | `compat/deepagents_path.py:72-73` | Add a version guard before patching. On mismatch, raise a message naming U.4 — not an `AttributeError` forty frames deep |
| F2 | `middleware/task_anchor.py:20` | Same guard treatment for the private `_utils` import |
| F1a | `agent/planner_agent.py:92` | Add `FixWriteParamsMiddleware()` to the planner's middleware list, restoring the fence-strip the deleted backend provided |
| F1b | `middleware/fix_write_params.py:19` | Widen `_FENCE_RE`'s info-string class from `[a-zA-Z0-9_\-]*` to `[^\n]*` to match the backend's prior coverage |
| U.5 | — | No change |
| U.6 | — | No change |

### 6.3 Dependencies

`pyproject.toml` becomes the single source of truth; `uv.lock` is the pinned artifact.

```toml
deepagents==0.7.4          # EXACT — compat/deepagents_path.py monkeypatches
                           # deepagents internals. See TODO.md U.4 / C0.8
langchain>=1.3.14
langchain-core>=1.5.0
langchain-ollama>=1.1.0
# transitive hard deps of deepagents 0.7.4, declared intentionally (U.2)
langchain-anthropic>=1.5.3
langchain-google-genai>=4.3.1
langsmith>=0.10.9
wcmatch>=11.0
packaging>=23.2
```

Then `uv sync`. **Delete `requirements.txt`** — resolves A4.4 early at zero cost.

Not touched here: `langchain-openai` (Step 5); moving `ruff` / `pytest` to a dev group (Step 3, C0.7).

**Decision on two adjacent step-3 items:** A1.12 (`aiosqlite` used at `main_agent.py:546`, undeclared) and A1.13 (`langgraph-checkpoint-sqlite` duplicated at `pyproject.toml:30` and `:58`) both live in the exact block being rewritten. They stay **deferred to Step 3** to keep Step 1's diff attributable. Revisit only if the resolver forces the issue.

---

## 7. Acceptance criteria

1. `.venv/bin/python -c "import importlib.metadata as m; print(m.version('deepagents'))"` prints `0.7.4`
2. `.venv/bin/pytest tests/test_deepagents_contract.py` — all pass; output pasted into `TODO.md`
3. `.venv/bin/rudra --version` runs without traceback (still prints `0.1.0`; A1.1 is Step 3)
4. Smoke: fresh temp dir, `rudra "build a python CLI that reverses a string"` against `gemma4:31b-cloud` → `.rudra/PLAN.md` written, at least one non-empty source file produced, no traceback
5. `.venv/bin/ruff check src/` reports **≤ 213** errors — no regression (fixing them is Step 3)
6. `TODO.md`: U.1–U.6 and U.12 marked `DONE` with verifying command output; new items U.13–U.15 added

Criterion 4 depends on Ollama cloud signin (`ollama signin`). If signin is unavailable, criterion 4 blocks and U.12 alone stays `PENDING` — criteria 1, 2, 3, 5, 6 still complete and U.1–U.6 still close.

---

## 8. TODO.md additions

Logged as `PENDING` **before** any fix, per session rule 2.

| New # | Item |
|---|---|
| U.13 | `task_anchor.py:20` imports private `deepagents.middleware._utils.append_to_system_message`. Needs a version guard; revisit if the module moves (F2) |
| U.14 | `planner_agent.py:92-95` lacks `FixWriteParamsMiddleware` — planner loses fence-stripping once `OverwriteFilesystemBackend` is deleted (F1a) |
| U.15 | `fix_write_params.py:19` `_FENCE_RE` info-string class `[a-zA-Z0-9_\-]*` is narrower than the backend's `[^\n]*` — misses ` ```py title="x" ` (F1b) |
| A4.8 | `.gitignore:215-216` ignored `docs/` and `tests/`. **Prerequisite, fixed before this spec was committed** — nothing under `tests/` could be tracked, making the step-4 CI gate structurally impossible and blocking this step's contract tests |

Also amend U.3's text: fence-stripping already lives in `FixWriteParamsMiddleware`; nothing needs porting.

---

## 9. Risks

| Risk | Caught by |
|---|---|
| `langchain>=1.3.14` conflicts with `langgraph 1.1.3` or `langchain-community 0.4.1` | Phase 2 resolve-only dry run |
| `langchain.agents.middleware.types` moved in langchain 1.3 → all 6 Rudra middlewares break | Contract test 5 |
| `ChatOllama` 1.1.0 drops or renames `reasoning=True` / `num_predict` (`planner_agent.py:78-80`, `coder_agent.py:64-66`) | Phase 5 import + phase 6 smoke |
| `gemma4:31b-cloud` tool-calling support unverified — deepagents hard-requires it | Phase 6; blocked on `ollama signin` |
| `validate_path` or `_utils` moved despite §F's reading | Contract tests 2, 3, 4 |

---

## 10. Definition of done

All six acceptance criteria met, `TODO.md` updated in the same commit as the code (session rule 6), branch ready to merge to `main`. Step 2 (`C0.2`, `C0.3`, `A2.1–A2.6`, D7) becomes unblocked.
