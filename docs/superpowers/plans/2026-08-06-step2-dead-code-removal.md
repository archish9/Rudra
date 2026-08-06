# Step 2 — Dead Code Removal + `project_tree()` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete ~1,425 lines of unreachable, superseded, and small-model-workaround code, and replace `VirtualFileSystem`'s one load-bearing behavior — telling the agent what files exist — with a capped, names-only `project_tree()` helper that never reads file content.

**Architecture:** Seven tasks, ordered so the tree is nothing but green tests between them. Task 1 writes ledger rows before any code moves (`CLAUDE.md` §2 rule 2). Task 2 adds the only new code, test-first. Tasks 3–5 delete in dependency order — consumers before the thing they consume — so no task ever leaves a dangling import. Tasks 6–7 sweep dead fields and verify end-to-end.

**Tech Stack:** Python 3.12, `deepagents 0.7.4`, `wcmatch 11.0` (already declared at `pyproject.toml:45`), `pytest`, `ruff`, Typer + Rich.

**Spec:** `docs/superpowers/specs/2026-08-06-step2-dead-code-removal-design.md`
**Branch:** `cleanup/step2-dead-code` (already created; spec committed at `6701b62`)
**Baseline:** `040dae4`

## Global Constraints

- **Ledger before code.** Nothing is fixed before it is listed in `TODO.md` as `PENDING`. Task 1 exists solely to satisfy this and must be committed on its own.
- **Evidence-based claims only.** Every claim about the codebase cites `file.py:line`, verified by reading the file — never copied from `TODO.md`, which has documented line-drift (A4.10).
- **Verify before claiming done.** Run the command, paste the output. No `DONE` mark without it.
- **Lint bar: no increase over 164 errors** (`.venv/bin/ruff check src/`), the merge-base figure at `b322978` recorded in `CLAUDE.md:179`. A decrease is expected here.
- **All 28 pre-existing tests stay green** at every commit. `.venv/bin/pytest tests/ -v`.
- **Python interpreter is `.venv/bin/python`**, pytest is `.venv/bin/pytest`, ruff is `.venv/bin/ruff`. Never bare `python`.
- **Do not touch** `planner_agent.py:64` (the prompt-level ask-block), the `.rudra/` directory layout (C0.9), or anything in step 3's list (A1.1, A1.5, A1.7, A1.11–A1.15). They are deliberately out of scope.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

---

## File Structure

| File | Fate | Responsibility after this plan |
|---|---|---|
| `src/rudra/filesystem/tree.py` | **create** | The only public surface of the `filesystem` package: `project_tree()` |
| `tests/test_project_tree.py` | **create** | Locks the tree helper's contract, including "never reads content" |
| `src/rudra/filesystem/virtual_fs.py` | **delete** | — |
| `src/rudra/filesystem/sync.py` | **delete** | — |
| `src/rudra/filesystem/__init__.py` | modify | Exports `project_tree` only |
| `src/rudra/middleware/block_premature_ask.py` | **delete** | — |
| `src/rudra/middleware/block_task_tool.py` | **delete** | — |
| `src/rudra/middleware/continue_after_write.py` | **delete** | — |
| `src/rudra/middleware/enforce_target_file.py` | **delete** | — |
| `src/rudra/middleware/__init__.py` | modify | Exports `FixWriteParamsMiddleware`, `TaskAnchorMiddleware` |
| `src/rudra/tools/code_tools.py` | **delete** | — (returns in C3.2 on backend `execute`) |
| `src/rudra/tools/__init__.py` | modify | Exports `create_interaction_tools`, `create_planning_tools` |
| `src/rudra/tools/planning_tools.py` | modify | Takes `project_path: Path` instead of a VFS |
| `execution_tools.py` (repo root) | **delete** | — |
| `src/rudra/agent/planner_agent.py` | modify | Prompt built from `project_tree(project_path)` |
| `src/rudra/agent/coder_agent.py` | modify | No `vfs`, no `target_file`, two middlewares |
| `src/rudra/agent/main_agent.py` | modify | No VFS plumbing, no dead context fields |
| `src/rudra/cli.py` | modify | No `watch`, no `--max-agents`, `/tree` uses `project_tree` |
| `src/rudra/config.py` | modify | No `SearchConfig`, no dead `AgentConfig` fields |
| `TODO.md` | modify | New rows in Task 1, `DONE` marks in Task 7 |

---

## Task 1: Ledger rows before any code moves

**Files:**
- Modify: `TODO.md` (Section 0 decision table; Section A2 table)

**Interfaces:**
- Consumes: nothing.
- Produces: ledger rows `A2.7`–`A2.10` and decision `D17`, referenced by Tasks 3, 5, 6, and 7.

`CLAUDE.md` §2 rule 2: *"Never fix a bug on discovery. Add it to TODO.md as PENDING with file:line evidence first."* These four surfaces were found while writing the spec and have no ledger entry. This task adds them and nothing else, in its own commit.

- [ ] **Step 1: Add decision D17 to the Section 0 table**

In `TODO.md`, find the row beginning `| D16 |` (the last row of the Section 0 decision table, about staying pure Python). Add immediately after it:

```markdown
| D17 | **Delete the `watch` command and `tools/code_tools.py`.** | Decided 2026-08-06. `watch` (`cli.py:351-414`) is a separate command, not part of a `rudra "<task>"` run, and its only actionable branch dispatches to a tool named `check_syntax` (`cli.py:396`) that exists solely in the dead `execution_tools.py:230` — never in `code_tools.py`, whose tools are `run_command` and `grep_in_file`. The branch has never executed. What remains is a printer of `Modified: <path>` lines. Deleting `watch` removes `code_tools.py`'s only consumer. Command execution returns in **C3.2** (step 7) on backend `execute`, with the log-offload behavior (big output → `.rudra/logs/`, short preview returned) ported as C3.2 already specifies. Run-visibility during a task run is served by `RudraAgent._log_single_message` (`main_agent.py:103-147`), improved later by C9.1/C9.7 |
```

- [ ] **Step 2: Add rows A2.7–A2.10 to the Section A2 table**

In `TODO.md`, find the row beginning `| A2.6 |` (the last row of the A2 — Dead code table). Add immediately after it:

```markdown
| A2.7 | PENDING | `SearchConfig` (tavily / duckduckgo) and `Config.search` are never referenced anywhere in `src/`. **Delete.** | `src/rudra/config.py:36-41` (class), `:50` (field); `grep -rn "config.search\|SearchConfig\|tavily\|duckduckgo" -i src/` returns only the definition itself |
| A2.8 | PENDING | `AgentResult.todo_summary` is never set and never read — the field is the only occurrence of the name in the repo. **Delete.** | `src/rudra/agent/main_agent.py:50`; `grep -rn "todo_summary" src/` → 1 hit |
| A2.9 | PENDING | `watch`'s `.py` branch dispatches to a tool named `check_syntax` that `create_code_tools` never produces — it exists only in the dead `execution_tools.py`. The branch has never executed, so `watch` has never reported an error. Resolved by the D17 deletion. | `src/rudra/cli.py:396` vs `src/rudra/tools/code_tools.py` (returns `[run_command, grep_in_file]`, `:135`) and `execution_tools.py:230` |
| A2.10 | PENDING | `--max-agents` writes `config.agent.max_agents`, which nothing reads — a live CLI flag with no effect. **Delete the flag with the field (A2.6).** Real sub-agent fan-out arrives with C6.2. | `src/rudra/cli.py:180` (option), `:192` (assignment); `src/rudra/config.py:29` (field) |
```

- [ ] **Step 3: Verify the rows render and cite real lines**

Run:
```bash
grep -n "A2.7\|A2.8\|A2.9\|A2.10\|D17" TODO.md
sed -n '36,41p;50p' src/rudra/config.py
sed -n '50p' src/rudra/agent/main_agent.py
sed -n '180p;192p;396p' src/rudra/cli.py
```
Expected: five ledger hits; `config.py` shows the `SearchConfig` class and the `search:` field; `main_agent.py:50` shows `todo_summary: str = ""`; `cli.py` shows the `max_agents` option, its assignment, and the `check_syntax` comparison.

- [ ] **Step 4: Commit**

```bash
git add TODO.md
git commit -m "$(cat <<'EOF'
docs(todo): log A2.7-A2.10 and D17 before touching the code

Four dead surfaces found while writing the Step 2 design had no ledger
entry: SearchConfig, AgentResult.todo_summary, watch's never-executed
check_syntax branch, and the --max-agents flag. D17 records the decision
to delete `watch` + code_tools.py.

Per CLAUDE.md rule 2, these are PENDING before any of them is touched.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `project_tree()` — the only new code

**Files:**
- Create: `src/rudra/filesystem/tree.py`
- Create: `tests/test_project_tree.py`
- Modify: `src/rudra/filesystem/__init__.py`

**Interfaces:**
- Consumes: `wcmatch.glob` (declared at `pyproject.toml:45`, installed 11.0).
- Produces: `project_tree(root: Path, *, max_depth: int = 5, max_entries: int = 300) -> str`, imported as `from rudra.filesystem import project_tree` by Task 5 (`planner_agent.py`, `cli.py`).

Two behaviors verified by probe before this plan was written, both of which the implementation depends on:

1. `git ls-files --cached --others --exclude-standard -z` honors `!` negations and `dir/` rules correctly, and exits **128** outside a repo — a clean signal to fall back.
2. wcmatch's `**/build` matches `build` but **not** `build/artifact.o`. So the fallback walk *must* prune ignored directories in-place; matching files alone would leak the contents of every ignored directory.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_project_tree.py`:

```python
"""Tests for rudra.filesystem.tree.project_tree.

project_tree replaces VirtualFileSystem.get_tree(), deleted in Step 2 (D7).
The contract: report file NAMES only, never read the content of a listed
file, and stay bounded by an explicit depth and entry cap.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from rudra.filesystem.tree import project_tree

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not on PATH"
)


def _write(root: Path, rel: str, content: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "test")


@requires_git
def test_gitignored_paths_are_excluded(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "app.py")
    _write(tmp_path, "build/artifact.o")

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert "build/artifact.o" not in lines


def test_fallback_honors_gitignore_without_a_repo(tmp_path: Path) -> None:
    # No `git init` at all, so _git_listing returns None and the wcmatch
    # walk runs. Same expected output as the git path above.
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "app.py")
    _write(tmp_path, "build/artifact.o")

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert "build/artifact.o" not in lines


def test_max_depth_drops_deeper_paths(tmp_path: Path) -> None:
    _write(tmp_path, "a/b/shallow.txt")
    _write(tmp_path, "a/b/c/d/e/f/deep.txt")

    lines = project_tree(tmp_path, max_depth=3).splitlines()

    assert "a/b/shallow.txt" in lines
    assert "a/b/c/d/e/f/deep.txt" not in lines


def test_max_entries_truncates_and_reports_remainder(tmp_path: Path) -> None:
    for index in range(20):
        _write(tmp_path, f"file_{index:02d}.txt")

    lines = project_tree(tmp_path, max_entries=5).splitlines()

    assert len(lines) == 6
    assert lines[-1] == "… 15 more entries omitted (cap: 5)"


@requires_git
def test_builtin_skips_apply_even_when_git_tracks_them(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _write(tmp_path, "app.py")
    _write(tmp_path, ".rudra/PLAN.md")
    _write(tmp_path, "node_modules/pkg/index.js")
    _git(tmp_path, "add", "-f", "app.py", ".rudra/PLAN.md", "node_modules/pkg/index.js")

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert ".rudra/PLAN.md" not in lines
    assert "node_modules/pkg/index.js" not in lines


def test_never_reads_the_content_of_a_listed_file(tmp_path: Path, monkeypatch) -> None:
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "app.py", "secret" * 1000)
    _write(tmp_path, "src/models.py")

    real_read_text = Path.read_text

    def guarded(self: Path, *args, **kwargs):
        if self.name != ".gitignore":
            raise AssertionError(f"project_tree read file content: {self}")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)

    lines = project_tree(tmp_path).splitlines()

    assert "app.py" in lines
    assert "src/models.py" in lines


def test_empty_or_missing_root(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    assert project_tree(empty) == "(empty project)"
    assert project_tree(tmp_path / "does_not_exist") == "(empty project)"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_project_tree.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'rudra.filesystem.tree'`.

- [ ] **Step 3: Write the implementation**

Create `src/rudra/filesystem/tree.py`:

```python
"""Capped, names-only project listing for agent prompts.

Replaces VirtualFileSystem.get_tree(), deleted in Step 2 (TODO.md D7). The
VFS loaded every text file in the project into RAM to answer this one
question; project_tree answers it from filenames alone.

Listing strategy:
  1. `git ls-files --cached --others --exclude-standard` whenever git can
     answer. Git resolves .gitignore semantics itself — negations, leading
     `/`, `**`, trailing `/`, nested .gitignore files, global excludes —
     which is the direct fix for TODO.md A1.10.
  2. Otherwise an os.walk + wcmatch pass over the root .gitignore.

Never reads the content of a listed file. The only file this module opens
is the root .gitignore, and only on the fallback path.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from wcmatch import glob as wcglob

EMPTY_PROJECT = "(empty project)"

_GIT_TIMEOUT_SECONDS = 5

# Applied on BOTH listing paths, so they hold even in a repo that tracks
# them. `.rudra` is Rudra's own state directory and must never appear in
# the model's view of the project.
_ALWAYS_SKIP_DIRS = frozenset(
    {".git", ".rudra", "__pycache__", "node_modules", ".venv", "venv"}
)
_ALWAYS_SKIP_SUFFIXES = (".pyc",)

_WCMATCH_FLAGS = wcglob.GLOBSTAR | wcglob.NEGATE | wcglob.DOTGLOB


def project_tree(
    root: Path,
    *,
    max_depth: int = 5,
    max_entries: int = 300,
) -> str:
    """Return a capped, flat listing of the project's files — names only.

    Args:
        root: Project root directory.
        max_depth: Maximum number of path segments to keep. `pyproject.toml`
            is depth 1; `src/rudra/cli.py` is depth 3.
        max_entries: Maximum paths to list before truncating with a footer.

    Returns:
        One relative POSIX path per line, sorted, plus a truncation footer
        when entries were omitted. `"(empty project)"` when nothing matches.
    """
    root = Path(root)
    if not root.is_dir():
        return EMPTY_PROJECT

    entries = _git_listing(root)
    if entries is None:
        entries = _walk_listing(root)

    kept = sorted(
        {
            rel
            for rel in entries
            if not _is_always_skipped(rel)
            and rel.count("/") + 1 <= max_depth
            and (root / rel).is_file()
        }
    )
    if not kept:
        return EMPTY_PROJECT

    shown = kept[:max_entries]
    omitted = len(kept) - len(shown)
    lines = list(shown)
    if omitted:
        lines.append(f"… {omitted} more entries omitted (cap: {max_entries})")
    return "\n".join(lines)


def _is_always_skipped(rel_posix: str) -> bool:
    """True when any path segment is in the builtin skip set."""
    if any(part in _ALWAYS_SKIP_DIRS for part in rel_posix.split("/")):
        return True
    return rel_posix.endswith(_ALWAYS_SKIP_SUFFIXES)


def _git_listing(root: Path) -> list[str] | None:
    """Paths from git, or None when git cannot answer.

    None covers every failure mode identically: git missing, root outside a
    work tree (exit 128), or the call timing out.
    """
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    raw = completed.stdout.decode("utf-8", errors="replace")
    return [entry for entry in raw.split("\0") if entry]


def _walk_listing(root: Path) -> list[str]:
    """Fallback listing: os.walk pruned by the root .gitignore."""
    patterns = _gitignore_patterns(root)
    found: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)

        # Prune in place. wcmatch's `**/build` matches `build` but NOT
        # `build/artifact.o`, so pruning the directory is what actually
        # keeps an ignored subtree out — filtering files alone would not.
        dirnames[:] = [
            name
            for name in dirnames
            if name not in _ALWAYS_SKIP_DIRS
            and not _matches((rel_dir / name).as_posix(), patterns)
        ]

        for name in filenames:
            rel = (rel_dir / name).as_posix()
            if _is_always_skipped(rel) or _matches(rel, patterns):
                continue
            found.append(rel)

    return found


def _gitignore_patterns(root: Path) -> list[str]:
    """Translate the root .gitignore into wcmatch patterns."""
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return []

    patterns: list[str] = []
    for raw_line in gitignore.read_text(
        encoding="utf-8", errors="replace"
    ).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(_to_wcmatch(line))
    return patterns


def _to_wcmatch(line: str) -> str:
    """Convert one .gitignore line into a wcmatch pattern.

    A pattern containing no `/` matches at any depth, so it gets a `**/`
    prefix. A leading `/` anchors to the root. A trailing `/` marks a
    directory, which the caller handles by matching the directory name.
    `!` negation is passed through — wcmatch's NEGATE flag consumes it.
    """
    negated = line.startswith("!")
    if negated:
        line = line[1:]

    line = line.rstrip("/")
    if line.startswith("/"):
        pattern = line.lstrip("/")
    elif "/" in line:
        pattern = line
    else:
        pattern = f"**/{line}"

    return f"!{pattern}" if negated else pattern


def _matches(rel_posix: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    return wcglob.globmatch(rel_posix, patterns, flags=_WCMATCH_FLAGS)
```

- [ ] **Step 4: Export it from the package**

Edit `src/rudra/filesystem/__init__.py`. It currently reads:

```python
"""Filesystem module for virtual filesystem and sync."""

from rudra.filesystem.virtual_fs import VirtualFileSystem
from rudra.filesystem.sync import FileSyncManager, SyncMode

__all__ = ["VirtualFileSystem", "FileSyncManager", "SyncMode"]
```

Replace with (the VFS exports stay for now — Task 5 removes them once their consumers are gone):

```python
"""Filesystem module for virtual filesystem and sync."""

from rudra.filesystem.sync import FileSyncManager, SyncMode
from rudra.filesystem.tree import project_tree
from rudra.filesystem.virtual_fs import VirtualFileSystem

__all__ = ["VirtualFileSystem", "FileSyncManager", "SyncMode", "project_tree"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_project_tree.py -v`
Expected: **7 passed** (or 5 passed / 2 skipped on a machine without git).

Then the whole suite plus lint:
```bash
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/
```
Expected: 35 passed (28 + 7); ruff ≤ 164.

- [ ] **Step 6: Commit**

