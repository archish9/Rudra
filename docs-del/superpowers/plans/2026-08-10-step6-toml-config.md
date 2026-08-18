# Step 6 — Layered TOML Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Rudra's environment-variable-only configuration with a five-layer loader — builtin defaults, user TOML, project TOML, `RUDRA_*` environment, CLI flags — that records which layer set every value, and lay down the D15 `.rudra/` directory layout the later phases write into.

**Architecture:** Each layer is a function returning a plain nested `dict`. `loader.py` deep-merges them in order while recording provenance per leaf, validates the merged result once, and constructs frozen dataclasses from it. `config.py` becomes the `config/` package; `state/paths.py` owns the `.rudra/` layout that configuration consumes. The public surface (`get_config`, `reset_config`, `Config.model_for`, `ModelConfig` field names) is unchanged, so every Step 5 caller keeps working.

**Tech Stack:** Python 3.12+ (project `.venv` is 3.13) · `tomllib` (stdlib, read-only — no TOML writer dependency is added) · Typer · Rich · `python-dotenv` · pytest · ruff

**Spec:** `docs/superpowers/specs/2026-08-10-step6-toml-config-design.md` (commit `c4d6098`)

## Global Constraints

- **Session rule 2 (non-negotiable):** never fix a bug on discovery. Add it to `TODO.md` as `PENDING` with `file:line` evidence, then fix it, then mark `DONE`. Task 0 exists solely to satisfy this.
- **Session rule 3:** every claim about the codebase cites `file.py:line`. No assumptions.
- **Session rule 4:** verify before claiming done. Run the command, show the output.
- **Session rule 6:** update `TODO.md` in the same commit as the code it describes.
- **Ruff standard:** `.venv/bin/ruff check src/ tests/` must print `All checks passed!` and `.venv/bin/ruff format --check src/ tests/` must report all files formatted.
- **Test standard:** `.venv/bin/pytest -q` reports `187 passed, 2 skipped` at Step 5. It must never go down.
- **D1 — config format is TOML.** MCP stays in a separate `.mcp.json` and is not touched by this step.
- **D5 — permission mode defaults to `ask`.** Step 6 ships the surface only; enforcement is Step 7.
- **D6 — minimum local model is 32B.** No default anywhere may name a smaller model.
- **C1.5 — API keys never live in configuration.** Config names an environment *variable*; the value is read from the environment. No error message, log line, or repr may contain a key value.
- **S6.1 — `rudra config` is read-only.** No `set` subcommand, no TOML-writing dependency.
- **S6.4 — the `OLLAMA_*` deprecation shim survives this step.** Do not delete it.
- **Python version:** use `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`.
- **Live tests never run by default.** They require `RUDRA_LIVE_TESTS=1` plus a reachable backend.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/rudra/state/paths.py` | Create — D15 layout constants, `RudraPaths`, `rudra_paths()`, `ensure_layout()`, `.gitignore` writer | 1 |
| `src/rudra/config/__init__.py` | Create — public surface, re-exports; replaces `config.py` | 4 |
| `src/rudra/config/schema.py` | Create — frozen dataclasses, `DEFAULTS`, `RESERVED_SECTIONS`, valid-value sets | 2 |
| `src/rudra/config/layers.py` | Create — the five layer readers; each returns a dict, none knows about precedence | 3 |
| `src/rudra/config/loader.py` | Create — `deep_merge`, provenance, `validate`, `build_config`, `get_config`, `reset_config` | 4 |
| `src/rudra/config.py` | Delete — becomes the package above | 4 |
| `src/rudra/tools/planning_tools.py` | Modify — `run/` paths plus the agent-facing docstrings naming them | 5 |
| `src/rudra/agent/coder_agent.py` | Modify `:11,17,20,21` — prompt text naming `current_task.md` / `tech_stack.md` | 5 |
| `src/rudra/agent/main_agent.py` | Modify `:340,488,556,567` — prompt text, tech-stack writer, `plan_path=`, `rudra_dir` | 5 |
| `src/rudra/state/checkpoint.py` | Modify `:4,15,22,27` — `session_id.txt` moves to `run/` | 5 |
| `src/rudra/compat/deepagents_path.py` | Modify `:76` — docstring path | 5 |
| `src/rudra/cli.py` | Modify — `config` sub-Typer, `init`, `doctor`, permission flags, run-panel notice | 6, 7, 8, 9 |
| `tests/test_rudra_paths.py` | Create — layout, gitignore, idempotence, durable-file preservation | 1 |
| `tests/test_config_schema.py` | Create — defaults, reserved sections, valid-value sets | 2 |
| `tests/test_config_layers.py` | Create — each reader in isolation, env-name mapping | 3 |
| `tests/test_config_loader.py` | Create — precedence, per-leaf merge, role inheritance, provenance, errors | 4 |
| `tests/test_config.py` | Modify — existing Step 5 assertions kept green against the new package | 4 |
| `tests/test_rudra_dir_migration.py` | Create — no durable file moves; prompt strings match real paths | 5 |
| `tests/test_cli_config_commands.py` | Create — `init`, `config list`, `config get`, `doctor` | 6, 7, 8 |
| `tests/test_permissions_surface.py` | Create — mode parsing, flags, non-enforcement notice | 9 |
| `.env.example` | Modify — point at TOML, mark `VERBOSE` deprecated | 10 |
| `Documentation/02-configuration.md` | Rewrite — TOML is now the primary surface | 10 |
| `Documentation/04-cli-reference.md` | Modify — `init`, `config`, `doctor` | 10 |
| `Documentation/README.md` | Modify — index unchanged in shape, wording updated | 10 |
| `CLAUDE.md` | Modify §6 — mark which sections are live | 10 |
| `TODO.md` | Modify — findings in Task 0; rows closed as tasks land | 0, all |

**Boundaries:** `schema.py` imports nothing from Rudra. `layers.py` imports `schema` and `state.paths`. `loader.py` imports both. Nothing under `config/` imports `agent/`, `llm/`, or `cli.py`, so the dependency arrow points one way and the whole package is testable with no agent machinery.

---

## Task 0: Log findings in TODO.md before touching code

Session rule 2 is explicit and the owner asked for it directly. This task writes no source code.

**Files:**
- Modify: `TODO.md` (Section A ledger)

**Interfaces:**
- Consumes: nothing
- Produces: ledger IDs `A1.42`, `A1.43`, referenced by Tasks 3 and 4

- [ ] **Step 1: Find the highest allocated A1.x ID**

Run: `grep -oE '^\| A1\.[0-9]+' TODO.md | sort -t. -k2 -n | tail -3`

Expected: the highest is `A1.41`. If it is not, use the next IDs after whatever the real highest is and substitute them everywhere below.

- [ ] **Step 2: Append two PENDING rows to the Section A table**

Insert after the `A1.41` row, matching the existing column layout (`| ID | Status | Item | Evidence |`):

```markdown
| A1.42 | PENDING | **`VERBOSE` is an unprefixed environment variable and will collide with unrelated tooling.** `AgentConfig.verbose` reads a bare `VERBOSE` (`src/rudra/config.py:130`, `os.getenv("VERBOSE", "true").lower() != "false"`), unlike every other Rudra setting, all of which carry the `RUDRA_` prefix (`config.py:72-78`). `VERBOSE` is a common name — CI systems, shell profiles, and build tools set it — so a user with `VERBOSE=1` exported for an unrelated reason silently changes Rudra's output mode, and a user who unsets it for an unrelated reason silently changes it back. Found while designing Step 6. Fixed in Step 6 Task 3: `RUDRA_VERBOSE` becomes the supported name and `VERBOSE` keeps working for one release behind the same warn-once shim `OLLAMA_*` uses (`config.py:56-69`), listed for removal alongside them | `src/rudra/config.py:130` (bare `VERBOSE`) vs `:72-78` (`RUDRA_`-prefixed lookups for every other setting) |
| A1.43 | PENDING | **`Config.get_checkpoint_path` creates a directory as a side effect of a getter.** `src/rudra/config.py:172-176` calls `checkpoint_path.mkdir(parents=True, exist_ok=True)` inside a method whose name promises only a lookup, so merely asking where checkpoints *would* go creates `.rudra/` on disk. This makes read-only paths — `rudra config get`, `rudra doctor`, and any test that inspects configuration — silently mutate the filesystem, and it means a `--dry-run` or a failed early-exit still leaves a directory behind. Found while designing Step 6. Fixed in Step 6 Task 1/4: `rudra_paths()` is pure and `ensure_layout()` is the only function that creates anything, with `get_checkpoint_path` delegating to the pure one | `src/rudra/config.py:172-176` (`mkdir` inside `get_checkpoint_path`) |
```

- [ ] **Step 3: Verify both rows are present and the table still renders**

Run: `grep -c "^| A1\.4[23] " TODO.md`
Expected: `2`

- [ ] **Step 4: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): log A1.42, A1.43 — config findings found while designing Step 6"
```

---

## Task 1: The D15 `.rudra/` layout

**Files:**
- Create: `src/rudra/state/paths.py`
- Modify: `src/rudra/state/__init__.py` (export the new names)
- Test: `tests/test_rudra_paths.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `RUDRA_DIR_NAME: str = ".rudra"`
  - `@dataclass(frozen=True) RudraPaths` with fields `root, config_toml, agents_md, project_json, memory_export, memory_palace, run, checkpoints_db, plan_md, current_task_md, tech_stack_md, session_id_txt, logs` — all `Path`
  - `rudra_paths(project_root: Path) -> RudraPaths` — **pure**, creates nothing
  - `ensure_layout(project_root: Path) -> RudraPaths` — creates directories and `.rudra/.gitignore`, idempotent
  - `GITIGNORE_BODY: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rudra_paths.py`:

```python
"""The D15 .rudra/ layout: durable files at the root, volatile ones under run/."""

from pathlib import Path

from rudra.state.paths import GITIGNORE_BODY, ensure_layout, rudra_paths


def test_rudra_paths_creates_nothing(tmp_path: Path) -> None:
    """A1.43: asking where things go must not put anything on disk."""
    paths = rudra_paths(tmp_path)
    assert paths.root == tmp_path / ".rudra"
    assert not (tmp_path / ".rudra").exists()


def test_durable_files_sit_at_the_root(tmp_path: Path) -> None:
    paths = rudra_paths(tmp_path)
    assert paths.config_toml == tmp_path / ".rudra" / "config.toml"
    assert paths.agents_md == tmp_path / ".rudra" / "AGENTS.md"
    assert paths.project_json == tmp_path / ".rudra" / "project.json"
    assert paths.memory_export == tmp_path / ".rudra" / "memory" / "export"


def test_volatile_files_sit_under_run(tmp_path: Path) -> None:
    paths = rudra_paths(tmp_path)
    run = tmp_path / ".rudra" / "run"
    assert paths.run == run
    assert paths.checkpoints_db == run / "checkpoints.db"
    assert paths.plan_md == run / "PLAN.md"
    assert paths.current_task_md == run / "current_task.md"
    assert paths.tech_stack_md == run / "tech_stack.md"
    assert paths.session_id_txt == run / "session_id.txt"
    assert paths.logs == run / "logs"
    assert paths.memory_palace == tmp_path / ".rudra" / "memory" / "palace"


