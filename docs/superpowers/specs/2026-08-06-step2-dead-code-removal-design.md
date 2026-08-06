# Design — Step 2: Delete Dead Code, Delete the VFS, Add `project_tree()`

**Date:** 2026-08-06
**TODO.md item:** Section E, Stage I, **Step 2** — `C0.2, C0.3, A2.1–A2.6, D7`
**Branch:** `cleanup/step2-dead-code`
**Baseline commit:** `040dae4` (clean tree, `main`)
**Depends on:** Step 1 (**DONE** 2026-08-05 — `deepagents==0.7.4`, 24/24 tests green)

---

## 1. Goal

Remove ~1,425 lines of code that is either unreachable, superseded by deepagents 0.7.4, or was built as a workaround for a model class Rudra no longer targets (D6: minimum 32B). Replace the one genuinely load-bearing thing the deleted code did — showing the agent what files exist — with a helper that reports **names only** and never loads file content.

Success condition: **Rudra behaves as it does today, minus the deleted surfaces, with a strictly smaller memory footprint.** No new features. The only new code is `project_tree()` and its tests.

Why here: every later step touches less surface once this lands. D7's memory fix is the substantive part — `VirtualFileSystem.load_from_disk` reads *every* text file in the project into RAM before the planner's first token (`virtual_fs.py:114-157`), which is fatal for the local-first budget.

---

## 2. Scope

### In scope

| Item | Summary |
|---|---|
| A2.1 | Delete `execution_tools.py` (repo root, 255 lines, zero imports) |
| A2.2 | Delete `filesystem/sync.py` (271 lines, unreachable) |
| A2.3 | Delete `middleware/continue_after_write.py` (105 lines, zero call sites) |
| A2.4 | Delete `tools/code_tools.py` — see D17 below |
| A2.5 | Remove unused imports (`main_agent.py:5,11`; `cli.py:18`) |
| A2.6 | Remove never-read config/context fields |
| C0.2 | Execute the D4 middleware deletions (**partial** — see §6) |
| C0.3 | New capped `project_tree()` helper |
| D7 | Delete `VirtualFileSystem` + `FileSyncManager`; retarget all consumers |
| **D17** | *(new decision, 2026-08-06)* Delete the `watch` command and `code_tools.py` |
| **A2.7–A2.10** | *(new findings)* Four dead surfaces discovered during this design — logged before being touched |

### Closed as a side effect

These are `A1` bug rows whose subject code is deleted outright, so they close without a patch:

| # | Bug | Closed by |
|---|---|---|
| A1.4 | Disk-write failure silently swallowed, file then marked written (`virtual_fs.py:206-209`) | D7 deletion |
| A1.6 | `EnforceTargetFileMiddleware` matches on basename → target `src/models.py` permits `tests/models.py` | D4 deletion |
| A1.10 | `_is_ignored` runs raw `fnmatch` on `.gitignore` lines (`virtual_fs.py:78-112`) | D7 deletion + `project_tree`'s git-first design |

### Out of scope — deliberately deferred

| Deferred | Belongs to |
|---|---|
| The prompt-level ask-block at `planner_agent.py:64` | C6.8a (step 10) — see §6 |
| Rebuilding command execution (`run_command`) | C3.2 (step 7), on backend `execute` |
| `ruff check --fix`, `ruff format` | C0.4 (step 3) |
| Dep hygiene (`aiosqlite`, duplicate dep, dev extra) | C0.7 (step 3) |
| Full pytest suite + CI | C0.5 / C0.6 (step 4) |
| Run-trace improvements (300-char truncation, `--debug` logging) | C9.1 / C9.7 (step 15) |
| A1.1, A1.5, A1.7, A1.11–A1.15 | Step 3 |
| C0.9 (`.rudra/` durable-vs-volatile layout, D15) | Stage I, but a separate step — it moves paths this step does not touch |

---

## 3. Verified starting state

Measured 2026-08-06 against commit `040dae4`.

### Files to delete