```bash
git add src/rudra/filesystem/tree.py src/rudra/filesystem/__init__.py tests/test_project_tree.py
git commit -m "$(cat <<'EOF'
feat(filesystem): add capped names-only project_tree() (C0.3)

Replaces VirtualFileSystem.get_tree(), which required loading every text
file in the project into RAM (virtual_fs.py:114-157) to answer a question
about filenames.

git ls-files first, so .gitignore semantics are git's own — negations,
anchoring, **, nested ignores (fixes A1.10). os.walk + wcmatch fallback
outside a repo, pruning ignored directories in place.

Flat relative paths, depth 5 / 300 entries, truncation footer. Never
reads the content of a listed file; a test asserts this by monkeypatching
Path.read_text to raise.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Delete `watch`, `code_tools.py`, `execution_tools.py` (D17, A2.1, A2.4, A2.9)

**Files:**
- Delete: `execution_tools.py`, `src/rudra/tools/code_tools.py`
- Modify: `src/rudra/cli.py:347-414` (the `Sub-commands` header block through the end of `watch`), `src/rudra/tools/__init__.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: two fewer `VirtualFileSystem` consumers, which Task 5 depends on. Running this **before** Task 5 is what keeps Task 5 from having to retarget code that is about to be deleted.

- [ ] **Step 1: Delete the two files**

```bash
git rm execution_tools.py src/rudra/tools/code_tools.py
```

- [ ] **Step 2: Remove the `watch` command**

In `src/rudra/cli.py`, delete everything from the line reading:

```python
# ---------------------------------------------------------------------------
# Sub-commands
# ---------------------------------------------------------------------------

@app.command()
def watch(
```

through the end of that function (the line `    observer.join()`), inclusive — `cli.py:347-414`. Leave the file ending:

```python
def _print_help() -> None:
    ...
    console.print()


# Entry point
if __name__ == "__main__":
    app()
```

Do **not** remove `import asyncio` (`cli.py:5`) — the REPL still uses it at `asyncio.run(_repl_session())`. Do **not** remove the `if ctx.invoked_subcommand is not None: return` guard (`cli.py:186-188`); it is harmless with zero commands and correct again as soon as one returns.

Do **not** edit the coder's system prompt in `src/rudra/agent/coder_agent.py`, whose STOP CONDITION block says `"Do NOT call run_command. Do NOT call task. Do NOT call update_plan."`. Deleting `code_tools.py` makes the `run_command` clause inert text rather than wrong, and the `task` clause is now *more* relevant, not less — deleting `BlockTaskToolMiddleware` in Task 4 makes the `task` tool genuinely reachable for the first time. That prompt is rewritten wholesale in the C6.2 subagent work (step 9).

- [ ] **Step 3: Update the tools package exports**

`src/rudra/tools/__init__.py` currently reads:

```python
"""Agent tools module."""

from rudra.tools.code_tools import create_code_tools
from rudra.tools.interaction_tools import create_interaction_tools
from rudra.tools.planning_tools import create_planning_tools

__all__ = ["create_code_tools", "create_interaction_tools", "create_planning_tools"]
```

Replace with:

```python
"""Agent tools module."""

from rudra.tools.interaction_tools import create_interaction_tools
from rudra.tools.planning_tools import create_planning_tools

__all__ = ["create_interaction_tools", "create_planning_tools"]
```

- [ ] **Step 4: Verify nothing references the deleted code, and the CLI still resolves**

```bash
grep -rn "code_tools\|execution_tools\|create_code_tools\|check_syntax" src/ tests/
.venv/bin/python -c "import rudra.cli"
.venv/bin/rudra --help
.venv/bin/rudra --version
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/
```
Expected: the grep returns **nothing**; `--help` and `--version` both exit 0 (Typer tolerates zero registered commands — probed on this venv); 35 passed; ruff ≤ 164.

If `--help` fails, that is a real blocker, not a warning — stop and report rather than working around it.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: delete watch, code_tools.py, execution_tools.py (D17, A2.1, A2.4)

watch never did what it appeared to: it is a separate command, not part of
a `rudra "<task>"` run, and its only actionable branch dispatched to a tool
named check_syntax that exists solely in the dead execution_tools.py (A2.9).
The branch never executed. What remained printed "Modified: <path>".

Deleting it removes code_tools.py's last consumer. Command execution
returns in C3.2 on backend `execute`, with the log-offload behavior ported.

Removes the --max-agents-adjacent dead weight ahead of the VFS deletion:
two of the six VirtualFileSystem consumers are gone.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Delete the four middlewares, retarget the agents (C0.2, A2.3, D4)

**Files:**
- Delete: `src/rudra/middleware/block_premature_ask.py`, `block_task_tool.py`, `continue_after_write.py`, `enforce_target_file.py`
- Modify: `src/rudra/middleware/__init__.py`, `src/rudra/agent/coder_agent.py`, `src/rudra/agent/planner_agent.py:13-17,102`, `src/rudra/agent/main_agent.py:332`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `create_coder_agent(project_path, vfs, tech_stack_content, filesystem_backend, checkpointer)` — no `target_file` parameter. Task 5 removes `vfs` from the same signature.

Per D4, these four were built for qwen3:14b and the minimum target model is now 32B (D6); three of them actively block required features. Two middlewares survive: `FixWriteParamsMiddleware` and `TaskAnchorMiddleware`.

- [ ] **Step 1: Delete the four files**

```bash
git rm src/rudra/middleware/block_premature_ask.py \
       src/rudra/middleware/block_task_tool.py \
       src/rudra/middleware/continue_after_write.py \
       src/rudra/middleware/enforce_target_file.py
```

- [ ] **Step 2: Update `src/rudra/middleware/__init__.py`**

Replace the whole file with:

```python
"""Custom middleware for Rudra."""

from rudra.middleware.fix_write_params import FixWriteParamsMiddleware
from rudra.middleware.task_anchor import TaskAnchorMiddleware

__all__ = [
    "FixWriteParamsMiddleware",
    "TaskAnchorMiddleware",
]
```

- [ ] **Step 3: Retarget `src/rudra/agent/coder_agent.py`**

Change the import at `:12`:

```python
from rudra.middleware import BlockTaskToolMiddleware, EnforceTargetFileMiddleware, FixWriteParamsMiddleware, TaskAnchorMiddleware
```

to:

```python
from rudra.middleware import FixWriteParamsMiddleware, TaskAnchorMiddleware
```

Then replace the function signature and middleware construction (`:47-75`). It currently reads:

```python
def create_coder_agent(
    project_path: Path,
    vfs: VirtualFileSystem,
    tech_stack_content: str,
    filesystem_backend,
    checkpointer,
    target_file: str = "",
):
    """Create a fresh coder deep agent. Call once per file.

    Args:
        target_file: Exact relative path the coder must write (e.g. "src/models.py").
                     When provided, EnforceTargetFileMiddleware blocks writes to other files.
    """
    model = ChatOllama(
        model=config.ollama.model_coder,
        base_url=config.ollama.base_url,
        temperature=config.ollama.temperature,
        num_predict=config.ollama.num_predict,
        reasoning=True,
    )

    middleware = [
        FixWriteParamsMiddleware(),
        TaskAnchorMiddleware(_CODER_ANCHOR),
        BlockTaskToolMiddleware(),
    ]
    if target_file:
        middleware.append(EnforceTargetFileMiddleware(target_file))
```

