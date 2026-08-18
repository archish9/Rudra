# Step 11c — `rudra skills`, User Skills, and the Selection Measurement: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give users a way to see, validate and extend the skill library — `rudra skills list/validate/rebuild` plus their own skill directories — and settle `C5.9` (ship 9 skills or 13) with measured evidence instead of an estimate.

**Architecture:** A new Typer sub-app registered beside `models` and `config`. User skills mount as additional `CompositeBackend` routes pointing at their real directories rather than being rendered into the keyed cache, because their content changes freely and the cache key hashes vendored manifests only. `SkillsMiddleware` receives sources in precedence order — bundled, then user, then project — so later sources shadow earlier ones. Validation delegates to deepagents' own parser rather than reimplementing the contract.

**Tech Stack:** Python 3.12+, Typer + Rich, deepagents 0.7.4 (`CompositeBackend`, `SkillsMiddleware`, `_parse_skill_metadata`, `_validate_skill_name`), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-08-17-step11c-skills-cli-and-measurement-design.md`

> **Note on the spec's location.** `docs/` is gitignored deliberately (A1.83), so the spec is local-only. If you are executing this from a fresh clone and it is absent, stop and ask — do not reconstruct it from this plan. This plan argues *from* the spec and does not replace it.

## Global Constraints

- **`ruff check src/ tests/` must print `All checks passed!`**; `ruff format --check src/ tests/` clean.
- **`uv run pytest -q` must never go down.** Baseline at the start of this step is **1150 passed, 2 skipped**. Re-measure in Task 1 Step 1.
- **Prefer `uv run`** — a local `.venv` drifts from `uv.lock` and will lie to you (CLAUDE.md §9).
- **The suite is hermetic against your `.env` (A1.82) — keep it that way.** `tests/conftest.py` neutralises `load_dotenv` for the repo's own `.env`; any new test that builds a config must use `get_config(root)`, not `build_config(root)`, or it will resolve against `Path.cwd()` and read the developer's real configuration.
- **Never edit anything under `src/rudra/skills/bundles/`** — frozen and hash-verified.
- **Do not change `DEFAULT_ENABLED` until Task 7.** The measurement decides it; changing it first makes the measurement circular.
- **`ROUTE_PREFIXES` and the mounted routes must stay in agreement** — `test_the_normalizer_is_told_about_every_route_the_backend_mounts` enforces it, and that guard is why `A1.79` cannot recur silently. If you add a route, add its prefix.
- **Live runs need a reachable model.** The owner's `.env` points at NVIDIA (`nvidia/nemotron-3-ultra-550b-a55b`). OpenRouter's free tier 502s under load — see `A1.39`.
- **Commits:** work on `step-11c-skills-cli`, which already exists. Do not push.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/rudra/skills/sources.py` | **New.** Resolve the three skill sources and their precedence; the one place that knows where user skills live |
| `src/rudra/skills/validate.py` | **New.** `validate_skill_dir()` — delegates to deepagents' parser, returns findings |
| `src/rudra/cli.py` | Modify: register `skills_app` with `list` / `validate` / `rebuild` |
| `src/rudra/skills/cache.py` | Modify: `prune_stale(cache_home, keep)` for `rebuild` |
| `src/rudra/agent/main_agent.py` | Modify: mount user routes, extend `ROUTE_PREFIXES`, pass ordered sources |
| `tests/test_skills_multiroute_contract.py` | **New.** Task 1 — prove multiple routes resolve, no production code |
| `tests/test_skills_sources.py` | **New.** Precedence and resolution |
| `tests/test_skills_validate.py` | **New.** Validation findings |
| `tests/test_cli_skills.py` | **New.** The three commands |

---

## Task 1: Prove multiple routes resolve before building on them

**Files:**
- Create: `tests/test_skills_multiroute_contract.py`

**Interfaces:**
- Consumes: `render`, `BUNDLES`, `DEFAULT_ENABLED` (11a).
- Produces: nothing. No production code.

**Why first and alone:** the spec (§4) deliberately does not assume whether `CompositeBackend` resolves `/skills/` and `/skills/user/` unambiguously, or whether the shorter prefix swallows the longer. Everything downstream depends on the answer, and it decides the route layout. This is 11b's Task 1 pattern, which is why `A1.79` was that step's only route surprise instead of its second.

- [ ] **Step 1: Record the baseline**

```bash
cd /Users/archish/Documents/ai-ml/Rudra
git checkout step-11c-skills-cli
uv sync
uv run pytest -q 2>&1 | tail -3
```

Expected: `1150 passed, 2 skipped`.

- [ ] **Step 2: Write the probe**

Create `tests/test_skills_multiroute_contract.py`:

```python
"""Can one CompositeBackend serve several skill sources at once?

Step 11c mounts user and project skill directories alongside the bundled
cache. Whether they can be nested under one prefix (/skills/, /skills/user/)
or must be disjoint (/skills/, /user-skills/) is a deepagents behaviour, not
a Rudra decision, and it determines the route layout.

Written before any production code, for the reason 11b's route contract test
was: the cheapest possible check on the assumption everything else rests on.
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

SKILL = """---
name: {name}
description: {description}
---

# {name}

Body.
"""


def _write_skill(root: Path, name: str, description: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        SKILL.format(name=name, description=description), encoding="utf-8"
    )


@pytest.fixture(scope="module")
def bundled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("bundled")
    render(BUNDLES, DEFAULT_ENABLED, dest)
    return dest


def test_nested_prefixes_resolve_independently(tmp_path: Path, bundled: Path) -> None:
    """The layout the spec prefers: /skills/ and /skills/user/ side by side."""
    user = tmp_path / "user"
    _write_skill(user, "deploy-checklist", "Use when shipping to production")

    backend = CompositeBackend(
        default=LocalShellBackend(root_dir=str(tmp_path / "proj"), virtual_mode=True, env={}),
        routes={
            "/skills/": FilesystemBackend(root_dir=str(bundled), virtual_mode=True),
            "/skills/user/": FilesystemBackend(root_dir=str(user), virtual_mode=True),
        },
        artifacts_root="/artifacts",
    )

    bundled_skills = {s["name"] for s in _list_skills(backend, "/skills/active/")}
    user_skills = {s["name"] for s in _list_skills(backend, "/skills/user/")}

    assert "brainstorming" in bundled_skills
    assert user_skills == {"deploy-checklist"}


def test_disjoint_prefixes_resolve(tmp_path: Path, bundled: Path) -> None:
    """The fallback layout, if nesting turns out to be ambiguous."""
    user = tmp_path / "user"
    _write_skill(user, "deploy-checklist", "Use when shipping to production")

    backend = CompositeBackend(
        default=LocalShellBackend(root_dir=str(tmp_path / "proj"), virtual_mode=True, env={}),
        routes={
            "/skills/": FilesystemBackend(root_dir=str(bundled), virtual_mode=True),
            "/user-skills/": FilesystemBackend(root_dir=str(user), virtual_mode=True),
        },
        artifacts_root="/artifacts",
    )

    assert {s["name"] for s in _list_skills(backend, "/user-skills/")} == {"deploy-checklist"}


def test_later_sources_shadow_earlier_ones(tmp_path: Path) -> None:
    """Upstream's layering rule (skills.py:955), which C5.1's precedence relies on.

    Asserted through a real SkillsMiddleware rather than trusted from the
    comment: this is the mechanism that makes a project skill override a
    bundled one of the same name.
    """
    from deepagents.middleware.skills import SkillsMiddleware

    low, high = tmp_path / "low", tmp_path / "high"
    _write_skill(low, "shared", "the bundled description")
    _write_skill(high, "shared", "the project description")

    backend = CompositeBackend(
        default=LocalShellBackend(root_dir=str(tmp_path / "proj"), virtual_mode=True, env={}),
        routes={
            "/skills/": FilesystemBackend(root_dir=str(low), virtual_mode=True),
            "/skills/project/": FilesystemBackend(root_dir=str(high), virtual_mode=True),
        },
        artifacts_root="/artifacts",
    )
    middleware = SkillsMiddleware(
        backend=backend, sources=["/skills/", "/skills/project/"]
    )

    loaded = {s["name"]: s["description"] for s in middleware._list_all_skills()}

    assert loaded["shared"] == "the project description"
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_skills_multiroute_contract.py -q`

**Read the result before writing anything else — this is a decision point, not a formality.**

| Result | What it means | What to do |
|---|---|---|
| All 3 pass | Nesting works; layering confirmed | Use `/skills/`, `/skills/user/`, `/skills/project/`. Continue to Task 2 |
| `test_nested_prefixes_resolve_independently` fails, disjoint passes | The shorter prefix swallows the longer | Use `/skills/`, `/user-skills/`, `/project-skills/` throughout the rest of this plan. Record the finding in `TODO.md` before continuing |
| `test_later_sources_shadow_earlier_ones` fails | Upstream does not layer the way `skills.py:955` claims | **STOP.** The spec's precedence model (§4) is wrong and needs owner input |

If `SkillsMiddleware` exposes no `_list_all_skills`, find the equivalent and adapt — the contract under test is "later sources win", not the method name:

```bash
grep -n "def _list_all_skills\|def .*list.*skills" .venv/lib/python3.13/site-packages/deepagents/middleware/skills.py
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_skills_multiroute_contract.py
git commit -m "test(skills): prove multi-route resolution and source layering

Step 11c mounts user and project skill directories alongside the bundled
cache. Whether nested prefixes resolve independently is a deepagents
behaviour that decides the route layout, so it is settled before any
production code depends on it."
```

---

## Task 2: Resolve the three skill sources

**Files:**
- Create: `src/rudra/skills/sources.py`
- Test: `tests/test_skills_sources.py`

**Interfaces:**
- Consumes: `SkillCache` (11b).
- Produces: `rudra.skills.sources.SkillSource` (frozen dataclass: `label: str` — one of `"bundled"`, `"user"`, `"project"`; `route: str`; `local_path: Path`), and `resolve_sources(project_path: Path, cache: SkillCache | None, *, home: Path | None = None) -> tuple[SkillSource, ...]` returning them in **precedence order, lowest first**.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_sources.py`:

```python
from __future__ import annotations

from pathlib import Path

from rudra.skills.cache import ensure_cache
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.sources import resolve_sources


def _cache(tmp_path: Path):
    return ensure_cache(BUNDLES, DEFAULT_ENABLED, cache_home=tmp_path / "cache")


def test_bundled_only_when_no_user_directories_exist(tmp_path: Path) -> None:
    sources = resolve_sources(tmp_path / "proj", _cache(tmp_path), home=tmp_path / "home")

    assert [s.label for s in sources] == ["bundled"]


def test_user_and_project_directories_are_added_in_precedence_order(tmp_path: Path) -> None:
    """Lowest precedence first: SkillsMiddleware lets later sources win."""
    home = tmp_path / "home"
    (home / ".rudra" / "skills" / "mine").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "theirs").mkdir(parents=True)

    sources = resolve_sources(project, _cache(tmp_path), home=home)

    assert [s.label for s in sources] == ["bundled", "user", "project"]