| Path | Lines | Ledger |
|---|---|---|
| `execution_tools.py` | 255 | A2.1 |
| `src/rudra/filesystem/sync.py` | 271 | A2.2, D7 |
| `src/rudra/filesystem/virtual_fs.py` | 415 | D7 |
| `src/rudra/middleware/block_premature_ask.py` | 60 | D4 |
| `src/rudra/middleware/block_task_tool.py` | 53 | D4 |
| `src/rudra/middleware/continue_after_write.py` | 105 | A2.3, D4 |
| `src/rudra/middleware/enforce_target_file.py` | 66 | D4 |
| `src/rudra/tools/code_tools.py` | 135 | A2.4, D17 |
| `watch` command, `src/rudra/cli.py:351-414` | ~65 | D17 |
| **Total** | **~1,425** | |

`src/rudra` is 3,216 lines today. 1,170 of the total above is inside it (everything except `execution_tools.py`, which sits at the repo root), so `src/` drops to roughly **2,046** before the new helper and the smaller edits in §6 are applied.

### The VFS surface that is actually used

`VirtualFileSystem` is 415 lines exposing 15 methods. Only **five** are reached from outside `filesystem/`:

| Consumer | Call | Purpose |
|---|---|---|
| `planner_agent.py:32` | `vfs.get_tree()` | project structure in the planner system prompt |
| `cli.py:263-266` | `VirtualFileSystem(...)`, `load_from_disk()`, `get_tree()` | the `/tree` REPL command |
| `cli.py:377-385` | `load_gitignore()`, `_is_ignored()` | `watch` change filter |
| `cli.py:394` | `create_code_tools(self.vfs)` | `watch` syntax-check branch |
| `planning_tools.py:38,130` | `vfs.root_path` | locate `.rudra/PLAN.md`, `.rudra/current_task.md` |
| `planning_tools.py:92-93` | `vfs.files.pop(...)` | invalidate the VFS cache after a direct disk write |
| `code_tools.py:58` | `vfs.root_path` | subprocess `cwd` |
| `code_tools.py:116` | `vfs.read_file(...)` | read a log file for `grep_in_file` |
| `main_agent.py:24,528-530,535,581,590` | field + construction + propagation | plumbing only |

Everything else on the class — `write_file`, `edit_file`, `delete_file`, `create_directory`, `exists`, `get_modified_files`, `get_new_files`, `to_dict`, `from_dict` — has zero external callers. The write path was already superseded when deepagents' own filesystem tools became the agents' only write mechanism.

`planning_tools.py:92-93` deserves a note: it exists purely to work around the cache it is about to delete. The module docstring (`planning_tools.py:6-11`) explains that `update_plan`/`read_plan` bypass the VFS *specifically because* the cache goes stale. Deleting the VFS deletes the problem, not just the workaround.

### Tests

`tests/` holds 24 passing tests across four files. **None** reference `vfs`, `VirtualFileSystem`, `SyncMode`, or any of the four middlewares being deleted (verified: `grep -rn "vfs\|VirtualFileSystem\|SyncMode\|Block\|Enforce\|ContinueAfter" tests/` → no hits). `tests/test_agent_wiring.py::test_planner_wires_fix_write_params_middleware_first` asserts `FixWriteParamsMiddleware` is **first** in the planner's middleware list; removing `BlockPrematureAskMiddleware` from that list leaves the assertion true.

### Typer with zero subcommands

Deleting `watch` leaves `cli.py` with an `@app.callback()` and no `@app.command()`. Probed directly on the repo's own `.venv`, with an app configured identically (`invoke_without_command=True`, `no_args_is_help=False`):

```
$ python typer_probe.py "hello world"
CALLBACK RAN prompt= 'hello world' verbose= False
exit=0
$ python typer_probe.py --help
exit=0
```

Both work. One cosmetic wart: the usage line still reads `Usage: rudra [OPTIONS] [PROMPT] COMMAND [ARGS]...` even though no `COMMAND` exists. Accepted; the README rewrite (C9.2 / A4.1) is where CLI help text gets addressed.

### Lint baseline