def test_ensure_layout_creates_directories_and_gitignore(tmp_path: Path) -> None:
    paths = ensure_layout(tmp_path)
    assert paths.run.is_dir()
    assert paths.logs.is_dir()
    assert paths.memory_export.is_dir()
    assert paths.memory_palace.is_dir()
    gitignore = paths.root / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == GITIGNORE_BODY


def test_gitignore_scopes_only_the_rudra_directory(tmp_path: Path) -> None:
    """D15: Rudra never edits the project's own .gitignore."""
    ensure_layout(tmp_path)
    assert not (tmp_path / ".gitignore").exists()
    body = (tmp_path / ".rudra" / ".gitignore").read_text(encoding="utf-8")
    assert "run/" in body
    assert "memory/palace/" in body
    # No absolute or parent-escaping patterns.
    assert ".." not in body


def test_ensure_layout_is_idempotent(tmp_path: Path) -> None:
    ensure_layout(tmp_path)
    (tmp_path / ".rudra" / "run" / "PLAN.md").write_text("- [x] main.py", encoding="utf-8")
    ensure_layout(tmp_path)
    assert (tmp_path / ".rudra" / "run" / "PLAN.md").read_text(encoding="utf-8") == "- [x] main.py"


def test_ensure_layout_preserves_a_user_edited_gitignore(tmp_path: Path) -> None:
    """Rewriting on every run would silently discard a user's additions."""
    paths = ensure_layout(tmp_path)
    gitignore = paths.root / ".gitignore"
    gitignore.write_text(GITIGNORE_BODY + "scratch/\n", encoding="utf-8")
    ensure_layout(tmp_path)
    assert "scratch/" in gitignore.read_text(encoding="utf-8")


def test_ensure_layout_does_not_touch_durable_files(tmp_path: Path) -> None:
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "project.json").write_text('{"language": "rust"}', encoding="utf-8")
    (rudra / "AGENTS.md").write_text("# notes", encoding="utf-8")
    ensure_layout(tmp_path)
    assert (rudra / "project.json").read_text(encoding="utf-8") == '{"language": "rust"}'
    assert (rudra / "AGENTS.md").read_text(encoding="utf-8") == "# notes"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rudra_paths.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.state.paths'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/state/paths.py`:

```python
"""The `.rudra/` directory layout (TODO.md D15, §0.7, C0.9).

Two subtrees with different lifetimes:

* **durable** — `config.toml`, `AGENTS.md`, `project.json`, `memory/export/`.
  Text, worth committing, safe to commit.
* **volatile** — everything under `run/`, plus `memory/palace/`. Binary or
  regenerated on every run. Never worth committing.

Rudra writes `.rudra/.gitignore` covering only the volatile subtree, and
never touches the project's own `.gitignore`: whether `.rudra/` is committed
is the user's decision (D15).

`rudra_paths()` is pure and `ensure_layout()` is the only function here that
creates anything. That split exists because a getter that mkdirs as a side
effect made read-only commands mutate the filesystem — see TODO.md A1.43.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

RUDRA_DIR_NAME = ".rudra"

GITIGNORE_BODY = """\
# Written by Rudra. Scopes only this directory — your project's own
# .gitignore is never modified.
#
# Everything below is regenerated on every run or is a binary store.
# The durable files beside it (config.toml, AGENTS.md, project.json,
# memory/export/) are deliberately NOT ignored: committing them is safe,
# and whether you do is your call. See TODO.md D15.
run/
memory/palace/
"""


@dataclass(frozen=True)
class RudraPaths:
    """Every path Rudra owns inside one project, resolved but not created."""

    root: Path
    # durable
    config_toml: Path
    agents_md: Path
    project_json: Path
    memory_export: Path
    # volatile
    memory_palace: Path
    run: Path
    checkpoints_db: Path
    plan_md: Path
    current_task_md: Path
    tech_stack_md: Path
    session_id_txt: Path
    logs: Path


def rudra_paths(project_root: Path) -> RudraPaths:
    """Resolve the layout for a project. Creates nothing (A1.43)."""
    root = Path(project_root) / RUDRA_DIR_NAME
    memory = root / "memory"
    run = root / "run"
    return RudraPaths(
        root=root,
        config_toml=root / "config.toml",
        agents_md=root / "AGENTS.md",
        project_json=root / "project.json",
        memory_export=memory / "export",
        memory_palace=memory / "palace",
        run=run,
        checkpoints_db=run / "checkpoints.db",
        plan_md=run / "PLAN.md",
        current_task_md=run / "current_task.md",
        tech_stack_md=run / "tech_stack.md",
        session_id_txt=run / "session_id.txt",
        logs=run / "logs",
    )


def ensure_layout(project_root: Path) -> RudraPaths:
    """Create the directory tree and `.gitignore`. Idempotent.

    The `.gitignore` is written only when absent, so a user who adds their
    own patterns keeps them across runs.
    """
    paths = rudra_paths(project_root)
    for directory in (paths.root, paths.run, paths.logs, paths.memory_export, paths.memory_palace):
        directory.mkdir(parents=True, exist_ok=True)

    gitignore = paths.root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_BODY, encoding="utf-8")
    return paths


__all__ = ["GITIGNORE_BODY", "RUDRA_DIR_NAME", "RudraPaths", "ensure_layout", "rudra_paths"]
```

- [ ] **Step 4: Export from the state package**

In `src/rudra/state/__init__.py`, add to the existing imports and `__all__`:

```python
from rudra.state.paths import RudraPaths, ensure_layout, rudra_paths
```

Add `"RudraPaths"`, `"ensure_layout"`, `"rudra_paths"` to `__all__`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_rudra_paths.py -q`
Expected: `8 passed`

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: `195 passed, 2 skipped`, `All checks passed!`, all files formatted

- [ ] **Step 7: Commit**

```bash
git add src/rudra/state/paths.py src/rudra/state/__init__.py tests/test_rudra_paths.py
git commit -m "feat(state): add the D15 .rudra/ layout (C0.9)"
```

---

## Task 2: Configuration schema

**Files:**
- Create: `src/rudra/config/schema.py`
- Test: `tests/test_config_schema.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `ModelConfig` — frozen dataclass, **field names identical to today's** `src/rudra/config.py:38-53`: `provider, model, base_url, api_key_env, temperature, context_tokens, max_output_tokens, timeout`
  - `AgentConfig(verbose: bool)`, `PermissionsConfig(mode: str, allow: tuple[str, ...], deny: tuple[str, ...])`, `CompatConfig(task_anchor: bool, sandbox_paths: bool)` — all frozen
  - `DEFAULTS: dict` — layer 1, the full nested shape
  - `VALID_PROVIDERS: frozenset[str]`, `VALID_MODES: tuple[str, ...]`
  - `RESERVED_SECTIONS: dict[str, str]` — section name → the step that implements it
  - `MODEL_KEYS: frozenset[str]`, `BUILTIN_ROLES: tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_schema.py`:

```python
"""The schema is data. These tests pin the values other modules rely on."""

from rudra.config.schema import (
    BUILTIN_ROLES,
    DEFAULTS,
    MODEL_KEYS,
    RESERVED_SECTIONS,
    VALID_MODES,
    VALID_PROVIDERS,
)


def test_default_model_meets_the_32b_floor() -> None:
    """D6: no default anywhere may name a model below 32B."""
    assert DEFAULTS["model"]["default"]["model"] == "qwen3:32b"


def test_default_permission_mode_is_ask() -> None:
    """D5."""
    assert DEFAULTS["permissions"]["mode"] == "ask"


def test_compat_flags_default_off() -> None:
    """D4: both surviving middlewares are opt-in."""
    assert DEFAULTS["compat"] == {"task_anchor": False, "sandbox_paths": False}


def test_every_provider_the_factory_supports_is_valid_here() -> None:
    from rudra.llm.providers import PROVIDERS

    assert VALID_PROVIDERS == frozenset(PROVIDERS)


def test_valid_modes_are_exactly_the_d5_set() -> None:
    assert VALID_MODES == ("ask", "auto", "plan")


def test_reserved_sections_name_the_step_that_implements_them() -> None:
    assert set(RESERVED_SECTIONS) == {"skills", "memory", "tools", "mcp"}
    for section, note in RESERVED_SECTIONS.items():
        assert "Step" in note, f"{section} must tell the user when it arrives"


def test_model_keys_match_the_model_config_fields() -> None:
    from dataclasses import fields

    from rudra.config.schema import ModelConfig

    assert MODEL_KEYS == frozenset(f.name for f in fields(ModelConfig))


def test_builtin_roles_are_the_step5_set() -> None:
    assert BUILTIN_ROLES == ("default", "planner", "coder")


def test_defaults_only_declare_the_default_role() -> None:
    """Roles inherit; shipping per-role defaults would defeat that."""
    assert set(DEFAULTS["model"]) == {"default"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.config.schema'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/config/schema.py`:

```python
"""Configuration data: the dataclasses, the builtin defaults, the valid values.

This module imports nothing from Rudra except the provider registry, so it
can be read and tested without any agent machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from rudra.llm.providers import PROVIDERS

BUILTIN_ROLES = ("default", "planner", "coder")

VALID_PROVIDERS = frozenset(PROVIDERS)
VALID_MODES = ("ask", "auto", "plan")

RESERVED_SECTIONS = {
    "skills": "not supported yet — arrives in Step 11 (C5.1)",
    "memory": "not supported yet — arrives in Step 14 (C8.1)",
    "tools": "not supported yet — arrives in Step 7 (C3.1)",
    "mcp": "MCP is configured in a separate .mcp.json, not here — arrives in Step 13 (C4.2)",
}


@dataclass(frozen=True)
class ModelConfig:
    """Everything needed to construct one role's chat model.

    `api_key_env` names an environment variable. The key value itself is never
    stored here, never logged, and never written to a config file (C1.5).

    Field names are unchanged from Step 5 so `llm/factory.py` keeps working.
    """

    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None
    temperature: float | None
    context_tokens: int | None
    max_output_tokens: int | None
    timeout: int | None


@dataclass(frozen=True)
class AgentConfig:
    verbose: bool


@dataclass(frozen=True)
class PermissionsConfig:
    """Parsed but NOT enforced in Step 6 — enforcement is Step 7 (C3.3, U.7).

    `allow` and `deny` are shape-validated and stored; nothing reads them yet.
    `rudra doctor` says so explicitly rather than leaving the user to assume.
    """

    mode: str
    allow: tuple[str, ...]
    deny: tuple[str, ...]


@dataclass(frozen=True)
class CompatConfig:
    """The two middlewares D4 kept, both opt-in."""

    task_anchor: bool
    sandbox_paths: bool


MODEL_KEYS = frozenset(f.name for f in fields(ModelConfig))

DEFAULTS: dict[str, Any] = {
    "model": {
        "default": {
            "provider": "ollama",
            "model": "qwen3:32b",
            "base_url": "http://localhost:11434",
            "api_key_env": None,
            "temperature": 0.3,
            "context_tokens": None,
            "max_output_tokens": 131072,
            "timeout": 300,
        }
    },
    "agent": {"verbose": True},
    "permissions": {"mode": "ask", "allow": [], "deny": []},
    "compat": {"task_anchor": False, "sandbox_paths": False},
}

__all__ = [
    "BUILTIN_ROLES",
    "DEFAULTS",
    "MODEL_KEYS",
    "RESERVED_SECTIONS",
    "VALID_MODES",
    "VALID_PROVIDERS",
    "AgentConfig",
    "CompatConfig",
    "ModelConfig",
    "PermissionsConfig",
]
```