def test_an_empty_directory_is_not_offered_as_a_source(tmp_path: Path) -> None:
    """An empty dir would add a route and an index entry for nothing."""
    home = tmp_path / "home"
    (home / ".rudra" / "skills").mkdir(parents=True)

    sources = resolve_sources(tmp_path / "proj", _cache(tmp_path), home=home)

    assert [s.label for s in sources] == ["bundled"]


def test_no_cache_means_no_bundled_source(tmp_path: Path) -> None:
    """`[skills] enabled = []` builds no cache; user skills still work."""
    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "theirs").mkdir(parents=True)

    sources = resolve_sources(project, None, home=tmp_path / "home")

    assert [s.label for s in sources] == ["project"]


def test_routes_are_distinct(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".rudra" / "skills" / "mine").mkdir(parents=True)
    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "theirs").mkdir(parents=True)

    routes = [s.route for s in resolve_sources(project, _cache(tmp_path), home=home)]

    assert len(set(routes)) == len(routes)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_skills_sources.py -q`
Expected: FAIL — `No module named 'rudra.skills.sources'`

- [ ] **Step 3: Implement**

Create `src/rudra/skills/sources.py`. Use the route layout Task 1 established — the constants below assume nesting worked:

```python
"""Where skills come from, and in what order they win.

The one module that knows user skills live in `.rudra/skills/`. Everything
else takes a resolved tuple.

User skills are deliberately NOT rendered into the keyed cache: their
content changes freely, and the key hashes vendored manifests only (11a),
so a copy would go stale the moment someone edited a file. They mount at
their real directories instead.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BUNDLED_ROUTE = "/skills/"
USER_ROUTE = "/skills/user/"
PROJECT_ROUTE = "/skills/project/"


@dataclass(frozen=True)
class SkillSource:
    """One place skills come from.

    Attributes:
        label: "bundled", "user" or "project" -- what `rudra skills list`
            prints, and how a shadowed skill is explained.
        route: The backend-relative prefix it mounts at.
        local_path: The real directory behind that route.
    """

    label: str
    route: str
    local_path: Path

    @property
    def index_path(self) -> str:
        """The path handed to `create_deep_agent(skills=...)`.

        Bundled skills are indexed from `active/` inside the rendered cache;
        user directories are indexed at their root, because there is no
        enabled/available split for skills a user wrote themselves.
        """
        return f"{self.route}active/" if self.label == "bundled" else self.route


def _has_a_skill(directory: Path) -> bool:
    """Is there anything here worth mounting?

    An empty directory would add a route and a source for nothing, and a
    source that yields no skills is noise in `rudra skills list`.
    """
    return directory.is_dir() and any(child.is_dir() for child in directory.iterdir())


def resolve_sources(
    project_path: Path,
    cache: Any | None,
    *,
    home: Path | None = None,
) -> tuple[SkillSource, ...]:
    """Every skill source for this run, lowest precedence first.

    Order is the contract: `SkillsMiddleware` lets later sources override
    earlier ones by name (skills.py:955), so a project skill shadows a user
    one, which shadows a bundled one.
    """
    root = home if home is not None else Path.home()
    found: list[SkillSource] = []

    if cache is not None:
        found.append(SkillSource("bundled", BUNDLED_ROUTE, cache.root))

    user_dir = root / ".rudra" / "skills"
    if _has_a_skill(user_dir):
        found.append(SkillSource("user", USER_ROUTE, user_dir))

    project_dir = project_path / ".rudra" / "skills"
    if _has_a_skill(project_dir):
        found.append(SkillSource("project", PROJECT_ROUTE, project_dir))

    return tuple(found)


def routes_for(sources: Sequence[SkillSource]) -> tuple[str, ...]:
    """Route prefixes these sources need mounted."""
    return tuple(source.route for source in sources)


__all__ = [
    "BUNDLED_ROUTE",
    "PROJECT_ROUTE",
    "USER_ROUTE",
    "SkillSource",
    "resolve_sources",
    "routes_for",
]
```

- [ ] **Step 4: Run the tests, then the gates**

```bash
uv run pytest tests/test_skills_sources.py -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
```

Expected: 5 passed; `All checks passed!`; total = baseline + 8.

- [ ] **Step 5: Commit**

```bash
git add src/rudra/skills/sources.py tests/test_skills_sources.py
git commit -m "feat(skills): resolve bundled, user and project skill sources

Lowest precedence first, because SkillsMiddleware lets later sources win
by name. User directories mount at their real paths rather than being
rendered into the keyed cache -- their content changes freely and the key
hashes vendored manifests only, so a copy would go stale on the first edit.

An empty directory is not offered as a source: it would add a route and an
index entry for nothing."
```

---

## Task 3: Validate a skill against deepagents' own parser

**Files:**
- Create: `src/rudra/skills/validate.py`
- Test: `tests/test_skills_validate.py`

**Interfaces:**
- Consumes: nothing from Rudra.
- Produces: `rudra.skills.validate.Finding` (frozen dataclass: `path: Path`, `name: str`, `ok: bool`, `reason: str`) and `validate_tree(root: Path) -> list[Finding]`.

**Why delegate rather than reimplement:** `SkillsMiddleware` silently skips a skill it cannot parse — it logs a warning and carries on, so at runtime a broken skill is indistinguishable from one the model chose not to use. This command is the only way a user finds out. A hand-rolled check would drift from the real contract, and drift is precisely the failure this exists to prevent.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_validate.py`:

```python
from __future__ import annotations

from pathlib import Path

from rudra.skills.validate import validate_tree

GOOD = """---
name: deploy-checklist
description: Use when shipping to production
---