`.venv/bin/ruff check src/` → **164 errors** at `040dae4`, matching the merge-base figure recorded in `CLAUDE.md:179` (U.23). The standard for this step is **no increase**, and the expectation is a decrease, since ~1,360 lines of unlinted-clean code leave `src/`.

---

## 4. Decisions taken in this session

| # | Decision | Rationale |
|---|---|---|
| **D17** | **Delete the `watch` command and `tools/code_tools.py`** | `watch` does not do what it appears to. It is a separate command (`rudra watch`), not part of a `rudra "<task>"` run, and its only actionable branch dispatches to a tool named `check_syntax` (`cli.py:396`) that exists solely in the dead `execution_tools.py:230` — never in `code_tools.py`, whose tools are `run_command` and `grep_in_file`. So the branch has never executed. What remains is a printer of `Modified: <path>` lines. Deleting it removes `code_tools.py`'s only consumer; command execution returns properly in **C3.2** (step 7) built on backend `execute`, with the log-offload behavior (big output → `.rudra/logs/`, short preview returned) ported as C3.2 already specifies. |
| **C0.3-a** | **`project_tree()` uses git first, `wcmatch` as fallback** | Git resolves `.gitignore` semantics — negations, leading `/`, `**`, trailing `/`, nested `.gitignore` files, global excludes — correctly by definition. `git ls-files --cached --others --exclude-standard` also lists untracked-but-not-ignored files, which is exactly the set a coding agent should see. This is the direct answer to A1.10, and it removes any need to maintain a gitignore parser. Outside a git repo, a single `os.walk` + `wcmatch` pass covers the case. |
| **C0.3-b** | **Output is flat relative paths, one per line** | Every line is a complete, copy-pasteable relative path. An indented tree requires the model to reconstruct full paths from indentation, and that reconstruction failing is one route to the malformed plan entries A1.7 describes. Flat output is also the cheapest to generate (it *is* `git ls-files` output, filtered) and the most token-efficient. |
| **C0.3-c** | **Caps: `max_depth=5`, `max_entries=300`, then a truncation footer** | Bounded by construction, which is the point of D7. Both are keyword arguments so a caller can tighten them; neither is configurable via TOML yet — that waits for C2.1. |
| **Test scope** | **Test `project_tree()` only** | It is the only new code in the step. C0.5 already names "tree helper" as a test target; writing those tests here rather than in step 4 means the new code never ships untested into the planner prompt path. Deletions are verified by ruff, a clean import of every module, the existing 24 tests, and a live smoke run. |

---

## 5. The new helper — `src/rudra/filesystem/tree.py`

The `filesystem/` package survives the deletion of both its modules, holding exactly one public function. Keeping the package (rather than moving the helper to `tools/` or top-level) keeps the name accurate and the import churn minimal.

### Interface

```python
def project_tree(
    root: Path,
    *,
    max_depth: int = 5,
    max_entries: int = 300,
) -> str:
    """Return a capped, flat listing of the project's files — names only.

    Never reads the content of a listed file.
    """
```

### Behavior

1. **Listing.** Try `_git_listing(root)` first; fall back to `_walk_listing(root)` when it returns `None`.

   - `_git_listing(root)` runs `git -C <root> ls-files --cached --others --exclude-standard -z` with a 5-second timeout, splitting on `\0`. Returns `None` — not an exception — if `git` is absent, the exit code is non-zero, the directory is not a work tree, or the call times out.
   - `_walk_listing(root)` uses `os.walk` with `wcmatch` (`GLOBSTAR | NEGATE | DOTGLOB`) compiled from the root `.gitignore`, pruning ignored directories in-place via `dirnames[:] = [...]` so an ignored subtree is never descended. `wcmatch` is available as a `deepagents 0.7.4` dependency (TODO §F).

2. **Builtin skip set**, applied to *both* paths so it holds even in a repo that tracks these: `.git`, `.rudra`, `__pycache__`, `*.pyc`, `node_modules`, `.venv`, `venv`. `.rudra/` matters most — it is Rudra's own state directory and must never appear in the model's view of the project.