Replace with:

```python
def create_coder_agent(
    project_path: Path,
    vfs: VirtualFileSystem,
    tech_stack_content: str,
    filesystem_backend,
    checkpointer,
):
    """Create a fresh coder deep agent. Call once per file.

    The coder is steered to a single file by .rudra/current_task.md, by
    _CODER_ANCHOR, and by the per-file user message the orchestrator sends
    (main_agent.py). EnforceTargetFileMiddleware used to enforce this as
    well, but it matched on basename — target `src/models.py` permitted a
    write to `tests/models.py` (TODO.md A1.6) — and is deleted in D4.
    """
    model = ChatOllama(
        model=config.ollama.model_coder,
        base_url=config.ollama.base_url,
        temperature=config.ollama.temperature,
        num_predict=config.ollama.num_predict,
        reasoning=True,
    )

    middleware = [
        FixWriteParamsMiddleware(),
        TaskAnchorMiddleware(_CODER_ANCHOR),
    ]
```

Leave the `create_deep_agent(...)` call at the end of the function unchanged.

- [ ] **Step 4: Stop passing `target_file` from the orchestrator**

In `src/rudra/agent/main_agent.py:332`, change:

```python
        coder_config = {**self._coder_config, "target_file": filename}
        coder = create_coder_agent(**coder_config)
```

to:

```python
        coder = create_coder_agent(**self._coder_config)
```

- [ ] **Step 5: Retarget `src/rudra/agent/planner_agent.py`**

Change the import block at `:13-17`:

```python
from rudra.middleware import (
    BlockPrematureAskMiddleware,
    FixWriteParamsMiddleware,
    TaskAnchorMiddleware,
)
```

to:

```python
from rudra.middleware import (
    FixWriteParamsMiddleware,
    TaskAnchorMiddleware,
)
```

Then remove `BlockPrematureAskMiddleware(task),` from the middleware list (`:102`), leaving:

```python
        middleware=[
            # First in the list: cleans tool args before anything else sees
            # them. The planner previously got fence-stripping from
            # OverwriteFilesystemBackend, which U.3 deletes. See TODO.md U.14.
            FixWriteParamsMiddleware(),
            TaskAnchorMiddleware(task),
        ],
```

**Leave `planner_agent.py:64` alone.** The line `- Do NOT call ask_user() if the task already specifies a framework or language` is the prompt-level twin of the middleware just deleted, tracked as §0.5 surface #6 and removed in C6.8a (step 10). Removing it here — with no dynamic-clarification machinery to replace it, and with `ask_user`'s docstring still mandating one question at a time (§0.5 surface #3) — would ship a worse behavior four steps early.

- [ ] **Step 6: Verify**

```bash
grep -rn "BlockPrematureAsk\|BlockTaskTool\|ContinueAfterWrite\|EnforceTargetFile\|target_file" src/ tests/
.venv/bin/python -c "import rudra.agent.coder_agent, rudra.agent.planner_agent, rudra.agent.main_agent"
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/
```
Expected: the grep returns **nothing**; imports succeed; 35 passed — including `tests/test_agent_wiring.py::test_planner_wires_fix_write_params_middleware_first`, which still holds because `FixWriteParamsMiddleware` remains first; ruff ≤ 164.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(middleware): delete the four D4 middlewares (C0.2, A2.3)