# Deploy checklist
"""


def _skill(root: Path, name: str, body: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    return directory


def test_a_well_formed_skill_passes(tmp_path: Path) -> None:
    _skill(tmp_path, "deploy-checklist", GOOD)

    findings = validate_tree(tmp_path)

    assert [f.ok for f in findings] == [True]


def test_a_name_that_does_not_match_its_directory_fails(tmp_path: Path) -> None:
    """deepagents requires name == parent directory (skills.py:347)."""
    _skill(tmp_path, "deploy-checklist", GOOD.replace("deploy-checklist", "deploy", 1))

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False
    assert "directory" in findings[0].reason.lower()


def test_missing_frontmatter_fails(tmp_path: Path) -> None:
    _skill(tmp_path, "broken", "# No frontmatter here\n")

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False
    assert findings[0].reason


def test_an_empty_description_fails(tmp_path: Path) -> None:
    """An indexed skill with no description is unselectable."""
    _skill(tmp_path, "quiet", "---\nname: quiet\ndescription:\n---\n\n# Quiet\n")

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False


def test_a_directory_without_a_skill_file_fails(tmp_path: Path) -> None:
    (tmp_path / "empty-dir").mkdir()

    findings = validate_tree(tmp_path)

    assert findings[0].ok is False
    assert "SKILL.md" in findings[0].reason


def test_findings_are_sorted_and_cover_every_directory(tmp_path: Path) -> None:
    _skill(tmp_path, "beta", GOOD.replace("deploy-checklist", "beta"))
    _skill(tmp_path, "alpha", GOOD.replace("deploy-checklist", "alpha"))

    findings = validate_tree(tmp_path)

    assert [f.name for f in findings] == ["alpha", "beta"]


def test_the_vendored_corpus_validates_clean() -> None:
    """The strongest possible check: the real 14 pass their own parser."""
    from rudra.skills.registry import BUNDLES

    findings = validate_tree(BUNDLES[0].skills_path)

    assert [f for f in findings if not f.ok] == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_skills_validate.py -q`
Expected: FAIL — `No module named 'rudra.skills.validate'`

- [ ] **Step 3: Implement**

Create `src/rudra/skills/validate.py`:

```python
"""Would deepagents load this skill, and if not, why?

Delegates to deepagents' own parser rather than reimplementing its rules.
`SkillsMiddleware` *silently skips* a skill it cannot parse -- it logs a
warning and carries on -- so at runtime a broken skill looks exactly like
one the model chose not to use. This is the only place a user finds out,
which is why S11b.3 made it a precondition for shipping writable skill
directories.

Importing deepagents internals is deliberate. If an upgrade moves them,
this failing is the signal to re-verify the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from deepagents.middleware.skills import _parse_skill_metadata, _validate_skill_name


@dataclass(frozen=True)
class Finding:
    """One skill directory, and whether deepagents would load it."""

    path: Path
    name: str
    ok: bool
    reason: str


def validate_skill_dir(directory: Path) -> Finding:
    """Check one `<name>/SKILL.md`."""
    name = directory.name
    skill_md = directory / "SKILL.md"

    if not skill_md.is_file():
        return Finding(directory, name, False, "no SKILL.md in this directory")

    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return Finding(directory, name, False, f"unreadable: {exc}")

    metadata = _parse_skill_metadata(content, str(skill_md), name)
    if metadata is None:
        return Finding(
            directory,
            name,
            False,
            "frontmatter is missing or unparseable, or the description is empty "
            "or too long -- deepagents would skip this skill without an error",
        )

    valid, error = _validate_skill_name(metadata["name"], name)
    if not valid:
        return Finding(directory, name, False, error)

    return Finding(directory, name, True, "")


def validate_tree(root: Path) -> list[Finding]:
    """Every skill directory under `root`, sorted by name."""
    if not root.is_dir():
        return []
    return [validate_skill_dir(child) for child in sorted(root.iterdir()) if child.is_dir()]


__all__ = ["Finding", "validate_skill_dir", "validate_tree"]
```

- [ ] **Step 4: Run the tests and gates**

```bash
uv run pytest tests/test_skills_validate.py -q
uv run ruff check src/ tests/
uv run pytest -q 2>&1 | tail -3
```

Expected: 7 passed.

If `test_an_empty_description_fails` passes when it should fail, check whether `_parse_skill_metadata` treats an empty description as valid; if it does, add the emptiness check explicitly in `validate_skill_dir` and say so in a comment — the goal is "would this skill be usable", which is slightly stronger than "would it parse".

- [ ] **Step 5: Commit**

```bash
git add src/rudra/skills/validate.py tests/test_skills_validate.py
git commit -m "feat(skills): validate a skill against deepagents' own parser

SkillsMiddleware silently skips a skill it cannot parse, so at runtime a
broken skill is indistinguishable from one the model ignored. Delegating
to the real parser rather than reimplementing its rules means this cannot
drift from the contract it exists to check."
```

---

## Task 4: The `rudra skills` command

**Files:**
- Modify: `src/rudra/cli.py`
- Modify: `src/rudra/skills/cache.py` (add `prune_stale`)
- Test: `tests/test_cli_skills.py`

**Interfaces:**
- Consumes: `resolve_sources`, `validate_tree`, `ensure_cache`, `cache_key`.
- Produces: `rudra skills list`, `rudra skills validate [PATH]`, `rudra skills rebuild`; `rudra.skills.cache.prune_stale(cache_home: Path | None, keep: str) -> list[str]` returning removed key names.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_skills.py`:

```python
"""Step 11c's user-facing surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from rudra.cli import app
from rudra.config.loader import reset_config

GOOD = """---
name: {name}
description: {description}
---

# {name}
"""


@pytest.fixture(autouse=True)
def _clean_config():
    reset_config()
    yield
    reset_config()


def _project(tmp_path: Path, body: str = "") -> Path:
    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True, exist_ok=True)
    (rudra / "config.toml").write_text(body, encoding="utf-8")
    return tmp_path


def _skill(root: Path, name: str, description: str = "Use when testing") -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        GOOD.format(name=name, description=description), encoding="utf-8"
    )


def test_list_shows_bundled_skills_and_their_enabled_state(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["skills", "list", "-d", str(_project(tmp_path))])

    assert result.exit_code == 0
    assert "brainstorming" in result.output
    assert "bundled" in result.output
    # A skill that ships disabled must still be listed, marked as such.
    assert "using-git-worktrees" in result.output


def test_list_shows_a_project_skill(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _skill(project / ".rudra" / "skills", "deploy-checklist")

    result = CliRunner().invoke(app, ["skills", "list", "-d", str(project)])

    assert result.exit_code == 0
    assert "deploy-checklist" in result.output
    assert "project" in result.output


def test_validate_accepts_a_good_tree(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    _skill(root, "deploy-checklist")

    result = CliRunner().invoke(app, ["skills", "validate", str(root)])

    assert result.exit_code == 0


def test_validate_rejects_a_broken_skill_and_says_why(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    directory = root / "broken"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("# no frontmatter\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["skills", "validate", str(root)])

    assert result.exit_code == 1
    assert "broken" in result.output


def test_rebuild_reports_the_cache_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    result = CliRunner().invoke(app, ["skills", "rebuild", "-d", str(_project(tmp_path))])

    assert result.exit_code == 0
    assert "skills" in result.output


def test_rebuild_prunes_stale_keys(tmp_path: Path, monkeypatch) -> None:
    """Old renders accumulate silently; rebuild is where they are cleared."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    stale = tmp_path / "cache" / "rudra" / "skills" / "deadbeefdeadbeef"
    stale.mkdir(parents=True)
    (stale / ".complete").write_text("", encoding="utf-8")

    result = CliRunner().invoke(app, ["skills", "rebuild", "-d", str(_project(tmp_path))])

    assert result.exit_code == 0
    assert not stale.exists()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_cli_skills.py -q`
Expected: FAIL — no `skills` command.

- [ ] **Step 3: Add `prune_stale` to the cache**

In `src/rudra/skills/cache.py`, add before `__all__` and export it:

```python
def prune_stale(cache_home: Path | None, keep: str) -> list[str]:
    """Remove every rendered cache except `keep`. Returns what went.

    Old keys accumulate silently -- a new one appears whenever Rudra's
    version, the corpus or the enabled set changes -- and nothing else ever
    removes them. 11a deferred this deliberately until there was a command
    to hang it on (`rudra skills rebuild`).
    """
    root = _cache_home(cache_home)
    if not root.is_dir():
        return []

    removed = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name != keep:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
    return removed
```

- [ ] **Step 4: Add the sub-app**

In `src/rudra/cli.py`, beside the `models_app` / `config_app` registrations (`cli.py:64-68`):

```python
skills_app = typer.Typer(help="Inspect and validate the skill library.")
app.add_typer(skills_app, name="skills")
```

Then add the three commands. Keep imports function-local, as the other commands do, so `rudra --help` stays fast:

```python
@skills_app.command("list")
def skills_list(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Show every skill Rudra can see, and whether it is in the prompt."""
    from rudra.skills.cache import ensure_cache
    from rudra.skills.registry import BUNDLES
    from rudra.skills.sources import resolve_sources
    from rudra.skills.validate import validate_tree

    cfg = _load_config_or_exit(project_dir)
    root = Path(project_dir) if project_dir else Path.cwd()
    enabled = frozenset(cfg.skills.enabled)
    cache = ensure_cache(BUNDLES, enabled) if enabled else None
    sources = resolve_sources(root, cache)

    # Later sources win, so walk in order and let the last writer own the
    # name -- the same rule SkillsMiddleware applies (skills.py:955).
    owner: dict[str, str] = {}
    rows: list[tuple[str, str, str, str]] = []
    for source in sources:
        listing = (
            validate_tree(source.local_path / "library" / "superpowers")
            if source.label == "bundled"
            else validate_tree(source.local_path)
        )
        for finding in listing:
            state = "enabled" if (source.label != "bundled" or finding.name in enabled) else "—"
            rows.append((finding.name, source.label, state, ""))
            owner[finding.name] = source.label

    table = Table(title="Skills", header_style="bold")
    for column in ("Skill", "Source", "In prompt", "Note"):
        table.add_column(column, overflow="fold")
    for name, label, state, _ in rows:
        note = f"shadowed by {owner[name]}" if owner[name] != label else ""
        table.add_row(name, label, "" if note else state, note)
    console.print(table)


@skills_app.command("validate")
def skills_validate(
    path: Optional[Path] = typer.Argument(None, help="A directory of skills to check"),
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Check whether skills would actually load, and say why not.

    Exists because SkillsMiddleware skips an unparseable skill in silence.
    """
    from rudra.skills.sources import resolve_sources
    from rudra.skills.validate import validate_tree

    if path is not None:
        trees = [Path(path)]
    else:
        root = Path(project_dir) if project_dir else Path.cwd()
        trees = [s.local_path for s in resolve_sources(root, None)]

    problems = 0
    for tree in trees:
        for finding in validate_tree(tree):
            if finding.ok:
                console.print(f"[green]ok[/green]      {finding.name}")
            else:
                problems += 1
                console.print(f"[red]invalid[/red] {finding.name}: {finding.reason}")

    if problems:
        console.print(f"\n[red]{problems} skill(s) would not load.[/red]")
        raise typer.Exit(1)
    console.print("\n[green]All skills valid.[/green]")


@skills_app.command("rebuild")
def skills_rebuild(
    project_dir: Optional[Path] = typer.Option(None, "--project-dir", "-d"),
) -> None:
    """Re-render the bundled skill cache and drop stale copies."""
    from rudra.skills.cache import cache_key, ensure_cache, prune_stale
    from rudra.skills.registry import BUNDLES

    cfg = _load_config_or_exit(project_dir)
    enabled = frozenset(cfg.skills.enabled)
    if not enabled:
        console.print("Skills are disabled ([skills] enabled = []); nothing to build.")
        return

    key = cache_key(BUNDLES, enabled)
    removed = prune_stale(None, key)
    cache = ensure_cache(BUNDLES, enabled)
    console.print(f"Skill cache: {cache.root}")
    if removed:
        console.print(f"Removed {len(removed)} stale cache(s).")
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest tests/test_cli_skills.py -q
uv run pytest tests/test_cli_smoke.py -q
```