3. **Formatting.** POSIX separators (`/`) on every platform. Sort. **Depth is the number of path segments**, so `pyproject.toml` is depth 1 and `src/rudra/cli.py` is depth 3; a path is kept when its segment count is `<= max_depth`. Truncate to `max_entries`. When truncated, append:

   ```
   … 47 more entries omitted (cap: 300)
   ```

4. **Edge cases.** A missing or empty root returns the literal `(empty project)` rather than an empty string, so the planner prompt never contains a blank section.

### Sample output

```
pyproject.toml
src/rudra/agent/main_agent.py
src/rudra/agent/planner_agent.py
src/rudra/cli.py
src/rudra/config.py
tests/test_agent_wiring.py
```

### The content guarantee

`project_tree` opens exactly one file, ever: the root `.gitignore`, and only on the fallback path. It opens no listed file. This is the whole reason D7 exists, so it is asserted directly in the test suite (§7, test 6) rather than left as a property of the implementation.

---

## 6. Changes to surviving code

### `src/rudra/agent/planner_agent.py`

- `build_planner_prompt(task, vfs, tech_stack_content)` → `build_planner_prompt(task, project_path: Path, tech_stack_content)`. The `## PROJECT STRUCTURE` block (`:31-32`) embeds `project_tree(project_path)`.
- `create_planner_agent(...)` drops its `vfs` parameter and passes `project_path` to both `build_planner_prompt` and `create_planning_tools`.
- Drop `BlockPrematureAskMiddleware` from the import (`:13-17`) and from the middleware list (`:102`).
- **The prompt rule at `:64` stays.** `"Do NOT call ask_user() if the task already specifies a framework or language"` is the prompt-level twin of the deleted middleware. C0.2's own ledger note calls this out: deleting the middleware does not end the ask-blocking. It is §0.5 surface #6 and dies in C6.8a (step 10). Removing it here, with no dynamic-clarification machinery to replace it, would let the planner interrogate the user through a tool (`ask_user`) whose docstring still mandates one question at a time (§0.5 surface #3) — a worse behavior than the current one, shipped four steps early.

### `src/rudra/agent/coder_agent.py`

- Drop the `vfs` parameter.
- Drop the `target_file` parameter (`:53`, `:74-75`) and its docstring lines (`:57-59`). It exists only to construct `EnforceTargetFileMiddleware`; with the middleware deleted, it has no effect. The coder is still steered to the right file by `.rudra/current_task.md`, by `_CODER_ANCHOR` (`:14-17`), and by the per-file user message the orchestrator sends (`main_agent.py:335-338`).
- Drop `BlockTaskToolMiddleware` (`:12`, `:72`) and `EnforceTargetFileMiddleware` (`:12`, `:75`) from imports and middleware. Middleware becomes `[FixWriteParamsMiddleware(), TaskAnchorMiddleware(_CODER_ANCHOR)]`.
- Line `:43` of the system prompt (`"Do NOT call run_command. Do NOT call task. Do NOT call update_plan."`) stays: the `task` tool is now genuinely reachable, and a prompt-level "stay in your lane" instruction for a single-file coder is correct until the C6.2 subagent rewrite. `run_command` no longer exists as a tool name, so that clause becomes inert text — harmless, and cleaned up in step 9 when the prompt is rewritten anyway.

### `src/rudra/tools/planning_tools.py`

- `create_planning_tools(vfs, task="")` → `create_planning_tools(project_path: Path, task: str = "")`.
- `plan_path` (`:38`) and `task_path` (`:130`) derive from `project_path` directly.
- Delete the cache-invalidation lines (`:91-93`).
- Rewrite the module docstring's filesystem paragraph (`:6-11`), which describes a cache that no longer exists.

### `src/rudra/agent/main_agent.py`

- Drop `from rudra.filesystem import VirtualFileSystem` (`:14`).
- Drop `AgentContext.vfs` (`:24`) and the VFS construction (`:528-530`).
- Drop `"vfs"` from `coder_config` (`:590`) and the `"target_file"` injection (`:332`).
- A2.5: drop unused `asyncio` (`:5`) and `create_deep_agent` (`:11`) imports.
- A2.6: drop `AgentContext.max_iterations` (`:30`), `file_path` (`:34`), `issue` (`:35`). `stop_on_error` (`:31`) **stays** — it is read at `:287`.
- A2.8: drop `AgentResult.todo_summary` (`:50`).