BlockPrematureAsk, BlockTaskTool, ContinueAfterWrite, EnforceTargetFile —
all built for qwen3:14b, which is below the 32B minimum target (D6), and
three of them blocked required features: dynamic clarifying questions
(#4), subagents (#3), and the one-file-per-agent loop being replaced.

ContinueAfterWrite already had zero call sites (A2.3). EnforceTargetFile
also carried a basename-matching hole — target src/models.py permitted a
write to tests/models.py (A1.6) — which this closes by deletion.

coder_agent loses its now-inert target_file parameter.

C0.2 is only PARTIALLY closed: the prompt-level ask-block at
planner_agent.py:64 survives deliberately, tracked as TODO.md 0.5
surface #6, and dies with C6.8a.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Delete the VFS, retarget every remaining consumer (D7)

**Files:**
- Delete: `src/rudra/filesystem/virtual_fs.py`, `src/rudra/filesystem/sync.py`
- Modify: `src/rudra/filesystem/__init__.py`, `src/rudra/tools/planning_tools.py`, `src/rudra/agent/planner_agent.py`, `src/rudra/agent/coder_agent.py`, `src/rudra/agent/main_agent.py`, `src/rudra/cli.py`

**Interfaces:**
- Consumes: `project_tree` from Task 2; the `target_file`-free coder signature from Task 4.
- Produces: `create_planning_tools(project_path: Path, task: str = "") -> list`; `build_planner_prompt(task: str, project_path: Path, tech_stack_content: str = "") -> str`; `create_planner_agent(task, project_path, tech_stack_content, filesystem_backend, checkpointer, console)`; `create_coder_agent(project_path, tech_stack_content, filesystem_backend, checkpointer)`.

This is the memory fix. `VirtualFileSystem.load_from_disk` reads every text file in the project into RAM (`virtual_fs.py:114-157`) before the planner's first token. Do all edits in this task before running anything — the tree is intentionally broken mid-task.

- [ ] **Step 1: Retarget `src/rudra/tools/planning_tools.py`**

Replace the module docstring's filesystem paragraph (`:6-11`), which describes a cache that is about to stop existing. The docstring currently contains:

```python
IMPORTANT — filesystem design:
  update_plan / read_plan both operate on the REAL disk file directly,
  bypassing the VirtualFileSystem in-memory cache. This keeps them in sync
  with deepagents' built-in edit_file / read_file tools, which also write
  to real disk. If read_plan used vfs.read_file(), it would return a stale
  cache that was never updated when edit_file ran.
```

Replace those six lines with:

```python
IMPORTANT — filesystem design:
  update_plan / read_plan both operate on the REAL disk file directly, the
  same disk deepagents' built-in edit_file / read_file tools write to, so
  the plan the orchestrator parses is always the plan the agent last wrote.
```

Then change the function signature (`:27-38`). It currently reads:

```python
def create_planning_tools(vfs, task: str = "") -> list:
    """Create planning tools that read/write .rudra/PLAN.md directly on disk.

    Args:
        vfs: VirtualFileSystem anchored to the project root (used for root_path only).
        task: The original user task — injected into return messages to remind the
              model of the framework/requirements.

    Returns:
        List of LangChain tools: [update_plan, read_plan]
    """
    plan_path: Path = vfs.root_path / ".rudra" / "PLAN.md"
```

Replace with:

```python
def create_planning_tools(project_path: Path, task: str = "") -> list:
    """Create planning tools that read/write .rudra/PLAN.md directly on disk.

    Args:
        project_path: Project root directory.
        task: The original user task — injected into return messages to remind the
              model of the framework/requirements.

    Returns:
        List of LangChain tools: [update_plan, read_plan, write_task_assignment]
    """
    plan_path: Path = project_path / ".rudra" / "PLAN.md"
```

Delete the cache-invalidation block (`:91-93`):

```python
        # Invalidate VFS cache — pop both separator styles (Windows uses \, POSIX uses /)
        vfs.files.pop(str(Path(".rudra") / "PLAN.md"), None)
        vfs.files.pop(".rudra/PLAN.md", None)
```

Change the comment on the line above it (`:88`) from:

```python
        # Write directly to disk — bypasses VFS cache so read_plan stays in sync
```

to:

```python
        # Write directly to disk — the same file read_plan and edit_file see
```

Finally, change `task_path` (`:130`):

```python
    task_path: Path = vfs.root_path / ".rudra" / "current_task.md"
```

to:

```python
    task_path: Path = project_path / ".rudra" / "current_task.md"
```

- [ ] **Step 2: Retarget `src/rudra/agent/planner_agent.py`**

Change the import at `:12`:

```python
from rudra.filesystem import VirtualFileSystem
```

to:

```python
from rudra.filesystem import project_tree
```

Change `build_planner_prompt`'s signature and structure block (`:22-33`):

```python
def build_planner_prompt(
    task: str,
    vfs: VirtualFileSystem,
    tech_stack_content: str = "",
) -> str:
    base = f"""You are a senior software architect and planning agent for Rudra.

Your ONLY job: ANALYZE tasks and CREATE plans. You NEVER write project code files directly.

## PROJECT STRUCTURE
{vfs.get_tree()}
"""
```

to:

```python
def build_planner_prompt(
    task: str,
    project_path: Path,
    tech_stack_content: str = "",
) -> str:
    base = f"""You are a senior software architect and planning agent for Rudra.

Your ONLY job: ANALYZE tasks and CREATE plans. You NEVER write project code files directly.

## PROJECT STRUCTURE
{project_tree(project_path)}
"""
```

Change `create_planner_agent`'s signature (`:69-77`) by deleting the `vfs: VirtualFileSystem,` line, leaving:

```python
def create_planner_agent(
    task: str,
    project_path: Path,
    tech_stack_content: str,
    filesystem_backend,
    checkpointer,
    console: Console,
):
```

Change the tool construction (`:87`):

```python
    custom_tools = create_planning_tools(vfs, task=task) + create_interaction_tools(console, project_path)
```

to:

```python
    custom_tools = create_planning_tools(project_path, task=task) + create_interaction_tools(console, project_path)
```

And the prompt call (`:92`):

```python
        system_prompt=build_planner_prompt(task, vfs, tech_stack_content),
```

to:

```python
        system_prompt=build_planner_prompt(task, project_path, tech_stack_content),
```

- [ ] **Step 3: Retarget `src/rudra/agent/coder_agent.py`**

Delete the import at `:11`:

```python
from rudra.filesystem import VirtualFileSystem
```

Delete the `vfs: VirtualFileSystem,` line from `create_coder_agent`'s signature, leaving (after Task 4's edits):

```python
def create_coder_agent(
    project_path: Path,
    tech_stack_content: str,
    filesystem_backend,
    checkpointer,
):
```

- [ ] **Step 4: Retarget `src/rudra/agent/main_agent.py`**

Delete the import at `:14`:

```python
from rudra.filesystem import VirtualFileSystem
```

Delete the `vfs: VirtualFileSystem` field from `AgentContext` (`:24`).

Delete the VFS construction (`:528-530`):

```python
    vfs = VirtualFileSystem(project_path)
    if project_path.exists():
        vfs.load_from_disk()
```

Delete `vfs=vfs,` from the `AgentContext(...)` construction (`:535`).

Delete `vfs=vfs,` from the `create_planner_agent(...)` call (`:581`).

Delete `"vfs": vfs,` from the `coder_config` dict (`:590`), leaving:

```python
    coder_config = {
        "project_path": project_path,
        "tech_stack_content": tech_stack_content,
        "filesystem_backend": filesystem_backend,
        "checkpointer": checkpointer,
    }
```

- [ ] **Step 5: Retarget `src/rudra/cli.py`**

Change the import at `:18`:

```python
from rudra.filesystem import VirtualFileSystem, FileSyncManager, SyncMode
```

to:

```python
from rudra.filesystem import project_tree
```

Change the `/tree` branch (`:262-267`):

```python
                    elif cmd == "/tree":
                        vfs = VirtualFileSystem(project_path)
                        if project_path.exists():
                            vfs.load_from_disk()
                        console.print(vfs.get_tree())
                        continue
```

to:

```python
                    elif cmd == "/tree":
                        console.print(project_tree(project_path))
                        continue
```

- [ ] **Step 6: Delete the two modules and clean the package exports**

```bash
git rm src/rudra/filesystem/virtual_fs.py src/rudra/filesystem/sync.py
```

Replace `src/rudra/filesystem/__init__.py` entirely with:

```python
"""Filesystem helpers for Rudra."""

from rudra.filesystem.tree import project_tree

__all__ = ["project_tree"]
```

- [ ] **Step 7: Verify nothing survives and everything still imports**

```bash
grep -rn "VirtualFileSystem\|FileSyncManager\|SyncMode\|virtual_fs\|\bvfs\b" src/ tests/
.venv/bin/python -c "import rudra.cli, rudra.agent.main_agent, rudra.agent.planner_agent, \
  rudra.agent.coder_agent, rudra.tools.planning_tools, rudra.filesystem"
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/
```
Expected: the grep returns **nothing**; imports succeed; 35 passed; ruff ≤ 164 and materially lower.

- [ ] **Step 8: Prove the planner prompt still carries real structure**