- [ ] **Step 4: Create the package `__init__.py` as a stub so imports resolve**

Create `src/rudra/config/__init__.py` with a single line for now; Task 4 fills it in:

```python
"""Layered configuration (TODO.md C2.1). Public surface lands in Task 4."""
```

Note: `src/rudra/config.py` still exists at this point. A module and a package of the same name cannot coexist — **delete `src/rudra/config.py` in Task 4, not here.** Until then, run this task's tests with the package shadowed by moving the old file aside only if imports fail; if `.venv/bin/pytest tests/test_config_schema.py -q` passes as written, leave it alone.

If the collision does bite, do the move now instead of in Task 4:

```bash
git mv src/rudra/config.py src/rudra/config/_legacy.py
```

and re-export its names from `src/rudra/config/__init__.py` with `from rudra.config._legacy import *  # noqa: F403` until Task 4 removes it.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config_schema.py -q`
Expected: `9 passed`

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/`
Expected: no decrease from `195 passed, 2 skipped`; `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/rudra/config/ tests/test_config_schema.py
git commit -m "feat(config): add the TOML schema, defaults, and reserved sections"
```

---

## Task 3: The five layer readers

**Files:**
- Create: `src/rudra/config/layers.py`
- Test: `tests/test_config_layers.py`

**Interfaces:**
- Consumes: `rudra.config.schema` (`DEFAULTS`, `BUILTIN_ROLES`, `MODEL_KEYS`), `rudra.state.paths.rudra_paths`
- Produces:
  - `builtin_layer() -> dict`
  - `user_toml_path() -> Path` — `$XDG_CONFIG_HOME/rudra/config.toml`, else `~/.config/rudra/config.toml`
  - `read_toml(path: Path) -> dict` — `{}` when absent; raises `ConfigError` on syntax or permission failure
  - `user_toml_layer() -> dict`
  - `project_toml_layer(project_root: Path) -> dict`
  - `env_layer(known_roles: Iterable[str]) -> dict`
  - `cli_layer(verbose: bool | None, permission_mode: str | None) -> dict`
  - `LAYER_NAMES: tuple[str, ...] = ("builtin", "user", "project", "env", "cli")`
  - `ConfigError(Exception)` — defined here, re-exported by `loader`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_layers.py`:

```python
"""Each layer reader in isolation. None of them knows about precedence."""

from pathlib import Path

import pytest

from rudra.config.layers import (
    ConfigError,
    builtin_layer,
    cli_layer,
    env_layer,
    project_toml_layer,
    read_toml,
    user_toml_path,
)


def test_builtin_layer_is_a_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutating a returned layer must not poison DEFAULTS for the next call."""
    first = builtin_layer()
    first["model"]["default"]["model"] = "tampered"
    assert builtin_layer()["model"]["default"]["model"] == "qwen3:32b"


def test_read_toml_returns_empty_for_a_missing_file(tmp_path: Path) -> None:
    assert read_toml(tmp_path / "nope.toml") == {}


def test_read_toml_parses_a_real_file(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text('[model.planner]\nmodel = "x"\n', encoding="utf-8")
    assert read_toml(target) == {"model": {"planner": {"model": "x"}}}


def test_read_toml_names_the_file_and_line_on_a_syntax_error(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text("[model\nmodel = 'x'\n", encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        read_toml(target)
    message = str(excinfo.value)
    assert str(target) in message
    assert "line" in message.lower()


def test_user_toml_path_honors_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert user_toml_path() == tmp_path / "rudra" / "config.toml"


def test_user_toml_path_falls_back_to_dot_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert user_toml_path() == Path.home() / ".config" / "rudra" / "config.toml"


def test_project_toml_layer_reads_the_d15_location(tmp_path: Path) -> None:
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "config.toml").write_text('[agent]\nverbose = false\n', encoding="utf-8")
    assert project_toml_layer(tmp_path) == {"agent": {"verbose": False}}


def test_env_layer_maps_bare_names_to_the_default_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUDRA_MODEL", "bare")
    assert env_layer(("default", "planner", "coder"))["model"]["default"]["model"] == "bare"


def test_env_layer_maps_role_scoped_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_PLANNER_MODEL", "scoped")
    layer = env_layer(("default", "planner", "coder"))
    assert layer["model"]["planner"]["model"] == "scoped"
    assert "default" not in layer.get("model", {})


def test_env_layer_recognises_a_role_declared_only_in_toml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declaring [model.reviewer] makes RUDRA_REVIEWER_MODEL work, no code change."""
    monkeypatch.setenv("RUDRA_REVIEWER_MODEL", "judge")
    layer = env_layer(("default", "planner", "coder", "reviewer"))
    assert layer["model"]["reviewer"]["model"] == "judge"


def test_env_layer_without_that_role_does_not_invent_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUDRA_REVIEWER_MODEL", "judge")
    layer = env_layer(("default", "planner", "coder"))
    assert "reviewer" not in layer.get("model", {})


def test_env_layer_maps_non_model_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_VERBOSE", "false")
    monkeypatch.setenv("RUDRA_PERMISSIONS_MODE", "auto")
    layer = env_layer(("default",))
    assert layer["agent"]["verbose"] is False
    assert layer["permissions"]["mode"] == "auto"


def test_bare_verbose_still_works_and_warns_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A1.42: VERBOSE is unprefixed and collision-prone. One release of grace."""
    from rudra.config.layers import _reset_deprecation_warnings

    _reset_deprecation_warnings()
    monkeypatch.delenv("RUDRA_VERBOSE", raising=False)
    monkeypatch.setenv("VERBOSE", "false")
    with pytest.warns(DeprecationWarning, match="RUDRA_VERBOSE"):
        assert env_layer(("default",))["agent"]["verbose"] is False
    import warnings as _w

    with _w.catch_warnings():
        _w.simplefilter("error")
        env_layer(("default",))  # second call must not warn again


def test_rudra_verbose_beats_bare_verbose(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VERBOSE", "false")
    monkeypatch.setenv("RUDRA_VERBOSE", "true")
    assert env_layer(("default",))["agent"]["verbose"] is True


def test_ollama_shim_still_populates_the_default_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S6.4: the OLLAMA_* shim survives Step 6."""
    from rudra.config.layers import _reset_deprecation_warnings

    _reset_deprecation_warnings()
    monkeypatch.setenv("OLLAMA_MODEL", "legacy:32b")
    with pytest.warns(DeprecationWarning, match="RUDRA_MODEL"):
        layer = env_layer(("default",))
    assert layer["model"]["default"]["model"] == "legacy:32b"


def test_cli_layer_omits_unset_flags() -> None:
    assert cli_layer(verbose=None, permission_mode=None) == {}