### `src/rudra/cli.py`

- Drop `from rudra.filesystem import VirtualFileSystem, FileSyncManager, SyncMode` (`:18`); import `project_tree` instead.
- `/tree` (`:262-267`) becomes `console.print(project_tree(project_path))`.
- Delete the entire `watch` command (`:351-414`).
- A2.10: delete the `--max-agents` option (`:180`) and its assignment (`:192`).

### `src/rudra/config.py`

- A2.6: drop `AgentConfig.max_agents`, `max_iterations`, `checkpoint_interval` (`:29-31`). `verbose` (`:33`) **stays** — read at `cli.py:179`.
- A2.7: drop `SearchConfig` (`:36-41`) and `Config.search` (`:50`).

### Package `__init__` files

| File | After |
|---|---|
| `src/rudra/filesystem/__init__.py` | exports `project_tree` only |
| `src/rudra/middleware/__init__.py` | exports `FixWriteParamsMiddleware`, `TaskAnchorMiddleware` |
| `src/rudra/tools/__init__.py` | exports `create_interaction_tools`, `create_planning_tools` |

### User-visible consequences

Two, both deliberate:

1. **`rudra watch` is gone.** It printed change notifications and nothing else; its error-reporting branch never ran.
2. **`--max-agents` is gone.** The flag wrote `config.agent.max_agents`, which nothing read. Real sub-agent fan-out arrives with C6.2 (step 9), at which point the flag can return backed by behavior.

Neither is documented accurately in the README today, which already describes seven subcommands that do not exist (A4.1). The README rewrite is C9.2 / step 16.

---

## 7. Tests

New file `tests/test_project_tree.py`, written **before** `tree.py` (test-driven).

| # | Test | Needs git | Asserts |
|---|---|---|---|
| 1 | `test_gitignored_paths_are_excluded` | yes | Real `git init` in `tmp_path`, a `.gitignore` containing `build/`, files inside and outside it. Listing contains the tracked file, omits the ignored one |
| 2 | `test_fallback_honors_gitignore_without_a_repo` | no | Same file layout built in a plain directory with **no `git init` at all**, so `_git_listing` returns `None` and the `wcmatch` path runs. Produces the same set of paths test 1 expects |
| 3 | `test_max_depth_drops_deeper_paths` | no | `a/b/c/d/e/f/deep.txt` absent at `max_depth=3`; `a/b/shallow.txt` (depth 3) present |
| 4 | `test_max_entries_truncates_and_reports_remainder` | no | 20 files at `max_entries=5` → 5 path lines plus the footer; footer states `15 more` and `cap: 5` |
| 5 | `test_builtin_skips_apply_even_when_git_tracks_them` | yes | `git add -f .rudra/PLAN.md` and `node_modules/x.js` → neither appears |
| 6 | `test_never_reads_file_content` | no | Monkeypatch `pathlib.Path.read_text` to raise for every path except `.gitignore`; `project_tree` must return normally |
| 7 | `test_empty_or_missing_root` | no | Empty dir and a nonexistent dir both return `(empty project)` |

Tests 1 and 5 skip via `@pytest.mark.skipif(shutil.which("git") is None, ...)` rather than failing, so the suite stays green on a machine without git. Test 2 never invokes git, so the fallback path is covered unconditionally — the two paths are asserted against the same expected output, which is what makes the fallback a genuine substitute rather than a second, differently-behaved implementation.

---

## 8. Verification

Nothing is marked `DONE` in `TODO.md` without pasted command output, per `CLAUDE.md` §2 rule 4.