Expected: 6 passed, and the existing CLI smoke tests still green.

Two likely adjustments, both fine to make — the contract is the behaviour, not my guess at the helper names: `_load_config_or_exit` and `console` must be the ones `cli.py` already defines (check with `grep -n "^console\|def _load_config_or_exit" src/rudra/cli.py`), and `prune_stale(None, key)` relies on `_cache_home(None)` resolving the default location.

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra/cli.py src/rudra/skills/cache.py tests/test_cli_skills.py
git commit -m "feat(cli): rudra skills list / validate / rebuild

validate is the precondition S11b.3 set for shipping writable skill
directories: SkillsMiddleware skips an unparseable skill silently, so
without this a typo means a skill that never loads and never says why.

rebuild also prunes stale cache keys -- 11a deferred that until there was
a command to hang it on. No add/remove (they are [skills] enabled edits)
and no update (the corpus is frozen, S11a.3)."
```

---

## Task 5: Mount user skills in a real run

**Files:**
- Modify: `src/rudra/agent/main_agent.py`
- Modify: `tests/test_skills_wiring.py`

**Interfaces:**
- Consumes: `resolve_sources`, `routes_for`.
- Produces: no new names. `ROUTE_PREFIXES` becomes dynamic.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills_wiring.py`:

```python
def test_user_and_project_skills_are_mounted_and_indexed(tmp_path: Path, cache_root: Path) -> None:
    """C5.1's remaining half: a user's own skills reach the agent."""
    from rudra.agent.main_agent import build_backend
    from rudra.skills.sources import resolve_sources

    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "deploy-checklist").mkdir(parents=True)
    (project / ".rudra" / "skills" / "deploy-checklist" / "SKILL.md").write_text(
        "---\nname: deploy-checklist\ndescription: Use when shipping\n---\n\n# Deploy\n",
        encoding="utf-8",
    )
    cfg = _project(project)

    class _Cache:
        root = cache_root

    sources = resolve_sources(project, _Cache(), home=tmp_path / "home")
    backend = build_backend(cfg, project, skills_root=cache_root, sources=sources)

    assert "/skills/project/" in backend.routes


def test_the_normalizer_still_knows_every_mounted_route(
    tmp_path: Path, cache_root: Path
) -> None:
    """A1.79's guard, extended to the routes this step adds."""
    from rudra.agent import main_agent
    from rudra.skills.sources import resolve_sources

    project = tmp_path / "proj"
    (project / ".rudra" / "skills" / "x").mkdir(parents=True)
    (project / ".rudra" / "skills" / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: d\n---\n\n# x\n", encoding="utf-8"
    )
    cfg = _project(project)

    class _Cache:
        root = cache_root

    sources = resolve_sources(project, _Cache(), home=tmp_path / "home")
    mounted = set(main_agent.build_backend(cfg, project, cache_root, sources).routes)

    assert mounted <= set(main_agent.route_prefixes(sources))
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_skills_wiring.py -q`
Expected: FAIL — `build_backend()` got an unexpected keyword argument `sources`.

- [ ] **Step 3: Implement**

In `src/rudra/agent/main_agent.py`:

Replace the module-level constant with a function, keeping the constant for the artifacts route:

```python
ARTIFACTS_PREFIX = "/artifacts/"


def route_prefixes(sources=()) -> tuple[str, ...]:
    """Every prefix the backend mounts, for the path normalizer.

    A route the normalizer does not know about gets its path trimmed to the
    last two segments, so the model can list the files and never read one
    (A1.79). This is the single source both `build_backend` and
    `install_path_normalizer` read, and a test asserts they agree.
    """
    from rudra.skills.sources import routes_for

    return (ARTIFACTS_PREFIX, *routes_for(sources))
```

Give `build_backend` the sources and mount each one:

```python
def build_backend(cfg, project_path: Path, skills_root: Path | None = None, sources=()):
```