def test_cli_layer_carries_set_flags() -> None:
    layer = cli_layer(verbose=True, permission_mode="auto")
    assert layer == {"agent": {"verbose": True}, "permissions": {"mode": "auto"}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_layers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.config.layers'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/config/layers.py`:

```python
"""The five configuration layers (TODO.md C2.1).

Each reader returns a plain nested dict in the same shape. None of them
knows about precedence — that lives entirely in `loader.deep_merge`, which
is the point: two of this project's three config defects (A5.1, A5.2) were
"which source won?" questions that per-field resolver chains could not
answer.
"""

from __future__ import annotations

import copy
import os
import tomllib
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rudra.config.schema import DEFAULTS, MODEL_KEYS
from rudra.state.paths import rudra_paths

LAYER_NAMES = ("builtin", "user", "project", "env", "cli")

# OLLAMA_* -> the RUDRA_* name that replaced it (C1.3, kept one more release
# by S6.4). VERBOSE joins them per A1.42.
_DEPRECATED_VARS = {
    "OLLAMA_BASE_URL": "RUDRA_BASE_URL",
    "OLLAMA_MODEL": "RUDRA_MODEL",
    "OLLAMA_MODEL_PLANNER": "RUDRA_PLANNER_MODEL",
    "OLLAMA_MODEL_CODER": "RUDRA_CODER_MODEL",
    "OLLAMA_TEMPERATURE": "RUDRA_TEMPERATURE",
    "OLLAMA_TIMEOUT": "RUDRA_TIMEOUT",
    "OLLAMA_NUM_PREDICT": "RUDRA_MAX_OUTPUT_TOKENS",
    "VERBOSE": "RUDRA_VERBOSE",
}

_warned_vars: set[str] = set()
"""Deduped explicitly rather than via the warnings module's per-location
filter, so "warns once per variable" is deterministically testable."""


def _reset_deprecation_warnings() -> None:
    """Test support: let a later test observe the first warning again."""
    _warned_vars.clear()


class ConfigError(Exception):
    """A configuration file is unreadable, malformed, or invalid.

    Always names the offending file, and the line where the source gives one.
    """


def _legacy(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    if name not in _warned_vars:
        _warned_vars.add(name)
        warnings.warn(
            f"{name} is deprecated; use {_DEPRECATED_VARS[name]} instead. "
            f"It will be removed in the next release.",
            DeprecationWarning,
            stacklevel=4,
        )
    return value


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() not in {"false", "0", "no", "off", ""}


def _as_number(raw: str) -> Any:
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def builtin_layer() -> dict[str, Any]:
    """Layer 1. A deep copy — callers merge into their result in place."""
    return copy.deepcopy(DEFAULTS)


def user_toml_path() -> Path:
    """Layer 2's location: `$XDG_CONFIG_HOME/rudra/`, else `~/.config/rudra/`."""
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "rudra" / "config.toml"


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file. Absent is `{}`; unreadable or malformed raises.

    A file that exists but cannot be read is never silently skipped — that
    would hand the user a config which looks applied and is not.
    """
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML — {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot be read — {exc}") from exc


def user_toml_layer() -> dict[str, Any]:
    """Layer 2."""
    return read_toml(user_toml_path())


def project_toml_layer(project_root: Path) -> dict[str, Any]:
    """Layer 3."""
    return read_toml(rudra_paths(project_root).config_toml)


def _split_role_and_suffix(tail: str, known_roles: Iterable[str]) -> tuple[str, str]:
    """Map `PLANNER_MODEL` -> ("planner", "model"), `MODEL` -> ("default", "model").

    Roles are matched first, which is what `config.py:72-78` did before this
    rewrite. Ambiguity is real: `RUDRA_PLANNER_MODEL` could be a suffix named
    `PLANNER_MODEL`, and only the known-role list settles it.
    """
    lowered = tail.lower()
    for role in known_roles:
        prefix = f"{role}_"
        if role != "default" and lowered.startswith(prefix):
            return role, lowered[len(prefix) :]
    return "default", lowered


def env_layer(known_roles: Iterable[str]) -> dict[str, Any]:
    """Layer 4: `RUDRA_*`, plus the deprecated shims.

    `known_roles` is the builtin set union whatever roles the merged TOML
    declared, so `[model.reviewer]` makes `RUDRA_REVIEWER_MODEL` work with no
    code change here.
    """
    roles = tuple(known_roles)
    layer: dict[str, Any] = {}

    def put(section: str, *path: str, value: Any) -> None:
        node = layer.setdefault(section, {})
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    for name, raw in os.environ.items():
        if not name.startswith("RUDRA_") or name == "RUDRA_LIVE_TESTS":
            continue
        tail = name[len("RUDRA_") :]
        if tail == "VERBOSE":
            put("agent", "verbose", value=_as_bool(raw))
            continue
        if tail == "PERMISSIONS_MODE":
            put("permissions", "mode", value=raw.strip())
            continue
        role, suffix = _split_role_and_suffix(tail, roles)
        if suffix in MODEL_KEYS:
            value: Any = raw
            if suffix in {"temperature"}:
                value = _as_number(raw)
            elif suffix in {"context_tokens", "max_output_tokens", "timeout"}:
                value = _as_number(raw)
            put("model", role, suffix, value=value)

    # Deprecated shims, applied only where the modern name did not win.
    legacy_model = _legacy("OLLAMA_MODEL")
    legacy_pairs: list[tuple[str, str, Any]] = [
        ("default", "base_url", _legacy("OLLAMA_BASE_URL")),
        ("default", "model", legacy_model),
        ("planner", "model", _legacy("OLLAMA_MODEL_PLANNER") or legacy_model),
        ("coder", "model", _legacy("OLLAMA_MODEL_CODER") or legacy_model),
        ("default", "temperature", _legacy("OLLAMA_TEMPERATURE")),
        ("default", "timeout", _legacy("OLLAMA_TIMEOUT")),
        ("default", "max_output_tokens", _legacy("OLLAMA_NUM_PREDICT")),
    ]
    for role, key, value in legacy_pairs:
        if value is None:
            continue
        if layer.get("model", {}).get(role, {}).get(key) is not None:
            continue
        put("model", role, key, value=_as_number(value) if key != "model" else value)

    if "RUDRA_VERBOSE" not in os.environ:
        legacy_verbose = _legacy("VERBOSE")
        if legacy_verbose is not None:
            put("agent", "verbose", value=_as_bool(legacy_verbose))

    return layer


def cli_layer(verbose: bool | None, permission_mode: str | None) -> dict[str, Any]:
    """Layer 5. Only flags the user actually passed appear."""
    layer: dict[str, Any] = {}
    if verbose is not None:
        layer["agent"] = {"verbose": verbose}
    if permission_mode is not None:
        layer["permissions"] = {"mode": permission_mode}
    return layer


__all__ = [
    "LAYER_NAMES",
    "ConfigError",
    "builtin_layer",
    "cli_layer",
    "env_layer",
    "project_toml_layer",
    "read_toml",
    "user_toml_layer",
    "user_toml_path",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config_layers.py -q`
Expected: `17 passed`

- [ ] **Step 5: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/`
Expected: no decrease; `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/rudra/config/layers.py tests/test_config_layers.py
git commit -m "feat(config): add the five layer readers (C2.1)

VERBOSE joins the deprecation shim per A1.42."
```

---

## Task 4: Merge, provenance, validation, and the public surface

This is the task that deletes `src/rudra/config.py`. Every existing caller must keep working unmodified.

**Files:**
- Create: `src/rudra/config/loader.py`
- Modify: `src/rudra/config/__init__.py` (real public surface)
- Delete: `src/rudra/config.py`
- Test: `tests/test_config_loader.py`; modify `tests/test_config.py`

**Interfaces:**
- Consumes: `layers` (all five readers, `ConfigError`, `LAYER_NAMES`), `schema` (everything)
- Produces:
  - `deep_merge(layers: list[tuple[str, dict]]) -> tuple[dict, dict[str, str]]` — merged values, and dotted-key → winning layer name
  - `validate(merged: dict, provenance: dict[str, str]) -> None` — raises `ConfigError`
  - `Config` — frozen; `.agent`, `.permissions`, `.compat`, `.models`, `.provenance`, `.project_root`, `.sources`
  - `Config.model_for(role: str) -> ModelConfig` — **unchanged signature and fallback semantics**
  - `Config.get_checkpoint_path(project_dir: Path) -> Path` — now pure (A1.43)
  - `get_config(project_root=None, *, verbose=None, permission_mode=None) -> Config`
  - `reset_config() -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_loader.py`:

```python
"""Precedence, provenance, and validation — the heart of C2.1."""

from pathlib import Path

import pytest

from rudra.config import Config, ConfigError, get_config, reset_config
from rudra.config.loader import deep_merge


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """No ambient config may reach these tests."""
    from rudra.config.layers import _reset_deprecation_warnings

    for name in list(__import__("os").environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _reset_deprecation_warnings()
    reset_config()
    yield
    reset_config()


def _write_project_toml(root: Path, body: str) -> None:
    (root / ".rudra").mkdir(parents=True, exist_ok=True)
    (root / ".rudra" / "config.toml").write_text(body, encoding="utf-8")


def _write_user_toml(tmp_path: Path, body: str) -> None:
    target = tmp_path / "xdg" / "rudra"
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.toml").write_text(body, encoding="utf-8")


def test_merge_is_per_leaf_not_per_table() -> None:
    merged, _ = deep_merge(
        [
            ("user", {"model": {"planner": {"temperature": 0.5, "model": "a"}}}),
            ("project", {"model": {"planner": {"model": "b"}}}),
        ]
    )
    assert merged["model"]["planner"] == {"temperature": 0.5, "model": "b"}


def test_provenance_names_the_winning_layer() -> None:
    _, provenance = deep_merge(
        [
            ("builtin", {"agent": {"verbose": True}}),
            ("project", {"agent": {"verbose": False}}),
        ]
    )
    assert provenance["agent.verbose"] == "project"


def test_project_toml_beats_user_toml(tmp_path: Path) -> None:
    _write_user_toml(tmp_path, '[model.default]\nmodel = "from-user"\n')
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.default]\nmodel = "from-project"\n')
    cfg = get_config(project)
    assert cfg.model_for("default").model == "from-project"
    assert cfg.provenance["model.default.model"] == "project"


def test_env_beats_project_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.default]\nmodel = "from-project"\n')
    monkeypatch.setenv("RUDRA_MODEL", "from-env")
    cfg = get_config(project)
    assert cfg.model_for("default").model == "from-env"
    assert cfg.provenance["model.default.model"] == "env"


def test_cli_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_VERBOSE", "true")
    cfg = get_config(tmp_path, verbose=False)
    assert cfg.agent.verbose is False
    assert cfg.provenance["agent.verbose"] == "cli"


def test_role_inheritance_happens_after_the_cross_layer_merge(tmp_path: Path) -> None:
    """The subtle one. A project [model.planner] must still inherit a
    user-level [model.default]."""
    _write_user_toml(tmp_path, '[model.default]\ntemperature = 0.9\n')
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.planner]\nmodel = "planner-only"\n')
    planner = get_config(project).model_for("planner")
    assert planner.model == "planner-only"
    assert planner.temperature == 0.9


def test_an_undeclared_role_falls_back_to_default(tmp_path: Path) -> None:
    """Step 9 calls build_model("reviewer") before reviewer config exists."""
    cfg = get_config(tmp_path)
    assert cfg.model_for("reviewer") == cfg.model_for("default")


def test_a_toml_declared_role_is_addressable_by_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.reviewer]\nmodel = "declared"\n')
    monkeypatch.setenv("RUDRA_REVIEWER_MODEL", "env-wins")
    assert get_config(project).model_for("reviewer").model == "env-wins"


def test_unknown_key_is_a_hard_error_naming_the_nearest_match(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.default]\ntempreature = 0.3\n')
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    message = str(excinfo.value)
    assert "tempreature" in message
    assert "temperature" in message
    assert "config.toml" in message


