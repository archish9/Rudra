# Step 11b — Wiring the Skill Corpus to the Agents: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the vendored superpowers corpus reachable by Rudra's agents — build it into a keyed cache at run start, mount it on the existing composite backend, and hand it to the planner stages, the coder and the tester.

**Architecture:** A new `skills/cache.py` renders 11a's pure `render()` into `${XDG_CACHE_HOME:-~/.cache}/rudra/skills/<key>/`, keyed on everything that can change the output, marked complete by a sentinel file and published by an atomic rename, with a per-run temp directory as the fallback when the cache root is unwritable. `build_backend` gains one `/skills/` route beside the existing `/artifacts/` one; the two `create_deep_agent` call sites gain an optional `skills_sources`. The rendered bootstrap skill is appended to each planner stage's system prompt.

**Tech Stack:** Python 3.12+, stdlib only for the cache (`hashlib`, `os`, `shutil`, `tempfile`, `pathlib`), deepagents 0.7.4 (`CompositeBackend`, `FilesystemBackend`, `SkillsMiddleware`), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-17-step11b-skill-wiring-design.md`

## Global Constraints

- **`ruff check src/ tests/` must print `All checks passed!`** — absolute gate since Step 3. `ruff format --check src/ tests/` must be clean.
- **`uv run pytest -q` must never go down.** Baseline at the start of this step is **1099 passed, 2 skipped**. Re-measure in Task 1 Step 1 and compare after every task.
- **Prefer `uv run`** over `.venv/bin/` — a local `.venv` drifts from `uv.lock` and will lie to you (CLAUDE.md §9).
- **ruff config:** line-length 100, target py312, rules `E,F,I,W`, `E501` ignored. `src/rudra/skills/bundles` is excluded.
- **Every new module starts with `from __future__ import annotations`.**
- **Never edit anything under `src/rudra/skills/bundles/`.** It is frozen and hash-verified; `tests/test_skills_manifest.py` fails if you do. The transform edits rendered output, never the source.
- **`skills_sources=None` must remain the default everywhere**, so every existing test constructs agents without a cache. `None` means "pass no `skills=` at all", not "pass an empty list".
- **Do not change `DEFAULT_ENABLED`.** S11a.7 defers enablement to 11c.
- **Do not add user skill directories.** S11b.3 defers `~/.rudra/skills/` and `<project>/.rudra/skills/` to 11c.
- **Commits:** the repo is on `main`. Branch first: `git checkout -b step-11b-skill-wiring`. Do not push.
- **Live acceptance (Task 8) needs a reachable model.** If none is available, stop and report rather than marking the step complete — Step 3 shipped with un-run acceptance evidence and it stayed open for two steps.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/rudra/skills/cache.py` | **New.** `SkillCache`, `cache_key()`, `ensure_cache()`. The only module that knows where rendered skills live |
| `src/rudra/skills/transform.py` | Modify: add `TRANSFORM_VERSION` |
| `src/rudra/config/schema.py` | Modify: un-reserve `[skills]`, add `SkillsConfig`, add `DEFAULTS["skills"]` |
| `src/rudra/config/loader.py` | Modify: validate `[skills]`, construct `SkillsConfig` |
| `src/rudra/config/template.py` | Modify: document `[skills]` in the scaffold |
| `src/rudra/agent/main_agent.py` | Modify: build the cache, add the `/skills/` route, pass sources onward |
| `src/rudra/agent/planner_agent.py` | Modify: `skills_sources` argument, `skills=` on `create_deep_agent`, bootstrap in the prompt |
| `src/rudra/subagents/spec.py` | Modify: `wants_skills: bool = False` on `RudraSubagent` |
| `src/rudra/subagents/registry.py` | Modify: set `wants_skills=True` on coder and tester |
| `src/rudra/subagents/runner.py` | Modify: `skills_sources` on `SubagentContext` |
| `src/rudra/subagents/build.py` | Modify: pass `skills=` when the spec wants them |
| `tests/test_skills_backend_contract.py` | **New.** Task 1 — the route mechanism, proven against real deepagents |
| `tests/test_skills_cache.py` | **New.** Task 2 — key, reuse, completeness, atomicity, fallback |
| `tests/test_config_skills.py` | **New.** Task 3 — the config section |
| `tests/test_skills_wiring.py` | **New.** Tasks 4–6 — who gets `skills=`, who gets the bootstrap |

---

## Task 1: Prove the route mechanism before building on it

**Files:**
- Create: `tests/test_skills_backend_contract.py`

**Interfaces:**
- Consumes: `rudra.skills.transform.render`, `rudra.skills.registry.BUNDLES`/`DEFAULT_ENABLED` (all from 11a).
- Produces: nothing. This task writes no production code.

**Why this is first and alone:** every later task rests on one assumption 11a argued from source but never executed — that `SkillsMiddleware` can read skills through a `CompositeBackend` route, with `virtual_mode=True` stripping the `/skills/` prefix. If that is false, the design is wrong and there is no point building a cache for it. It is also the cheapest possible check: no cache, no config, no model.

- [ ] **Step 1: Record the baseline and branch**

```bash
cd /Users/archish/Documents/ai-ml/Rudra
git checkout -b step-11b-skill-wiring
uv sync
uv run pytest -q 2>&1 | tail -3
```

Expected: `1099 passed, 2 skipped`. Write the number down; every later task compares against it.

- [ ] **Step 2: Write the failing test**

Create `tests/test_skills_backend_contract.py`:

```python
"""Can deepagents read skills through a CompositeBackend route?

Everything in Step 11b rests on this. D13 argued it from the 0.7.4 source
-- filesystem.py:132 says virtual_mode's primary use case is exactly a
CompositeBackend stripping a route prefix, and composite.py:774 says
execute still delegates to the default -- but nothing had run it.

These tests import deepagents internals deliberately. If an upgrade moves
them, this file failing is the signal to re-verify the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware.skills import _list_skills

from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import render


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("skills-cache")
    render(BUNDLES, DEFAULT_ENABLED, dest)
    return dest


def _composite(project: Path, skills_root: Path) -> CompositeBackend:
    """The backend Rudra builds, plus the route this step adds."""
    return CompositeBackend(
        default=LocalShellBackend(root_dir=str(project), virtual_mode=True, env={}),
        routes={
            "/artifacts/": FilesystemBackend(root_dir=str(project / "art"), virtual_mode=True),
            "/skills/": FilesystemBackend(root_dir=str(skills_root), virtual_mode=True),
        },
        artifacts_root="/artifacts",
    )


def test_skills_load_through_the_route(tmp_path: Path, rendered: Path) -> None:
    """The load-bearing assumption of the whole step."""
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)

    skills = _list_skills(_composite(project, rendered), "/skills/active/")

    names = sorted(skill["name"] for skill in skills)
    assert names == sorted(DEFAULT_ENABLED)


def test_skill_paths_are_route_relative(tmp_path: Path, rendered: Path) -> None:
    """The model is shown a path it can actually read_file."""
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)

    skills = _list_skills(_composite(project, rendered), "/skills/active/")

    for skill in skills:
        assert skill["path"].startswith("/skills/active/")
        assert skill["path"].endswith("/SKILL.md")


def test_the_library_tree_is_readable_through_the_route(tmp_path: Path, rendered: Path) -> None:
    """Cross-references point into library/, so it must be reachable too.

    library/ is deliberately NOT a skills source -- it is not indexed --
    but every rewritten cross-reference is a /skills/library/... path the
    model is expected to read.
    """
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)
    backend = _composite(project, rendered)

    response = backend.download_files(
        ["/skills/library/superpowers/using-git-worktrees/SKILL.md"]
    )[0]

    assert response is not None


def test_execute_still_reaches_the_project(tmp_path: Path, rendered: Path) -> None:
    """Adding a route must not break C3.1. Regression guard."""
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)
    (project / "marker.txt").write_text("hello", encoding="utf-8")

    result = _composite(project, rendered).execute("cat marker.txt")

    assert "hello" in str(result)
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_skills_backend_contract.py -q`
Expected: **4 passed.**

This is a characterization test of deepagents, so it should pass immediately — the mechanism either works or the design is wrong.

**If `test_skills_load_through_the_route` fails: STOP.** Do not proceed to Task 2, and do not work around it. Report what `_list_skills` returned. The likely causes, in order: the route key needs no trailing slash, `virtual_mode` behaves differently than `filesystem.py:132` describes, or source paths must omit the route prefix. Each of those changes §4 of the spec, which is a design decision, not an implementation detail.

**If `test_execute_still_reaches_the_project` fails**, the composite is not delegating and `C3.1` is broken by the route — also a stop.

If the `download_files` or `execute` call signatures differ from the above, read the current ones and adapt — the contract under test is "the route serves files and shell still reaches the project", not the argument list:

```bash
grep -n "def download_files\|def execute" .venv/lib/python3.13/site-packages/deepagents/backends/composite.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_skills_backend_contract.py
git commit -m "test(skills): prove SkillsMiddleware reads through a composite route

Step 11b rests on this and 11a never executed it. Also guards C3.1: adding
a route must not stop execute delegating to the project-rooted default."
```

---

## Task 2: The cache

**Files:**
- Create: `src/rudra/skills/cache.py`
- Modify: `src/rudra/skills/transform.py` (add `TRANSFORM_VERSION`)
- Test: `tests/test_skills_cache.py`

**Interfaces:**
- Consumes: `render`, `BUNDLES`, `DEFAULT_ENABLED`, `manifest_root_hash` (11a).
- Produces: `rudra.skills.cache.SkillCache` (frozen dataclass: `root: Path`, `active_path: str`, `fell_back: bool`), `cache_key(bundles, enabled) -> str`, `ensure_cache(bundles, enabled, *, cache_home: Path | None = None) -> SkillCache`. `rudra.skills.transform.TRANSFORM_VERSION: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_cache.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from rudra.skills.cache import SENTINEL, cache_key, ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED


def test_key_is_stable_for_the_same_inputs() -> None:
    assert cache_key(BUNDLES, DEFAULT_ENABLED) == cache_key(BUNDLES, DEFAULT_ENABLED)


def test_key_changes_with_the_enabled_set() -> None:
    fewer = frozenset(set(DEFAULT_ENABLED) - {"brainstorming"})

    assert cache_key(BUNDLES, DEFAULT_ENABLED) != cache_key(BUNDLES, fewer)


def test_key_ignores_enabled_ordering() -> None:
    """A frozenset has no order; the key must not acquire one."""
    a = frozenset({"brainstorming", "writing-plans"})
    b = frozenset({"writing-plans", "brainstorming"})

    assert cache_key(BUNDLES, a) == cache_key(BUNDLES, b)


def test_builds_a_usable_cache(tmp_path: Path) -> None:
    cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    assert cache.active_path == "/skills/active/"
    assert cache.fell_back is False
    assert (cache.root / "active" / "brainstorming" / "SKILL.md").is_file()
    assert (cache.root / "library" / "superpowers" / "writing-skills" / "SKILL.md").is_file()
    assert (cache.root / SENTINEL).is_file()


def test_a_complete_cache_is_reused_not_rebuilt(tmp_path: Path) -> None:
    first = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)
    marker = first.root / "active" / "brainstorming" / "SKILL.md"
    marker.write_text("TOUCHED", encoding="utf-8")

    second = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    assert second.root == first.root
    assert marker.read_text(encoding="utf-8") == "TOUCHED"


def test_an_incomplete_cache_is_rebuilt(tmp_path: Path) -> None:
    """Existence is not completeness.

    A run killed mid-render leaves a half-populated directory. The key is a
    pure function of its inputs, so nothing would ever invalidate it --
    every later run would reuse the wreckage.
    """
    first = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)
    (first.root / SENTINEL).unlink()
    (first.root / "active" / "brainstorming" / "SKILL.md").write_text("HALF", encoding="utf-8")

    second = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    body = (second.root / "active" / "brainstorming" / "SKILL.md").read_text(encoding="utf-8")
    assert body != "HALF"
    assert (second.root / SENTINEL).is_file()


def test_no_temp_directories_are_left_behind(tmp_path: Path) -> None:
    cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path)

    siblings = [p.name for p in cache.root.parent.iterdir()]
    assert [n for n in siblings if ".tmp-" in n] == []


def test_falls_back_to_a_temp_dir_when_the_root_is_unwritable(tmp_path: Path) -> None:
    """S11b.2: an unwritable cache must not change how the agent reasons."""
    blocked = tmp_path / "readonly"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=blocked)

        assert cache.fell_back is True
        assert (cache.root / "active" / "brainstorming" / "SKILL.md").is_file()
        assert (cache.root / SENTINEL).is_file()
    finally:
        blocked.chmod(0o700)


def test_the_fallback_cache_is_still_readable_by_deepagents(tmp_path: Path) -> None:
    """Asserted through the middleware, not by trusting the flag."""
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.skills import _list_skills

    blocked = tmp_path / "readonly"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        cache = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=blocked)
        backend = FilesystemBackend(root_dir=str(cache.root), virtual_mode=True)

        skills = _list_skills(backend, "/active/")

        assert sorted(s["name"] for s in skills) == sorted(DEFAULT_ENABLED)
    finally:
        blocked.chmod(0o700)


def test_an_empty_enabled_set_still_renders_the_library(tmp_path: Path) -> None:
    """`enabled = []` means "index nothing", not "build nothing".

    Callers skip the cache entirely in that case (see main_agent), but the
    cache module itself must not special-case it into a broken tree.
    """
    cache = ensure_cache(BUNDLES, frozenset(), cache_home=tmp_path)

    assert list((cache.root / "active").iterdir()) == []
    assert (cache.root / "library" / "superpowers" / "brainstorming" / "SKILL.md").is_file()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_skills_cache.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.skills.cache'`