and inside, replacing the current route construction:

```python
    routes = {
        ARTIFACTS_PREFIX: FilesystemBackend(root_dir=str(paths.artifacts), virtual_mode=True),
    }
    for source in sources:
        routes[source.route] = FilesystemBackend(
            root_dir=str(source.local_path), virtual_mode=True
        )
    if skills_root is not None and not sources:
        # Bundled-only callers that never resolved sources.
        routes["/skills/"] = FilesystemBackend(root_dir=str(skills_root), virtual_mode=True)
```

In `create_main_agent`, resolve sources once and thread them through:

```python
    from rudra.skills.sources import resolve_sources

    sources = resolve_sources(project_path, skill_cache)
    install_path_normalizer(project_path, route_prefixes=route_prefixes(sources))
    filesystem_backend = build_backend(
        cfg, project_path, skills_root=skill_cache.root if skill_cache else None, sources=sources
    )
    skills_sources = tuple(source.index_path for source in sources) or None
```

Delete the old `ROUTE_PREFIXES` constant and update its two other uses.

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_skills_wiring.py -q
uv run pytest tests/test_backend_wiring.py tests/test_agent_wiring.py -q
uv run pytest -q 2>&1 | tail -3
```

Expected: all green. **If `test_the_normalizer_is_told_about_every_route_the_backend_mounts` fails, do not weaken it** — it is `A1.79`'s guard and the failure means a route is mounted that the normalizer will mangle.

- [ ] **Step 5: Commit**

```bash
git add src/rudra/agent/main_agent.py tests/test_skills_wiring.py
git commit -m "feat(skills): mount user and project skill directories

Closes the second half of C5.1. Sources are resolved once per run and
drive both the mounted routes and the prefixes the path normalizer is told
to leave alone, so the two cannot disagree -- which is A1.79's guard,
extended rather than bypassed."
```

---

## Task 6: The `C5.8` measurement

**Files:**
- Modify: `TODO.md` (results only)

**Interfaces:** none.

**Needs a reachable model.** If none is available, stop and report — do not skip to Task 7, which depends on this evidence.

**Read the spec's §5 before starting.** Its criteria and decision rule are pre-registered precisely so the results cannot be interpreted after the fact to suit either answer. Do not adjust them once runs begin.

- [ ] **Step 1: Prepare the arms**

Two project directories, identical but for `[skills] enabled`. Both carry the owner's `.env` (NVIDIA).

```bash
for arm in arm-a-9 arm-b-13; do
  d=$(mktemp -d)/$arm && mkdir -p "$d" && cd "$d" && git init -q
  env -i PATH="$PATH" HOME="$HOME" /path/to/Rudra/.venv/bin/rudra init >/dev/null
  cp /path/to/Rudra/.env .env
  printf '[agent]\nverbose = true\nmax_questions = 0\n' > .rudra/config.toml
  echo "$d"
done
```

Arm B additionally gets:

```toml
[skills]
enabled = ["brainstorming", "writing-plans", "executing-plans",
           "systematic-debugging", "test-driven-development",
           "verification-before-completion", "requesting-code-review",
           "receiving-code-review", "using-superpowers",
           "using-git-worktrees", "finishing-a-development-branch",
           "subagent-driven-development", "dispatching-parallel-agents"]
```

Confirm both resolve before spending runs: `rudra skills list -d <dir>` should show 9 in prompt for A and 13 for B.

- [ ] **Step 2: Run the four tasks in each arm**

| # | Task text | Skill that should apply |
|---|---|---|
| 1 | `"tests/test_parser.py::test_quoted fails with AssertionError. Find out why and fix it."` (seed a repo with a genuinely broken parser first) | `systematic-debugging` |
| 2 | `"add a roman_numeral(n) function to convert.py, with tests"` | `test-driven-development` |
| 3 | `"build something that helps me track my reading"` (deliberately under-specified) | `brainstorming` / `writing-plans` |
| 4 | `"the reviewer said the CSV parser mishandles quoted commas — address it"` | `receiving-code-review` |

```bash
env -i PATH="$PATH" HOME="$HOME" /path/to/Rudra/.venv/bin/rudra --auto --allow-shell "<task>" > runN.log 2>&1
```

- [ ] **Step 3: Score each run**

**Count skill reads by path alone**, not with a line-anchored regex:

```bash
grep -c "/skills/.*SKILL\.md" runN.log
grep -oE "/skills/[a-z/]*/[a-z-]+/SKILL\.md" runN.log | sort -u
grep -c "SUBAGENT-STOP" runN.log
grep -E "Tasks: .* done" runN.log | tail -1
```

Rich wraps long lines, so `grep "CALL read_file.*'/skills/"` under-reports — measured in 11b, where it returned 0 for a run that demonstrably read a skill.

Record per run: skill read (y/n), which, apt (y/n), tasks done/blocked, `A1.80` echo count.

- [ ] **Step 4: Apply the decision rule**

From the spec's §5 table, unchanged:

- Arm B ≥ Arm A on apt reads, no extra blocked tasks → **enable the four**.
- Arm B worse → **stay at 9**, recording the degradation.
- Both near zero → **stay at 9**, and the finding is that the bootstrap fails at this scale; reopen `A1.80`'s primer question.

- [ ] **Step 5: Record the results in `TODO.md`**

Write the full 8-run table into `C5.8`'s row: task, arm, skill read, aptness, outcome. Then state the `C5.9` decision and **which branch of the rule produced it**.

State the limitation in the same row: measured on `nvidia/nemotron-3-ultra-550b-a55b`, not at D6's 32B floor, so the conclusion does not transfer to the smallest supported model (S11c.3).

Close or confirm `A1.80` from the echo count across all 8 runs.

- [ ] **Step 6: Commit**

```bash
git add TODO.md
git commit -m "docs: C5.8 measured — <one-line result>

