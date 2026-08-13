# Step 10a — Open Fact Store and Q&A Purge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Rudra's fixed 4-field tech-stack questionnaire with an open key/value fact store that every agent can read and that records *why* each fact is believed.

**Architecture:** A new pure module `src/rudra/facts/` holds the store (`store.py`) and its prompt renderer (`render.py`), importing nothing from Rudra — the rule `loop/ledger.py` already follows. One `FactStore` per run is shared **by reference** between the planner's tools and `SubagentContext`, so a fact recorded at minute one appears in the next coder dispatch, because `build_agent` runs per invocation (`runner.py:124`). Two agent-facing tools replace `save_project_context`: `record_fact(key, value, why, source)` and a batched `ask_user(questions, keys)` that records its own answers and is not registered at all when nobody can answer.

**Tech Stack:** Python 3.12+, `deepagents` 0.7.4, LangChain tools (`@tool`), Rich console, pytest, ruff, `uv`.

**Spec:** `docs/superpowers/specs/2026-08-13-step10a-fact-store-design.md` — read it before Task 1. Owner decisions are S10a.1–S10a.8.

## Global Constraints

- **Session rule 2 (non-negotiable):** never fix a bug on discovery. Add it to `TODO.md` as `PENDING` with `file:line` evidence **first**, then fix, then mark `DONE`.
- **Evidence-based only.** Every claim about the codebase cites `file.py:line`.
- `.venv/bin/ruff check src/ tests/` must print `All checks passed!` — absolute gate.
- `.venv/bin/ruff format --check src/ tests/` must be clean.
- `uv run pytest -q` must never drop below **880 passed, 2 skipped**. Prefer `uv run` over `.venv/bin/…`: a local `.venv` drifts from `uv.lock` and will lie (CLAUDE.md §9).
- **Never build a `.rudra/...` path by hand.** `src/rudra/state/paths.py` is the only source (D15).
- **No prompt may name a `.rudra/` path.** `tests/test_rudra_dir_migration.py::test_no_source_names_a_stale_volatile_path` enforces it and must keep passing.
- Fact keys are **not enumerated anywhere in code**. Validation covers types and size, never names (§0.5).
- The model never emits JSON structure — typed flat tool args only (C6.10).
- `source` is exactly one of `asked` · `inferred` · `detected`.
- Limits, verbatim: key `[a-z0-9_.-]{1,64}`; `value` and `why` non-empty, ≤ 512 chars; ≤ 100 facts; `[agent] max_questions` default 5, validated `>= 0`.
- Commit after every task, with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` as the last line of the message.

---

### Task 1: The fact store

**Files:**
- Create: `src/rudra/facts/__init__.py`
- Create: `src/rudra/facts/store.py`
- Test: `tests/test_facts_store.py`

**Interfaces:**
- Consumes: nothing. This module imports nothing from Rudra.
- Produces: `Fact(value: str, why: str, source: str)` (frozen dataclass); `FactStore` with `record(key, value, why, source) -> Fact` (raises `FactRejected`), `get(key) -> Fact | None`, `items() -> list[tuple[str, Fact]]`, `save(path: Path) -> None`, `FactStore.load(path: Path) -> FactStore`, and the attribute `facts: dict[str, Fact]`; the exception `FactRejected(ValueError)`; the constants `KEY_PATTERN`, `VALID_SOURCES`, `MAX_TEXT`, `MAX_FACTS`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facts_store.py`:

```python
"""The open fact store (Step 10a, C6.8a).

Pure data plus one file: these tests need no model, no backend and no
gate, which is the same property loop/ledger.py's tests have.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rudra.facts import Fact, FactRejected, FactStore


def test_record_returns_the_fact_and_stores_it():
    store = FactStore()
    fact = store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    assert fact == Fact(value="Rust", why="user said 'CLI in Rust'", source="inferred")
    assert store.get("language") == fact


def test_get_returns_none_for_an_unknown_key():
    assert FactStore().get("nothing") is None


def test_keys_are_not_enumerated_anywhere():
    """The whole point of C6.8a: any key a model invents is accepted."""
    store = FactStore()
    store.record("min_rust_version", "1.75", "clap 4 needs it", "detected")
    store.record("no-async", "true", "the user asked for a sync CLI", "asked")
    assert [key for key, _ in store.items()] == ["min_rust_version", "no-async"]


def test_items_preserve_insertion_order_across_a_save_and_load(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    store.record("cli_framework", "clap", "stated", "inferred")
    path = tmp_path / "facts.json"
    store.save(path)

    loaded = FactStore.load(path)
    assert [key for key, _ in loaded.items()] == ["language", "cli_framework"]
    assert loaded.get("cli_framework").why == "stated"


def test_recording_a_key_twice_overwrites_in_place():
    store = FactStore()
    store.record("language", "Python", "guessed", "inferred")
    store.record("database", "SQLite", "stated", "asked")
    store.record("language", "Rust", "the user corrected me", "asked")

    assert [key for key, _ in store.items()] == ["language", "database"]
    assert store.get("language").value == "Rust"
    assert store.get("language").source == "asked"


@pytest.mark.parametrize(
    "key",
    ["", "Language", "has space", "a" * 65, "emoji🙂", "slash/key"],
)
def test_a_bad_key_is_rejected(key: str):
    with pytest.raises(FactRejected):
        FactStore().record(key, "v", "w", "asked")


@pytest.mark.parametrize("field_value", ["", "   ", "x" * 513])
def test_a_bad_value_or_why_is_rejected(field_value: str):
    with pytest.raises(FactRejected):
        FactStore().record("k", field_value, "why", "asked")
    with pytest.raises(FactRejected):
        FactStore().record("k", "value", field_value, "asked")


def test_a_non_string_value_is_rejected():
    with pytest.raises(FactRejected):
        FactStore().record("k", 3, "why", "asked")  # type: ignore[arg-type]


def test_an_unknown_source_is_rejected_and_names_the_valid_ones():
    with pytest.raises(FactRejected) as caught:
        FactStore().record("k", "v", "w", "guessed")
    message = str(caught.value)
    assert "asked" in message and "inferred" in message and "detected" in message


def test_a_rejected_write_leaves_the_store_untouched():
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    with pytest.raises(FactRejected):
        store.record("BAD KEY", "x", "y", "asked")
    assert [key for key, _ in store.items()] == ["language"]


def test_the_store_is_capped_and_an_overwrite_still_fits():
    store = FactStore()
    for index in range(100):
        store.record(f"k{index}", "v", "w", "detected")
    with pytest.raises(FactRejected):
        store.record("one_too_many", "v", "w", "detected")
    # An existing key is not a new fact, so it must still be writable.
    store.record("k0", "changed", "w", "detected")
    assert store.get("k0").value == "changed"


def test_save_writes_json_and_leaves_no_temp_file(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    path = tmp_path / "facts.json"
    store.save(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "facts": {"language": {"value": "Rust", "why": "stated", "source": "inferred"}}
    }
    assert list(tmp_path.iterdir()) == [path]


def test_save_creates_the_parent_directory(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    store.save(tmp_path / "nested" / "facts.json")
    assert (tmp_path / "nested" / "facts.json").is_file()


def test_load_of_a_missing_file_is_an_empty_store(tmp_path: Path):
    assert FactStore.load(tmp_path / "absent.json").items() == []


def test_load_of_a_corrupt_file_is_an_empty_store(tmp_path: Path):
    """Same contract as Ledger.load (loop/ledger.py:113-116)."""
    path = tmp_path / "facts.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert FactStore.load(path).items() == []


def test_load_drops_entries_that_fail_validation(tmp_path: Path):
    """A hand-edited file must not smuggle a 4-field questionnaire back in."""
    path = tmp_path / "facts.json"
    path.write_text(
        json.dumps(
            {
                "facts": {
                    "language": {"value": "Rust", "why": "stated", "source": "inferred"},
                    "BAD KEY": {"value": "x", "why": "y", "source": "asked"},
                    "bad_source": {"value": "x", "why": "y", "source": "invented"},
                    "not_a_dict": "Rust",
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = FactStore.load(path)
    assert [key for key, _ in loaded.items()] == ["language"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_facts_store.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'rudra.facts'`.

- [ ] **Step 3: Write the store**

Create `src/rudra/facts/store.py`:

```python
"""Whatever this project's agents have established, and why.

Replaces the fixed 4-field ProjectContext (TODO.md §0.5). Keys are not
enumerated anywhere in code -- validation covers types and size, never
names, because the moment code lists the legal keys the questionnaire is
back. A fact carries its rationale because C6.7 must persist decisions
AND their reasoning, and a second store for the reasoning would let the
two drift.

Pure data plus one file. Imports nothing from Rudra, so its tests need no
model, no backend and no gate -- the rule loop/ledger.py follows.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Lowercase, because a store that distinguishes `Language` from `language`
# holds the same fact twice and shows the model both.
KEY_PATTERN = re.compile(r"^[a-z0-9_.-]{1,64}$")

# How the fact came to be believed. `asked` is the user's word, `inferred`
# is the model's reading of the request, `detected` is read off the
# project. The distinction matters to a human reading facts.json later.
VALID_SOURCES = ("asked", "inferred", "detected")

MAX_TEXT = 512
MAX_FACTS = 100


class FactRejected(ValueError):
    """A fact failed validation.

    The message is written to be shown to a model verbatim: it says what
    was wrong and what would be acceptable, because the next thing that
    happens is the model trying again.
    """


@dataclass(frozen=True)
class Fact:
    """One thing believed about this project, and why."""

    value: str
    why: str
    source: str


def _check_text(name: str, text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        msg = f"{name} must be a non-empty string, got {text!r}."
        raise FactRejected(msg)
    stripped = text.strip()
    if len(stripped) > MAX_TEXT:
        msg = f"{name} must be at most {MAX_TEXT} characters, got {len(stripped)}."
        raise FactRejected(msg)
    return stripped


@dataclass
class FactStore:
    """Every fact this project has established, in the order established.

    One store per run, shared by reference: the planner's tools mutate it
    and every later subagent build renders it into a prompt. A copy would
    leave the coder reading a stale store -- the failure the shared Ledger
    already exists to avoid (main_agent.py:412-414).
    """

    facts: dict[str, Fact] = field(default_factory=dict)

    def record(self, key: str, value: str, why: str, source: str) -> Fact:
        """Validate and store one fact. Raises FactRejected, changes nothing on failure."""
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            msg = (
                f"'{key}' is not a valid key: use 1-64 characters from "
                "a-z, 0-9, underscore, dot and hyphen."
            )
            raise FactRejected(msg)
        if source not in VALID_SOURCES:
            msg = f"source must be one of {', '.join(VALID_SOURCES)}, got {source!r}."
            raise FactRejected(msg)
        clean_value = _check_text("value", value)
        clean_why = _check_text("why", why)
        # An existing key is a correction, not a new fact, so it is never
        # blocked by the cap -- a full store must still be fixable.
        if key not in self.facts and len(self.facts) >= MAX_FACTS:
            msg = f"the fact store is full ({MAX_FACTS} facts); nothing was recorded."
            raise FactRejected(msg)

        fact = Fact(value=clean_value, why=clean_why, source=source)
        self.facts[key] = fact
        return fact

    def get(self, key: str) -> Fact | None:
        return self.facts.get(key)

    def items(self) -> list[tuple[str, Fact]]:
        """Every fact in insertion order. Overwriting a key keeps its place."""
        return list(self.facts.items())

    def save(self, path: Path) -> None:
        """Write atomically: temp file, then os.replace.

        A crash mid-write must leave the previous file readable rather
        than a truncated one -- the guarantee Ledger.save gives
        (loop/ledger.py:95-107).
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"facts": {key: asdict(fact) for key, fact in self.facts.items()}}
        temporary = path.with_name(f"{path.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, path: Path) -> FactStore:
        """Read a store, or an empty one when the file is absent or unreadable.

        Entries that fail validation are dropped rather than raising: this
        file is durable and hand-editable, and one bad line must not stop
        a run that has nothing to do with it.
        """
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()

        store = cls()
        entries = payload.get("facts", {})
        if not isinstance(entries, dict):
            return store
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            try:
                store.record(
                    key,
                    entry.get("value"),
                    entry.get("why"),
                    entry.get("source"),
                )
            except FactRejected:
                continue
        return store


__all__ = [
    "KEY_PATTERN",
    "MAX_FACTS",
    "MAX_TEXT",
    "VALID_SOURCES",
    "Fact",
    "FactRejected",
    "FactStore",
]
```

Create `src/rudra/facts/__init__.py`:

```python
"""The open fact store (Step 10a, C6.8a)."""

from rudra.facts.store import (
    MAX_FACTS,
    MAX_TEXT,
    VALID_SOURCES,
    Fact,
    FactRejected,
    FactStore,
)

__all__ = [
    "MAX_FACTS",
    "MAX_TEXT",
    "VALID_SOURCES",
    "Fact",
    "FactRejected",
    "FactStore",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facts_store.py -q`
Expected: PASS, 15 tests (the two parametrized cases expand to 6 and 3).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/rudra/facts tests/test_facts_store.py
uv run ruff format src/rudra/facts tests/test_facts_store.py
git add src/rudra/facts tests/test_facts_store.py
git commit -m "$(cat <<'EOF'
feat(facts): an open key/value fact store with rationale

Replaces the 4-field ProjectContext's data model (TODO.md §0.5 surface
#1). A fact is {value, why, source} because C6.7 must persist decisions
AND rationale, and a second store would let the two drift (S10a.2).

Validation covers types and size, never names: the moment code lists the
legal keys the questionnaire is back. Imports nothing from Rudra, so the
tests need no model, backend or gate.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: The prompt renderer

**Files:**
- Create: `src/rudra/facts/render.py`
- Modify: `src/rudra/facts/__init__.py`
- Test: `tests/test_facts_render.py`

**Interfaces:**
- Consumes: `FactStore` and `Fact` from Task 1.
- Produces: `facts_block(store: FactStore | None) -> str` — a markdown block ending in a newline, or `""` for an empty or absent store. Exported from `rudra.facts`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_facts_render.py`:

```python
"""Rendering facts into a system prompt (Step 10a)."""

from __future__ import annotations

from rudra.facts import FactStore, facts_block


def test_an_empty_store_renders_nothing():
    """A greenfield run must add no section and spend no tokens."""
    assert facts_block(FactStore()) == ""


def test_no_store_at_all_renders_nothing():
    assert facts_block(None) == ""


def test_every_fact_appears_with_its_value_source_and_why():
    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    block = facts_block(store)

    assert "## PROJECT FACTS" in block
    assert "language" in block
    assert "Rust" in block
    assert "inferred" in block
    assert "user said 'CLI in Rust'" in block
    assert block.endswith("\n")


def test_facts_render_in_insertion_order():
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    store.record("cli_framework", "clap", "stated", "inferred")
    block = facts_block(store)
    assert block.index("language") < block.index("cli_framework")


def test_a_value_containing_rich_markup_is_rendered_literally():
    """This block is printed through a Rich console in the live trace.

    A1.48 and A1.67 are both this defect: Rich parses [word] as a style
    tag and prints nothing. A fact value is user text and must survive.
    """
    store = FactStore()
    store.record("style", "[bold]never[/bold]", "the user pasted it", "asked")
    block = facts_block(store)
    assert "[bold]never[/bold]" in block
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_facts_render.py -q`
Expected: FAIL — `ImportError: cannot import name 'facts_block' from 'rudra.facts'`.

- [ ] **Step 3: Write the renderer**

Create `src/rudra/facts/render.py`:

```python
"""Facts as a prompt section.

Pure: takes a store, returns a string, touches nothing. Rendered at build
time rather than baked into a spec because build_agent runs per
invocation (subagents/runner.py:124), so a fact recorded during one task
reaches the next dispatch without any plumbing.

Nothing here escapes Rich markup. The block goes into a system prompt,
where brackets are ordinary characters; the console printing of any tool
result is where escaping belongs (A1.67).
"""

from __future__ import annotations

from rudra.facts.store import FactStore

_HEADING = "## PROJECT FACTS"
_PREAMBLE = (
    "Established for this project. Treat these as binding unless the user "
    "says otherwise. Each line reads: key = value (source: why)."
)


def facts_block(store: FactStore | None) -> str:
    """The facts as a markdown block, or "" when there are none."""
    if store is None or not store.facts:
        return ""
    lines = [_HEADING, "", _PREAMBLE, ""]
    lines.extend(
        f"- {key} = {fact.value}  ({fact.source}: {fact.why})" for key, fact in store.items()
    )
    return "\n".join(lines) + "\n"


__all__ = ["facts_block"]
```

Modify `src/rudra/facts/__init__.py` — add the import and the export:

```python
from rudra.facts.render import facts_block
from rudra.facts.store import (
    MAX_FACTS,
    MAX_TEXT,
    VALID_SOURCES,
    Fact,
    FactRejected,
    FactStore,
)