- [ ] **Step 3: Add the transform version**

In `src/rudra/skills/transform.py`, directly below `_REWRITABLE_SUFFIXES`:

```python
# Bumped whenever the rendering rules change. The cache key hashes the
# transform's *inputs*, which cannot detect a change to the transform
# itself -- without this, a Rudra upgrade that rewrote cross-references
# differently would silently reuse the old rendering.
TRANSFORM_VERSION = 1
```

Add `"TRANSFORM_VERSION"` to that module's `__all__` if it has one; if not, no change is needed.

- [ ] **Step 4: Write the cache**

Create `src/rudra/skills/cache.py`:

```python
"""Where rendered skills live on disk.

The only module that knows about cache locations. `transform.render()`
stays pure and unaware, which is what lets tests render anywhere.

One install serves many project directories (D13 / TODO.md §0.4: `rudra`
is installed once, globally, then run wherever you cd to), so the cache is
user-level rather than per-project. Two projects with the same enabled set
share one directory; a project that enables a different set gets its own
key rather than fighting over one.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import rudra
from rudra.skills.bundle import Bundle
from rudra.skills.manifest import manifest_root_hash
from rudra.skills.transform import TRANSFORM_VERSION, render

# Written last, so a directory that exists but lacks it is known to be
# half-built. Existence is not completeness: the key is a pure function of
# its inputs, so a run killed mid-render would otherwise leave wreckage
# that nothing ever invalidates.
SENTINEL = ".complete"

_ACTIVE_ROUTE = "/skills/active/"


@dataclass(frozen=True)
class SkillCache:
    """One run's rendered corpus.

    Attributes:
        root: The directory holding `library/` and `active/`.
        active_path: The backend-relative source to hand `create_deep_agent`.
        fell_back: True when the cache root was unwritable and this is a
            per-run temp directory. Callers report it; nothing else changes,
            which is the point (S11b.2).
    """

    root: Path
    active_path: str = _ACTIVE_ROUTE
    fell_back: bool = False


def _cache_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "rudra" / "skills"


def cache_key(bundles: Sequence[Bundle], enabled: frozenset[str]) -> str:
    """A digest of everything that can change the rendered output.

    The corpus hash is what makes a hand-update of a vendored bundle
    (S11a.3) invalidate every user's cache with no version negotiation
    anywhere: different bytes, different manifest, different key.
    """
    digest = hashlib.sha256()
    digest.update(rudra.__version__.encode("utf-8"))
    digest.update(str(TRANSFORM_VERSION).encode("utf-8"))
    for bundle in bundles:
        digest.update(bundle.name.encode("utf-8"))
        digest.update(manifest_root_hash(bundle.manifest_path).encode("utf-8"))
    for name in sorted(enabled):
        digest.update(name.encode("utf-8"))
    return digest.hexdigest()[:16]


def _build(bundles: Sequence[Bundle], enabled: frozenset[str], target: Path) -> None:
    """Render beside `target`, then publish with one atomic rename.

    Nothing is ever written into the final path, so a concurrent run either
    sees no cache or a complete one -- never a partial tree.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f"{target.name}.tmp-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        render(bundles, enabled, staging)
        (staging / SENTINEL).write_text("", encoding="utf-8")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def ensure_cache(
    bundles: Sequence[Bundle],
    enabled: frozenset[str],
    *,
    cache_home: Path | None = None,
) -> SkillCache:
    """The rendered corpus for these inputs, building it if needed.

    Falls back to a per-run temp directory when the cache root cannot be
    written -- a read-only home, a locked-down CI box, a sandboxed
    container. Rendering costs about 20ms for 550KB, so the fallback buys
    the property that matters: the same command produces the same agent
    whether or not the cache is writable.
    """
    target = _cache_home(cache_home) / cache_key(bundles, enabled)

    if (target / SENTINEL).is_file():
        return SkillCache(root=target)

    try:
        _build(bundles, enabled, target)
    except OSError:
        scratch = Path(tempfile.mkdtemp(prefix="rudra-skills-"))
        _build(bundles, enabled, scratch / "rendered")
        return SkillCache(root=scratch / "rendered", fell_back=True)

    return SkillCache(root=target)


__all__ = ["SENTINEL", "SkillCache", "cache_key", "ensure_cache"]
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_skills_cache.py -q`
Expected: **10 passed.**

If `test_falls_back_to_a_temp_dir_when_the_root_is_unwritable` passes when it should fail, check whether you are running as root — `chmod 0o500` does not stop root from writing. Run as a normal user.

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra/skills tests/test_skills_cache.py
git commit -m "feat(skills): build the rendered corpus into a keyed cache

Keyed on rudra version, each bundle's content hash, the transform version
and the enabled set -- every input that can change the output. A .complete
sentinel written last distinguishes a finished cache from one left by a
killed run, which the key alone could never invalidate.