The planner's system prompt is the single most important consumer of the deleted `get_tree()`. Verify the replacement produces a usable prompt against this repo itself:

```bash
.venv/bin/python -c "
from pathlib import Path
from rudra.agent.planner_agent import build_planner_prompt
prompt = build_planner_prompt('build a flask app', Path('.'), '')
assert 'src/rudra/cli.py' in prompt, 'project structure missing from prompt'
assert '.rudra/' not in prompt, 'rudra state dir leaked into prompt'
print(prompt[:600])
"
```
Expected: prints a prompt whose `## PROJECT STRUCTURE` section lists real repo paths, one per line, and both assertions hold.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor(filesystem): delete VirtualFileSystem and FileSyncManager (D7)

load_from_disk read every text file in the project into RAM before the
planner's first token (virtual_fs.py:114-157) — fatal for the local-first
memory budget, and the reason D7 exists. Of the class's 15 methods only
five were reached from outside the package; the write path was superseded
when deepagents' filesystem tools became the agents' only write mechanism.

Consumers retargeted: planner prompt and the /tree REPL command now use
project_tree(); planning_tools and coder_agent take a project_path instead
of a VFS handle. planning_tools loses its cache-invalidation lines, which
existed only to work around the cache now deleted.

Closes by deletion: A1.4 (write failures silently swallowed, then the file
marked written) and A1.10 (raw fnmatch on .gitignore lines).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Sweep dead fields, imports, and flags (A2.5–A2.8, A2.10)