```bash
# 1. Lint — must not increase vs the 164 baseline at b322978 (CLAUDE.md:179)
.venv/bin/ruff check src/

# 2. Tests — 24 existing + 7 new, all green
.venv/bin/pytest tests/ -v

# 3. No survivor references the deleted machinery
grep -rn "VirtualFileSystem\|FileSyncManager\|SyncMode\|vfs\|code_tools\|execution_tools" src/ tests/
grep -rn "BlockPrematureAsk\|BlockTaskTool\|ContinueAfterWrite\|EnforceTargetFile" src/ tests/

# 4. Every module still imports
.venv/bin/python -c "import rudra.cli, rudra.agent.main_agent, rudra.agent.planner_agent, \
  rudra.agent.coder_agent, rudra.tools.planning_tools, rudra.filesystem"

# 5. CLI still resolves with zero subcommands
.venv/bin/rudra --help && .venv/bin/rudra --version

# 6. Live smoke run, mirroring U.12. OLLAMA_NUM_PREDICT is mandatory —
#    the repo .env sets -1, which Ollama cloud rejects (A5.1).
RUDRA=/home/a/code/ai-ml/agent/RudraAnvil/.venv/bin/rudra
cd "$(mktemp -d)" && OLLAMA_MODEL_PLANNER=gemma4:31b-cloud \
  OLLAMA_MODEL_CODER=gemma4:31b-cloud OLLAMA_NUM_PREDICT=131072 \
  timeout 900 "$RUDRA" "build a python CLI that reverses a string"
```

Expected of step 6: non-empty `.rudra/PLAN.md`, a non-empty generated `.py` file, exit 0 — the same outcome U.12 recorded, proving the planner prompt still carries usable project structure after `get_tree()` was replaced.

Grep expectations for step 3: the only permitted hits are historical prose in comments and `TODO.md` rows describing the deletion, never an import or a call. (This mirrors the rule A4.9 established for the `overwrite_backend.py` deletion: prose that explains *why* code exists is fine; a numeric `file:line` citation into a deleted file is not.)

---

## 9. Ledger updates

### New rows to add **before** any code is touched

`CLAUDE.md` §2 rule 2 is explicit: nothing is fixed before it is listed as `PENDING`. These four were found while writing this design and have no ledger entry, so they land in a **first commit** containing only `TODO.md` changes, and are deleted in a later one.

| # | Item | Evidence |
|---|---|---|
| A2.7 | `SearchConfig` (tavily / duckduckgo) and `Config.search` are never referenced anywhere in `src/` | `config.py:36-41,50`; `grep -rn "config.search\|SearchConfig\|tavily\|duckduckgo" -i src/` → only the definition |
| A2.8 | `AgentResult.todo_summary` is never set and never read | `main_agent.py:50`; sole occurrence in the repo |
| A2.9 | `watch` dispatches to a tool named `check_syntax` that exists only in the dead `execution_tools.py` — the branch has never executed | `cli.py:396` vs `code_tools.py` (tools: `run_command`, `grep_in_file`) and `execution_tools.py:230` |
| A2.10 | `--max-agents` writes `config.agent.max_agents`, which nothing reads | `cli.py:180,192`; `config.py:29` |

Also added: **D17** in §0 (delete `watch` + `code_tools.py`, with the C3.2 pointer), and the `project_tree` design decisions recorded on the C0.3 row (git-first, flat paths, depth 5 / 300 entries).

### Rows to mark `DONE` on completion

`C0.2` (partial — with an explicit note that `planner_agent.py:64` survives as §0.5 surface #6), `C0.3`, `A2.1`, `A2.2`, `A2.3`, `A2.4`, `A2.5`, `A2.6`, `A2.7`, `A2.8`, `A2.9`, `A2.10`, `D7`, `A1.4`, `A1.6`, `A1.10`.

Section E, Step 2's row gains a completion note naming Step 3 as unblocked, matching the pattern Step 1's row uses.

### Citation hygiene

Every `file:line` in this document was read directly at `040dae4` rather than copied from `TODO.md`. A4.10 records that hand-written line numbers in this repo's ledger drift silently; the same hazard applies to this spec the moment code moves. Any row marked `DONE` from this step should cite a **symbol name** where one is stable enough to serve, and a line number only where it is not.