An unwritable cache root falls back to a per-run temp dir rather than
degrading the agent, because a run that reasons differently on a
locked-down box than on a laptop is worse than one that spends 20ms."
```

---

## Task 3: The `[skills]` config section

**Files:**
- Modify: `src/rudra/config/schema.py`
- Modify: `src/rudra/config/loader.py`
- Modify: `src/rudra/config/template.py`
- Test: `tests/test_config_skills.py`

**Interfaces:**
- Consumes: `BUNDLES` (for validating names).
- Produces: `rudra.config.schema.SkillsConfig` with one field, `enabled: tuple[str, ...]`; `cfg.skills.enabled` on the loaded `Config`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_skills.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from rudra.config.loader import ConfigError, load_config
from rudra.skills.registry import DEFAULT_ENABLED


def _write(tmp_path: Path, body: str) -> Path:
    rudra_dir = tmp_path / ".rudra"
    rudra_dir.mkdir(parents=True, exist_ok=True)
    (rudra_dir / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def test_skills_defaults_to_the_shipped_set(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, "[agent]\nmax_questions = 2\n"))

    assert set(cfg.skills.enabled) == set(DEFAULT_ENABLED)


def test_skills_section_is_no_longer_reserved(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, '[skills]\nenabled = ["brainstorming"]\n'))

    assert cfg.skills.enabled == ("brainstorming",)


def test_an_empty_enabled_list_is_allowed(tmp_path: Path) -> None:
    """The off switch. S11b.5: one key does both jobs."""
    cfg = load_config(_write(tmp_path, "[skills]\nenabled = []\n"))

    assert cfg.skills.enabled == ()


def test_an_unknown_skill_name_is_a_hard_error(tmp_path: Path) -> None:
    """A typo must not mean a skill that silently never loads."""
    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, '[skills]\nenabled = ["brainstorm"]\n'))

    message = str(excinfo.value)
    assert "brainstorm" in message
    assert "brainstorming" in message


def test_an_unknown_key_in_skills_is_a_hard_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, "[skills]\nenable = true\n"))

    assert "enable" in str(excinfo.value)


def test_enabled_must_be_a_list(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(_write(tmp_path, '[skills]\nenabled = "brainstorming"\n'))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_config_skills.py -q`
Expected: FAIL — the reserved-section check rejects `[skills]`, and `cfg.skills` does not exist.

If `load_config`'s signature differs from `load_config(project_path)`, read it and adapt the helper:

```bash
grep -n "^def load_config" -A8 src/rudra/config/loader.py
```

- [ ] **Step 3: Un-reserve the section and add the dataclass**

In `src/rudra/config/schema.py`, remove the `"skills"` entry from `RESERVED_SECTIONS` (leaving `memory` and `mcp`), then add beside `ToolsConfig`:

```python
@dataclass(frozen=True)
class SkillsConfig:
    """Which skills enter an agent's prompt index. Un-reserved in Step 11b.

    One key, deliberately. `enabled = []` is the off switch, so a separate
    `enable = false` would be a second way to say the same thing.

    Names are validated against the vendored bundles at load time: an
    unknown name is a typo, and `SkillsMiddleware` *silently skips* a skill
    it cannot find, so accepting one would mean a skill that never loads
    and never explains itself.
    """

    enabled: tuple[str, ...]
```

Add to `DEFAULTS`, after `"tools"`:

```python
    # The nine DEFAULT_ENABLED ships as data in rudra.skills.registry, not
    # duplicated here -- loader.py fills it in when the key is absent.
    "skills": {},
```

Add `"SkillsConfig"` to `__all__`.

- [ ] **Step 4: Validate and construct it**

In `src/rudra/config/loader.py`, add the validation beside the other per-section validators (follow the shape of the `[permissions]` one at `:171`):

```python
def _validate_skills(section: dict) -> None:
    from rudra.skills.registry import BUNDLES

    valid_keys = ("enabled",)
    for key in section:
        if key not in valid_keys:
            raise ConfigError(f"Unknown key '{key}' in [skills].{_suggest(key, valid_keys)}")

    enabled = section.get("enabled")
    if enabled is None:
        return
    if not isinstance(enabled, list) or not all(isinstance(n, str) for n in enabled):
        msg = "[skills] enabled must be a list of skill names, e.g. enabled = [\"brainstorming\"]"
        raise ConfigError(msg)

    known = tuple(sorted({name for bundle in BUNDLES for name in bundle.skill_names()}))
    for name in enabled:
        if name not in known:
            raise ConfigError(f"Unknown skill '{name}' in [skills] enabled.{_suggest(name, known)}")
```

Call `_validate_skills(merged.get("skills", {}))` alongside the other validators, then construct the config beside `tools=`:

```python
        skills=SkillsConfig(
            enabled=tuple(merged.get("skills", {}).get("enabled", sorted(DEFAULT_ENABLED)))
        ),
```

Import `SkillsConfig` from the schema and `DEFAULT_ENABLED` from `rudra.skills.registry` at the top of the constructing function, not at module scope — `schema.py` imports nothing from Rudra by design, and Step 6 deadlocked on exactly this kind of circular import.

Add the `skills: SkillsConfig` field to the `Config` dataclass wherever `tools: ToolsConfig` is declared.

- [ ] **Step 5: Document it in the scaffold**

In `src/rudra/config/template.py`, add after the `[tools]` block:

```toml
# [skills]
# Which of the bundled superpowers skills enter an agent's prompt index.
# Omit the section for the shipped set; `enabled = []` turns skills off.
# Names must match a vendored skill -- a typo is an error, not a silent skip.
# enabled = ["brainstorming", "test-driven-development", "systematic-debugging"]
```

Leave it commented: the default is the shipped set, and an uncommented list here would freeze today's nine into every new project's config.

- [ ] **Step 6: Run the tests**

```bash
uv run pytest tests/test_config_skills.py -q
uv run pytest tests/test_config_schema.py tests/test_config_loader.py tests/test_config_layers.py -q
```

Expected: 6 passed, then the existing config suites still green. If a test asserts `[skills]` is reserved, it is now wrong — update it to assert `[memory]` instead, and say so in the commit.

- [ ] **Step 7: Gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra/config tests/test_config_skills.py
git commit -m "feat(config): un-reserve [skills] with a single enabled key