**Files:**
- Modify: `src/rudra/agent/main_agent.py` (module imports, `AgentContext`, `AgentResult`), `src/rudra/config.py` (`AgentConfig`, `SearchConfig`, `Config`), `src/rudra/cli.py` (the `main` callback's `--max-agents` option and its assignment)

> Line numbers are deliberately omitted here: Tasks 3–5 delete code above every one of these sites, so any number written today is wrong by the time this task runs. Each step below quotes the exact current text to match on instead.

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks. Task 7 verifies the result.

Two fields in this neighborhood are **live** and must survive: `AgentContext.stop_on_error` (read at `main_agent.py:287`) and `AgentConfig.verbose` (read at `cli.py:179`).

- [ ] **Step 1: Remove unused imports from `main_agent.py` (A2.5)**

Delete `import asyncio` (`:5`) and `from deepagents import create_deep_agent` (`:11`). Confirm they are genuinely unused first:

```bash
grep -n "asyncio\.\|create_deep_agent" src/rudra/agent/main_agent.py
```
Expected: no hits. If there are hits, do not delete that import — report the discrepancy.

- [ ] **Step 2: Remove dead `AgentContext` fields (A2.6, A2.8)**

In `src/rudra/agent/main_agent.py`, the dataclass currently reads (after Task 5 removed `vfs`):

```python
@dataclass
class AgentContext:
    """Context passed to the agent during execution."""

    project_path: Path
    task: str
    console: Console
    project_context: Optional[ProjectContext] = None

    dry_run: bool = False
    verbose: bool = False
    max_iterations: int = 100
    stop_on_error: bool = True

    command: str = "auto"
    file_path: Optional[str] = None
    issue: Optional[str] = None

    planner_model: str = ""
    coder_model: str = ""
```

Replace with:

```python
@dataclass
class AgentContext:
    """Context passed to the agent during execution."""

    project_path: Path
    task: str
    console: Console
    project_context: Optional[ProjectContext] = None

    dry_run: bool = False
    verbose: bool = False
    stop_on_error: bool = True

    command: str = "auto"

    planner_model: str = ""
    coder_model: str = ""
```

Then delete `todo_summary: str = ""` from `AgentResult` (`:50`), leaving:

```python
@dataclass
class AgentResult:
    """Result of an agent execution."""

    success: bool
    message: str
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    iterations: int = 0
```

- [ ] **Step 3: Remove dead config (A2.6, A2.7)**

In `src/rudra/config.py`, replace `AgentConfig`:

```python
@dataclass
class AgentConfig:
    """Agent execution configuration."""
    
    max_agents: int = field(default_factory=lambda: int(os.getenv("MAX_AGENTS", "6")))
    max_iterations: int = field(default_factory=lambda: int(os.getenv("MAX_ITERATIONS", "100")))
    checkpoint_interval: int = field(default_factory=lambda: int(os.getenv("CHECKPOINT_INTERVAL", "5")))
    # Verbose logging — ON by default, set VERBOSE=false in .env to disable
    verbose: bool = field(default_factory=lambda: os.getenv("VERBOSE", "true").lower() != "false")
```

with:

```python
@dataclass
class AgentConfig:
    """Agent execution configuration."""

    # Verbose logging — ON by default, set VERBOSE=false in .env to disable
    verbose: bool = field(default_factory=lambda: os.getenv("VERBOSE", "true").lower() != "false")
```

Delete the entire `SearchConfig` class (`:36-41`):

```python
@dataclass
class SearchConfig:
    """Web search tool configuration."""
    
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    use_duckduckgo: bool = field(default_factory=lambda: os.getenv("USE_DUCKDUCKGO", "true").lower() == "true")
```

And delete the `search:` field from `Config` (`:50`), leaving:

```python
@dataclass
class Config:
    """Main configuration container."""
    
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    
    # Paths
    checkpoint_dir: str = ".rudra"
```

- [ ] **Step 4: Remove the `--max-agents` flag (A2.10)**

In `src/rudra/cli.py`, delete the option at `:180`:

```python
    max_agents: int = typer.Option(6, "--max-agents", help="Maximum number of sub-agents"),
```

and its assignment at `:192`:

```python
    config.agent.max_agents = max_agents
```

- [ ] **Step 5: Verify**

```bash
grep -rn "max_agents\|max_iterations\|checkpoint_interval\|SearchConfig\|config.search\|todo_summary\|\bissue\b" src/
.venv/bin/python -c "import rudra.cli, rudra.agent.main_agent, rudra.config; from rudra.config import config; print(config)"
.venv/bin/rudra --help
.venv/bin/pytest tests/ -v
.venv/bin/ruff check src/
```
Expected: grep returns nothing (`file_path` will still appear as a *parameter name* in tool signatures — that is unrelated and correct; only the `AgentContext.file_path` field was removed); `config` prints without `search=` or `max_agents=`; `--help` no longer lists `--max-agents`; 35 passed; ruff ≤ 164.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
refactor: remove dead fields, imports, and the --max-agents flag

A2.5: unused asyncio / create_deep_agent imports in main_agent.
A2.6: AgentContext.max_iterations/file_path/issue and
      AgentConfig.max_agents/max_iterations/checkpoint_interval.
A2.7: SearchConfig (tavily/duckduckgo) and Config.search — zero references.
A2.8: AgentResult.todo_summary — never set, never read.
A2.10: the --max-agents CLI flag, which wrote a field nothing read. Real
       sub-agent fan-out arrives with C6.2.

Kept deliberately: AgentContext.stop_on_error (read at main_agent.py:287)
and AgentConfig.verbose (read at cli.py:179).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Full verification, live smoke run, ledger close-out

**Files:**
- Modify: `TODO.md` (status marks + Section E Step 2 completion note)

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: the `DONE` marks that unblock Step 3.

- [ ] **Step 1: Run the full static gate and capture the output**

```bash
.venv/bin/ruff check src/
.venv/bin/pytest tests/ -v
grep -rn "VirtualFileSystem\|FileSyncManager\|SyncMode\|code_tools\|execution_tools" src/ tests/
grep -rn "BlockPrematureAsk\|BlockTaskTool\|ContinueAfterWrite\|EnforceTargetFile" src/ tests/
.venv/bin/python -c "import rudra.cli, rudra.agent.main_agent, rudra.agent.planner_agent, \
  rudra.agent.coder_agent, rudra.tools.planning_tools, rudra.filesystem"
.venv/bin/rudra --help
.venv/bin/rudra --version
```

Expected: ruff ≤ 164 (record the exact number — it is the new baseline for step 3); 35 passed; both greps return **nothing**; imports succeed; `--help` and `--version` exit 0.

Grep exception, matching the rule A4.9 set for the `overwrite_backend.py` deletion: **prose in a comment explaining why code exists is fine; an import, a call, or a numeric `file:line` citation into a deleted file is not.** If a hit is prose, leave it. If it is a citation with a line number into a now-deleted file, fix it in this task by quoting the relevant code inline instead.

- [ ] **Step 2: Count the lines actually removed**

```bash
git diff --stat 040dae4..HEAD -- src/ execution_tools.py
```
Expected: roughly 1,425 deletions against ~250 insertions (`tree.py` plus edits). Record the real numbers — the ledger rows in step 3 should cite measurements, not the spec's estimate.

- [ ] **Step 3: Live smoke run**

`OLLAMA_NUM_PREDICT` is **mandatory**: the repo's gitignored `.env` sets `-1`, `config.py:9`'s bare `load_dotenv()` resolves it from the module's own location regardless of cwd, and Ollama's cloud endpoint rejects a non-positive value with `max_tokens must be positive, got: -1` (A5.1).

```bash
RUDRA=/home/a/code/ai-ml/agent/RudraAnvil/.venv/bin/rudra
SMOKE=$(mktemp -d) && cd "$SMOKE" && \
OLLAMA_MODEL_PLANNER=gemma4:31b-cloud \
OLLAMA_MODEL_CODER=gemma4:31b-cloud \
OLLAMA_NUM_PREDICT=131072 \
timeout 900 "$RUDRA" "build a python CLI that reverses a string" 2>&1 | tee "$SMOKE/smoke.log"
echo "exit=$?"
ls -la "$SMOKE"
cat "$SMOKE/.rudra/PLAN.md"
```

Expected, mirroring U.12: exit 0, a non-empty `.rudra/PLAN.md` with a checked-off entry, and a non-empty generated `.py` file. **Copy the console summary line and the generated file's byte count into the ledger row** — U.12's own evidence pointers went stale because they lived only in `/tmp` (noted in that row), so the substance goes inline this time.

If the run fails for a model-availability reason rather than a Rudra reason, say so explicitly and record which. Do not mark Step 2 `DONE` on an unverified smoke run.

- [ ] **Step 4: Mark the ledger**

In `TODO.md`, set to `**DONE** 2026-08-06` with the measured evidence pasted into each row: `A2.1`, `A2.2`, `A2.3`, `A2.4`, `A2.5`, `A2.6`, `A2.7`, `A2.8`, `A2.9`, `A2.10`, `C0.3`, `A1.4`, `A1.6`, `A1.10`.

`C0.2` gets **`DONE` (partial)** with this note appended verbatim:

> Four D4 middlewares deleted. **Not fully closed:** the prompt-level ask-block at `planner_agent.py:64` (`"Do NOT call ask_user() if the task already specifies a framework or language"`) survives by design — it is §0.5 surface #6 and dies with C6.8a. `execution_tools.py`, `filesystem/sync.py`, `filesystem/virtual_fs.py` deleted here; `compat/overwrite_backend.py` was already deleted in U.3.

D7's row in the Section 0 table gets `**DONE** 2026-08-06` appended to its Consequence cell.

Section E, Step 2's row gets a completion note in the pattern Step 1 uses:

> **STEP 2 COMPLETE 2026-08-06.** `C0.2` (partial — `planner_agent.py:64` survives per §0.5 #6), `C0.3`, `A2.1`–`A2.10`, `D7`, `A1.4`, `A1.6`, `A1.10` are DONE. `<N>` lines removed from `src/`; `project_tree()` replaces `VirtualFileSystem.get_tree()`. `ruff check src/` → `<N>` errors; `pytest tests/` → `<N>` passed; live smoke run against `gemma4:31b-cloud` exited 0 with a non-empty `PLAN.md` and a non-empty generated file. **Step 3 (`A1.1, A1.5, A1.7, A1.11–A1.15, C0.4, C0.7, C0.8`) is unblocked.**

Replace every `<N>` with the real measured number. Prefer symbol names over bare line numbers in new citations — A4.10 records that hand-written line numbers in this ledger drift silently.

- [ ] **Step 5: Commit**

```bash
git add TODO.md
git commit -m "$(cat <<'EOF'
docs(todo): close out Step 2 — dead code, VFS deletion, project_tree

C0.3, A2.1-A2.10, D7, A1.4, A1.6, A1.10 all DONE with measured evidence.
C0.2 partial: planner_agent.py's prompt-level ask-block survives by design
(0.5 surface #6, dies with C6.8a).

Step 3 unblocked.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Report**

State plainly: lines removed, final ruff count vs the 164 baseline, test count, smoke-run outcome, and anything left undone with the reason. If the smoke run did not happen, say so — a skipped verification reported as done is worse than a failure reported honestly.