__all__ = [
    "MAX_FACTS",
    "MAX_TEXT",
    "VALID_SOURCES",
    "Fact",
    "FactRejected",
    "FactStore",
    "facts_block",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_facts_render.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/rudra/facts tests/test_facts_render.py
uv run ruff format src/rudra/facts tests/test_facts_render.py
git add src/rudra/facts tests/test_facts_render.py
git commit -m "$(cat <<'EOF'
feat(facts): render facts as a prompt block

An empty store renders "" so a greenfield run adds no section and spends
no tokens. Insertion order is preserved, which is the order the model
established things in and the order a human reads facts.json in.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `[agent] max_questions`

**Files:**
- Modify: `src/rudra/config/schema.py:79` (add the field), `src/rudra/config/schema.py:150` (defaults)
- Modify: `src/rudra/config/loader.py:45` (`_AGENT_KEYS`), `:146-154` (validation), `:303` (construction)
- Modify: `src/rudra/config/template.py:48`
- Test: `tests/test_config_loader.py` (append)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `cfg.agent.max_questions: int`, default `5`, validated `>= 0`. Tasks 4, 6 and 7 read it.

Note: `cli.py`'s `_flatten` (`cli.py:311-338`) derives its rows from each dataclass's own fields, so `config list` picks the key up with no CLI change. That is A1.54's structural fix; this task adds the test that proves it holds for a newly added key.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_loader.py`. That file already imports `pytest`, `Path`, `ConfigError` and `get_config`, has an autouse `_clean` fixture that resets config around every test (`:12-25`), and provides `_write_project_toml(root, body)` (`:27`) — use them rather than resetting by hand:

```python
def test_max_questions_defaults_to_five(tmp_path: Path) -> None:
    assert get_config(tmp_path).agent.max_questions == 5


def test_max_questions_can_be_zero_to_disable_asking(tmp_path: Path) -> None:
    """0 is legal here and illegal for max_fix_attempts, deliberately: a
    run that asks nothing is a normal unattended run, while 0 attempts
    would block every task without the coder running once."""
    project = tmp_path / "proj"
    _write_project_toml(project, "[agent]\nmax_questions = 0\n")
    assert get_config(project).agent.max_questions == 0
    assert get_config(project).provenance["agent.max_questions"] == "project"


@pytest.mark.parametrize("bad", ["-1", "true", '"five"', "2.5"])
def test_a_bad_max_questions_is_a_config_error(tmp_path: Path, bad: str) -> None:
    project = tmp_path / "proj"
    _write_project_toml(project, f"[agent]\nmax_questions = {bad}\n")
    with pytest.raises(ConfigError) as caught:
        get_config(project)
    assert "max_questions" in str(caught.value)


def test_config_list_reports_max_questions(tmp_path: Path) -> None:
    """A key that is honoured and never printed is A1.54 exactly."""
    from rudra.cli import _flatten

    keys = [key for key, _ in _flatten(get_config(tmp_path))]
    assert "agent.max_questions" in keys
```

Note `max_fix_attempts` has **no** loader test today (`grep -rln max_fix_attempts tests/` hits only the loop tests), so these four are the first coverage of `[agent]`'s numeric validation. Do not add tests for `max_fix_attempts` here — that is unrelated scope.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config_loader.py -q -k max_questions`
Expected: FAIL — `AttributeError: 'AgentConfig' object has no attribute 'max_questions'`.

- [ ] **Step 3: Add the key**

In `src/rudra/config/schema.py`, extend `AgentConfig` (after `max_fix_attempts`, line 79):

```python
    # How many clarifying questions the planner may ask across one whole
    # run (C6.8). 0 disables asking outright -- unlike max_fix_attempts,
    # where 0 would block every task without the coder running once, a
    # run that asks nothing is a legitimate unattended run.
    max_questions: int = 5
```

In the same file, line 150:

```python
    "agent": {"verbose": True, "max_fix_attempts": 3, "max_questions": 5},
```

In `src/rudra/config/loader.py`, line 45:

```python
_AGENT_KEYS = frozenset({"verbose", "max_fix_attempts", "max_questions"})
```

After the `max_fix_attempts` validation block (ends line 154), add:

```python
    questions = agent.get("max_questions")
    if questions is not None and (
        not isinstance(questions, int) or isinstance(questions, bool) or questions < 0
    ):
        # 0 is legal: it is how an unattended workflow says "never ask".
        raise ConfigError(
            f"max_questions in [agent] must be a whole number of 0 or more, got {questions!r}."
        )
```

At line 303, extend the `AgentConfig(...)` construction:

```python
            max_questions=int(merged.get("agent", {}).get("max_questions", 5)),
```

In `src/rudra/config/template.py`, after `max_fix_attempts = 3` (line 48):

```toml

# How many clarifying questions the planner may ask across one run.
# Set to 0 to never ask; unattended runs (--auto) never ask regardless.
max_questions = 5
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_config_loader.py -q`
Expected: PASS, including the four new cases.

Run: `uv run pytest -q`
Expected: `883 passed, 2 skipped` or higher — the template test (if any) may need its expected text updated; if a test asserts the template body verbatim, update that expectation, do not change the template.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/
uv run ruff format src/rudra/config tests/test_config_loader.py
git add src/rudra/config tests/test_config_loader.py
git commit -m "$(cat <<'EOF'
feat(config): [agent] max_questions, the clarification budget

Default 5, validated >= 0. Zero is legal and means never ask, which is
how an unattended workflow says so in config rather than at the command
line (S10a.6).

config list picks it up with no CLI change because _flatten derives its
rows from the dataclass fields -- A1.54's structural fix, now covered by
a test that proves it holds for a newly added key.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: The two interaction tools

**Files:**
- Modify (rewrite): `src/rudra/tools/interaction_tools.py`
- Test: `tests/test_interaction_tools.py` (create)

**Interfaces:**
- Consumes: `FactStore`, `FactRejected` (Task 1); `cfg.agent.max_questions` (Task 3).
- Produces: `create_interaction_tools(console, store, path, *, max_questions=5, interactive=True) -> list[BaseTool]`, returning `[record_fact]` or `[record_fact, ask_user]`. **The old signature `(console, project_path)` is gone**; Task 6 updates its only caller (`planner_agent.py:131`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_interaction_tools.py`:

```python
"""record_fact and the batched ask_user (Step 10a, C6.8/C6.8a).

The old tool pair asked one question at a time and wrote into a 4-field
allowlist that silently discarded everything else (interaction_tools.py:67).
These tests pin the replacement's contract, including every REJECTED path:
a model reads those strings and tries again, so they are behaviour.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from rudra.facts import FactStore
from rudra.tools.interaction_tools import create_interaction_tools


def _tools(tmp_path: Path, store=None, **kwargs):
    store = store if store is not None else FactStore()
    tools = create_interaction_tools(
        Console(quiet=True), store, tmp_path / "facts.json", **kwargs
    )
    return store, {tool.name: tool for tool in tools}


def test_record_fact_stores_and_persists(tmp_path: Path):
    store, tools = _tools(tmp_path)
    out = tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "stated", "source": "inferred"}
    )
    assert "language" in out
    assert store.get("language").value == "Rust"
    assert (tmp_path / "facts.json").is_file()


def test_record_fact_returns_rejected_rather_than_raising(tmp_path: Path):
    store, tools = _tools(tmp_path)
    out = tools["record_fact"].invoke(
        {"key": "BAD KEY", "value": "x", "why": "y", "source": "asked"}
    )
    assert out.startswith("REJECTED:")
    assert store.items() == []
    assert not (tmp_path / "facts.json").exists()


def test_ask_user_is_absent_when_the_run_is_unattended(tmp_path: Path):
    """S10a.5: absence, not a refusal message. The S9b.3 precedent."""
    _store, tools = _tools(tmp_path, interactive=False)
    assert "ask_user" not in tools
    assert "record_fact" in tools


def test_ask_user_is_absent_when_the_budget_is_zero(tmp_path: Path):
    _store, tools = _tools(tmp_path, max_questions=0)
    assert "ask_user" not in tools


def test_ask_user_records_each_answer_as_a_fact(tmp_path: Path, monkeypatch):
    answers = iter(["Rust", "clap"])
    monkeypatch.setattr(
        "rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: next(answers)
    )
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {
            "questions": ["Which language?", "Which arg parser?"],
            "keys": ["language", "cli_framework"],
        }
    )

    assert store.get("language").value == "Rust"
    assert store.get("language").source == "asked"
    assert "Which language?" in store.get("language").why
    assert store.get("cli_framework").value == "clap"
    assert "Rust" in out and "clap" in out


def test_ask_user_rejects_a_key_question_length_mismatch(tmp_path: Path, monkeypatch):
    called = []
    monkeypatch.setattr(
        "rudra.tools.interaction_tools.Prompt.ask",
        lambda *a, **k: called.append(1) or "x",
    )
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke({"questions": ["a?", "b?"], "keys": ["a"]})

    assert out.startswith("REJECTED:")
    assert called == []          # nothing prompted
    assert store.items() == []   # nothing recorded


def test_ask_user_rejects_an_empty_question_list(tmp_path: Path):
    _store, tools = _tools(tmp_path)
    assert tools["ask_user"].invoke({"questions": [], "keys": []}).startswith("REJECTED:")


def test_an_empty_answer_is_not_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "  ")
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke({"questions": ["Which database?"], "keys": ["database"]})

    assert store.get("database") is None
    assert "(no answer)" in out


def test_the_budget_is_spent_across_calls_and_then_refuses(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "yes")
    store, tools = _tools(tmp_path, max_questions=2)

    tools["ask_user"].invoke({"questions": ["a?", "b?"], "keys": ["a", "b"]})
    out = tools["ask_user"].invoke({"questions": ["c?"], "keys": ["c"]})

    assert out.startswith("REJECTED:")
    assert "2" in out
    assert store.get("c") is None


def test_a_batch_larger_than_the_budget_asks_what_fits_and_says_so(
    tmp_path: Path, monkeypatch
):
    """Refusing the whole batch would punish the batching C6.8 asks for."""
    asked: list[str] = []

    def _ask(*_args, **_kwargs):
        return "yes"

    monkeypatch.setattr(
        "rudra.tools.interaction_tools.Prompt.ask",
        lambda *a, **k: asked.append(1) or _ask(),
    )
    store, tools = _tools(tmp_path, max_questions=2)

    out = tools["ask_user"].invoke(
        {"questions": ["a?", "b?", "c?"], "keys": ["a", "b", "c"]}
    )

    assert len(asked) == 2
    assert store.get("c") is None
    assert "not asked" in out


def test_eof_stops_the_batch_and_keeps_what_was_already_answered(
    tmp_path: Path, monkeypatch
):
    calls = {"n": 0}

    def _ask(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Rust"
        raise EOFError

    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", _ask)
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {"questions": ["Which language?", "Which parser?"], "keys": ["language", "parser"]}
    )

    assert store.get("language").value == "Rust"
    assert store.get("parser") is None
    assert "(no answer)" in out


def test_an_invalid_key_rejects_only_its_own_pair(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "yes")
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {"questions": ["a?", "b?"], "keys": ["BAD KEY", "good_key"]}
    )

    assert store.get("good_key").value == "yes"
    assert "NOT recorded" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_interaction_tools.py -q`
Expected: FAIL — `TypeError: create_interaction_tools() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Rewrite the tools**

Replace the whole of `src/rudra/tools/interaction_tools.py`:

```python
"""Asking the user, and recording what the run establishes.

Both tools replace the static questionnaire TODO.md §0.5 inventories.
`save_project_context` accepted four field names and silently discarded
everything else (surface #2); `ask_user` mandated one question at a time
(surface #3), which is the opposite of what C6.8 needs.

Neither tool raises. A model reads what comes back and tries again, so a
rejection is a sentence explaining what would be acceptable -- the same
REJECTED idiom loop/tools.py uses.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, tool
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

from rudra.facts import FactRejected, FactStore


def create_interaction_tools(
    console: Console,
    store: FactStore,
    path: Path,
    *,
    max_questions: int = 5,
    interactive: bool = True,
) -> list[BaseTool]:
    """The interaction tools for one run.

    Args:
        console: Rich console for the questions.
        store: The live FactStore the loop also reads -- the same object,
            not a copy, for the reason the Ledger is shared.
        path: Where to persist after each successful record.
        max_questions: The run's whole clarification budget
            (`[agent] max_questions`). Counted in questions, not calls.
        interactive: False when nobody can answer. `ask_user` is then not
            registered at all rather than returning a refusal (S10a.5):
            the reviewer cannot write because its tools do not exist, and
            this follows that precedent.

    Returns:
        [record_fact], plus [ask_user] when a user can actually answer.
    """
    remaining = {"questions": max(0, int(max_questions))}

    @tool
    def record_fact(key: str, value: str, why: str, source: str) -> str:
        """Record one thing you have established about this project.

        Record everything you rely on, whether the user told you or you
        worked it out -- the coder, the tester and the reviewer all read
        these facts, and a fact you keep to yourself is one they do not have.

        Args:
            key: A short lowercase name: "language", "cli_framework",
                "min_python_version". Invent whatever fits; there is no
                fixed list.
            value: What you established, e.g. "Rust".
            why: Why you believe it, e.g. "the user asked for a Rust CLI".
            source: "asked" if the user told you, "inferred" if you worked
                it out from the request, "detected" if you read it off the
                project.

        Returns:
            Confirmation, or REJECTED and what would be acceptable.
        """
        try:
            fact = store.record(key, value, why, source)
        except FactRejected as exc:
            return f"REJECTED: {exc}"
        store.save(path)
        return f'Recorded {key} = "{fact.value}" ({fact.source}).'

    tools: list[BaseTool] = [record_fact]
    if not interactive or remaining["questions"] <= 0:
        return tools

    @tool
    def ask_user(questions: list[str], keys: list[str]) -> str:
        """Ask the user a batch of related questions and record the answers.

        Ask only what you cannot infer from the request or the codebase.
        Send related questions together in ONE call rather than one per
        call. Every answer is recorded as a fact automatically, so you do
        not need to call record_fact for it.

        Args:
            questions: The questions, in the order to ask them.
            keys: One fact key per question, same order and same length:
                ["language", "cli_framework"].

        Returns:
            A numbered question-and-answer block, or REJECTED and why.
        """
        if not questions:
            return "REJECTED: give at least one question."
        if len(keys) != len(questions):
            return (
                f"REJECTED: give one key per question; got {len(questions)} "
                f"question(s) and {len(keys)} key(s)."
            )
        if remaining["questions"] <= 0:
            return (
                f"REJECTED: question budget spent ({max_questions}) — "
                "infer the rest from the request and record it with record_fact."
            )

        # Ask what fits rather than refusing the batch: refusing would
        # punish exactly the batching this tool exists to encourage.
        askable = list(zip(keys, questions))[: remaining["questions"]]
        skipped = len(questions) - len(askable)

        lines: list[str] = []
        for index, (key, question) in enumerate(askable, start=1):
            remaining["questions"] -= 1
            console.print(f"\n[bold cyan]?[/bold cyan] {escape(question)}")
            try:
                answer = Prompt.ask("[bold yellow]Answer[/bold yellow]", default="").strip()
            except EOFError:
                # stdin closed mid-batch. Everything answered so far is
                # already recorded; the rest simply did not happen.
                lines.append(f"{index}. {question} → (no answer)")
                break
            if not answer:
                lines.append(f"{index}. {question} → (no answer)")
                continue
            try:
                store.record(key, answer, f"user answered: {question}", "asked")
            except FactRejected as exc:
                lines.append(f"{index}. {question} → {answer}  (NOT recorded: {exc})")
                continue
            store.save(path)
            lines.append(f"{index}. {question} → {answer}  (recorded as {key})")

        if skipped:
            lines.append(f"({skipped} question(s) not asked: the question budget is spent.)")
        return "\n".join(lines)

    return [record_fact, ask_user]


__all__ = ["create_interaction_tools"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_interaction_tools.py -q`
Expected: PASS, 12 tests.

Run: `uv run pytest -q`
Expected: failures in `tests/test_agent_wiring.py` only — `create_interaction_tools` has a new signature and `planner_agent.py:131` still calls the old one. Task 6 fixes that; do **not** patch it here.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/rudra/tools tests/test_interaction_tools.py
uv run ruff format src/rudra/tools tests/test_interaction_tools.py
git add src/rudra/tools/interaction_tools.py tests/test_interaction_tools.py
git commit -m "$(cat <<'EOF'
feat(tools): record_fact and a batched ask_user

Replaces save_project_context's 4-name allowlist, which discarded any
other fact silently (§0.5 surface #2), and ask_user's "ONE focused
question at a time", which contradicted C6.8 outright (surface #3).

ask_user takes one key per question so Python never guesses a fact name
from prose, records each answer itself, and is not registered at all when
nobody can answer (S10a.5). A batch larger than the budget asks what fits
rather than refusing, because refusing would punish batching.

Note: the planner still calls the old signature; Task 6 rewires it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Facts in every subagent prompt

**Files:**
- Modify: `src/rudra/subagents/runner.py` (add `facts` to `SubagentContext`, around `:47-62`)
- Modify: `src/rudra/subagents/build.py` (add `_prompt_for`; use it at `:141` and `:161`)
- Test: `tests/test_subagents_build.py` (append)

**Interfaces:**
- Consumes: `facts_block` (Task 2).
- Produces: `SubagentContext.facts: Any = None`; `_prompt_for(spec, context) -> str` in `build.py`, used by both `build_agent` and `to_subagent_spec`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_subagents_build.py` (reuse the file's existing context fixture — find it with `grep -n "SubagentContext(" tests/test_subagents_build.py` and match its style):

```python
def test_the_facts_block_is_appended_to_every_subagent_prompt(tmp_path, monkeypatch):
    """The coder has never seen the project facts before Step 10a."""
    from rudra.facts import FactStore
    from rudra.subagents.build import _prompt_for
    from rudra.subagents.registry import REGISTRY

    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    context = _context(tmp_path, facts=store)

    for name in ("coder", "tester", "reviewer", "general-purpose"):
        prompt = _prompt_for(REGISTRY[name], context)
        assert REGISTRY[name].system_prompt in prompt
        assert "## PROJECT FACTS" in prompt
        assert "Rust" in prompt


def test_a_prompt_is_unchanged_when_there_are_no_facts(tmp_path):
    from rudra.facts import FactStore
    from rudra.subagents.build import _prompt_for
    from rudra.subagents.registry import REGISTRY

    context = _context(tmp_path, facts=FactStore())
    assert _prompt_for(REGISTRY["coder"], context) == REGISTRY["coder"].system_prompt


def test_a_context_without_facts_still_builds(tmp_path):
    """SubagentContext.facts defaults to None; nothing may require it."""
    from rudra.subagents.build import _prompt_for
    from rudra.subagents.registry import REGISTRY

    context = _context(tmp_path)
    assert _prompt_for(REGISTRY["coder"], context) == REGISTRY["coder"].system_prompt


def test_a_fact_recorded_after_construction_reaches_the_next_build(tmp_path):
    """build_agent runs per invocation (runner.py:124) -- that is the point."""
    from rudra.facts import FactStore
    from rudra.subagents.build import _prompt_for
    from rudra.subagents.registry import REGISTRY

    store = FactStore()
    context = _context(tmp_path, facts=store)
    assert "clap" not in _prompt_for(REGISTRY["coder"], context)

    store.record("cli_framework", "clap", "the user answered", "asked")
    assert "clap" in _prompt_for(REGISTRY["coder"], context)


def test_both_assembly_paths_carry_the_same_prompt(tmp_path, monkeypatch):
    """The delegating path must not drift from the direct one.

    Same rule _tools_for and _middleware_for already follow: a subagent
    reached through `task` and one invoked directly are one thing.
    """
    from rudra.facts import FactStore
    from rudra.subagents.build import _prompt_for, to_subagent_spec
    from rudra.subagents.registry import REGISTRY

    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    context = _context(tmp_path, facts=store)

    for name, spec in REGISTRY.items():
        assert to_subagent_spec(spec, context)["system_prompt"] == _prompt_for(spec, context)
```

If the file has no `_context(...)` helper, add one that mirrors the existing fixture, e.g.:

```python
def _context(tmp_path, *, facts=None):
    from rich.console import Console

    from rudra.config.loader import build_config, reset_config
    from rudra.subagents import SubagentContext

    reset_config()
    cfg = build_config(tmp_path)
    return SubagentContext(
        project_path=tmp_path,
        backend=object(),
        gate=None,
        console=Console(quiet=True),
        cfg=cfg,
        facts=facts,
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_subagents_build.py -q -k facts`
Expected: FAIL — `TypeError: SubagentContext.__init__() got an unexpected keyword argument 'facts'`.

- [ ] **Step 3: Wire the facts through**

In `src/rudra/subagents/runner.py`, add the field to `SubagentContext` (after `session_id`):

```python
    session_id: str = ""
    # The run's FactStore, shared by reference so a fact recorded during
    # one task reaches the next dispatch's prompt. Optional because the
    # subagent machinery must stay constructible without a run (Step 9b's
    # tests build one with no facts at all).
    facts: Any = None
```

In `src/rudra/subagents/build.py`, add the import and the helper (place `_prompt_for` beside `_tools_for`):

```python
from rudra.facts import facts_block
```

```python
def _prompt_for(spec: RudraSubagent, context: Any) -> str:
    """The spec's prompt, plus whatever this project has established.

    Rendered here rather than baked into the spec because build_agent runs
    once per invocation (runner.py:124): a fact recorded during task 1
    reaches task 2's coder with no plumbing. Shared with to_subagent_spec
    for the reason _tools_for and _middleware_for are shared -- the
    delegating path must not quietly differ from the direct one.

    Before this existed, facts reached the planner's prompt alone
    (planner_agent.py:42), so a project whose facts said Rust had a coder
    that was never told.
    """
    block = facts_block(getattr(context, "facts", None))
    if not block:
        return spec.system_prompt
    return f"{spec.system_prompt}\n\n{block}"
```

In `build_agent`, replace `system_prompt=spec.system_prompt` with:

```python
        system_prompt=_prompt_for(spec, context),
```

In `to_subagent_spec`, replace `"system_prompt": spec.system_prompt,` with:

```python
        "system_prompt": _prompt_for(spec, context),
```

Add `_prompt_for` to `build.py`'s `__all__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_subagents_build.py -q`
Expected: PASS, including the five new cases.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/rudra/subagents tests/test_subagents_build.py
uv run ruff format src/rudra/subagents tests/test_subagents_build.py
git add src/rudra/subagents tests/test_subagents_build.py
git commit -m "$(cat <<'EOF'
feat(subagents): the coder can finally see the project facts

Measured while designing 10a: tech_stack_content reached exactly one
place, the planner's system prompt (planner_agent.py:42). The coder, the
tester and the reviewer got spec.system_prompt and nothing else, so a
project whose facts said Rust had a coder that was never told.

_prompt_for renders the facts block at build time, and build_agent runs
per invocation (runner.py:124), so a fact recorded during one task
reaches the next dispatch for free. Shared with to_subagent_spec so the
delegating path cannot drift.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: The planner — facts in, questionnaire out

**Files:**
- Modify: `src/rudra/agent/planner_agent.py:29-80` (prompt), `:104-157` (constructor)
- Modify: `tests/test_agent_wiring.py:132,157,273` and `:307-309`
- Modify: `tests/test_no_direct_provider_imports.py:110`
- Test: `tests/test_planner_prompt.py` (create)

**Interfaces:**
- Consumes: `facts_block` (Task 2), `create_interaction_tools` (Task 4), `cfg.agent.max_questions` (Task 3).
- Produces: `build_planner_prompt(task, project_path, facts=None, *, can_ask=True, max_questions=5) -> str`; `create_planner_agent(task, project_path, filesystem_backend, checkpointer, console, gate=None, ledger=None, paths=None, facts=None, interactive=True)`. **`tech_stack_content` is gone from both.** Task 7 updates the caller.

- [ ] **Step 1: Write the failing test**

Create `tests/test_planner_prompt.py`:

```python
"""The planner's prompt after the static-Q&A purge (Step 10a, C6.8a).

Surface #6 was a single line -- "Do NOT call ask_user() if the task
already specifies a framework or language" (planner_agent.py:78) -- and
it is the prompt-level twin of the middleware C0.2 deleted. Deleting the
middleware never removed it.
"""

from __future__ import annotations

from pathlib import Path

from rudra.agent.planner_agent import build_planner_prompt
from rudra.facts import FactStore


def test_the_prohibition_on_asking_is_gone(tmp_path: Path):
    prompt = build_planner_prompt("build a web API", tmp_path)
    assert "Do NOT call ask_user" not in prompt


def test_no_static_field_names_survive(tmp_path: Path):
    """The four boxes must not reappear as prompt text."""
    prompt = build_planner_prompt("build a web API", tmp_path).lower()
    for field_name in ("primary_language", "primary language", "database"):
        assert field_name not in prompt


def test_the_prompt_states_the_question_budget(tmp_path: Path):
    prompt = build_planner_prompt("build a web API", tmp_path, can_ask=True, max_questions=4)
    assert "4" in prompt
    assert "ask_user" in prompt
    assert "record_fact" in prompt


def test_an_unattended_run_is_told_there_is_nobody_to_ask(tmp_path: Path):
    prompt = build_planner_prompt("build a web API", tmp_path, can_ask=False)
    assert "ask_user" not in prompt
    assert "unattended" in prompt
    assert "record_fact" in prompt


def test_established_facts_are_rendered_into_the_prompt(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    prompt = build_planner_prompt("add a --format flag", tmp_path, facts=store)
    assert "## PROJECT FACTS" in prompt
    assert "Rust" in prompt


def test_no_facts_adds_no_section(tmp_path: Path):
    prompt = build_planner_prompt("add a --format flag", tmp_path, facts=FactStore())
    assert "PROJECT FACTS" not in prompt


def test_the_prompt_still_names_no_rudra_path(tmp_path: Path):
    """C6.10's property: the ledger is reached only through tools."""
    prompt = build_planner_prompt("build a web API", tmp_path)
    assert ".rudra" not in prompt
```

Then update the existing callers:

- `tests/test_agent_wiring.py:132`, `:157`, `:273` — delete the `tech_stack_content="",` argument line.
- `tests/test_no_direct_provider_imports.py:110` — same deletion.
- `tests/test_agent_wiring.py:307-309` — replace the body so it asserts the new pair, and add the unattended case:

```python
def test_the_interaction_tools_survive_the_ledger_switch(monkeypatch, tmp_path):
    """Step 9c replaced the planning tools; 10a replaced these two."""
    names = set(_planner_tool_names(monkeypatch, tmp_path))
    assert "record_fact" in names
    assert "ask_user" in names
    assert "save_project_context" not in names


def test_an_unattended_planner_has_no_ask_user(monkeypatch, tmp_path):
    """S10a.5: the tool is absent, not refusing."""
    names = set(_planner_tool_names(monkeypatch, tmp_path, interactive=False))
    assert "record_fact" in names
    assert "ask_user" not in names
```

If `_planner_tool_names` does not accept extra keyword arguments, extend it to forward `**kwargs` to `create_planner_agent` (check its definition with `grep -n "_planner_tool_names" -A 15 tests/test_agent_wiring.py`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_planner_prompt.py tests/test_agent_wiring.py -q`
Expected: FAIL — `build_planner_prompt() got an unexpected keyword argument 'facts'`, plus the `create_interaction_tools` signature error inherited from Task 4.

- [ ] **Step 3: Rewrite the prompt and the constructor**

In `src/rudra/agent/planner_agent.py`, replace `build_planner_prompt` (lines 29-80) with:

```python
def build_planner_prompt(
    task: str,
    project_path: Path,
    facts: Any = None,
    *,
    can_ask: bool = True,
    max_questions: int = 5,
) -> str:
    base = f"""You are a senior software architect and planning agent for Rudra.

Your ONLY job: decide WHAT WORK the request needs. You never write project code.

## PROJECT STRUCTURE
{project_tree(project_path)}
"""
    block = facts_block(facts)
    if block:
        base += f"\n{block}"

    base += f"""
## REQUEST
{task}

## WHAT A TASK IS

A task is a unit of WORK, described in plain language. Not a filename.

  GOOD: "write a CSV parser that handles quoted commas"
  GOOD: "write tests for the parser"
  GOOD: "add a --format flag to the CLI"
  BAD:  "parser.py"
  BAD:  "create the project structure"

One task may touch several files. Work that needs tests gets its own task.

## ESTABLISHING FACTS
"""

    if can_ask:
        base += f"""
Ask only what you cannot infer from the request or the codebase. Batch
related questions into a SINGLE ask_user() call — one key per question.
You have {max_questions} questions for this whole run.
"""
    else:
        base += """
This run is unattended: there is nobody to ask. Infer what you need from
the request and the codebase.
"""

    base += """
Record every fact you establish — whether you inferred it or the user
answered it — with record_fact(), and say WHY you believe it. The coder,
the tester and the reviewer all read those facts; one you keep to
yourself is one they do not have.

## WORKFLOW

1. Call read_file() on anything you need to understand the project
2. Establish and record the facts the work depends on
3. Call add_tasks() ONCE with every task you can foresee
4. STOP

You will be consulted again if a task fails or if the work runs out. When
that happens, add a task taking a DIFFERENT approach, or drop_task() one
that turned out to be unnecessary.

## WHAT YOU CANNOT DO

You cannot mark anything done. A deterministic verification gate decides
that — it parses the code, type checks it, runs the tests, and scans for
placeholders. Do not claim a task is complete, and do not add a task whose
description is "verify" or "check": that already happens on its own.

- NEVER call write_file() on project source files — the coder handles that
"""
    return base
```

Add the import at the top of the module:

```python
from rudra.facts import facts_block
```

Replace `create_planner_agent`'s signature and body (lines 104-157) — the changed lines only:

```python
def create_planner_agent(
    task: str,
    project_path: Path,
    filesystem_backend,
    checkpointer,
    console: Console,
    gate=None,
    ledger=None,
    paths=None,
    facts=None,
    interactive: bool = True,
):
    """Create the planner deep agent.

    `ledger` and `facts` must be the SAME objects the loop reads: the
    planner's tools mutate them in place, and a copy would leave the
    engine with no tasks and the coder with no facts.

    `interactive` False means nobody can answer, so ask_user is never
    registered (S10a.5). The prompt is built to match, so the model is
    never told to call a tool it does not have.
    """
    from rudra.facts import FactStore
    from rudra.loop.ledger import Ledger
    from rudra.state.paths import rudra_paths

    model = build_model("planner")
    cfg = get_config()
    ledger = ledger if ledger is not None else Ledger()
    facts = facts if facts is not None else FactStore()
    paths = paths if paths is not None else rudra_paths(project_path)

    # The ledger replaced PLAN.md and current_task.md (C6.10); the fact
    # store replaced project.json's four fields (C6.8a). git and testing
    # tools are gone from the planner: the loop runs the gate itself, and
    # the tester subagent writes tests (S9c.5).
    custom_tools = create_ledger_tools(ledger, paths.ledger_json) + create_interaction_tools(
        console,
        facts,
        paths.facts_json,
        max_questions=cfg.agent.max_questions,
        interactive=interactive,
    )

    middleware = build_planner_middleware(
        task,
        compat_task_anchor=cfg.compat.task_anchor,
        compat_sandbox_paths=cfg.compat.sandbox_paths,
    )
    if gate is not None:
        # First in the list: a denied call must be stopped before any other
        # middleware rewrites its arguments.
        middleware.insert(0, gate.middleware)

    # permissions= is deliberately absent. It raises NotImplementedError on
    # any execute-capable backend, which is every backend Rudra now builds.
    # See TODO.md U.7.
    return create_deep_agent(
        model=model,
        tools=custom_tools,
        system_prompt=build_planner_prompt(
            task,
            project_path,
            facts,
            can_ask=interactive and cfg.agent.max_questions > 0,
            max_questions=cfg.agent.max_questions,
        ),
        backend=filesystem_backend,
        checkpointer=checkpointer,
        memory=[".rudra/AGENTS.md"],
        middleware=middleware,
        interrupt_on=gate.interrupt_on if gate is not None else None,
    )
```

`paths.facts_json` does not exist yet — Task 7 adds it. Add it **now** as a one-line change to `src/rudra/state/paths.py` so this task's tests can run: add `facts_json: Path` to `RudraPaths` (after `project_json`) and `facts_json=root / "facts.json",` to `rudra_paths`. Task 7 removes `project_json` and `tech_stack_md` and updates the docstrings.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_planner_prompt.py tests/test_agent_wiring.py tests/test_no_direct_provider_imports.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: failures confined to `tests/test_main_agent_helpers.py` and any test constructing `create_main_agent` — `main_agent.py:418` still passes `tech_stack_content=`. Task 7 fixes it.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/ tests/
uv run ruff format src/rudra/agent src/rudra/state tests/
git add src/rudra/agent/planner_agent.py src/rudra/state/paths.py tests/
git commit -m "$(cat <<'EOF'
feat(planner): a question budget replaces the prohibition on asking

§0.5 surface #6 was one line -- "Do NOT call ask_user() if the task
already specifies a framework or language" -- the prompt-level twin of
the middleware C0.2 deleted. Deleting the middleware never removed it.

It becomes a budget: ask only what you cannot infer, batch related
questions into one call, N for the whole run. An unattended run is told
plainly that nobody can answer, and ask_user is not registered there, so
the prompt never names a tool the model does not have.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Delete the questionnaire

**Files:**
- Delete: `src/rudra/state/project_config.py`
- Modify: `src/rudra/state/__init__.py` (drop both exports)
- Modify: `src/rudra/state/paths.py` (drop `project_json` and `tech_stack_md`; docstrings)
- Modify: `src/rudra/agent/main_agent.py` — `:14`, `:24`, `:209-239` (`_ensure_agents_md`), `:242-279` (delete `_write_tech_stack_file`), `:330-448` (`create_main_agent`)
- Modify: `src/rudra/cli.py:22`, `:212-214`, `:685`, `:706`, `:790`
- Modify: `tests/test_main_agent_helpers.py`, `tests/test_rudra_paths.py`, `tests/test_rudra_dir_migration.py`
- Test: `tests/test_static_qa_purged.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: `create_main_agent(project_path, task, command="build", console=None, dry_run=False, verbose=False, **kwargs)` — **`project_context` is gone**; `_ensure_agents_md(rudra_dir, facts)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_static_qa_purged.py`:

```python
"""C6.8a: the static questionnaire is gone, and stays gone.

TODO.md §0.5 counts seven surfaces. Six were live at Step 10a; the
seventh (README) was already removed by an earlier step and its citation
was stale -- see A1.69. These are the guards that stop any of them
growing back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "rudra"


def _sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))


def test_project_context_is_gone():
    with pytest.raises(ImportError):
        from rudra.state import ProjectContext  # noqa: F401


def test_project_config_manager_is_gone():
    with pytest.raises(ImportError):
        from rudra.state import ProjectConfigManager  # noqa: F401


def test_the_project_config_module_is_deleted():
    assert not (SRC / "state" / "project_config.py").exists()


@pytest.mark.parametrize(
    "field_name", ["primary_language", "additional_context", "save_project_context"]
)
def test_no_source_mentions_a_questionnaire_field(field_name: str):
    assert field_name not in _sources()


def test_the_tech_stack_inference_table_is_gone():
    """A1.30: an 8-row lookup table that contradicted the stack registry."""
    text = _sources()
    assert "_write_tech_stack_file" not in text
    assert "Rails" not in text
    assert "Spring Boot" not in text


def test_the_paths_module_no_longer_offers_tech_stack_md(tmp_path: Path):
    from rudra.state import rudra_paths

    assert not hasattr(rudra_paths(tmp_path), "tech_stack_md")


def test_facts_json_is_durable(tmp_path: Path):
    """D15: durable files sit at the .rudra/ root, volatile ones under run/."""
    from rudra.state import rudra_paths

    paths = rudra_paths(tmp_path)
    assert paths.facts_json == paths.root / "facts.json"


def test_the_readme_no_longer_documents_the_four_question_form():
    """A1.69: §0.5's surface #7 citation points past the end of the file."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    lowered = readme.lower()
    assert "primary language" not in lowered
    assert "additional context" not in lowered
```

Update `tests/test_main_agent_helpers.py` — delete the four `_write_tech_stack_file` tests and the `ProjectContext` import, and replace the two `_ensure_agents_md` tests with:

```python
from rudra.agent.main_agent import _ensure_agents_md
from rudra.facts import FactStore


def _store() -> FactStore:
    store = FactStore()
    store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    return store


def test_ensure_agents_md_creates_the_file_once(tmp_path: Path):
    _ensure_agents_md(tmp_path, _store())
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()

    agents.write_text("hand-edited by the user\n", encoding="utf-8")
    _ensure_agents_md(tmp_path, _store())

    assert agents.read_text(encoding="utf-8") == "hand-edited by the user\n"


def test_ensure_agents_md_renders_whatever_facts_exist(tmp_path: Path):
    """D18: a Rust project must not be rendered as a Python one."""
    _ensure_agents_md(tmp_path, _store())
    content = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "Rust" in content


def test_ensure_agents_md_handles_an_empty_store(tmp_path: Path):
    _ensure_agents_md(tmp_path, FactStore())
    assert (tmp_path / "AGENTS.md").is_file()


def test_ensure_agents_md_handles_no_facts_at_all(tmp_path: Path):
    _ensure_agents_md(tmp_path, None)
    assert (tmp_path / "AGENTS.md").is_file()
```

In `tests/test_rudra_paths.py:29`, delete the `tech_stack_md` assertion and add:

```python
    assert paths.facts_json == paths.root / "facts.json"
```

In `tests/test_rudra_dir_migration.py`, delete the `ProjectConfigManager` test (the block at `:58-62`). Leave `.rudra/tech_stack.md` in the stale-path list — asserting the absence of a path that no longer exists is still the guard that stops it coming back.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_static_qa_purged.py -q`
Expected: FAIL — `ProjectContext` still imports, `_write_tech_stack_file` still in the sources, `tech_stack_md` still on `RudraPaths`.

- [ ] **Step 3: Delete the surfaces**

1. `git rm src/rudra/state/project_config.py`

2. `src/rudra/state/__init__.py` — remove the `project_config` import and both `__all__` entries:

```python
"""State management module."""

from rudra.state.checkpoint import get_or_create_session_id
from rudra.state.paths import RudraPaths, ensure_layout, rudra_paths

__all__ = [
    "RudraPaths",
    "ensure_layout",
    "get_or_create_session_id",
    "rudra_paths",
]
```

3. `src/rudra/state/paths.py` — delete `project_json` from `RudraPaths` and from `rudra_paths()`; delete `tech_stack_md` from both (Task 6 already added `facts_json`). Update the module docstring's durable list and `GITIGNORE_BODY`'s comment, replacing `project.json` with `facts.json`:

```python
* **durable** — `config.toml`, `AGENTS.md`, `facts.json`, `memory/export/`.
```

```python
# The durable files beside it (config.toml, AGENTS.md, facts.json,
```

4. `src/rudra/agent/main_agent.py`:
   - `:14` → `from rudra.state import ensure_layout`
   - `:24` — delete the `project_context` field from `AgentContext`
   - `:242-279` — delete `_write_tech_stack_file` entirely
   - `:209-239` — replace `_ensure_agents_md`:

```python
def _ensure_agents_md(rudra_dir: Path, facts: Any = None) -> None:
    """Create a starter AGENTS.md if one does not already exist.

    Renders whatever facts exist rather than four fixed fields (C6.8a).
    Still create-once: A1.9 -- this file is never written again -- is real
    and belongs to C7.3, which makes it a living document.
    """
    agents_md = rudra_dir / "AGENTS.md"
    if agents_md.exists():
        return

    block = facts_block(facts).strip()
    stack_section = block if block else "(not yet determined — the agent will fill this in)"

    agents_md.write_text(
        f"# Project Memory\n\n"
        f"This file is your persistent memory across sessions.\n"
        f"Update it using edit_file after completing any task.\n\n"
        f"{stack_section}\n\n"
        f"## Project Structure\n(not yet built)\n\n"
        f"## Architecture Notes\n(none yet)\n\n"
        f"## Session Log\n(no sessions yet)\n",
        encoding="utf-8",
    )
```

   - add `from rudra.facts import FactStore, facts_block` to the imports
   - `create_main_agent` — delete the `project_context` parameter and its `AgentContext` argument; build the store and thread it through:

```python
    paths = ensure_layout(project_path)

    # Durable: what previous runs established is still true (D15). An
    # absent or corrupt file loads empty rather than raising.
    facts = FactStore.load(paths.facts_json)
```

```python
    # A user exists only when a terminal does and the run is not
    # unattended. `plan` mode keeps asking -- that is the one mode where
    # clarification is the entire point.
    interactive = cfg.permissions.mode != "auto" and stdin_is_interactive()
```

(import `stdin_is_interactive` beside `build_gate`: `from rudra.permissions import build_gate, stdin_is_interactive`)

```python
    _ensure_agents_md(paths.root, facts)
```
(delete the `_write_tech_stack_file` call at `:382` and the `tech_stack_content` local)

```python
    subagent_context = SubagentContext(
        project_path=project_path,
        backend=filesystem_backend,
        gate=gate,
        console=console,
        cfg=cfg,
        checkpointer=checkpointer,
        session_id=session_id,
        facts=facts,
    )
```

```python
    planner = create_planner_agent(
        task=task,
        project_path=project_path,
        filesystem_backend=filesystem_backend,
        checkpointer=checkpointer,
        console=console,
        gate=gate,
        ledger=ledger,
        paths=paths,
        facts=facts,
        interactive=interactive,
    )
```

5. `src/rudra/cli.py`:
   - `:22` → `from rudra.state import ensure_layout` if `ensure_layout` is used there, otherwise delete the import line entirely (check with `grep -n "rudra.state" src/rudra/cli.py`)
   - delete `load_project_context` (`:212-214`)
   - delete `project_context = load_project_context(project_path)` (`:685`)
   - delete the `project_context=project_context,` argument at `:706` and `:790`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_static_qa_purged.py tests/test_main_agent_helpers.py tests/test_rudra_paths.py tests/test_rudra_dir_migration.py -q`
Expected: PASS.

Run: `uv run pytest -q`
Expected: `≥ 880 passed, 2 skipped`, zero failures. Any remaining failure names a call site the deletion missed — fix it there, not by restoring anything.

Run: `uv run ruff check src/ tests/`
Expected: `All checks passed!` — ruff is what catches a dangling reference in a function with no unit coverage (the Step 6 lesson).

Run: `uv run rudra --version` then `uv run rudra config list | grep max_questions`
Expected: `Rudra v0.2.0`; a row reading `agent.max_questions   5   builtin`.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/ tests/
git add -A src/ tests/
git commit -m "$(cat <<'EOF'
refactor: delete the static tech-stack questionnaire

Six live surfaces from TODO.md §0.5, in one commit because each one on
its own leaves the other five re-imposing the same four boxes:
ProjectContext and its manager, save_project_context's allowlist,
ask_user's one-question rule, _ensure_agents_md's four fields,
_write_tech_stack_file with its 8-row inference table, and the planner's
prohibition on asking.

Closes A1.30 by deletion: the inference table offered Spring and Rails,
which stacks/registry.py deliberately excludes, and omitted Angular,
which it ships.

project.json is ignored rather than migrated (S10a.8). Stated cost: a
project that already answered the four questions loses them and the
agent re-establishes or re-asks. The file is left on disk, unreferenced.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Documentation and ledger

**Files:**
- Modify: `CLAUDE.md` (architecture tree, `.rudra/` state table, §6 config block)
- Modify: `TODO.md` (`C6.8a`, `A1.30`, `A1.69`, Step 10 rows in §E, §0.5 note)
- Modify: `Documentation/01-getting-started.md:305,313`, `02-configuration.md:63,200,271,275`, `05-how-it-works.md:97,136,139,212`, `07-development.md:139`, `08-project-status.md:71`

**Interfaces:**
- Consumes: the shipped behaviour of Tasks 1-7. Produces: no code.

- [ ] **Step 1: Update `CLAUDE.md`**

In the architecture tree, add after `verify/`:

```
├── facts/                  The open fact store (Step 10a, C6.8a).
│                           store.py imports nothing from Rudra. A fact is
│                           {value, why, source}; keys are enumerated
│                           nowhere. render.py puts them in every agent's
│                           prompt — planner at construction, subagents at
│                           each build_agent call
```

In the `.rudra/` table: delete the `run/tech_stack.md` row and the `project.json` row; add:

```
| `facts.json` | durable | `record_fact` / `ask_user` (agent) | every agent's prompt | Open key/value: `{key: {value, why, source}}`. Keys are not enumerated in code. Replaces `project.json`, which is **ignored, not migrated** (S10a.8) |
```

In §6's config block, add under `[agent]`:

```toml
max_questions = 5                       # clarification budget for the run (0 = never ask)
```

- [ ] **Step 2: Update `TODO.md`**

- `C6.8a` → `**DONE** 2026-08-13`, citing the spec and plan paths, the surfaces closed, the pytest count, and the acceptance runs from Task 9.
- `A1.30` → `**DONE** 2026-08-13 (by deletion)` — the table it describes no longer exists.
- `A1.69` → `**DONE** 2026-08-13` — §0.5's surface #7 note corrected in the same commit.
- In §0.5, mark surface #7's row as already-removed with the A1.69 cross-reference, so the "7 surfaces" count stops sending future sessions hunting.
- In §E, replace the single Step 10 row with three rows — 10a (**COMPLETE**, with evidence), 10b (`C6.7`, `C6.8`'s planning stages; depends on 10a), 10c (`C6.9`; depends on 10b) — and record S10a.1 as the decomposition decision.
- `C6.8` stays **PENDING**: its tool contract shipped here, its three-stage sequencing is 10b. Say exactly that in the row rather than leaving it ambiguous.

- [ ] **Step 3: Update `Documentation/`**

- `01-getting-started.md:305` — `project.json` row becomes `facts.json | What this project's agents have established, and why`. Delete `:313`'s `run/tech_stack.md` row.
- `02-configuration.md:63` — add `max_questions = 5` to the `[agent]` sample; `:200` — add a table row: `` | `max_questions` | How many clarifying questions the planner may ask across one run. Default 5; 0 never asks. Unattended runs never ask regardless | ``; `:271`/`:275` — swap `project.json` for `facts.json`, delete `tech_stack.md` from the volatile list.
- `05-how-it-works.md:97` — the stack-detection paragraph no longer writes `tech_stack.md`; say the detected stack reaches the gate through `stacks/registry.py` and anything the agent concludes is recorded as a fact. `:136`/`:139` — same table swap as above. `:212` — update the `ask_user` row to the batched contract and add a `record_fact` row.
- `07-development.md:139` — `interaction_tools.py  ask_user · record_fact`, and add a `facts/` entry to the tree.
- `08-project-status.md:71` — "No clarifying questions" is now wrong; rewrite it to describe the budgeted, batched clarification that shipped, and note that three-stage planning is 10b.

- [ ] **Step 4: Verify the docs match the code**

Run: `grep -rn "tech_stack\|project.json\|save_project_context\|primary_language" Documentation/ CLAUDE.md README.md`
Expected: no hits outside historical/dated records. A hit in `docs/superpowers/specs/` or `docs/superpowers/plans/` is fine — those are dated records of what was true when written.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md TODO.md Documentation/
git commit -m "$(cat <<'EOF'
docs: close C6.8a, A1.30, A1.69; split Step 10 into 10a/10b/10c

C6.8 stays PENDING on purpose: its tool contract shipped with 10a, its
three-stage sequencing is 10b. §0.5's surface #7 is marked already-gone
so the "7 surfaces" count stops sending sessions after a surface that is
not there.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Verification and live acceptance

**Files:** none created. This task produces evidence.

**Interfaces:** Consumes everything. Produces the transcript quoted in `TODO.md`'s `C6.8a` row (Task 8 step 2 — run this task first if the row is written from its output).

- [ ] **Step 1: The three local gates**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
```

Expected, in order: `All checks passed!`; `N files already formatted`; `≥ 880 passed, 2 skipped`. Record the real numbers — a count that fell means a test was deleted, and that needs a sentence saying which and why.

- [ ] **Step 2: Acceptance run 1 — the §0.5 test, greenfield**

```bash
cd "$(mktemp -d)" && git init -q .
mkdir -p .rudra && cp ~/path/to/a/known-good/config.toml .rudra/config.toml   # ask the owner for theirs
env -i HOME="$HOME" PATH="$PATH" /full/path/to/.venv/bin/rudra --auto --allow-shell \
  "build a CLI in Rust with clap"
cat .rudra/facts.json
```

Expected: `facts.json` contains `rust` and `clap` as fact values with non-empty `why` and `source: "inferred"`; the ledger's tasks describe Rust work; no Python file is generated. Quote `facts.json` verbatim into the TODO row. **This is the acceptance test §0.5 wrote for this row** — before 10a, surface #5 had no Rust row and surface #6 forbade asking, so the run steered to Python.

- [ ] **Step 3: Acceptance run 2 — ask_user is absent when unattended**

In the same run as step 2, confirm from the live trace that no `ask_user` call appears and the run completes without a prompt. If the trace is gone, re-run with `--auto` and pipe to a file. Expected: `record_fact` calls present, `ask_user` absent — the tool was never registered.

- [ ] **Step 4: Acceptance run 3 — batching, through a real pty**

```bash
cd "$(mktemp -d)" && git init -q .
mkdir -p .rudra && cp <the same config.toml> .rudra/config.toml
python3 -c "import pty,sys; pty.spawn(['/full/path/to/.venv/bin/rudra', 'build a web API'])"
```

Answer the questions when prompted. Expected: **one** `ask_user` call carrying several questions (not several calls carrying one each); `facts.json` afterwards holds each answer with `source: "asked"` and a `why` naming the question that produced it. This is the run that proves C6.8's batching rather than the plumbing — surface #3 forbade exactly this.

- [ ] **Step 5: Record and commit the evidence**

Write the measured numbers and quoted output into `TODO.md`'s `C6.8a` row and the §E Step 10a row (Task 8 step 2). Log any defect the runs surface as a new `PENDING` row **before** fixing it — session rule 2 — continuing from `A1.69`.

```bash
git add TODO.md
git commit -m "$(cat <<'EOF'
docs: Step 10a acceptance evidence

Three runs: the §0.5 Rust/clap test greenfield, ask_user absent under
--auto, and a real-pty run proving the planner batches its questions
into one call. Numbers and transcripts quoted in the C6.8a row.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage.** §3 store → Task 1. §3 renderer → Task 2. §4 tools, every rejection row → Task 4. §4 registration rule → Tasks 4, 6, 7. §5 data flow and shared store → Tasks 5, 6, 7. §6 surfaces 1-6 → Task 7; surface 7 → Task 7's README guard test. §7 prompt rules, both variants → Task 6. §8 config → Task 3. §9 tests → each task's own step, plus Task 7's guard file. §10 acceptance, all three runs → Task 9. §11 out-of-scope items appear in no task. No gaps.

**Placeholder scan.** Every code step carries real code. Three steps deliberately point at the repo rather than quoting it — the `_context` fixture in Task 5, `_planner_tool_names` in Task 6, and the config-test module name in Task 3 — because each depends on a helper whose current shape must be read before it is extended; each names the exact `grep` that finds it.

**Type consistency.** `FactStore.record(key, value, why, source) -> Fact` is called identically in Tasks 1, 4, 5, 7. `facts_block(store | None) -> str` takes the same argument in Tasks 2, 5, 6, 7. `create_interaction_tools(console, store, path, *, max_questions, interactive)` is defined in Task 4 and called exactly that way in Task 6. `_prompt_for(spec, context)` is defined once in Task 5 and used at both `build.py` call sites. `paths.facts_json` is added in Task 6 and depended on in Tasks 6, 7, and both guard tests.