enabled = [] is the off switch, so no separate enable flag. Names are
validated against the vendored bundles because SkillsMiddleware silently
skips a skill it cannot find -- accepting a typo would mean a skill that
never loads and never says why."
```

---

## Task 4: Mount the cache and give the planner skills

**Files:**
- Modify: `src/rudra/agent/main_agent.py`
- Modify: `src/rudra/agent/planner_agent.py`
- Test: `tests/test_skills_wiring.py`

**Interfaces:**
- Consumes: `ensure_cache` (Task 2), `cfg.skills.enabled` (Task 3).
- Produces: `build_backend(cfg, project_path, skills_root: Path | None = None)`; `create_planner_agent(..., skills_sources: tuple[str, ...] | None = None)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_wiring.py`:

```python
"""Who is constructed with skills, and who is not."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from rudra.skills.cache import ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED


@pytest.fixture
def cache_root(tmp_path: Path) -> Path:
    return ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path / "cache").root


def test_build_backend_mounts_the_skills_route(tmp_path: Path, cache_root: Path) -> None:
    from rudra.agent.main_agent import build_backend
    from rudra.config.loader import load_config

    (tmp_path / ".rudra").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rudra" / "config.toml").write_text("", encoding="utf-8")
    cfg = load_config(tmp_path)

    backend = build_backend(cfg, tmp_path, skills_root=cache_root)

    assert "/skills/" in backend.routes


def test_build_backend_without_a_cache_has_no_skills_route(tmp_path: Path) -> None:
    """Every existing caller passes no cache and must keep working."""
    from rudra.agent.main_agent import build_backend
    from rudra.config.loader import load_config

    (tmp_path / ".rudra").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rudra" / "config.toml").write_text("", encoding="utf-8")
    cfg = load_config(tmp_path)

    backend = build_backend(cfg, tmp_path)

    assert "/skills/" not in backend.routes
    assert "/artifacts/" in backend.routes


def test_planner_is_constructed_with_skills(tmp_path: Path) -> None:
    from rudra.agent import planner_agent

    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="x",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage="clarify",
            skills_sources=("/skills/active/",),
        )

    assert spy.call_args.kwargs["skills"] == ["/skills/active/"]


def test_planner_without_sources_passes_no_skills_argument(tmp_path: Path) -> None:
    """None means absent, not empty -- every existing test relies on it."""
    from rudra.agent import planner_agent

    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="x",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage="clarify",
        )

    assert spy.call_args.kwargs.get("skills") is None


def _console():
    from rich.console import Console

    return Console(quiet=True)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_skills_wiring.py -q`
Expected: FAIL — `build_backend()` got an unexpected keyword argument `skills_root`.

If `create_planner_agent`'s parameters differ from the call above, read the signature and adapt — its arguments changed across Steps 9c and 10b:

```bash
grep -n "^def create_planner_agent" -A16 src/rudra/agent/planner_agent.py
```

- [ ] **Step 3: Add the route**

In `src/rudra/agent/main_agent.py`, change `build_backend`'s signature and route dict:

```python
def build_backend(cfg, project_path: Path, skills_root: Path | None = None):
```

and inside, replacing the `return CompositeBackend(...)`:

```python
    routes = {
        "/artifacts/": FilesystemBackend(root_dir=str(paths.artifacts), virtual_mode=True),
    }
    if skills_root is not None:
        # Read-only in practice: nothing writes here, and the agents given
        # skills have no reason to. virtual_mode is what lets the composite
        # strip the "/skills/" prefix (filesystem.py:132).
        routes["/skills/"] = FilesystemBackend(root_dir=str(skills_root), virtual_mode=True)

    return CompositeBackend(
        default=default,
        routes=routes,
        artifacts_root="/artifacts",
    )
```

Update the docstring's "Step 11 adds a /skills/ route" line to say it now has.

- [ ] **Step 4: Build the cache during run setup**

In `create_main_agent`, immediately before the `build_backend` call at `main_agent.py:410`:

```python
    # Skills are optional by configuration: `enabled = []` means no cache is
    # built at all, not an empty one rendered and mounted for nothing.
    skill_cache = None
    if cfg.skills.enabled:
        from rudra.skills.cache import ensure_cache
        from rudra.skills.registry import BUNDLES

        skill_cache = ensure_cache(BUNDLES, frozenset(cfg.skills.enabled))
        if skill_cache.fell_back:
            console.print(
                "[dim]Skills cache is not writable; using a temporary copy for this run.[/dim]"
            )

    filesystem_backend = build_backend(
        cfg, project_path, skills_root=skill_cache.root if skill_cache else None
    )
```

Then define, for the constructors below it:

```python
    skills_sources = (skill_cache.active_path,) if skill_cache else None
```

and pass `skills_sources=skills_sources` to the planner construction.

- [ ] **Step 5: Accept and forward it in the planner**

In `src/rudra/agent/planner_agent.py`, add `skills_sources: tuple[str, ...] | None = None` to `create_planner_agent`'s keyword arguments, then in the `create_deep_agent(...)` call at `:296` add:

```python
        skills=list(skills_sources) if skills_sources else None,
```

- [ ] **Step 6: Run the tests**

```bash
uv run pytest tests/test_skills_wiring.py -q
uv run pytest tests/test_backend_wiring.py tests/test_agent_wiring.py -q
```

Expected: 4 passed, and the existing wiring suites still green.

- [ ] **Step 7: Gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra/agent tests/test_skills_wiring.py
git commit -m "feat(skills): mount the cache and index it for the planner

build_backend gains an optional /skills/ route beside /artifacts/, and the
three planner stages are constructed with it. skills_root=None keeps every
existing caller building the backend it built before.

enabled = [] builds no cache at all rather than mounting an empty one."
```

---

## Task 5: Give the coder and tester skills

**Files:**
- Modify: `src/rudra/subagents/spec.py`
- Modify: `src/rudra/subagents/registry.py`
- Modify: `src/rudra/subagents/runner.py`
- Modify: `src/rudra/subagents/build.py`
- Modify: `src/rudra/agent/main_agent.py` (pass sources into the context)
- Modify: `tests/test_skills_wiring.py`

**Interfaces:**
- Consumes: Task 4's `skills_sources`.
- Produces: `RudraSubagent.wants_skills: bool = False`; `SubagentContext.skills_sources: tuple[str, ...] | None = None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills_wiring.py`:

```python
def test_only_the_writing_subagents_want_skills() -> None:
    """S11b.1. The reviewer reads a diff; it is not choosing a method."""
    from rudra.subagents.registry import REGISTRY

    wanting = {name for name, spec in REGISTRY.items() if spec.wants_skills}

    assert wanting == {"coder", "tester"}


@pytest.mark.parametrize("name", ["coder", "tester"])
def test_wanting_subagents_are_built_with_skills(name: str, tmp_path: Path) -> None:
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=("/skills/active/",))

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY[name], context)

    assert spy.call_args.kwargs["skills"] == ["/skills/active/"]


@pytest.mark.parametrize("name", ["reviewer", "general-purpose"])
def test_other_subagents_are_built_without_skills(name: str, tmp_path: Path) -> None:
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=("/skills/active/",))

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY[name], context)

    assert spy.call_args.kwargs.get("skills") is None


def test_no_subagent_gets_skills_when_the_run_has_none(tmp_path: Path) -> None:
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=None)

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY["coder"], context)

    assert spy.call_args.kwargs.get("skills") is None


def _subagent_context(tmp_path: Path, *, skills_sources):
    from rudra.config.loader import load_config
    from rudra.subagents.runner import SubagentContext

    (tmp_path / ".rudra").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".rudra" / "config.toml").write_text("", encoding="utf-8")
    return SubagentContext(
        project_path=tmp_path,
        backend=None,
        gate=None,
        console=_console(),
        cfg=load_config(tmp_path),
        skills_sources=skills_sources,
    )
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_skills_wiring.py -q`
Expected: FAIL — `RudraSubagent` has no attribute `wants_skills`.

If `build_agent` fails constructing a model for a context with no gate, check how `tests/test_subagents_build.py` builds its context and copy that shape — the point of these tests is the `skills` kwarg, not model construction.

- [ ] **Step 3: Add the field to the spec**

In `src/rudra/subagents/spec.py`, add to `RudraSubagent` after `rudra_tools`:

```python
    wants_skills: bool = False
```

and to the docstring's attribute list:

```
        wants_skills: Does this subagent get the run's skill index? A
            methodology library helps an agent choosing *how* to work; the
            reviewer reads a diff and reports, and would pay ~916 tokens
            for descriptions it cannot act on (S11b.1).
```

- [ ] **Step 4: Set it on coder and tester**

In `src/rudra/subagents/registry.py`, add `wants_skills=True` to the `coder` and `tester` specs only. Leave `reviewer` and `general-purpose` untouched — the default is False.

- [ ] **Step 5: Carry the sources on the context**

In `src/rudra/subagents/runner.py`, add to `SubagentContext` after `facts`:

```python
    # The run's skill sources, or None when skills are off. Shared by
    # reference for the same reason the gate and FactStore are: one run,
    # one cache.
    skills_sources: tuple[str, ...] | None = None
```

In `src/rudra/subagents/build.py`, inside `build_agent`'s `create_deep_agent(...)` call:

```python
        skills=list(context.skills_sources)
        if (spec.wants_skills and context.skills_sources)
        else None,
```

Do the same in `to_subagent_spec` so the delegated path matches the direct one — adding the `"skills"` key only when the spec wants them, since `create_sub_agent` reads it from the dict (`graph.py:676-678`).

- [ ] **Step 6: Pass it from run setup**

In `src/rudra/agent/main_agent.py`, wherever `SubagentContext(...)` is constructed, add `skills_sources=skills_sources`. Find it with:

```bash
grep -n "SubagentContext(" src/rudra/agent/main_agent.py src/rudra/loop/*.py
```

- [ ] **Step 7: Run the tests**

```bash
uv run pytest tests/test_skills_wiring.py -q
uv run pytest tests/test_subagents_build.py tests/test_subagents_registry.py -q
```

Expected: 9 passed in the first, existing suites green.

- [ ] **Step 8: Gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra tests/test_skills_wiring.py
git commit -m "feat(skills): index skills for the coder and tester

wants_skills is declared on the spec, so which subagents get a skill index
stays a data change in registry.py like every other capability. The
reviewer and general-purpose agent are left out: neither is choosing a
method, and both would pay ~916 tokens for descriptions they cannot act on."
```

---

## Task 6: Inject the bootstrap into the planner

**Files:**
- Modify: `src/rudra/agent/planner_agent.py`
- Modify: `tests/test_skills_wiring.py`

**Interfaces:**
- Consumes: the rendered cache from Task 2, the sources from Task 4.
- Produces: `planner_agent.bootstrap_text(cache_root: Path) -> str | None`.

**Why the bootstrap is planner-only:** `using-superpowers/SKILL.md:5-7` opens with `<SUBAGENT-STOP>` — *"If you were dispatched as a subagent to execute a specific task, ignore this skill."* Injecting it into the coder spends ~780 tokens telling it to disregard what it just read. Upstream drew this line (S11b.4).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills_wiring.py`:

```python
def test_bootstrap_is_read_from_the_rendered_cache(cache_root: Path) -> None:
    """It must be the rendered copy, not the packaged one.

    The rendered copy carries both 11a adaptations: cross-references
    rewritten to paths, and the Platform Adaptation list naming
    rudra-tools.md. A copy imported from src/ would send the model looking
    for a plugin namespace that does not exist.
    """
    from rudra.agent.planner_agent import bootstrap_text

    text = bootstrap_text(cache_root)

    assert text is not None
    assert "references/rudra-tools.md" in text
    assert "superpowers:" not in text


def test_bootstrap_is_none_when_it_is_not_in_the_cache(tmp_path: Path) -> None:
    from rudra.agent.planner_agent import bootstrap_text

    assert bootstrap_text(tmp_path) is None


@pytest.mark.parametrize("stage", ["clarify", "architect", "breakdown"])
def test_every_planner_stage_carries_the_bootstrap(stage: str, tmp_path: Path, cache_root: Path) -> None:
    from rudra.agent import planner_agent

    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="x",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage=stage,
            skills_sources=("/skills/active/",),
            skills_cache_root=cache_root,
        )

    prompt = spy.call_args.kwargs["system_prompt"]
    assert "Platform Adaptation" in prompt


def test_the_planner_prompt_has_no_bootstrap_without_a_cache(tmp_path: Path) -> None:
    from rudra.agent import planner_agent

    with patch.object(planner_agent, "create_deep_agent") as spy:
        planner_agent.create_planner_agent(
            task="x",
            project_path=tmp_path,
            filesystem_backend=None,
            checkpointer=None,
            console=_console(),
            stage="clarify",
        )

    assert "Platform Adaptation" not in spy.call_args.kwargs["system_prompt"]


def test_subagent_prompts_never_carry_the_bootstrap(tmp_path: Path) -> None:
    """SUBAGENT-STOP: upstream tells subagents to ignore this skill."""
    from rudra.subagents import build
    from rudra.subagents.registry import REGISTRY

    context = _subagent_context(tmp_path, skills_sources=("/skills/active/",))

    with patch.object(build, "create_deep_agent") as spy:
        build.build_agent(REGISTRY["coder"], context)

    assert "Platform Adaptation" not in spy.call_args.kwargs["system_prompt"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_skills_wiring.py -q`
Expected: FAIL — `cannot import name 'bootstrap_text'`.

- [ ] **Step 3: Implement it**

In `src/rudra/agent/planner_agent.py`, add near the other module-level helpers:

```python
BOOTSTRAP_SKILL = "using-superpowers"


def bootstrap_text(cache_root: Path | None) -> str | None:
    """The rendered bootstrap skill, or None when it is not there.

    Read from the *cache*, never from the package: the rendered copy has
    its cross-references rewritten to readable paths and its Platform
    Adaptation list pointing at references/rudra-tools.md. A packaged copy
    would send the model after a plugin namespace Rudra does not have.

    Skills are inert without this (C5.3). The index gives the model nine
    descriptions; this is what tells it to act on them.
    """
    if cache_root is None:
        return None
    path = cache_root / "library" / "superpowers" / BOOTSTRAP_SKILL / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None
```

Add `skills_cache_root: Path | None = None` to `create_planner_agent`'s keyword arguments, and append the bootstrap to the prompt it builds:

```python
    system_prompt = build_planner_prompt(
        task,
        project_path,
        facts,
        stage=stage,
        can_ask=interactive and stage == "clarify" and cfg.agent.max_questions > 0,
        max_questions=cfg.agent.max_questions,
    )
    bootstrap = bootstrap_text(skills_cache_root)
    if bootstrap:
        system_prompt = f"{system_prompt}\n\n---\n\n{bootstrap}"
```

then pass `system_prompt=system_prompt` in the `create_deep_agent` call instead of the inline `build_planner_prompt(...)`.

In `src/rudra/agent/main_agent.py`, pass `skills_cache_root=skill_cache.root if skill_cache else None` alongside `skills_sources` at the planner construction site.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_skills_wiring.py -q`
Expected: **15 passed.**

- [ ] **Step 5: Read the assembled prompt once, by eye**

```bash
uv run python - <<'PY'
import tempfile, pathlib
from rudra.skills.cache import ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.agent.planner_agent import bootstrap_text, build_planner_prompt

root = ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=pathlib.Path(tempfile.mkdtemp())).root
prompt = build_planner_prompt("build a CSV parser", pathlib.Path("."), None, stage="clarify")
full = prompt + "\n\n---\n\n" + bootstrap_text(root)
print(full[-2600:])
print("\n--- total chars:", len(full), "~tokens:", len(full)//4)
PY
```

Read the seam where Rudra's prompt meets the bootstrap. Confirm the two do not contradict each other — specifically that the bootstrap's insistence on invoking skills before responding does not read as licence to skip the stage's own instructions. If it does, that is a finding for the ledger, not something to fix by quietly editing the vendored text (which is frozen anyway).

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra/agent tests/test_skills_wiring.py
git commit -m "feat(skills): inject the rendered bootstrap into the planner

Closes C5.3. Read from the cache rather than the package so it carries
both 11a adaptations -- rewritten cross-references and the Rudra platform
reference line -- which is what closes the chain from index to skill.

Planner stages only: using-superpowers opens with SUBAGENT-STOP telling
dispatched subagents to ignore it."
```

---

## Task 7: Update the documentation this step makes false

**Files:**
- Modify: `Documentation/12-skills.md`
- Modify: `Documentation/08-project-status.md`
- Modify: `Documentation/02-configuration.md`
- Modify: `Documentation/11-tools.md`
- Modify: `README.md`

**Interfaces:** none. Documentation only.

**Why this is a task and not a footnote:** all five files were written on 2026-08-17 stating plainly that skills are *not wired*. That was true when written and is false the moment Task 6 lands. A status page that overclaims is worse than one that underclaims, and this repo's `08-project-status.md` had already drifted three steps once.

- [ ] **Step 1: Take down the banner in `12-skills.md`**

Replace the whole `> ### Status: vendored, not yet active` blockquote with:

```markdown
> ### Status: live, for some agents
>
> The three planning stages, the coder and the tester carry the skill index
> and can read any of the fourteen skills by path. The reviewer and the
> general-purpose agent do not — neither is choosing a method, and both
> would pay tokens for descriptions they cannot act on.
>
> Select a different set with `[skills] enabled` in `config.toml`, or turn
> skills off entirely with `enabled = []`. You cannot yet add your own —
> see [Adding your own](#adding-your-own).
```

Then update the "Adding your own" section: it currently reads as though the entire feature is inert. It should say the bundled corpus is live and that *user-authored* skills, plus `rudra skills`, arrive next — the reason being that a malformed `SKILL.md` is silently skipped and the command that diagnoses it does not exist yet.

- [ ] **Step 2: Move the claim in `08-project-status.md`**

Delete the `### Skills ship but aren't connected` entry from *What doesn't work yet*. Rewrite the *What works* bullet that currently begins "**A vendored methodology library — on disk, not yet in use.**" to describe what now happens, and say what is still missing: no user-authored skills, and no `rudra skills` command until 11c. Update the page's "Last updated" line to the date the work lands. In the roadmap, the **Skills** row's remaining scope is now 11c only — user skills, the CLI, the session-start hook, and the selection-quality measurement.

- [ ] **Step 3: Correct `02-configuration.md`**

Two edits. First, the page quotes a reserved-section error that no longer fires:

```
Configuration error: [skills] is not supported yet — arrives in Step 11 (C5.1).
```

Replace it with `[memory]`, which is still reserved and makes the same point:

```
Configuration error: [memory] is not supported yet — arrives in Step 14 (C8.1).
```

Delete the paragraph beneath it that begins "`[skills]` is worth a note, because the library it would configure is already in the package" — it exists only to explain the reserved section.

Second, document the live section beside `[tools]`:

```markdown
## The skills section

| Key | Means |
|---|---|
| `enabled` | Which bundled skills enter an agent's prompt index |

Omit the section entirely for the shipped set of nine. Name a subset to
narrow it, or set `enabled = []` to switch skills off for every agent.

An unknown name is an error, not a warning. `SkillsMiddleware` silently
skips a skill it cannot find, so accepting a typo would mean a skill that
never loads and never explains why.

See [Skills](12-skills.md) for what each one does.
```

- [ ] **Step 4: Add a line to `11-tools.md` and `README.md`**

`11-tools.md`: in the per-agent grant table's surrounding prose, note that the planner stages, coder and tester also carry a skill index, and link to `12-skills.md`.

`README.md`: replace the `> **Vendored, not yet wired.**` blockquote in the superpowers section with one sentence saying which agents draw on the library and how to turn it off.

- [ ] **Step 5: Check every link still resolves**

```bash
for f in README.md Documentation/*.md; do
  grep -oE '\]\(([^)#h][^)]*)\)' "$f" | sed 's/](//;s/)$//' | while read -r link; do
    dir=$(dirname "$f"); [ -e "$dir/$link" ] || echo "BROKEN in $f -> $link"
  done
done; echo "(no BROKEN lines = all resolve)"
```

- [ ] **Step 6: Commit**

```bash
git add README.md Documentation/
git commit -m "docs: skills are wired — correct the five files that said otherwise

12-skills.md's not-yet-active banner, 08-project-status.md's entry under
what doesn't work, 02-configuration.md's quote of a reserved-section error
that no longer fires, plus a line each in 11-tools.md and README.md."
```

---

## Task 8: Acceptance and the ledger

**Files:**
- Modify: `TODO.md`

**Interfaces:** none.

**This task needs a reachable model.** If none is available, stop and report rather than marking Step 11b complete. Step 3 shipped with un-run acceptance evidence and it stayed open across two later steps; do not repeat that.

- [ ] **Step 1: Prepare a clean acceptance environment**

```bash
cd "$(mktemp -d)"
mkdir skills-acceptance && cd skills-acceptance
git init -q
env -i PATH="$PATH" HOME="$HOME" /path/to/Rudra/.venv/bin/rudra init
```

Edit the generated `.rudra/config.toml` to point at a reachable model, then confirm it works before spending a run on it:

```bash
env -i PATH="$PATH" HOME="$HOME" /path/to/Rudra/.venv/bin/rudra models test
```

Expected: every role `ok` at Construct, Reach and Tools.

- [ ] **Step 2: Run 1 — the default run**

```bash
env -i PATH="$PATH" HOME="$HOME" /path/to/Rudra/.venv/bin/rudra --auto --allow-shell \
  "write a CSV parser that handles quoted commas, with tests" 2>&1 | tee run1.log
```

**The evidence being sought is a log line showing the model calling `read_file` on a `/skills/…/SKILL.md` path of its own accord.** Search for it:

```bash
grep -n "skills/" run1.log | head
```

Record: whether it happened, which skill, at which stage, and whether the run still completed. **If the model never reads a skill, record that verbatim** — it is `C5.8`'s finding arriving early, and it means a 32B model needs more than upstream's bootstrap. Do not tune the prompt until the run looks better; that converts a measurement into a wish.

- [ ] **Step 3: Run 2 — the control**

Add to `.rudra/config.toml`:

```toml
[skills]
enabled = []
```

Re-run the same task. Confirm: no skills in any prompt, no cache directory created, run completes normally. This proves the off switch and isolates run 1's behaviour to the skills change.

- [ ] **Step 4: Run 3 — the unwritable cache**

```bash
mkdir -p /tmp/ro-cache && chmod 500 /tmp/ro-cache
env -i PATH="$PATH" HOME="$HOME" XDG_CACHE_HOME=/tmp/ro-cache \
  /path/to/Rudra/.venv/bin/rudra --auto --allow-shell "write a hello.py that prints Hello" 2>&1 | tee run3.log
chmod 700 /tmp/ro-cache
```

Expected: the line `Skills cache is not writable; using a temporary copy for this run.`, and the run otherwise behaving like run 1 — same skills available, same ability to read them.

- [ ] **Step 5: Run 4 — the plan-approval regression**

```bash
env -i PATH="$PATH" HOME="$HOME" /path/to/Rudra/.venv/bin/rudra \
  "write a wordcount CLI" 2>&1 | tee run4.log
```

Through a real pty so `isatty()` is true. Confirm the plan is presented, that `r` revises and `c` cancels, and that the facts and tasks still render. The planner prompt just grew by ~780 tokens, which is exactly the kind of change that quietly breaks a prompt-sensitive flow.

- [ ] **Step 6: Update the ledger**

In `TODO.md` Phase 5, mark **DONE** with evidence: `C5.2a` (the cache, keyed and atomic, with the fallback), `C5.2b` (the route, plus the `execute` regression guard), `C5.3` (the bootstrap, planner-only per `SUBAGENT-STOP`). Mark `C5.1` **half done** — vendored sources wired, user directories deferred to 11c per S11b.3. Leave `C5.9` untouched.

In §E Stage IV, mark **11b COMPLETE** in the shape 11a's row uses: spec and plan paths, rows closed, the S11b.1–S11b.5 decisions, the measured test count, all four acceptance runs with their outcomes, and the statement that **11c (`C5.5`, `C5.6`, `C5.8`) is unblocked**. If run 1 showed no skill being read, say so in that row — it is the single most important thing 11c inherits.

Add a row for anything the acceptance runs surfaced, `PENDING`, with `file:line` evidence, before fixing it.

- [ ] **Step 7: Final verification and commit**

```bash
cd /path/to/Rudra
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
uv run rudra --version
git diff --stat main..HEAD -- src/ tests/
git add TODO.md
git commit -m "docs: Step 11b ledger — C5.2a, C5.2b, C5.3 done

Skills are wired: the corpus renders into a keyed cache, mounts on the
composite, and reaches the planner stages, coder and tester. C5.1 is half
closed; user skill directories go to 11c with the tooling to debug them."
```

---

## Verification

Run before declaring 11b complete:

```bash
uv run ruff check src/ tests/          # All checks passed!
uv run ruff format --check src/ tests/ # clean
uv run pytest -q                        # >= 1099 + the new tests, zero failures
uv run rudra --version                  # Rudra v0.2.0
```

The four acceptance runs in Task 8 are part of this, not optional. Run 1 in particular: without a log line showing a model reading a skill by path, this step has wired something nobody has watched work.