Eight runs, four tasks across two arms on the NVIDIA 550B. Decision rule
from the spec applied unchanged; the limitation stands, this says nothing
about the 32B floor."
```

---

## Task 7: Apply the `C5.9` decision and update the docs

**Files:**
- Modify: `src/rudra/skills/registry.py` (only if the measurement says to)
- Modify: `Documentation/12-skills.md`, `Documentation/08-project-status.md`, `Documentation/04-cli-reference.md`, `Documentation/README.md`, `README.md`
- Modify: `TODO.md`

- [ ] **Step 1: Apply the decision**

If Task 6's evidence says enable the four, add them to `DEFAULT_ENABLED` in `src/rudra/skills/registry.py` and update the comment to cite the measurement rather than D11's estimate. If it says stay at 9, change nothing in code and say why in the comment.

Either way, `tests/test_skills_registry.py::test_default_enabled_is_the_eight_plus_the_bootstrap` will need updating — including its name if the set changed.

- [ ] **Step 2: `Documentation/12-skills.md`**

Replace the "Adding your own — not yet supported" section with the real thing: where to put a skill (`<project>/.rudra/skills/<name>/SKILL.md`), the frontmatter shape, that `name` must equal the directory, that `rudra skills validate` checks it, and that project shadows user shadows bundled. Update the enabled table if Task 6 changed it.

- [ ] **Step 3: `Documentation/08-project-status.md`**

Delete "You cannot add your own skills" from *What doesn't work yet*, and add a *What works* bullet for user-authored skills and `rudra skills`. Update the roadmap's Skills row — after this step, Step 11 is complete.

- [ ] **Step 4: `Documentation/04-cli-reference.md`, index, README**

Add `rudra skills list/validate/rebuild` to the CLI reference in the shape `models test` uses. Add a row to `Documentation/README.md` if the guide list needs it, and update `README.md`'s superpowers section to mention that users can add their own.

- [ ] **Step 5: Check every link resolves**

```bash
for f in README.md Documentation/*.md; do
  grep -oE '\]\(([^)#h][^)]*)\)' "$f" | sed 's/](//;s/)$//' | while read -r l; do
    d=$(dirname "$f"); [ -e "$d/$l" ] || echo "BROKEN in $f -> $l"
  done
done; echo "(no output = all resolve)"
```

- [ ] **Step 6: Close the ledger**

Mark `C5.6`, `C5.1`, `C5.8`, `C5.9` DONE and `C5.5` **OBSOLETE** with §1's evidence. Update §E Stage IV: mark **11c COMPLETE**, and note that **Step 11 as a whole is now closed** — 11a the corpus, 11b the wiring, 11c the surface and the measurement — so a fresh session starts at **Step 12** (`C7.1`–`C7.8`, context management).

- [ ] **Step 7: Final verification and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q
uv run rudra skills list
git add -A
git commit -m "docs: Step 11c complete — Step 11 closed

<result of C5.9>. Users can author skills, rudra skills lists and
validates them, and the enabled set is now backed by measurement rather
than an estimate."
```

---

## Task 8: Acceptance — a user's own skill is actually read

**Files:** none.

**Why this is separate from Task 6:** the measurement runs test *selection among bundled skills*. Nothing in them proves a user-authored skill in `<project>/.rudra/skills/` is reachable. Mounted-and-ignored looks identical to mounted-and-working in every test that does not involve a model choosing.

- [ ] **Step 1: Author a skill the task cannot be done well without**

In a fresh project, write `.rudra/skills/project-conventions/SKILL.md` with a `description` that clearly matches the task you will give, and a body containing a specific, checkable instruction — for example, that every module in this project must begin with a particular header comment.

- [ ] **Step 2: Validate it before relying on it**

```bash
rudra skills validate
rudra skills list
```

Expected: valid, and listed with source `project`.

- [ ] **Step 3: Run a task that should trigger it**

```bash
rudra --auto --allow-shell "add a utils.py with a slugify(text) function" > user-skill.log 2>&1
```

- [ ] **Step 4: Judge it honestly**

Two separate questions, and the second is the real one:

1. Did the model `read_file` a `/skills/project/…` path? (`grep "/skills/project/" user-skill.log`)
2. Does the generated code follow the instruction the skill contains?

**A "no" to both is a finding, not a failure to retry until it passes.** Record it against `C5.8` — it would mean user skills are reachable but not reliably *used*, which is the same selection question the bundled measurement asks, and it belongs in the ledger rather than in a re-roll.

- [ ] **Step 5: Record the outcome in `TODO.md`** under `C5.1`, with the log evidence either way.

---

## Verification

Before declaring 11c complete:

```bash
uv run ruff check src/ tests/          # All checks passed!
uv run ruff format --check src/ tests/ # clean
uv run pytest -q                        # >= 1150 + new tests, zero failures
uv run rudra skills list                # renders
uv run rudra skills validate            # exits 0 on a clean tree
```

Task 6's eight runs and Task 8's acceptance are part of this, not optional. If the model never reads a skill in any of them, **that is the result** — record it, apply the decision rule's third branch, and leave `A1.80`'s primer question open for a later step. Do not tune the prompt until a run looks better; that converts a measurement into a wish.