def test_reserved_section_names_the_step_that_implements_it(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[skills]\nsuperpowers = true\n')
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    assert "Step 11" in str(excinfo.value)


def test_invalid_provider_lists_the_valid_ones(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[model.default]\nprovider = "banana"\n')
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    message = str(excinfo.value)
    assert "banana" in message
    assert "openai_compatible" in message


def test_invalid_permission_mode_lists_the_valid_ones(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[permissions]\nmode = "yolo"\n')
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    message = str(excinfo.value)
    assert "yolo" in message
    assert "ask" in message


def test_negative_context_tokens_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[model.default]\ncontext_tokens = -5\n")
    with pytest.raises(ConfigError, match="context_tokens"):
        get_config(project)


def test_allow_and_deny_must_be_lists_of_strings(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, "[permissions]\nallow = 5\n")
    with pytest.raises(ConfigError, match="allow"):
        get_config(project)


def test_an_api_key_value_never_appears_in_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1.5, asserted as a property rather than assumed."""
    monkeypatch.setenv("MY_SECRET", "sk-do-not-leak-me")
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(
        project, '[model.default]\napi_key_env = "MY_SECRET"\nprovider = "banana"\n'
    )
    with pytest.raises(ConfigError) as excinfo:
        get_config(project)
    assert "sk-do-not-leak-me" not in str(excinfo.value)


def test_project_dir_selects_the_project_toml(tmp_path: Path) -> None:
    """A5.2, extended from .env to config.toml."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write_project_toml(elsewhere, '[model.default]\nmodel = "the-right-one"\n')
    cwd_project = tmp_path / "cwd"
    cwd_project.mkdir()
    _write_project_toml(cwd_project, '[model.default]\nmodel = "the-wrong-one"\n')
    assert get_config(elsewhere).model_for("default").model == "the-right-one"


def test_sources_records_which_files_were_found(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    _write_project_toml(project, '[agent]\nverbose = false\n')
    cfg = get_config(project)
    assert cfg.sources["project"] == project / ".rudra" / "config.toml"
    assert cfg.sources["user"] is None


def test_get_checkpoint_path_creates_nothing(tmp_path: Path) -> None:
    """A1.43."""
    cfg = get_config(tmp_path)
    path = cfg.get_checkpoint_path(tmp_path)
    assert path == tmp_path / ".rudra" / "run" / "checkpoints.db"
    assert not (tmp_path / ".rudra").exists()


def test_config_is_cached_until_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = get_config(tmp_path)
    monkeypatch.setenv("RUDRA_MODEL", "changed")
    assert get_config(tmp_path) is first
    reset_config()
    assert get_config(tmp_path).model_for("default").model == "changed"


def test_config_is_frozen(tmp_path: Path) -> None:
    cfg = get_config(tmp_path)
    with pytest.raises(Exception):
        cfg.agent = None  # type: ignore[misc]
    assert isinstance(cfg, Config)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config_loader.py -q`
Expected: FAIL — `ImportError: cannot import name 'ConfigError' from 'rudra.config'`

- [ ] **Step 3: Write the loader**

Create `src/rudra/config/loader.py`:

```python
"""Merge the layers, record provenance, validate once, build the dataclasses.

Precedence exists in exactly one place — `deep_merge` — and every value
carries the name of the layer that set it. That is the whole reason this
module replaced a chain of per-field `x or y or default` lookups: A5.1 and
A5.2 were both "which source won?" defects, invisible to the old shape.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from rudra.config.layers import (
    ConfigError,
    builtin_layer,
    cli_layer,
    env_layer,
    project_toml_layer,
    read_toml,
    user_toml_path,
)
from rudra.config.schema import (
    BUILTIN_ROLES,
    MODEL_KEYS,
    RESERVED_SECTIONS,
    VALID_MODES,
    VALID_PROVIDERS,
    AgentConfig,
    CompatConfig,
    ModelConfig,
    PermissionsConfig,
)
from rudra.state.paths import rudra_paths

_TOP_LEVEL = ("model", "agent", "permissions", "compat")
_AGENT_KEYS = frozenset({"verbose"})
_PERMISSION_KEYS = frozenset({"mode", "allow", "deny"})
_COMPAT_KEYS = frozenset({"task_anchor", "sandbox_paths"})
_POSITIVE_INT_KEYS = ("context_tokens", "max_output_tokens", "timeout")


def deep_merge(layers: list[tuple[str, dict]]) -> tuple[dict[str, Any], dict[str, str]]:
    """Merge layer dicts in order, per leaf, recording who set what.

    Returns the merged mapping and a dotted-key -> layer-name provenance map.
    Merging per leaf (not per table) is what lets a project override one key
    of `[model.planner]` without erasing its siblings from the user file.
    """
    merged: dict[str, Any] = {}
    provenance: dict[str, str] = {}

    def walk(target: dict[str, Any], source: dict[str, Any], layer: str, prefix: str) -> None:
        for key, value in source.items():
            dotted = f"{prefix}{key}"
            if isinstance(value, dict):
                node = target.setdefault(key, {})
                if not isinstance(node, dict):
                    node = {}
                    target[key] = node
                walk(node, value, layer, f"{dotted}.")
            else:
                target[key] = value
                provenance[dotted] = layer

    for name, layer_dict in layers:
        walk(merged, layer_dict, name, "")
    return merged, provenance


def _where(provenance: dict[str, str], sources: dict[str, Path | None], dotted: str) -> str:
    layer = provenance.get(dotted)
    path = sources.get(layer) if layer else None
    return f"{path}" if path else f"the {layer} layer" if layer else "configuration"


def _suggest(unknown: str, valid: frozenset[str] | tuple[str, ...]) -> str:
    close = difflib.get_close_matches(unknown, sorted(valid), n=1)
    return f" Did you mean '{close[0]}'?" if close else f" Valid keys: {', '.join(sorted(valid))}."


def validate(
    merged: dict[str, Any], provenance: dict[str, str], sources: dict[str, Path | None]
) -> None:
    """Reject anything the rest of the program would otherwise misread.

    Unknown keys are fatal because the common case is a typo, and silently
    ignoring one means the user's setting never applies with no signal.
    """
    for section in merged:
        if section in RESERVED_SECTIONS:
            raise ConfigError(f"[{section}] is {RESERVED_SECTIONS[section]}.")
        if section not in _TOP_LEVEL:
            raise ConfigError(f"Unknown section [{section}].{_suggest(section, _TOP_LEVEL)}")

    for role, settings in merged.get("model", {}).items():
        if not isinstance(settings, dict):
            raise ConfigError(f"[model.{role}] must be a table of settings, not {type(settings).__name__}.")
        for key, value in settings.items():
            dotted = f"model.{role}.{key}"
            if key not in MODEL_KEYS:
                raise ConfigError(
                    f"Unknown key '{key}' in [model.{role}] "
                    f"({_where(provenance, sources, dotted)}).{_suggest(key, MODEL_KEYS)}"
                )
            if key == "provider" and value not in VALID_PROVIDERS:
                raise ConfigError(
                    f"Unknown provider '{value}' in [model.{role}] "
                    f"({_where(provenance, sources, dotted)}). "
                    f"Valid providers: {', '.join(sorted(VALID_PROVIDERS))}."
                )
            if key in _POSITIVE_INT_KEYS and value is not None:
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    raise ConfigError(
                        f"{key} in [model.{role}] must be a positive integer, got {value!r} "
                        f"({_where(provenance, sources, dotted)})."
                    )
            if key == "temperature" and value is not None:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ConfigError(
                        f"temperature in [model.{role}] must be a number, got {value!r}."
                    )

    for key in merged.get("agent", {}):
        if key not in _AGENT_KEYS:
            raise ConfigError(f"Unknown key '{key}' in [agent].{_suggest(key, _AGENT_KEYS)}")

    permissions = merged.get("permissions", {})
    for key in permissions:
        if key not in _PERMISSION_KEYS:
            raise ConfigError(
                f"Unknown key '{key}' in [permissions].{_suggest(key, _PERMISSION_KEYS)}"
            )
    mode = permissions.get("mode")
    if mode not in VALID_MODES:
        raise ConfigError(
            f"Unknown permissions mode '{mode}' "
            f"({_where(provenance, sources, 'permissions.mode')}). "
            f"Valid modes: {', '.join(VALID_MODES)}."
        )
    for key in ("allow", "deny"):
        value = permissions.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigError(f"[permissions] {key} must be a list of strings, got {value!r}.")

    for key, value in merged.get("compat", {}).items():
        if key not in _COMPAT_KEYS:
            raise ConfigError(f"Unknown key '{key}' in [compat].{_suggest(key, _COMPAT_KEYS)}")
        if not isinstance(value, bool):
            raise ConfigError(f"[compat] {key} must be true or false, got {value!r}.")


@dataclass(frozen=True)
class Config:
    """Fully resolved configuration for one project."""

    agent: AgentConfig
    permissions: PermissionsConfig
    compat: CompatConfig
    models: dict[str, ModelConfig]
    provenance: dict[str, str] = field(default_factory=dict)
    sources: dict[str, Path | None] = field(default_factory=dict)
    project_root: Path | None = None

    def model_for(self, role: str) -> ModelConfig:
        """Settings for a role, falling back to `default` for unknown roles.

        Step 9 calls build_model("reviewer") before any reviewer config
        exists. Falling back beats raising, and beats shipping config keys
        for roles with no consumer yet.
        """
        return self.models.get(role, self.models["default"])

    def get_checkpoint_path(self, project_dir: Path) -> Path:
        """Where checkpoints live. Creates nothing — see TODO.md A1.43."""
        return rudra_paths(project_dir).checkpoints_db


def _build_models(merged: dict[str, Any]) -> dict[str, ModelConfig]:
    """Apply role inheritance AFTER the cross-layer merge.

    Doing it per layer would mean a project-level [model.planner] fails to
    inherit a user-level [model.default].
    """
    table = merged.get("model", {})
    base = dict(table.get("default", {}))
    roles = {*BUILTIN_ROLES, *table}
    models: dict[str, ModelConfig] = {}
    for role in roles:
        settings = {**base, **table.get(role, {})}
        models[role] = ModelConfig(**{key: settings.get(key) for key in MODEL_KEYS})
    return models


def build_config(
    project_root: Path | None = None,
    *,
    verbose: bool | None = None,
    permission_mode: str | None = None,
) -> Config:
    """Read all five layers, merge, validate, construct."""
    root = Path(project_root) if project_root is not None else Path.cwd()

    # .env feeds layer 4 only, read from the project root with override=False
    # so a real environment variable still beats it (A5.1).
    load_dotenv(root / ".env")

    user_path = user_toml_path()
    user = read_toml(user_path)
    project = project_toml_layer(root)

    # Roles the env layer must recognise = builtin plus whatever TOML declared.
    declared = {*BUILTIN_ROLES, *user.get("model", {}), *project.get("model", {})}

    ordered = [
        ("builtin", builtin_layer()),
        ("user", user),
        ("project", project),
        ("env", env_layer(declared)),
        ("cli", cli_layer(verbose, permission_mode)),
    ]
    merged, provenance = deep_merge(ordered)

    project_toml = rudra_paths(root).config_toml
    sources: dict[str, Path | None] = {
        "builtin": None,
        "user": user_path if user_path.exists() else None,
        "project": project_toml if project_toml.exists() else None,
        "env": None,
        "cli": None,
    }

    validate(merged, provenance, sources)

    permissions = merged.get("permissions", {})
    return Config(
        agent=AgentConfig(verbose=bool(merged.get("agent", {}).get("verbose", True))),
        permissions=PermissionsConfig(
            mode=permissions.get("mode", "ask"),
            allow=tuple(permissions.get("allow", [])),
            deny=tuple(permissions.get("deny", [])),
        ),
        compat=CompatConfig(**merged.get("compat", {})),
        models=_build_models(merged),
        provenance=provenance,
        sources=sources,
        project_root=root,
    )


_config: Config | None = None


def get_config(
    project_root: Path | None = None,
    *,
    verbose: bool | None = None,
    permission_mode: str | None = None,
) -> Config:
    """Return the process-wide Config, loading it on first use.

    `project_root` is honored only on the call that actually constructs the
    Config — which is why cli.py must resolve the project path before its
    first get_config() call (TODO.md A5.2).
    """
    global _config
    if _config is None:
        _config = build_config(project_root, verbose=verbose, permission_mode=permission_mode)
    return _config


def reset_config() -> None:
    """Drop the cached Config. Test support, and the REPL's `/reload`."""
    global _config
    _config = None


__all__ = [
    "Config",
    "ConfigError",
    "build_config",
    "deep_merge",
    "get_config",
    "reset_config",
    "validate",
]
```

- [ ] **Step 4: Write the package's public surface**

Replace `src/rudra/config/__init__.py` entirely:

```python
"""Layered configuration (TODO.md C2.1, D1).

Load order, later overriding earlier:

1. built-in defaults          (schema.DEFAULTS)
2. ~/.config/rudra/config.toml
3. <project>/.rudra/config.toml
4. RUDRA_* environment variables (including .env)
5. CLI flags

Every resolved value records which layer set it — see `Config.provenance`
and `rudra config list`.
"""

from rudra.config.layers import ConfigError, user_toml_path
from rudra.config.loader import Config, build_config, get_config, reset_config
from rudra.config.schema import (
    AgentConfig,
    CompatConfig,
    ModelConfig,
    PermissionsConfig,
)

__all__ = [
    "AgentConfig",
    "CompatConfig",
    "Config",
    "ConfigError",
    "ModelConfig",
    "PermissionsConfig",
    "build_config",
    "get_config",
    "reset_config",
    "user_toml_path",
]
```

- [ ] **Step 5: Delete the old module**

```bash
git rm src/rudra/config.py
```

- [ ] **Step 6: Update `tests/test_config.py` to the new surface**

The Step 5 assertions stay meaningful and must keep passing. Two changes only:

1. `from rudra.config import get_config, reset_config` — unchanged, the package re-exports both.
2. Any test asserting `cfg.ollama` or importing `OllamaConfig` was already removed in Step 5; if a test constructs `Config()` with no arguments, change it to `build_config(tmp_path)`.

Run: `.venv/bin/pytest tests/test_config.py -q` and fix only what the failures name. Do not weaken an assertion to make it pass — if a Step 5 guarantee genuinely broke, that is a real regression.

- [ ] **Step 7: Run the new tests**

Run: `.venv/bin/pytest tests/test_config_loader.py -q`
Expected: `21 passed`

- [ ] **Step 8: Run the full suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: no decrease from the running total; `All checks passed!`

- [ ] **Step 9: Verify no caller still imports the deleted module path**

Run: `grep -rn "from rudra.config import\|import rudra.config" src/ tests/`
Expected: every hit resolves against the package — no `rudra.config.OllamaConfig`, no `rudra.config.config`.

- [ ] **Step 10: Commit**

```bash
git add -A src/rudra/config tests/test_config_loader.py tests/test_config.py
git commit -m "feat(config): layered loader with provenance and validation (C2.1)

Replaces config.py with the config/ package. Public surface is unchanged,
so every Step 5 caller keeps working. Closes A1.43 — get_checkpoint_path
no longer mkdirs as a side effect."
```

---

## Task 5: Migrate `.rudra/` consumers to the D15 layout

**Files:**
- Modify: `src/rudra/tools/planning_tools.py`, `src/rudra/agent/coder_agent.py`, `src/rudra/agent/main_agent.py`, `src/rudra/state/checkpoint.py`, `src/rudra/state/project_config.py`, `src/rudra/compat/deepagents_path.py`
- Test: `tests/test_rudra_dir_migration.py`; update `tests/test_plan_items.py`, `tests/test_project_tree.py`, `tests/test_deepagents_path.py`

**Interfaces:**
- Consumes: `rudra.state.paths.rudra_paths`, `ensure_layout`
- Produces: no new API — every module stops constructing `.rudra/...` by hand and asks `rudra_paths()` instead

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rudra_dir_migration.py`:

```python
"""The volatile files moved to run/; the durable ones did not.

The dangerous failure mode is a path that moved in code while an
agent-facing prompt still names the old one — the coder would then write
where nothing reads. See TODO.md A1.25 for how that surfaced before.
"""

from pathlib import Path

from rudra.state.paths import rudra_paths


def test_planning_tools_write_plan_under_run(tmp_path: Path) -> None:
    from rudra.tools.planning_tools import create_planning_tools

    tools = {tool.name: tool for tool in create_planning_tools(tmp_path)}
    tools["update_plan"].func(plan_markdown="- [ ] main.py")
    assert rudra_paths(tmp_path).plan_md.exists()
    assert not (tmp_path / ".rudra" / "PLAN.md").exists()


def test_task_assignment_lands_under_run(tmp_path: Path) -> None:
    from rudra.tools.planning_tools import create_planning_tools

    tools = {tool.name: tool for tool in create_planning_tools(tmp_path)}
    tools["write_task_assignment"].func(filename="main.py", instructions="do it")
    assert rudra_paths(tmp_path).current_task_md.exists()


def test_durable_files_did_not_move(tmp_path: Path) -> None:
    paths = rudra_paths(tmp_path)
    assert paths.agents_md == tmp_path / ".rudra" / "AGENTS.md"
    assert paths.project_json == tmp_path / ".rudra" / "project.json"


def test_project_config_manager_still_uses_the_durable_location(tmp_path: Path) -> None:
    from rudra.state import ProjectConfigManager

    assert ProjectConfigManager(tmp_path).config_file == rudra_paths(tmp_path).project_json


def _sources() -> str:
    root = Path(__file__).resolve().parent.parent / "src" / "rudra"
    return "\n".join(
        path.read_text(encoding="utf-8") for path in root.rglob("*.py")
    )


def test_no_source_names_a_stale_volatile_path() -> None:
    """Prompt strings and code must agree. This is the whole risk of C0.9."""
    text = _sources()
    for stale in (
        ".rudra/PLAN.md",
        ".rudra/current_task.md",
        ".rudra/tech_stack.md",
        ".rudra/checkpoints.db",
        ".rudra/session_id.txt",
    ):
        assert stale not in text, f"{stale} moved to .rudra/run/ — update this reference"


def test_agents_md_reference_is_left_alone() -> None:
    """AGENTS.md is durable; planner_agent.py:88 must still point at it."""
    text = _sources()
    assert ".rudra/AGENTS.md" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_rudra_dir_migration.py -q`
Expected: FAIL — `test_no_source_names_a_stale_volatile_path` reports `.rudra/PLAN.md`

- [ ] **Step 3: Migrate `planning_tools.py`**

Replace the hand-built paths at `:53` and `:164` and update every docstring naming them. The path construction becomes:

```python
from rudra.state.paths import ensure_layout

def create_planning_tools(project_path: Path) -> list:
    paths = ensure_layout(project_path)
    plan_path: Path = paths.plan_md
    ...
    task_path: Path = paths.current_task_md
```

Then update the agent-facing text — these are prompts the model reads, so they must name the real path:

| Location | Old | New |
|---|---|---|
| `:4` module docstring | `.rudra/PLAN.md` | `.rudra/run/PLAN.md` |
| `:14` example | `edit_file('.rudra/PLAN.md', ...)` | `edit_file('.rudra/run/PLAN.md', ...)` |
| `:43`, `:57`, `:147` docstrings | `.rudra/PLAN.md` | `.rudra/run/PLAN.md` |
| `:168`, `:193` docstrings | `.rudra/current_task.md` | `.rudra/run/current_task.md` |

Leave `:116` and `:121` alone — they sanitise any `.rudra/`-prefixed entry out of the plan, and that prefix check still holds for the deeper path.

- [ ] **Step 4: Migrate the coder prompt**

In `src/rudra/agent/coder_agent.py`, lines `:11`, `:17`, `:20`, `:21`: change `.rudra/current_task.md` → `.rudra/run/current_task.md` and `.rudra/tech_stack.md` → `.rudra/run/tech_stack.md`.

- [ ] **Step 5: Migrate `main_agent.py`**

- `:340` prompt text: `.rudra/current_task.md` → `.rudra/run/current_task.md`
- `:488` `_write_tech_stack_file`: write to `rudra_paths(project_path).tech_stack_md`
- `:556`: `plan_path=rudra_paths(project_path).plan_md`
- `:567`: replace `rudra_dir = project_path / ".rudra"` with `paths = ensure_layout(project_path)` and use `paths.root` / `paths.checkpoints_db` where the old variable was used

- [ ] **Step 6: Migrate `checkpoint.py`**

`get_or_create_session_id` currently takes the `.rudra/` directory and writes `session_id.txt` into it (`:32-33`). Change the parameter to the project root and use `ensure_layout(project_root).session_id_txt`, updating the docstrings at `:4`, `:22`, `:27`. Note the function still has zero call sites (`A1.3`) — this keeps it consistent rather than fixing `A1.2`.

- [ ] **Step 7: Update `deepagents_path.py:76` docstring**

`.rudra/PLAN.md` → `.rudra/run/PLAN.md`. Documentation only; no logic change.

- [ ] **Step 8: Update the three test files asserting literal paths**

- `tests/test_plan_items.py:68,77` — `.rudra/PLAN.md` → `.rudra/run/PLAN.md`
- `tests/test_project_tree.py:93,95,100` — same
- `tests/test_deepagents_path.py:41` — same

- [ ] **Step 9: Run everything**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: no decrease; `All checks passed!`

- [ ] **Step 10: Re-run the grep sweep rather than trusting the table**

Run: `grep -rn "\.rudra" src/ tests/ --include="*.py"`
Expected: every remaining hit is either `.rudra/AGENTS.md`, `.rudra/project.json`, `.rudra/run/...`, `.rudra/config.toml`, a skip-dir name in `filesystem/tree.py`, or the `RUDRA_DIR_NAME` constant in `state/paths.py`.

- [ ] **Step 11: Commit**

```bash
git add -A src/ tests/
git commit -m "refactor(state): move volatile files to .rudra/run/ (C0.9, D15)

Code paths and agent-facing prompt strings change together — a prompt
naming the old path would send the coder somewhere nothing reads."
```

---

## Task 6: `rudra init`

**Files:**
- Modify: `src/rudra/cli.py`
- Create: `src/rudra/config/template.py`
- Test: `tests/test_cli_config_commands.py`

**Interfaces:**
- Consumes: `ensure_layout`, `user_toml_path`, `build_config`
- Produces: `CONFIG_TEMPLATE: str`; the `init` command

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_config_commands.py`:

```python
"""CLI surface for configuration: init, config list, config get, doctor."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config import reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import os

    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    reset_config()
    yield
    reset_config()


def test_init_writes_a_config_that_parses_back(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output
    written = tmp_path / ".rudra" / "config.toml"
    assert written.exists()
    import tomllib

    with written.open("rb") as handle:
        tomllib.load(handle)


def test_init_creates_the_d15_layout(tmp_path: Path) -> None:
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    assert (tmp_path / ".rudra" / "run").is_dir()
    assert (tmp_path / ".rudra" / ".gitignore").exists()


def test_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    (tmp_path / ".rudra" / "config.toml").write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "-d", str(tmp_path)])
    assert result.exit_code != 0
    assert "--force" in result.output
    assert (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8") == "# mine\n"


def test_init_force_overwrites(tmp_path: Path) -> None:
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    (tmp_path / ".rudra" / "config.toml").write_text("# mine\n", encoding="utf-8")
    result = runner.invoke(app, ["init", "-d", str(tmp_path), "--force"])
    assert result.exit_code == 0
    assert "# mine" not in (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8")


def test_init_global_writes_to_the_user_location(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--global"])
    assert result.exit_code == 0
    assert (tmp_path / "xdg" / "rudra" / "config.toml").exists()


def test_the_template_contains_no_api_key_value(tmp_path: Path) -> None:
    """C1.5: the scaffold names an env var, never a key."""
    runner.invoke(app, ["init", "-d", str(tmp_path)])
    body = (tmp_path / ".rudra" / "config.toml").read_text(encoding="utf-8")
    assert "api_key_env" in body
    assert "api_key " not in body
    assert "sk-" not in body
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_cli_config_commands.py -q`
Expected: FAIL — `No such command 'init'`

- [ ] **Step 3: Write the template**

Create `src/rudra/config/template.py`:

```python
"""The commented scaffold `rudra init` writes.

Kept as a literal rather than generated from DEFAULTS: the comments are the
point, and no TOML writer in the stdlib preserves them (S6.1).
"""

CONFIG_TEMPLATE = '''\
# Rudra configuration.
#
# Load order, later overriding earlier:
#   1. built-in defaults
#   2. ~/.config/rudra/config.toml
#   3. this file
#   4. RUDRA_* environment variables (and .env)
#   5. command-line flags
#
# `rudra config list` shows every effective value and which layer set it.

[model.default]
# ollama | openai_compatible | anthropic | openai | google
provider    = "ollama"
base_url    = "http://localhost:11434"
model       = "qwen3:32b"
temperature = 0.3

# The NAME of an environment variable holding your key — never the key
# itself. Rudra reads the variable at run time and never stores its value.
# api_key_env = "RUDRA_API_KEY"

# Your model's real context window. Without it, history compaction never
# triggers for local models. See TODO.md A1.17 / C1.4a.
# context_tokens = 32768

# Per-role overrides. Anything omitted is inherited from [model.default].
[model.planner]
model = "qwen3:32b"

[model.coder]
model = "qwen3-coder:32b"

[agent]
verbose = true

[permissions]
# ask | auto | plan
#
# NOT ENFORCED YET. Enforcement arrives in Step 7; today Rudra writes and
# overwrites files without prompting regardless of this setting.
mode  = "ask"
allow = []
deny  = []

[compat]
# Workarounds kept for small models. Both off by default — see TODO.md D4.
task_anchor   = false
sandbox_paths = false

# Not supported yet, listed so you know where they will go:
#   [skills]  Step 11    [tools]  Step 7    [memory]  Step 14
# MCP servers are configured in a separate .mcp.json (Step 13).
'''

__all__ = ["CONFIG_TEMPLATE"]
```

- [ ] **Step 4: Add the `init` command to `cli.py`**

Insert after the `models_test` command:

```python
@app.command("init")
def init_command(
    project_dir: Optional[Path] = typer.Option(
        None, "--project-dir", "-d", help="Project directory (defaults to current directory)"
    ),
    global_: bool = typer.Option(
        False, "--global", help="Write ~/.config/rudra/config.toml instead of the project file"
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config file"),
) -> None:
    """Scaffold a commented config.toml and create the .rudra/ layout."""
    from rudra.config import user_toml_path
    from rudra.config.template import CONFIG_TEMPLATE
    from rudra.state.paths import ensure_layout

    if global_:
        target = user_toml_path()
        target.parent.mkdir(parents=True, exist_ok=True)
    else:
        project_path = get_project_path(project_dir)
        target = ensure_layout(project_path).config_toml

    if target.exists() and not force:
        console.print(f"[red]{target} already exists.[/red] Pass --force to overwrite it.")
        raise typer.Exit(code=1)

    target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    console.print(f"[green]Wrote[/green] {target}")
    console.print("[dim]Edit it, then run `rudra models test` to check your model.[/dim]")
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_config_commands.py -q`
Expected: `6 passed`

- [ ] **Step 6: Full suite, lint, commit**

```bash
.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/
git add src/rudra/config/template.py src/rudra/cli.py tests/test_cli_config_commands.py
git commit -m "feat(cli): rudra init scaffolds config.toml and the .rudra layout (C2.2)"
```

---

## Task 7: `rudra config list` and `rudra config get`

**Files:**
- Modify: `src/rudra/cli.py`
- Test: extend `tests/test_cli_config_commands.py`

**Interfaces:**
- Consumes: `Config.provenance`, `Config.sources`, `Config.model_for`
- Produces: `config_app` sub-Typer with `list` and `get`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_config_commands.py`:

```python
def test_config_list_shows_values_and_their_source(tmp_path: Path) -> None:
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text(
        '[model.default]\nmodel = "from-project"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "from-project" in result.output
    assert "project" in result.output


def test_config_list_marks_builtin_values(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert result.exit_code == 0
    assert "builtin" in result.output


def test_config_get_returns_one_value(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "get", "model.default.provider", "-d", str(tmp_path)])
    assert result.exit_code == 0
    assert "ollama" in result.output


def test_config_get_rejects_an_unknown_key(tmp_path: Path) -> None:
    result = runner.invoke(app, ["config", "get", "model.default.nonsense", "-d", str(tmp_path)])
    assert result.exit_code != 0
    assert "nonsense" in result.output


def test_config_get_never_prints_an_api_key_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C1.5."""
    monkeypatch.setenv("MY_SECRET", "sk-do-not-leak-me")
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text(
        '[model.default]\napi_key_env = "MY_SECRET"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["config", "get", "model.default.api_key_env", "-d", str(tmp_path)])
    assert "MY_SECRET" in result.output
    assert "sk-do-not-leak-me" not in result.output


def test_a_bad_config_file_reports_the_path_not_a_traceback(tmp_path: Path) -> None:
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text("[model\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "list", "-d", str(tmp_path)])
    assert result.exit_code != 0
    assert "config.toml" in result.output
    assert "Traceback" not in result.output
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_cli_config_commands.py -q -k "config_list or config_get or bad_config"`
Expected: FAIL — `No such command 'config'`

- [ ] **Step 3: Implement the sub-app**

Add near `models_app` in `cli.py`:

```python
config_app = typer.Typer(help="Inspect effective configuration (read-only).")
app.add_typer(config_app, name="config")
```

Add a shared helper and the two commands:

```python
def _load_config_or_exit(project_dir: Optional[Path]):
    """Build a Config, turning ConfigError into a clean message.

    A malformed config file is a user error, not a crash — never show a
    traceback for one.
    """
    from rudra.config import ConfigError, build_config

    try:
        return build_config(get_project_path(project_dir))
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=1) from None


def _flatten(cfg) -> list[tuple[str, object]]:
    """Every effective leaf as a dotted key, in a stable order."""
    from dataclasses import asdict

    rows: list[tuple[str, object]] = []
    for role in sorted(cfg.models):
        for key, value in asdict(cfg.models[role]).items():
            rows.append((f"model.{role}.{key}", value))
    rows.append(("agent.verbose", cfg.agent.verbose))
    rows.append(("permissions.mode", cfg.permissions.mode))
    rows.append(("permissions.allow", list(cfg.permissions.allow)))
    rows.append(("permissions.deny", list(cfg.permissions.deny)))
    rows.append(("compat.task_anchor", cfg.compat.task_anchor))
    rows.append(("compat.sandbox_paths", cfg.compat.sandbox_paths))
    return rows


@config_app.command("list")
def config_list(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
    role: Optional[str] = typer.Option(None, "--role", help="Show one model role only"),
) -> None:
    """Show every effective value and the layer that set it."""
    cfg = _load_config_or_exit(project_dir)

    table = Table(title="Effective configuration", header_style="bold")
    for column in ("Key", "Value", "Source"):
        table.add_column(column, overflow="fold")

    for key, value in _flatten(cfg):
        if role and key.startswith("model.") and not key.startswith(f"model.{role}."):
            continue
        # Inherited role keys carry the default role's provenance.
        source = cfg.provenance.get(key)
        if source is None and key.startswith("model."):
            _, _, leaf = key.split(".", 2)
            source = cfg.provenance.get(f"model.default.{leaf}")
        table.add_row(key, "-" if value is None else str(value), source or "builtin")

    console.print(table)
    for layer in ("user", "project"):
        path = cfg.sources.get(layer)
        console.print(f"[dim]{layer:>8}:[/dim] {path or '(none)'}")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Dotted key, e.g. model.planner.model"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Print one value and where it came from."""
    cfg = _load_config_or_exit(project_dir)
    for candidate, value in _flatten(cfg):
        if candidate == key:
            source = cfg.provenance.get(key)
            if source is None and key.startswith("model."):
                _, _, leaf = key.split(".", 2)
                source = cfg.provenance.get(f"model.default.{leaf}")
            console.print(f"{'-' if value is None else value}  [dim](from {source or 'builtin'})[/dim]")
            return
    console.print(f"[red]Unknown key '{key}'.[/red] Run `rudra config list` to see valid keys.")
    raise typer.Exit(code=1)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_config_commands.py -q`
Expected: `12 passed`

- [ ] **Step 5: Full suite, lint, commit**

```bash
.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/
git add src/rudra/cli.py tests/test_cli_config_commands.py
git commit -m "feat(cli): rudra config list/get with provenance (C2.3, read-only per S6.1)"
```

---

## Task 8: `rudra doctor`

**Files:**
- Modify: `src/rudra/cli.py`
- Test: extend `tests/test_cli_config_commands.py`

**Interfaces:**
- Consumes: `build_config`, `rudra_paths`, `llm.probe.probe_role`, `compat.version_guard`
- Produces: the `doctor` command

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli_config_commands.py`:

```python
def test_doctor_reports_config_sources(tmp_path: Path) -> None:
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert result.exit_code == 0, result.output
    assert "config" in result.output.lower()


def test_doctor_states_that_permissions_are_not_enforced(tmp_path: Path) -> None:
    """The core honesty requirement of Step 6 (spec §6)."""
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert "NOT ENFORCED" in result.output


def test_doctor_flags_a_stale_top_level_checkpoint_db(tmp_path: Path) -> None:
    rudra = tmp_path / ".rudra"
    rudra.mkdir()
    (rudra / "checkpoints.db").write_bytes(b"stale")
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert "checkpoints.db" in result.output


def test_doctor_reports_a_broken_config_cleanly(tmp_path: Path) -> None:
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text("[model\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "-d", str(tmp_path), "--offline"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_cli_config_commands.py -q -k doctor`
Expected: FAIL — `No such command 'doctor'`

- [ ] **Step 3: Implement**

```python
@app.command("doctor")
def doctor_command(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
    offline: bool = typer.Option(
        False, "--offline", help="Skip model reachability checks (no network calls)"
    ),
) -> None:
    """Diagnose configuration, layout, and model reachability."""
    from importlib.metadata import version

    from rudra.compat.version_guard import EXPECTED_DEEPAGENTS_VERSION
    from rudra.state.paths import rudra_paths

    project_path = get_project_path(project_dir)
    cfg = _load_config_or_exit(project_dir)
    paths = rudra_paths(project_path)

    table = Table(title="rudra doctor", header_style="bold")
    for column in ("Check", "Status", "Detail"):
        table.add_column(column, overflow="fold")

    table.add_row("project", "ok", str(project_path))
    for layer in ("user", "project"):
        path = cfg.sources.get(layer)
        table.add_row(f"config ({layer})", "ok" if path else "-", str(path) if path else "not present")

    dotenv = project_path / ".env"
    table.add_row(".env", "ok" if dotenv.exists() else "-", str(dotenv) if dotenv.exists() else "not present")

    table.add_row(
        ".rudra layout",
        "ok" if paths.run.is_dir() else "missing",
        "run `rudra init`" if not paths.run.is_dir() else str(paths.root),
    )

    stale = paths.root / "checkpoints.db"
    if stale.exists():
        table.add_row(
            "stale checkpoints.db",
            "warn",
            f"{stale} predates the run/ layout and is unused — safe to delete",
        )

    installed = version("deepagents")
    table.add_row(
        "deepagents",
        "ok" if installed == EXPECTED_DEEPAGENTS_VERSION else "warn",
        f"{installed} (pinned {EXPECTED_DEEPAGENTS_VERSION})",
    )

    table.add_row(
        "permissions",
        "warn",
        f"mode = {cfg.permissions.mode} — NOT ENFORCED. Enforcement arrives in Step 7; "
        f"Rudra currently writes and overwrites files without prompting. "
        f"allow/deny are parsed but unused ({len(cfg.permissions.allow)} allow, "
        f"{len(cfg.permissions.deny)} deny).",
    )

    if not offline:
        from rudra.llm.probe import ROLES_TO_PROBE, probe_role

        for name in ROLES_TO_PROBE:
            result = probe_role(name)
            table.add_row(
                f"model ({name})",
                "ok" if result.ok else "fail",
                f"{result.provider} {result.model} — {result.reach}, tools: {result.tools}",
            )

    console.print(table)
    console.print(
        "[dim]MCP servers, skills, and memory are not checked — they arrive in "
        "Steps 13, 11, and 14.[/dim]"
    )
```

- [ ] **Step 4: Run the tests, full suite, lint, commit**

```bash
.venv/bin/pytest tests/test_cli_config_commands.py -q
.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/
git add src/rudra/cli.py tests/test_cli_config_commands.py
git commit -m "feat(cli): rudra doctor (C2.3)

Reports only what exists today; states plainly that permissions are
parsed and not enforced."
```

---

## Task 9: Permission surface, flags, and the non-enforcement notice

**Files:**
- Modify: `src/rudra/cli.py`, `src/rudra/agent/planner_agent.py`, `src/rudra/agent/coder_agent.py`
- Test: `tests/test_permissions_surface.py`

**Interfaces:**
- Consumes: `Config.permissions`, `Config.compat`
- Produces: `--auto` / `--yolo` / `--plan` flags; `_permission_notice(cfg) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_permissions_surface.py`:

```python
"""C2.5's Step 6 half: the surface exists, enforcement does not, and the UI
says so. An inert default of "ask" claims MORE safety than the user gets —
that is the A1.40 mistake applied to a safety setting."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config import build_config, reset_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import os

    for name in list(os.environ):
        if name.startswith(("RUDRA_", "OLLAMA_")) or name == "VERBOSE":
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    reset_config()
    yield
    reset_config()


def test_default_mode_is_ask(tmp_path: Path) -> None:
    assert build_config(tmp_path).permissions.mode == "ask"


def test_auto_flag_sets_the_mode(tmp_path: Path) -> None:
    assert build_config(tmp_path, permission_mode="auto").permissions.mode == "auto"


def test_notice_fires_for_the_default_not_only_for_flags(tmp_path: Path) -> None:
    """The dangerous misreading is "ask means it will ask me"."""
    from rudra.cli import _permission_notice

    notice = _permission_notice(build_config(tmp_path))
    assert "NOT ENFORCED" in notice
    assert "ask" in notice


def test_notice_fires_for_auto_too(tmp_path: Path) -> None:
    from rudra.cli import _permission_notice

    assert "NOT ENFORCED" in _permission_notice(build_config(tmp_path, permission_mode="auto"))


def test_compat_flags_default_off(tmp_path: Path) -> None:
    """D4: both surviving middlewares are opt-in."""
    compat = build_config(tmp_path).compat
    assert compat.task_anchor is False
    assert compat.sandbox_paths is False


def test_compat_can_be_switched_on_from_toml(tmp_path: Path) -> None:
    (tmp_path / ".rudra").mkdir()
    (tmp_path / ".rudra" / "config.toml").write_text(
        "[compat]\ntask_anchor = true\n", encoding="utf-8"
    )
    assert build_config(tmp_path).compat.task_anchor is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_permissions_surface.py -q`
Expected: FAIL — `ImportError: cannot import name '_permission_notice'`

- [ ] **Step 3: Add the notice helper and flags to `cli.py`**

```python
def _permission_notice(cfg) -> str:
    """One line, shown on every run — not only when a flag is passed.

    A user reading `mode = "ask"` reasonably concludes Rudra will prompt
    before touching files. It will not: enforcement is Step 7 and today
    files are overwritten silently (TODO.md A1.16). Saying so on the
    default path is the point; an inert --yolo is harmless by comparison,
    because it claims less safety rather than more.
    """
    return (
        f"permissions: {cfg.permissions.mode} — NOT ENFORCED (Step 7); "
        f"files are written and overwritten without prompting"
    )
```

Add the flags to the `main` callback signature:

```python
    auto: bool = typer.Option(False, "--auto", "--yolo", help="Permission mode: auto (not enforced yet)"),
    plan: bool = typer.Option(False, "--plan", help="Permission mode: plan (not enforced yet)"),
```

In the callback body, immediately after `project_path = get_project_path(project_dir)`, replace the `cfg = get_config(project_path)` line with:

```python
    permission_mode = "auto" if auto else "plan" if plan else None
    cfg = get_config(project_path, verbose=verbose, permission_mode=permission_mode)
    if permission_mode is not None:
        console.print(f"[yellow]Note:[/yellow] {_permission_notice(cfg)}")
```

- [ ] **Step 4: Add the notice to the run panel**

In the single-shot task panel (`cli.py:322-331`), append a line to the Panel body:

```python
                f"[dim]│  Coder:[/dim] {cfg.model_for('coder').model}\n"
                f"[yellow]{_permission_notice(cfg)}[/yellow]",
```

Note: the existing panel calls `get_config()` twice inline; use the already-bound `cfg` instead, which is the same cached object.

- [ ] **Step 5: Gate the two surviving middlewares on `[compat]`**

In `src/rudra/agent/planner_agent.py` and `src/rudra/agent/coder_agent.py`, the middleware list currently always includes `TaskAnchorMiddleware`. Make it conditional:

```python
    middleware = [FixWriteParamsMiddleware()]
    if get_config().compat.task_anchor:
        middleware.append(TaskAnchorMiddleware(task))
```

`sandbox_paths` gates only the sandbox-prefix stripping inside `FixWriteParamsMiddleware`; fence-stripping and the `filename`/`path` → `file_path` aliasing stay always-on per D4. Pass the flag into the middleware's constructor:

```python
    FixWriteParamsMiddleware(strip_sandbox_prefixes=get_config().compat.sandbox_paths)
```

and default that parameter to `False` in the middleware, guarding the prefix-stripping branch with it.

- [ ] **Step 6: Run the tests, full suite, lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src/ tests/ && .venv/bin/ruff format --check src/ tests/`
Expected: no decrease; `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/rudra/cli.py src/rudra/agent/ src/rudra/middleware/ tests/test_permissions_surface.py
git commit -m "feat(config): permission surface and [compat] gating (C2.5, C1.8)

Mode parses and is settable; nothing enforces it, and the run panel and
doctor both say so. Both D4 middlewares are now opt-in and default off."
```

---

## Task 10: Documentation, ledger, and the acceptance run

**Files:**
- Modify: `.env.example`, `Documentation/02-configuration.md`, `Documentation/04-cli-reference.md`, `Documentation/README.md`, `Documentation/08-project-status.md`, `CLAUDE.md`, `TODO.md`

**Interfaces:**
- Consumes: everything above
- Produces: the closing evidence for `C2.1`–`C2.5`, `C1.8`, `C0.9`, `A1.42`, `A1.43`

- [ ] **Step 1: Update `.env.example`**

Add a header directing users to TOML first, keep the `RUDRA_*` block as the override path, and mark `VERBOSE` deprecated in favour of `RUDRA_VERBOSE` (A1.42). Do not delete the `OLLAMA_*` block — S6.4 keeps it one more release.

- [ ] **Step 2: Rewrite `Documentation/02-configuration.md`**

TOML becomes the primary surface: the five layers with a worked precedence example, the full annotated schema, `rudra init` / `config list` / `config get` / `doctor`, and an explicit note that `config set` does not exist and why (S6.1). State plainly that `[permissions]` is parsed and not enforced.

- [ ] **Step 3: Update `Documentation/04-cli-reference.md`**

Add `init`, `config list`, `config get`, `doctor`. Note that `config` and `models` are reserved first tokens, so a task starting with either word must be quoted differently or rephrased.

- [ ] **Step 4: Update `Documentation/08-project-status.md`**

Move configuration from "env vars only" to "TOML, layered". Keep the alpha framing honest: permissions still unenforced, shell still absent.

- [ ] **Step 5: Mark live sections in `CLAUDE.md` §6**

Annotate the example config: `[model.*]`, `[agent]`, `[permissions]`, `[compat]` are live as of Step 6; `[skills]`, `[memory]`, `[tools]` are reserved and error with the step that implements them. Also update §3's architecture tree — `config.py` is now `config/`, and `.rudra/` has the D15 layout.

- [ ] **Step 6: Close the ledger rows**

Mark `C2.1`, `C2.2`, `C2.3` (narrowed per S6.1 — record the deviation in the row), `C2.4`, `C2.5` (Step 6 half only; enforcement stays with Step 7), `C1.8`, `C0.9`, `A1.42`, `A1.43` as **DONE** with the verifying command output. Update the Section E Step 6 row with the completion summary, in the same shape Steps 1–5 use, and state that Step 7 is unblocked.

- [ ] **Step 7: Run the acceptance test — TOML only, no environment variables**

This is the evidence the whole step turns on. Step 5's acceptance ran from a `.env`; this one must prove layer 3 reaches the model factory.

```bash
cd "$(mktemp -d)"
env -u RUDRA_MODEL -u RUDRA_PLANNER_MODEL -u RUDRA_CODER_MODEL \
    -u RUDRA_BASE_URL -u RUDRA_PROVIDER -u RUDRA_API_KEY_ENV \
    -u OLLAMA_MODEL -u OLLAMA_BASE_URL -u VERBOSE \
    /path/to/repo/.venv/bin/rudra init
# edit .rudra/config.toml to point at a reachable model
env -i HOME="$HOME" PATH="$PATH" OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
    /path/to/repo/.venv/bin/rudra models test
env -i HOME="$HOME" PATH="$PATH" OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
    /path/to/repo/.venv/bin/rudra "write a python script wordcount.py that counts lines, words and characters in a file"
```

Expected: `models test` exits 0 with all stages `ok`; the agent run exits 0, `.rudra/run/PLAN.md` shows the file checked off, and the generated script runs correctly.

Record in the ledger: the exact commands, the `config.toml` used, the console summary, and the generated file's verified output. If `A1.39` (transient provider errors) interrupts a run, retry and say so — do not record a provider 502 as a configuration failure.

- [ ] **Step 8: Verify the compat default-off behavior change**

The acceptance run above is the first live run without `TaskAnchorMiddleware`. Record explicitly whether output quality held. If it regressed, log a new `A1.x` row with evidence — that is data for revisiting D4, **not** grounds for quietly re-enabling the middleware.

- [ ] **Step 9: Final verification**

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/pytest -q
.venv/bin/rudra --version
git status --short
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "docs: close out Step 6 — layered TOML configuration

Records the acceptance run (TOML only, no environment variables) and the
S6.1/S6.2/S6.3/S6.4 deviations from the ledger's original C2.x wording."
```

---

## Self-Review

**Spec coverage.** Every section of `2026-08-10-step6-toml-config-design.md` maps to a task: §3.1–3.2 architecture → Tasks 2–4; §3.3 preserved surface → Task 4 Step 6; §4 schema → Task 2; §5 precedence and the three ordering rules → Task 4 (`test_project_dir_selects_the_project_toml`, `test_role_inheritance_happens_after_the_cross_layer_merge`) and Task 3 (`.env` untouched); §5's `VERBOSE` deprecation → Task 0 (`A1.42`) and Task 3; §6 permissions → Task 9; §7 layout and the 35 literals → Tasks 1 and 5; §8 commands → Tasks 6–8; §9 error handling → Task 4's error tests, one per row; §10 testing and the acceptance bar → Task 10 Step 7; §11 risks → Task 10 Step 8 (compat), Task 5 Step 10 (grep sweep), Task 10 Step 7 (A1.39).

**Placeholder scan.** No "TBD", no "add error handling", no "similar to Task N". Every code step carries real code; every test step carries real assertions.

**Type consistency.** `rudra_paths`/`ensure_layout` return `RudraPaths` in Tasks 1, 5, 6, 8. `ConfigError` is defined once in `layers.py` and re-exported by `loader.py` and `config/__init__.py` — Tasks 3, 4, 7 all use that one name. `ModelConfig`'s field names are identical to Step 5's, which is what lets `llm/factory.py` go untouched. `deep_merge` returns `(dict, dict[str, str])` in Task 4's definition and both callers. `_permission_notice` is defined in Task 9 Step 3 and used in Steps 3 and 4 of the same task plus `tests/test_permissions_surface.py`.

**One known gap, deliberate.** Task 2 Step 4 flags that `src/rudra/config.py` and `src/rudra/config/` cannot coexist, and gives the fallback if the collision bites before Task 4 deletes the module. An implementer working strictly task-by-task should expect to hit this and follow the documented escape rather than improvising.
