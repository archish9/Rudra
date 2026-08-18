# Step 11a — Vendored Skill Corpus and Its Transform: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor the superpowers 6.3.0 skill corpus into Rudra as a frozen, self-describing bundle, and build the pure transform that renders it into a cache tree an agent could read — without wiring anything to an agent yet.

**Architecture:** One package, `src/rudra/skills/`, holding a byte-identical frozen copy of upstream under `bundles/<name>/src/`, a typed registry of bundles, and a pure `render()` function that copies the corpus into `library/<bundle>/<skill>/` (namespaced, `read_file`-able) and `active/<skill>/` (flat, indexed), rewriting inter-skill cross-references to paths and injecting a Rudra platform-reference file on the way. The frozen copy is protected by a per-file sha256 manifest that a test verifies, so the freeze rule is mechanical rather than a promise. Nothing an agent can see changes in this step.

**Tech Stack:** Python 3.12+, stdlib only for the transform (`pathlib`, `hashlib`, `shutil`, `re`, `tomllib`), pytest, ruff, hatchling.

**Spec:** `docs/superpowers/specs/2026-08-17-step11a-skill-corpus-design.md`

## Global Constraints

- **`ruff check src/ tests/` must print `All checks passed!`** — absolute gate since Step 3. `ruff format --check src/ tests/` must be clean.
- **`uv run pytest -q` must never go down.** Record the baseline count in Task 1 Step 1 and compare at the end of every task.
- **Prefer `uv run` over `.venv/bin/`** — a local `.venv` drifts from `uv.lock` and will lie to you (CLAUDE.md §9).
- **ruff config:** line-length 100, target py312, lint rules `E,F,I,W`, `E501` ignored (`pyproject.toml:84-90`).
- **Every new module starts with `from __future__ import annotations`** — the convention throughout `src/rudra/`.
- **The vendored tree under `bundles/<name>/src/` is frozen and byte-identical to upstream.** No edits, ever, by anyone. Task 1's manifest test enforces this. If a task seems to require editing a vendored file, it is wrong — the transform edits the *rendered* copy, never the source.
- **`transform.py` imports nothing from Rudra except `rudra.skills.registry` / `rudra.skills.bundle`** — the same boundary rule `facts/store.py` and `loop/ledger.py` already hold.
- **Vendor source of truth:** `~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0` — obra/superpowers at release 6.3.0, MIT, Copyright (c) 2025 Jesse Vincent.
- **Corpus shape, verified 2026-08-17:** 14 `SKILL.md` + 37 supporting files = **51 files**, 472K, **no `.py` files**.
- **Cross-references to rewrite: 26**, all of the form `superpowers:<skill-name>`.
- **Do NOT perform D10's tool-name rewrite.** Spec §1: the 103 `Write`/`Task`/`Skill`/`Read` grep hits are English prose. Renaming them corrupts the corpus. `C5.4`, `C5.4a`, `C5.4b`, `C5.4c` are obsolete.
- **Commits:** the repo is on `main`. Branch before the first commit (`git checkout -b step-11a-skill-corpus`). Do not push.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/rudra/skills/__init__.py` | Package marker. Exports nothing — callers import submodules |
| `src/rudra/skills/manifest.py` | sha256 manifest: compute, write, verify. Used by the freeze test and by 11b's cache key |
| `src/rudra/skills/bundle.py` | `Bundle` dataclass + `load_bundle()` — parses one `BUNDLE.toml` |
| `src/rudra/skills/registry.py` | `BUNDLES`, `DEFAULT_ENABLED` — the one list of what ships |
| `src/rudra/skills/transform.py` | `render()`, `RenderReport`, `DuplicateSkillError` — the pure transform |
| `src/rudra/skills/rudra_tools.py` | `RUDRA_TOOLS_MD` — the Rudra platform-reference text, and the Platform Adaptation line |
| `src/rudra/skills/notice.py` | `render_notice()` — generates `NOTICE` from the registry |
| `src/rudra/skills/bundles/superpowers/BUNDLE.toml` | Bundle metadata + per-bundle transform declarations |
| `src/rudra/skills/bundles/superpowers/LICENSE` | MIT, Jesse Vincent — byte-identical |
| `src/rudra/skills/bundles/superpowers/MANIFEST.sha256` | Per-file hashes of `src/` — the immutable pin |
| `src/rudra/skills/bundles/superpowers/src/` | Byte-identical upstream: `skills/` (51 files) + `hooks/session-start` |
| `NOTICE` | Repo root. Rudra's license + every bundle's attribution |
| `tests/test_skills_manifest.py` | Task 1 — the freeze rule |
| `tests/test_skills_registry.py` | Task 2 — bundle metadata loads and is well-formed |
| `tests/test_skills_transform.py` | Tasks 3, 4, 5 — layout, invariant, determinism, rewrites, injection |
| `tests/test_skills_deepagents_contract.py` | Task 6 — the rendered corpus parses under deepagents' own parser |
| `tests/test_skills_notice.py` | Task 7 — NOTICE matches the registry; the wheel carries the corpus |

**Why `active/` is copied from the rendered `library/`, not from source:** it inherits the cross-reference rewrite and the platform-reference injection for free, so those operations are written once and cannot drift between the two trees.

---

## Task 1: Vendor the frozen corpus and lock it with a manifest

**Files:**
- Create: `src/rudra/skills/__init__.py`
- Create: `src/rudra/skills/manifest.py`
- Create: `src/rudra/skills/bundles/superpowers/LICENSE` (copied)
- Create: `src/rudra/skills/bundles/superpowers/src/skills/**` (copied, 51 files)
- Create: `src/rudra/skills/bundles/superpowers/src/hooks/session-start` (copied)
- Create: `src/rudra/skills/bundles/superpowers/MANIFEST.sha256` (generated)
- Modify: `pyproject.toml` — add a ruff exclude for the vendored tree
- Test: `tests/test_skills_manifest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `rudra.skills.manifest.compute_manifest(root: Path) -> list[tuple[str, str]]` (sorted `(relpath, sha256hex)`), `write_manifest(root: Path, out: Path) -> None`, `verify_manifest(root: Path, manifest_path: Path) -> list[str]` (returns a sorted list of human-readable mismatch descriptions; empty means clean). The vendored tree at `src/rudra/skills/bundles/superpowers/src/`.

- [ ] **Step 1: Record the pytest baseline**

```bash
cd /Users/archish/Documents/ai-ml/Rudra
git checkout -b step-11a-skill-corpus
uv sync
uv run pytest -q 2>&1 | tail -3
```

Write the passed/skipped count down. Every later task compares against it. It must never go down.

- [ ] **Step 2: Copy the corpus, byte-identical**

```bash
SRC=~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0
DST=src/rudra/skills/bundles/superpowers

mkdir -p "$DST/src"
cp -R "$SRC/skills" "$DST/src/skills"
mkdir -p "$DST/src/hooks"
cp "$SRC/hooks/session-start" "$DST/src/hooks/session-start"
cp "$SRC/LICENSE" "$DST/LICENSE"

# macOS leaves these behind and they would enter the manifest
find "$DST" -name '.DS_Store' -delete
```

- [ ] **Step 3: Verify the copy is exactly what the spec measured**

```bash
DST=src/rudra/skills/bundles/superpowers
find "$DST/src/skills" -name SKILL.md | wc -l          # expect 14
find "$DST/src/skills" -type f ! -name SKILL.md | wc -l # expect 37
find "$DST/src" -name '*.py' | wc -l                    # expect 0
diff -r ~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0/skills "$DST/src/skills" && echo IDENTICAL
```

Expected: `14`, `37`, `0`, `IDENTICAL`. If any number differs, stop — the plan's measurements no longer describe the corpus on disk, and every later count in this plan (26 cross-references, 51 files) is suspect.

- [ ] **Step 4: Write the failing test**

Create `tests/test_skills_manifest.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

from rudra.skills.manifest import compute_manifest, verify_manifest, write_manifest

BUNDLE_ROOT = Path(__file__).parent.parent / "src" / "rudra" / "skills" / "bundles" / "superpowers"


def test_compute_manifest_is_sorted_and_hashes_content(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("beta", encoding="utf-8")
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")

    entries = compute_manifest(tmp_path)

    assert [rel for rel, _ in entries] == ["a.txt", "b.txt"]
    assert entries[0][1] == hashlib.sha256(b"alpha").hexdigest()


def test_verify_manifest_reports_edited_file(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    manifest = tmp_path / "MANIFEST.sha256"
    write_manifest(root, manifest)

    (root / "a.txt").write_text("tampered", encoding="utf-8")

    problems = verify_manifest(root, manifest)

    assert len(problems) == 1
    assert "a.txt" in problems[0]


def test_verify_manifest_reports_added_and_removed_files(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    manifest = tmp_path / "MANIFEST.sha256"
    write_manifest(root, manifest)

    (root / "a.txt").unlink()
    (root / "b.txt").write_text("beta", encoding="utf-8")

    problems = verify_manifest(root, manifest)

    assert any("a.txt" in p for p in problems)
    assert any("b.txt" in p for p in problems)


def test_vendored_superpowers_tree_is_unmodified() -> None:
    """The freeze rule (spec S11a.3), enforced.

    The vendored copy is byte-identical to superpowers 6.3.0 and is never
    edited -- the transform rewrites the rendered output, never the source.
    A failure here means someone edited the frozen corpus.
    """
    problems = verify_manifest(BUNDLE_ROOT / "src", BUNDLE_ROOT / "MANIFEST.sha256")
    assert problems == []


def test_vendored_corpus_has_the_expected_shape() -> None:
    skills_root = BUNDLE_ROOT / "src" / "skills"
    skill_files = sorted(skills_root.glob("*/SKILL.md"))
    support_files = [p for p in skills_root.rglob("*") if p.is_file() and p.name != "SKILL.md"]

    assert len(skill_files) == 14
    assert len(support_files) == 37
    assert list(skills_root.rglob("*.py")) == []


def test_vendored_license_is_present_and_mit() -> None:
    text = (BUNDLE_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Jesse Vincent" in text
```

- [ ] **Step 5: Run the test to verify it fails**

Run: `uv run pytest tests/test_skills_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.skills'`

- [ ] **Step 6: Write the implementation**

Create `src/rudra/skills/__init__.py`:

```python
"""The vendored skill corpus and the transform that renders it.

Nothing here is wired to an agent. `bundles/<name>/src/` is a frozen,
byte-identical copy of an upstream project; `transform.render()` produces
the tree an agent would read. Step 11b decides where that tree lives and
hands it to `create_deep_agent(skills=...)`.
"""

from __future__ import annotations
```

Create `src/rudra/skills/manifest.py`:

```python
"""Per-file sha256 manifests over a vendored tree.

The vendored corpus is pinned by content, not by a git SHA: the upstream
copy Rudra was built from is a released plugin package, not a git checkout
(spec S11a.2). A manifest is therefore the immutable pin, and verifying it
is what makes the freeze rule (S11a.3) mechanical rather than a promise.

Step 11b reuses `manifest_root_hash` as one input to the cache key, so an
owner's hand-update of the corpus invalidates every user's cache without
any version negotiation.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 65536


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def compute_manifest(root: Path) -> list[tuple[str, str]]:
    """Every file under `root` as sorted `(posix relpath, sha256 hex)` pairs.

    Sorted so the output is deterministic across filesystems: a manifest
    that reordered itself would change the 11b cache key for no reason.
    """
    entries = [
        (path.relative_to(root).as_posix(), _hash_file(path))
        for path in root.rglob("*")
        if path.is_file()
    ]
    return sorted(entries)


def format_manifest(entries: list[tuple[str, str]]) -> str:
    """`sha256sum`-compatible text: `<hex>  <relpath>`, newline terminated."""
    return "".join(f"{digest}  {rel}\n" for rel, digest in entries)


def parse_manifest(text: str) -> list[tuple[str, str]]:
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        entries.append((rel, digest))
    return sorted(entries)


def write_manifest(root: Path, out: Path) -> None:
    out.write_text(format_manifest(compute_manifest(root)), encoding="utf-8")


def verify_manifest(root: Path, manifest_path: Path) -> list[str]:
    """Differences between `root` and its manifest, as readable lines.

    Empty means clean. Reports content changes, additions and removals
    separately, because "someone edited a skill" and "someone dropped a
    file in" are different accidents with different fixes.
    """
    recorded = dict(parse_manifest(manifest_path.read_text(encoding="utf-8")))
    actual = dict(compute_manifest(root))

    problems = []
    for rel in sorted(set(recorded) - set(actual)):
        problems.append(f"missing from tree: {rel}")
    for rel in sorted(set(actual) - set(recorded)):
        problems.append(f"not in manifest: {rel}")
    for rel in sorted(set(recorded) & set(actual)):
        if recorded[rel] != actual[rel]:
            problems.append(f"content changed: {rel}")
    return problems


def manifest_root_hash(manifest_path: Path) -> str:
    """One hex digest standing for a whole manifest. Used by 11b's cache key."""
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()
```

- [ ] **Step 7: Generate the manifest**

```bash
uv run python -c "
from pathlib import Path
from rudra.skills.manifest import write_manifest
root = Path('src/rudra/skills/bundles/superpowers')
write_manifest(root / 'src', root / 'MANIFEST.sha256')
print(sum(1 for _ in (root / 'MANIFEST.sha256').read_text().splitlines()), 'entries')
"
```

Expected: `52 entries` (51 files under `skills/` plus `hooks/session-start`).

- [ ] **Step 8: Exclude the vendored tree from ruff**

In `pyproject.toml`, under `[tool.ruff]` (currently `pyproject.toml:84-86`), add the exclude beneath `target-version`:

```toml
[tool.ruff]
line-length = 100
target-version = "py312"
# Vendored third-party corpora are frozen and byte-identical to upstream
# (spec S11a.3). They ship no Python today; this keeps a future bundle that
# does from silently entering Rudra's lint gate.
exclude = ["src/rudra/skills/bundles"]
```

- [ ] **Step 9: Run the tests and the gates**

```bash
uv run pytest tests/test_skills_manifest.py -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
```

Expected: 6 passed; `All checks passed!`; format clean; total count = baseline + 6.

- [ ] **Step 10: Commit**

```bash
git add src/rudra/skills tests/test_skills_manifest.py pyproject.toml
git commit -m "feat(skills): vendor superpowers 6.3.0 as a frozen bundle

Byte-identical copy of obra/superpowers 6.3.0 (MIT, Jesse Vincent) under
src/rudra/skills/bundles/superpowers/src/. Pinned by a per-file sha256
manifest rather than a git SHA, because the upstream copy is a released
plugin package and not a git checkout.

A test verifies the manifest, which is what makes the freeze rule
enforceable instead of a convention. Closes half of C5.2."
```

---

## Task 2: The bundle model and the registry

**Files:**
- Create: `src/rudra/skills/bundle.py`
- Create: `src/rudra/skills/registry.py`
- Create: `src/rudra/skills/bundles/superpowers/BUNDLE.toml`
- Test: `tests/test_skills_registry.py`

**Interfaces:**
- Consumes: the vendored tree from Task 1.
- Produces: `rudra.skills.bundle.Bundle` (frozen dataclass with fields `name: str`, `root: Path`, `upstream: str`, `version: str`, `copied_on: str`, `source: str`, `license: str`, `license_file: str`, `copyright: str`, `frozen: bool`, `skills_root: str`, `cross_ref_prefix: str | None`, `bootstrap_skill: str | None`, `platform_ref_section: str | None`, plus properties `skills_path -> Path`, `manifest_path -> Path`, `license_path -> Path`, and `skill_names() -> tuple[str, ...]`). `rudra.skills.bundle.load_bundle(root: Path) -> Bundle`. `rudra.skills.registry.BUNDLES: tuple[Bundle, ...]` and `rudra.skills.registry.DEFAULT_ENABLED: frozenset[str]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_registry.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from rudra.skills.bundle import load_bundle
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED


def test_registry_ships_superpowers() -> None:
    assert [b.name for b in BUNDLES] == ["superpowers"]


def test_superpowers_metadata_matches_upstream_release() -> None:
    bundle = BUNDLES[0]

    assert bundle.upstream == "https://github.com/obra/superpowers"
    assert bundle.version == "6.3.0"
    assert bundle.license == "MIT"
    assert "Jesse Vincent" in bundle.copyright
    assert bundle.frozen is True


def test_superpowers_declares_its_transform_hooks() -> None:
    bundle = BUNDLES[0]

    assert bundle.cross_ref_prefix == "superpowers:"
    assert bundle.bootstrap_skill == "using-superpowers"
    assert bundle.platform_ref_section == "## Platform Adaptation"


def test_bundle_paths_resolve_to_real_files() -> None:
    bundle = BUNDLES[0]

    assert bundle.skills_path.is_dir()
    assert bundle.manifest_path.is_file()
    assert bundle.license_path.is_file()
    assert (bundle.skills_path / bundle.bootstrap_skill / "SKILL.md").is_file()


def test_skill_names_are_the_fourteen_upstream_skills() -> None:
    names = BUNDLES[0].skill_names()

    assert len(names) == 14
    assert names == tuple(sorted(names))
    assert "brainstorming" in names
    assert "using-superpowers" in names


def test_default_enabled_is_the_eight_plus_the_bootstrap() -> None:
    assert DEFAULT_ENABLED == frozenset(
        {
            "brainstorming",
            "writing-plans",
            "executing-plans",
            "systematic-debugging",
            "test-driven-development",
            "verification-before-completion",
            "requesting-code-review",
            "receiving-code-review",
            "using-superpowers",
        }
    )


def test_every_enabled_name_exists_in_a_bundle() -> None:
    available = {name for bundle in BUNDLES for name in bundle.skill_names()}
    assert DEFAULT_ENABLED <= available


def test_load_bundle_rejects_a_missing_required_key(tmp_path: Path) -> None:
    (tmp_path / "BUNDLE.toml").write_text('name = "x"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="upstream"):
        load_bundle(tmp_path)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_skills_registry.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.skills.bundle'`

- [ ] **Step 3: Write the BUNDLE.toml**

Create `src/rudra/skills/bundles/superpowers/BUNDLE.toml`:

```toml
# Metadata for one vendored upstream corpus. Every bundle ships this file,
# so adding a second upstream is a directory plus one line in registry.py
# and nothing else (spec S11a.5).

name = "superpowers"
upstream = "https://github.com/obra/superpowers"
version = "6.3.0"
copied_on = "2026-08-17"
source = "~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0"

license = "MIT"
license_file = "LICENSE"
copyright = "Copyright (c) 2025 Jesse Vincent"

# Frozen: no version check, no update fetch, no network at runtime, ever.
# The owner updates this corpus by hand and re-runs the suite (S11a.3).
frozen = true

skills_root = "src/skills"

# Per-bundle transform declarations. A bundle needing neither declares neither.
#
# cross_ref_prefix: skills reference each other as "superpowers:<name>", a
#   plugin namespace Rudra does not have. The transform rewrites those to
#   /skills/library/superpowers/<name>/SKILL.md so the agent can read_file them.
# bootstrap_skill + platform_ref_section: upstream 6.3.0 speaks in actions and
#   resolves them per harness via references/<harness>-tools.md, listed under
#   "## Platform Adaptation". Rudra adds its own file and list entry rather
#   than forking 14 skills (spec section 1).
cross_ref_prefix = "superpowers:"
bootstrap_skill = "using-superpowers"
platform_ref_section = "## Platform Adaptation"
```

- [ ] **Step 4: Write `bundle.py`**

Create `src/rudra/skills/bundle.py`:

```python
"""One vendored upstream corpus, described by its own BUNDLE.toml.

A bundle is self-describing so the transform is not shaped around any
single upstream: shipping a second corpus is a directory plus one line in
registry.py (spec S11a.5).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

_REQUIRED = (
    "name",
    "upstream",
    "version",
    "copied_on",
    "source",
    "license",
    "license_file",
    "copyright",
    "frozen",
    "skills_root",
)


@dataclass(frozen=True)
class Bundle:
    """Metadata for one vendored corpus, plus where its files are."""

    name: str
    root: Path
    upstream: str
    version: str
    copied_on: str
    source: str
    license: str
    license_file: str
    copyright: str
    frozen: bool
    skills_root: str
    cross_ref_prefix: str | None = None
    bootstrap_skill: str | None = None
    platform_ref_section: str | None = None

    @property
    def skills_path(self) -> Path:
        return self.root / self.skills_root

    @property
    def manifest_path(self) -> Path:
        return self.root / "MANIFEST.sha256"

    @property
    def license_path(self) -> Path:
        return self.root / self.license_file

    def skill_names(self) -> tuple[str, ...]:
        """Directory names holding a SKILL.md, sorted.

        Sorted because the transform's output must be deterministic: 11b's
        cache key would otherwise change with directory iteration order.
        """
        return tuple(sorted(p.parent.name for p in self.skills_path.glob("*/SKILL.md")))


def load_bundle(root: Path) -> Bundle:
    """Parse `root/BUNDLE.toml` into a Bundle.

    Missing required keys raise rather than defaulting: a bundle with no
    declared license would otherwise ship with silently empty attribution.
    """
    data = tomllib.loads((root / "BUNDLE.toml").read_text(encoding="utf-8"))

    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        msg = f"{root / 'BUNDLE.toml'} is missing required key(s): {', '.join(missing)}"
        raise ValueError(msg)

    return Bundle(
        name=data["name"],
        root=root,
        upstream=data["upstream"],
        version=data["version"],
        copied_on=data["copied_on"],
        source=data["source"],
        license=data["license"],
        license_file=data["license_file"],
        copyright=data["copyright"],
        frozen=data["frozen"],
        skills_root=data["skills_root"],
        cross_ref_prefix=data.get("cross_ref_prefix"),
        bootstrap_skill=data.get("bootstrap_skill"),
        platform_ref_section=data.get("platform_ref_section"),
    )
```

- [ ] **Step 5: Write `registry.py`**

Create `src/rudra/skills/registry.py`:

```python
"""The one list of vendored skill bundles that ship with Rudra.

Adding an upstream corpus is a data change here, not a design change --
the same shape stacks/registry.py already uses for stack profiles.
"""

from __future__ import annotations

from pathlib import Path

from rudra.skills.bundle import Bundle, load_bundle

_BUNDLES_DIR = Path(__file__).parent / "bundles"

BUNDLES: tuple[Bundle, ...] = (load_bundle(_BUNDLES_DIR / "superpowers"),)

# The 8 skills D11 enables by default, plus the bootstrap.
#
# `using-superpowers` is not one of the 8 and is not optional: it is the
# instruction block that makes an agent reach for skills at all (C5.3).
# Listing it here rather than special-casing it in the transform keeps
# render() dumb -- `enabled` is exactly the set copied into active/.
#
# The 5 remaining skills stay off. The rewrite cost that originally deferred
# 4 of them is gone (spec section 1) and their prerequisites shipped in
# Steps 8 and 9b, but selection quality across a longer index at 32B is what
# C5.8 exists to measure, so D11 stands until 11c produces evidence.
DEFAULT_ENABLED: frozenset[str] = frozenset(
    {
        "brainstorming",
        "writing-plans",
        "executing-plans",
        "systematic-debugging",
        "test-driven-development",
        "verification-before-completion",
        "requesting-code-review",
        "receiving-code-review",
        "using-superpowers",
    }
)

__all__ = ["BUNDLES", "DEFAULT_ENABLED", "Bundle"]
```

- [ ] **Step 6: Run the tests**

```bash
uv run pytest tests/test_skills_registry.py -q
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Expected: 8 passed; `All checks passed!`; format clean.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/skills tests/test_skills_registry.py
git commit -m "feat(skills): add the bundle model and registry

Each vendored corpus describes itself in BUNDLE.toml -- provenance,
license, and the per-bundle transform declarations. Shipping a second
upstream is a directory plus one line in registry.py."
```

---

## Task 3: `render()` — the two-tree layout, the duplicate invariant, determinism

**Files:**
- Create: `src/rudra/skills/transform.py`
- Test: `tests/test_skills_transform.py`

**Interfaces:**
- Consumes: `Bundle`, `BUNDLES`, `DEFAULT_ENABLED` from Task 2.
- Produces: `rudra.skills.transform.render(bundles: Sequence[Bundle], enabled: frozenset[str], dest: Path) -> RenderReport`; `RenderReport` (frozen dataclass, fields `library_skills: tuple[str, ...]` as `"<bundle>/<skill>"`, `active_skills: tuple[str, ...]`, `cross_refs_rewritten: int`, `platform_refs_injected: tuple[str, ...]`); `DuplicateSkillError(ValueError)`. Tasks 4 and 5 extend this same function; the signature does not change.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_transform.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from rudra.skills.bundle import Bundle
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import DuplicateSkillError, render


def _fake_bundle(root: Path, name: str, skills: dict[str, str]) -> Bundle:
    """A minimal on-disk bundle, so layout tests do not depend on 472K of corpus."""
    skills_root = root / "src" / "skills"
    for skill_name, body in skills.items():
        skill_dir = skills_root / skill_name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill_name}\ndescription: test skill\n---\n\n{body}\n",
            encoding="utf-8",
        )
    return Bundle(
        name=name,
        root=root,
        upstream="https://example.invalid/x",
        version="1.0.0",
        copied_on="2026-08-17",
        source="fixture",
        license="MIT",
        license_file="LICENSE",
        copyright="Copyright (c) 2026 Nobody",
        frozen=True,
        skills_root="src/skills",
    )


def test_library_is_namespaced_by_bundle(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").is_file()
    assert (tmp_path / "out" / "library" / "demo" / "beta" / "SKILL.md").is_file()


def test_active_is_flat_and_holds_only_enabled_skills(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "active" / "alpha" / "SKILL.md").is_file()
    assert not (tmp_path / "out" / "active" / "beta").exists()
    assert report.active_skills == ("alpha",)
    assert report.library_skills == ("demo/alpha", "demo/beta")


def test_supporting_files_travel_with_their_skill(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})
    refs = bundle.skills_path / "alpha" / "references"
    refs.mkdir()
    (refs / "deep.md").write_text("reference body", encoding="utf-8")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert (tmp_path / "out" / "library" / "demo" / "alpha" / "references" / "deep.md").is_file()
    assert (tmp_path / "out" / "active" / "alpha" / "references" / "deep.md").is_file()


def test_duplicate_active_skill_name_raises_naming_both_bundles(tmp_path: Path) -> None:
    one = _fake_bundle(tmp_path / "one", "first", {"alpha": "A"})
    two = _fake_bundle(tmp_path / "two", "second", {"alpha": "A2"})

    with pytest.raises(DuplicateSkillError) as excinfo:
        render([one, two], frozenset({"alpha"}), tmp_path / "out")

    message = str(excinfo.value)
    assert "alpha" in message
    assert "first" in message
    assert "second" in message


def test_duplicate_names_are_fine_when_only_one_is_enabled(tmp_path: Path) -> None:
    one = _fake_bundle(tmp_path / "one", "first", {"alpha": "A"})
    two = _fake_bundle(tmp_path / "two", "second", {"alpha": "A2", "beta": "B"})

    render([one, two], frozenset({"beta"}), tmp_path / "out")

    assert (tmp_path / "out" / "active" / "beta").is_dir()


def test_render_into_an_existing_dest_replaces_it(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})
    dest = tmp_path / "out"
    (dest / "library" / "demo" / "stale").mkdir(parents=True)

    render([bundle], frozenset({"alpha"}), dest)

    assert not (dest / "library" / "demo" / "stale").exists()


def test_render_is_deterministic(tmp_path: Path) -> None:
    """Byte-identical output twice over, or 11b's cache key thrashes."""
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})

    render([bundle], frozenset({"alpha"}), tmp_path / "one")
    render([bundle], frozenset({"alpha"}), tmp_path / "two")

    first = sorted(p.relative_to(tmp_path / "one") for p in (tmp_path / "one").rglob("*"))
    second = sorted(p.relative_to(tmp_path / "two") for p in (tmp_path / "two").rglob("*"))
    assert first == second
    for rel in first:
        left, right = tmp_path / "one" / rel, tmp_path / "two" / rel
        if left.is_file():
            assert left.read_bytes() == right.read_bytes()


def test_renders_the_real_corpus(tmp_path: Path) -> None:
    report = render(BUNDLES, DEFAULT_ENABLED, tmp_path / "out")

    assert len(report.library_skills) == 14
    assert len(report.active_skills) == 9
    assert (tmp_path / "out" / "active" / "brainstorming" / "SKILL.md").is_file()
    assert (tmp_path / "out" / "library" / "superpowers" / "writing-skills" / "SKILL.md").is_file()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_skills_transform.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.skills.transform'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/skills/transform.py`:

```python
"""Render vendored bundles into the tree an agent reads.

Pure: takes bundles and an enabled set, writes a destination. It does not
know about caches, XDG paths, or agents -- Step 11b decides where this
output lives and hands it to create_deep_agent(skills=...).

Two output trees, with different rules:

  library/<bundle>/<skill>/   every skill, namespaced. Never indexed, only
                             read_file'd, so no name constraint applies and
                             collisions are impossible by construction.
  active/<skill>/             the enabled skills, flat. deepagents requires
                             the frontmatter `name` to equal the parent
                             directory name (middleware/skills.py:347), so
                             namespacing here would mean rewriting vendored
                             frontmatter.

active/ is copied from the rendered library/, not from the source, so it
inherits every rewrite for free and the two trees cannot drift.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from rudra.skills.bundle import Bundle


class DuplicateSkillError(ValueError):
    """Two bundles ship an enabled skill with the same name.

    Skill-name uniqueness across bundles is the owner's contract (spec
    S11a.6), so this is an invariant rather than a condition to resolve:
    there is no precedence rule and no config to disambiguate. It fails
    loudly because silent shadowing -- a second bundle quietly replacing a
    skill body -- would be unfindable at runtime.
    """


@dataclass(frozen=True)
class RenderReport:
    """What a render produced. Tests and 11b's cache builder read this."""

    library_skills: tuple[str, ...]
    active_skills: tuple[str, ...]
    cross_refs_rewritten: int
    platform_refs_injected: tuple[str, ...]


def _check_no_duplicate_active_names(
    bundles: Sequence[Bundle], enabled: frozenset[str]
) -> None:
    owner: dict[str, str] = {}
    for bundle in bundles:
        for name in bundle.skill_names():
            if name not in enabled:
                continue
            if name in owner:
                msg = (
                    f"enabled skill '{name}' is shipped by both bundle "
                    f"'{owner[name]}' and bundle '{bundle.name}'. Skill names "
                    f"must be unique across bundles."
                )
                raise DuplicateSkillError(msg)
            owner[name] = bundle.name


def render(
    bundles: Sequence[Bundle],
    enabled: frozenset[str],
    dest: Path,
) -> RenderReport:
    """Render `bundles` into `dest`, enabling `enabled` in the index."""
    _check_no_duplicate_active_names(bundles, enabled)

    library = dest / "library"
    active = dest / "active"
    if dest.exists():
        shutil.rmtree(dest)
    library.mkdir(parents=True)
    active.mkdir(parents=True)

    library_skills: list[str] = []
    for bundle in bundles:
        for name in bundle.skill_names():
            shutil.copytree(bundle.skills_path / name, library / bundle.name / name)
            library_skills.append(f"{bundle.name}/{name}")

    active_skills: list[str] = []
    for bundle in bundles:
        for name in bundle.skill_names():
            if name in enabled:
                shutil.copytree(library / bundle.name / name, active / name)
                active_skills.append(name)

    return RenderReport(
        library_skills=tuple(sorted(library_skills)),
        active_skills=tuple(sorted(active_skills)),
        cross_refs_rewritten=0,
        platform_refs_injected=(),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills_transform.py -q`
Expected: 8 passed

- [ ] **Step 5: Run the gates**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
```

Expected: `All checks passed!`; format clean; total = baseline + 22.

- [ ] **Step 6: Commit**

```bash
git add src/rudra/skills/transform.py tests/test_skills_transform.py
git commit -m "feat(skills): render bundles into library/ and active/

library/ is namespaced per bundle and never indexed; active/ is flat
because deepagents requires a skill's frontmatter name to match its
directory name. active/ is copied from the rendered library/ so later
rewrites apply to both without being written twice.

Duplicate enabled skill names across bundles raise rather than shadow."
```

---

## Task 4: Rewrite inter-skill cross-references to paths

**Files:**
- Modify: `src/rudra/skills/transform.py`
- Modify: `tests/test_skills_transform.py`

**Interfaces:**
- Consumes: `render()` from Task 3.
- Produces: no new public names. `RenderReport.cross_refs_rewritten` becomes non-zero.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills_transform.py`:

```python
def test_cross_refs_become_library_paths(tmp_path: Path) -> None:
    bundle = _fake_bundle(
        tmp_path / "b",
        "demo",
        {"alpha": "See demo:beta for details.", "beta": "B"},
    )
    bundle = replace(bundle, cross_ref_prefix="demo:")

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "/skills/library/demo/beta/SKILL.md" in body
    assert "demo:beta" not in body
    assert report.cross_refs_rewritten == 1


def test_active_copies_inherit_the_rewrite(tmp_path: Path) -> None:
    bundle = _fake_bundle(
        tmp_path / "b", "demo", {"alpha": "See demo:beta.", "beta": "B"}
    )
    bundle = replace(bundle, cross_ref_prefix="demo:")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "active" / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "/skills/library/demo/beta/SKILL.md" in body


def test_unknown_names_after_the_prefix_are_left_alone(tmp_path: Path) -> None:
    """Only real skill names are rewritten.

    Prose like "demo:whatever" is not a cross-reference, and turning it into
    a path to a file that does not exist would be worse than leaving it.
    """
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:nosuchskill."})
    bundle = replace(bundle, cross_ref_prefix="demo:")

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "demo:nosuchskill" in body
    assert report.cross_refs_rewritten == 0


def test_a_bundle_without_a_prefix_is_not_rewritten(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "See demo:beta.", "beta": "B"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert report.cross_refs_rewritten == 0


def test_supporting_markdown_is_rewritten_too(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A", "beta": "B"})
    bundle = replace(bundle, cross_ref_prefix="demo:")
    (bundle.skills_path / "alpha" / "notes.md").write_text("see demo:beta", encoding="utf-8")

    render([bundle], frozenset({"alpha"}), tmp_path / "out")

    body = (tmp_path / "out" / "library" / "demo" / "alpha" / "notes.md").read_text(
        encoding="utf-8"
    )
    assert "/skills/library/demo/beta/SKILL.md" in body


def test_real_corpus_has_no_surviving_namespace_refs(tmp_path: Path) -> None:
    report = render(BUNDLES, DEFAULT_ENABLED, tmp_path / "out")

    assert report.cross_refs_rewritten == 26

    survivors = [
        path
        for path in (tmp_path / "out").rglob("*.md")
        if "superpowers:" in path.read_text(encoding="utf-8")
    ]
    assert survivors == []


def test_every_rewritten_target_exists_on_disk(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    targets = set()
    for path in dest.rglob("*.md"):
        for match in re.finditer(r"/skills/library/([a-z0-9-]+)/([a-z0-9-]+)/SKILL\.md", path.read_text(encoding="utf-8")):
            targets.add((match.group(1), match.group(2)))

    assert targets
    for bundle_name, skill_name in sorted(targets):
        assert (dest / "library" / bundle_name / skill_name / "SKILL.md").is_file()
```

Add to that file's imports:

```python
import re
from dataclasses import replace
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skills_transform.py -q`
Expected: FAIL — the new tests report `cross_refs_rewritten == 0` where non-zero is asserted, and `demo:beta` still present.

- [ ] **Step 3: Write the implementation**

In `src/rudra/skills/transform.py`, add `import re` to the imports, then add this function above `render()`:

```python
_REWRITABLE_SUFFIXES = frozenset({".md"})


def _rewrite_cross_refs(skill_root: Path, bundle: Bundle) -> int:
    """Turn `<prefix><skill>` references into read_file-able library paths.

    Upstream skills reference each other through a plugin namespace
    ("superpowers:test-driven-development"). Rudra has no plugin namespace
    and no skill-invoke tool, so a reference has to become a path the model
    can read_file -- which is exactly what deepagents' own skills prompt
    tells it to do (middleware/skills.py:721).

    Only known skill names are rewritten. Prose that happens to follow the
    prefix is not a cross-reference, and pointing it at a file that does not
    exist would be worse than leaving it alone.

    Targets are library paths, never active ones: library/ holds every
    skill regardless of enablement, so a reference stays valid when the
    enabled set changes and C5.9 rewrites nothing (spec S11a.4).
    """
    if bundle.cross_ref_prefix is None:
        return 0

    known = set(bundle.skill_names())
    pattern = re.compile(re.escape(bundle.cross_ref_prefix) + r"([a-z0-9][a-z0-9-]*)")

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in known:
            return match.group(0)
        return f"/skills/library/{bundle.name}/{name}/SKILL.md"

    rewritten = 0
    for path in sorted(skill_root.rglob("*")):
        if not path.is_file() or path.suffix not in _REWRITABLE_SUFFIXES:
            continue
        original = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(_replace, original)
        if count:
            path.write_text(updated, encoding="utf-8")
        rewritten += sum(1 for m in pattern.finditer(original) if m.group(1) in known)
    return rewritten
```

Then in `render()`, replace the library-copy loop and the `cross_refs_rewritten=0` field:

```python
    library_skills: list[str] = []
    cross_refs = 0
    for bundle in bundles:
        for name in bundle.skill_names():
            target = library / bundle.name / name
            shutil.copytree(bundle.skills_path / name, target)
            cross_refs += _rewrite_cross_refs(target, bundle)
            library_skills.append(f"{bundle.name}/{name}")
```

and

```python
        cross_refs_rewritten=cross_refs,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills_transform.py -q`
Expected: 15 passed

If `test_real_corpus_has_no_surviving_namespace_refs` reports a count other than 26, do not change the assertion to match. Re-run the measurement and find out which references moved:

```bash
grep -rhoE 'superpowers:[a-z-]+' src/rudra/skills/bundles/superpowers/src/skills | sort | uniq -c
```

A changed count means the corpus on disk is not the one this plan measured, which invalidates the spec's §1 analysis too.

- [ ] **Step 5: Run the gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra/skills/transform.py tests/test_skills_transform.py
git commit -m "feat(skills): rewrite plugin-namespace cross-refs to library paths

Skills reference each other as superpowers:<name>, a namespace Rudra does
not have. Rewritten to /skills/library/superpowers/<name>/SKILL.md, which
the agent can read_file. Targets are library paths so they stay valid
regardless of which skills are enabled. Closes C5.2c."
```

---

## Task 5: Inject the Rudra platform reference

**Files:**
- Create: `src/rudra/skills/rudra_tools.py`
- Modify: `src/rudra/skills/transform.py`
- Modify: `tests/test_skills_transform.py`

**Interfaces:**
- Consumes: `render()` from Tasks 3-4.
- Produces: `rudra.skills.rudra_tools.RUDRA_TOOLS_MD: str`, `rudra.skills.rudra_tools.PLATFORM_REF_LINE: str`, `rudra.skills.rudra_tools.REFERENCE_FILENAME: str`. `RenderReport.platform_refs_injected` becomes non-empty.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills_transform.py`:

```python
def test_platform_reference_file_is_written_into_the_bootstrap_skill(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    report = render(BUNDLES, DEFAULT_ENABLED, dest)

    ref = dest / "library" / "superpowers" / "using-superpowers" / "references" / "rudra-tools.md"
    assert ref.is_file()
    assert report.platform_refs_injected == ("superpowers/using-superpowers",)


def test_platform_adaptation_list_names_rudra(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    body = (
        dest / "library" / "superpowers" / "using-superpowers" / "SKILL.md"
    ).read_text(encoding="utf-8")
    section = body.split("## Platform Adaptation", 1)[1].split("\n## ", 1)[0]

    assert "Rudra: `references/rudra-tools.md`" in section
    # Upstream's own entries survive -- this is an addition, not a replacement.
    assert "Codex:" in section


def test_the_active_bootstrap_carries_the_reference_too(tmp_path: Path) -> None:
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    assert (dest / "active" / "using-superpowers" / "references" / "rudra-tools.md").is_file()
    body = (dest / "active" / "using-superpowers" / "SKILL.md").read_text(encoding="utf-8")
    assert "Rudra: `references/rudra-tools.md`" in body


def test_reference_states_that_no_tool_can_mark_work_done(tmp_path: Path) -> None:
    """The reconciliation that justifies the file existing.

    verification-before-completion and executing-plans both instruct the
    agent to mark work complete. Only loop/engine.py writes DONE, and only
    on VerifyReport.passed (S9c.1). Unreconciled, the corpus argues with
    the architecture inside the model's context.
    """
    dest = tmp_path / "out"
    render(BUNDLES, DEFAULT_ENABLED, dest)

    ref = (
        dest / "library" / "superpowers" / "using-superpowers" / "references" / "rudra-tools.md"
    ).read_text(encoding="utf-8")

    assert "add_tasks" in ref
    assert "cannot mark" in ref
    assert "rudra verify" in ref
    assert "read_file" in ref


def test_injection_is_skipped_for_a_bundle_that_declares_none(tmp_path: Path) -> None:
    bundle = _fake_bundle(tmp_path / "b", "demo", {"alpha": "A"})

    report = render([bundle], frozenset({"alpha"}), tmp_path / "out")

    assert report.platform_refs_injected == ()


def test_a_missing_platform_section_raises(tmp_path: Path) -> None:
    """A silent skip here would ship a corpus that never mentions Rudra."""
    bundle = _fake_bundle(tmp_path / "b", "demo", {"boot": "no section here"})
    bundle = replace(
        bundle, bootstrap_skill="boot", platform_ref_section="## Platform Adaptation"
    )

    with pytest.raises(ValueError, match="Platform Adaptation"):
        render([bundle], frozenset({"boot"}), tmp_path / "out")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skills_transform.py -q`
Expected: FAIL — `rudra-tools.md` does not exist.

- [ ] **Step 3: Write the reference text**

Create `src/rudra/skills/rudra_tools.py`:

```python
"""Rudra's entry in superpowers' platform-adaptation scheme.

Upstream 6.3.0 speaks in actions -- "dispatch a subagent", "read a file" --
and resolves them per harness through references/<harness>-tools.md, listed
under "## Platform Adaptation" in the bootstrap skill. Rudra adds one file
and one list entry instead of forking 14 skills (spec section 1); this is
what replaces the obsolete D10 tool-name rewrite.

The file is not a lookup table. Several enabled skills instruct behavior
Rudra structurally forbids -- verification-before-completion and
executing-plans both tell the agent to mark work complete, while only
loop/engine.py writes DONE and only on VerifyReport.passed. Reconciling
that is the point.
"""

from __future__ import annotations

REFERENCE_FILENAME = "references/rudra-tools.md"

PLATFORM_REF_LINE = "- Rudra: `references/rudra-tools.md`"

RUDRA_TOOLS_MD = """# Rudra Tool Mapping

Skills speak in actions ("dispatch a subagent", "read a file", "create a
todo"). On Rudra these resolve to the tools below.

| Action skills request | Rudra equivalent |
| --- | --- |
| Read a file | `read_file(file_path=..., limit=1000)` |
| Write a new file | `write_file` |
| Change an existing file | `edit_file` |
| List or search files | `ls`, `glob`, `grep` |
| Run a command | `execute` |
| Dispatch a subagent | `task`, with `subagent_type` |
| Create or track todos | `add_tasks`, `drop_task` |
| Run the tests | `run_tests` |
| Git operations | `git_status`, `git_diff`, `git_log` |
| Record a decision | `record_fact` |
| Ask the human a question | `ask_user` |
| Invoke a skill | no such tool -- see below |

## Invoking a skill

Rudra has no skill-invoke tool. Skills are reached by reading them:
`read_file(file_path="/skills/library/superpowers/<name>/SKILL.md",
limit=1000)`. The default read limit of 100 lines truncates most skills, so
pass `limit`.

Cross-references in these skills are already written as
`/skills/library/...` paths. Read the path.

## You cannot mark work done

This is the most important difference, and it overrides any skill that says
otherwise.

`add_tasks` and `drop_task` add and remove work. **Neither can mark a task
complete, and no other tool can either.** Rudra's Python loop decides that,
and only when a deterministic gate passes. A skill instructing you to "mark
the task complete", "check it off" or "declare the work finished" is
describing a harness you are not running on. Do the work; the gate decides.

The gate is `rudra verify`: syntax, lint, typecheck, tests, and a scan for
stubbed-out code. It is deterministic and it is not your judgment. When it
fails, its output comes back to you verbatim and you fix what it named.

Code review is advisory here. A reviewer subagent runs once at the end and
prints its findings; it gates nothing.

## Subagents

`task` takes a `subagent_type`:

- `coder` -- writes and edits code
- `tester` -- writes and runs tests
- `reviewer` -- reads and reports; it has no write tools at all
- `general-purpose` -- anything else

The reviewer cannot write because its tools are never registered, not
because its prompt asks it not to. Do not ask it to apply a fix.

## Commands may be denied

`execute` passes through a permission gate before it runs. Under `ask` mode
the human approves each command. Under `--auto` commands are denied unless
the human opted in with `--allow-shell`, so an unattended run may be unable
to execute anything at all -- including tests.

A denial is an answer, not an error to route around. Do not rewrite a
denied command as a shell redirect, an editor invocation, or a script that
performs the same action. Report what was denied and continue with what you
can do.

## Asking questions

`ask_user` is budgeted by `[agent] max_questions` and may be zero. Ask only
what you cannot infer from the project itself. Record what you learn with
`record_fact`, including *why* -- facts reach every later agent, questions
do not.

## Not available on Rudra

- **Browser and visual companions.** No browser, and no Node runtime. The
  visual companion described in `brainstorming` cannot start; work in text.
- **Git worktrees and branch workflows** are available only through the
  `git_*` tools and `execute`, both gated as above.
- **Web search and fetch.** Rudra is local-first and ships no web tools.
"""
```

- [ ] **Step 4: Write the injection**

In `src/rudra/skills/transform.py`, add the import:

```python
from rudra.skills.rudra_tools import PLATFORM_REF_LINE, REFERENCE_FILENAME, RUDRA_TOOLS_MD
```

Add this function above `render()`:

```python
def _inject_platform_reference(skill_root: Path, bundle: Bundle) -> bool:
    """Add Rudra's mapping file and list it in the Platform Adaptation section.

    Returns True when the bundle declared the hook and it was applied.

    A bundle that declares `bootstrap_skill` but whose skill has no
    `platform_ref_section` raises rather than skipping: a silent skip ships
    a corpus that never tells the model Rudra exists, and the failure would
    only show up as poor tool use during a live run.
    """
    if bundle.bootstrap_skill is None or bundle.platform_ref_section is None:
        return False

    reference = skill_root / REFERENCE_FILENAME
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_text(RUDRA_TOOLS_MD, encoding="utf-8")

    skill_md = skill_root / "SKILL.md"
    body = skill_md.read_text(encoding="utf-8")
    if bundle.platform_ref_section not in body:
        msg = (
            f"bundle '{bundle.name}' declares platform_ref_section "
            f"'{bundle.platform_ref_section}', which is absent from "
            f"{bundle.bootstrap_skill}/SKILL.md"
        )
        raise ValueError(msg)

    head, _, tail = body.partition(bundle.platform_ref_section)
    lines = tail.split("\n")
    last_entry = max(
        (index for index, line in enumerate(lines) if line.startswith("- ")),
        default=0,
    )
    lines.insert(last_entry + 1, PLATFORM_REF_LINE)
    skill_md.write_text(head + bundle.platform_ref_section + "\n".join(lines), encoding="utf-8")
    return True
```

In `render()`, extend the library loop and the report field:

```python
    library_skills: list[str] = []
    cross_refs = 0
    injected: list[str] = []
    for bundle in bundles:
        for name in bundle.skill_names():
            target = library / bundle.name / name
            shutil.copytree(bundle.skills_path / name, target)
            cross_refs += _rewrite_cross_refs(target, bundle)
            if name == bundle.bootstrap_skill and _inject_platform_reference(target, bundle):
                injected.append(f"{bundle.name}/{name}")
            library_skills.append(f"{bundle.name}/{name}")
```

and

```python
        platform_refs_injected=tuple(sorted(injected)),
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills_transform.py -q`
Expected: 21 passed

- [ ] **Step 6: Read the rendered bootstrap by eye (spec §8 acceptance)**

```bash
uv run python -c "
import tempfile, pathlib
from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import render
d = pathlib.Path(tempfile.mkdtemp())
render(BUNDLES, DEFAULT_ENABLED, d)
print(d)
"
```

Open the printed directory and read
`library/superpowers/using-superpowers/SKILL.md`. Confirm by eye: the
Platform Adaptation list reads naturally with Rudra added, upstream's
entries are intact, and nothing above or below the section was disturbed.
Then open `library/superpowers/writing-plans/SKILL.md` and confirm a
rewritten cross-reference reads as a path a model would follow. The corpus
is prose a model must obey; a passing regex is not a human confirming it
reads correctly.

- [ ] **Step 7: Run the gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add src/rudra/skills tests/test_skills_transform.py
git commit -m "feat(skills): add Rudra's platform-adaptation reference

Superpowers 6.3.0 speaks in actions and resolves them per harness through
references/<harness>-tools.md. Rudra adds one file and one list entry
rather than rewriting tool names across 14 skills -- D10's premise does
not hold against 6.3.0 (spec section 1).

The file reconciles the corpus with Rudra's architecture: no tool can mark
work done, the deterministic gate decides, review is advisory, and a denied
command is an answer rather than something to route around."
```

---

## Task 6: Validate the rendered corpus against deepagents' own parser

**Files:**
- Create: `tests/test_skills_deepagents_contract.py`

**Interfaces:**
- Consumes: `render()`, `BUNDLES`, `DEFAULT_ENABLED`.
- Produces: nothing. This task is a contract test only.

**Why this is its own task:** `tests/test_deepagents_contract.py` already exists as the place upstream assumptions are pinned, and a reviewer could reasonably approve the transform while rejecting how it is validated against upstream. These tests import deepagents internals deliberately — if an upgrade moves them, this file is the trigger that says so.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_deepagents_contract.py`:

```python
"""The rendered corpus, checked against deepagents' own skill parser.

SkillsMiddleware silently *skips* a skill whose frontmatter will not parse
or whose description is over length (middleware/skills.py). A skipped skill
is invisible at runtime: no error, it simply never appears in the index and
the model never reaches for it. These tests make that failure loud.

They import deepagents internals on purpose. If an upgrade moves or renames
them, this file failing is the signal to re-verify the contract, not a
reason to delete the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.middleware.skills import _parse_skill_metadata, _validate_skill_name

from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import render


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("skills")
    render(BUNDLES, DEFAULT_ENABLED, dest / "out")
    return dest / "out"


def test_every_active_skill_parses(rendered: Path) -> None:
    for skill_md in sorted((rendered / "active").glob("*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None, f"deepagents would silently skip {skill_md}"


def test_every_active_skill_name_matches_its_directory(rendered: Path) -> None:
    for skill_md in sorted((rendered / "active").glob("*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None
        valid, error = _validate_skill_name(metadata["name"], skill_md.parent.name)
        assert valid, error


def test_every_library_skill_parses(rendered: Path) -> None:
    """library/ is not indexed, but a skill that cannot parse cannot be enabled later."""
    for skill_md in sorted((rendered / "library").glob("*/*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None, f"deepagents would silently skip {skill_md}"


def test_active_holds_exactly_the_enabled_set(rendered: Path) -> None:
    on_disk = {p.name for p in (rendered / "active").iterdir() if p.is_dir()}
    assert on_disk == set(DEFAULT_ENABLED)


def test_descriptions_are_non_empty(rendered: Path) -> None:
    """An empty description makes a skill unselectable even when indexed."""
    for skill_md in sorted((rendered / "active").glob("*/SKILL.md")):
        metadata = _parse_skill_metadata(
            skill_md.read_text(encoding="utf-8"), str(skill_md), skill_md.parent.name
        )
        assert metadata is not None
        assert metadata["description"].strip()
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/test_skills_deepagents_contract.py -q`
Expected: 5 passed.

If `_parse_skill_metadata`'s signature differs from `(content, skill_path, directory_name)`, read the current definition and adapt the call — the contract being tested is "the rendered corpus survives upstream's parser", not the argument list:

```bash
grep -n -A12 "^def _parse_skill_metadata" .venv/lib/python3.13/site-packages/deepagents/middleware/skills.py
```

- [ ] **Step 3: Run the gates and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add tests/test_skills_deepagents_contract.py
git commit -m "test(skills): validate the rendered corpus against deepagents' parser

SkillsMiddleware silently skips a skill it cannot parse, which at runtime
looks like a skill the model simply never uses. These tests make that
failure loud, and pin the frontmatter name/directory constraint the flat
active/ tree depends on."
```

---

## Task 7: Attribution and packaging

**Files:**
- Create: `src/rudra/skills/notice.py`
- Create: `NOTICE` (repo root)
- Create: `tests/test_skills_notice.py`
- Test: `tests/test_skills_notice.py`

**Interfaces:**
- Consumes: `BUNDLES` from Task 2.
- Produces: `rudra.skills.notice.render_notice(bundles: Sequence[Bundle]) -> str`; the repo-root `NOTICE` file.

- [ ] **Step 1: Write the failing test**

Create `tests/test_skills_notice.py`:

```python
from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

from rudra.skills.notice import render_notice
from rudra.skills.registry import BUNDLES

REPO_ROOT = Path(__file__).parent.parent


def test_notice_names_rudras_own_license() -> None:
    text = render_notice(BUNDLES)
    assert "Apache License" in text or "Apache-2.0" in text


def test_notice_attributes_every_bundle() -> None:
    text = render_notice(BUNDLES)
    for bundle in BUNDLES:
        assert bundle.name in text
        assert bundle.upstream in text
        assert bundle.version in text
        assert bundle.license in text
        assert bundle.copyright in text


def test_notice_states_what_rudra_modifies() -> None:
    """MIT requires the notice, not a changelog -- but the honest statement
    is that the distributed copy is unmodified and the runtime one is not."""
    text = render_notice(BUNDLES)
    assert "unmodified" in text
    assert "Cross-references between documents are rewritten" in text


def test_committed_notice_matches_the_registry() -> None:
    """NOTICE is generated, so it cannot drift when a bundle is added."""
    assert (REPO_ROOT / "NOTICE").read_text(encoding="utf-8") == render_notice(BUNDLES)


def test_built_wheel_carries_the_vendored_corpus(tmp_path: Path) -> None:
    """472K of non-Python data must survive packaging.

    Losing it would only surface for a pipx or PyPI user, long after merge.
    """
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1

    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())

    assert "rudra/skills/bundles/superpowers/src/skills/brainstorming/SKILL.md" in names
    assert "rudra/skills/bundles/superpowers/LICENSE" in names
    assert "rudra/skills/bundles/superpowers/MANIFEST.sha256" in names
    assert "rudra/skills/bundles/superpowers/BUNDLE.toml" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_skills_notice.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'rudra.skills.notice'`

- [ ] **Step 3: Write the implementation**

Create `src/rudra/skills/notice.py`:

```python
"""Generate the repo-root NOTICE from the bundle registry.

Generated rather than hand-written so attribution cannot drift as bundles
are added -- a test asserts the committed file matches this output.

On the MIT question (spec section 5): MIT requires that the copyright and
permission notice survive in copies. It does not require a statement of
modification -- that is Apache-2.0 section 4(b). Because Rudra's transform
runs at cache-build time, the corpus Rudra *distributes* is byte-identical
upstream and the modified copy exists only in the user's cache. The notice
states both facts rather than an obligation that is not there.
"""

from __future__ import annotations

from collections.abc import Sequence

from rudra.skills.bundle import Bundle

_HEADER = """Rudra
Copyright (c) 2026 Archish Patel

Licensed under the Apache License, Version 2.0. See LICENSE for the full text.

================================================================================
Third-party components
================================================================================

Rudra ships vendored copies of the third-party projects below. Each copy is
byte-identical to the upstream release named, and is frozen: Rudra performs
no version check and no update fetch at runtime.

Rudra renders a modified copy of these files into the user's cache directory
when it runs. The distributed copy in this repository is unmodified. The
rendered copy differs in exactly two ways:

  1. Cross-references between documents are rewritten from the upstream
     plugin-namespace form to filesystem paths Rudra's agents can read.
  2. One reference file describing Rudra's own tools is added, and listed in
     the upstream document that indexes such files.

No other content is altered, added, or removed.
"""

_BUNDLE_TEMPLATE = """
--------------------------------------------------------------------------------
{name} {version}
--------------------------------------------------------------------------------

Upstream:  {upstream}
Version:   {version}
Vendored:  {copied_on}
License:   {license}
{copyright}

Full license text: src/rudra/skills/bundles/{name}/{license_file}
Vendored files:    src/rudra/skills/bundles/{name}/{skills_root}/
Content manifest:  src/rudra/skills/bundles/{name}/MANIFEST.sha256
"""


def render_notice(bundles: Sequence[Bundle]) -> str:
    """The full NOTICE text for `bundles`, in registry order."""
    parts = [_HEADER]
    for bundle in bundles:
        parts.append(
            _BUNDLE_TEMPLATE.format(
                name=bundle.name,
                version=bundle.version,
                upstream=bundle.upstream,
                copied_on=bundle.copied_on,
                license=bundle.license,
                copyright=bundle.copyright,
                license_file=bundle.license_file,
                skills_root=bundle.skills_root,
            )
        )
    return "".join(parts)
```

- [ ] **Step 4: Generate the NOTICE file**

```bash
uv run python -c "
from pathlib import Path
from rudra.skills.notice import render_notice
from rudra.skills.registry import BUNDLES
Path('NOTICE').write_text(render_notice(BUNDLES), encoding='utf-8')
"
cat NOTICE
```

Read it. Confirm the copyright line for Rudra is right — if the repo's
preferred holder string differs from `Archish Patel`, fix `_HEADER` and
regenerate rather than editing `NOTICE` by hand.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_skills_notice.py -q`
Expected: 5 passed. The wheel test takes several seconds — that is the build running.

- [ ] **Step 6: Run the full gates**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
```

Expected: `All checks passed!`; format clean; total = baseline + 45.

- [ ] **Step 7: Commit**

```bash
git add NOTICE src/rudra/skills/notice.py tests/test_skills_notice.py
git commit -m "feat(skills): generate NOTICE from the bundle registry

Attribution for every vendored corpus, generated so it cannot drift as
bundles are added. States plainly that the distributed copy is unmodified
and that the runtime copy differs in exactly two ways.

Closes C5.7 and the NOTICE half of A4.7."
```

---

## Task 8: Update the ledger

**Files:**
- Modify: `TODO.md` — Phase 5 rows, §0.3, §E Stage IV

**Interfaces:**
- Consumes: nothing.
- Produces: nothing. Session rule 6 — the ledger moves in the same branch as the code.

**Why this task is not optional:** a fresh session reads `TODO.md` §E, sees Step 11, reads `C5.4`, and performs a rename that turns `## What is a Skill?` into `## What is a read_file?`. Leaving the obsolete rows in place leaves that trap armed.

- [ ] **Step 1: Mark the obsolete rows**

In `TODO.md` Phase 5 (`### Phase 5 — Skills + Superpowers (vendored, D2)`, around line 651), change the status column for these rows to `OBSOLETE` and append the reason to each item's text. Do not delete the rows — they record why the work was believed necessary.

- `C5.4` → `OBSOLETE (2026-08-17)` — the 101 counted sites are English prose in superpowers 6.3.0, not tool references. Evidence: spec `docs/superpowers/specs/2026-08-17-step11a-skill-corpus-design.md` §1. Only 3 literal tool references exist in the 51-file corpus, and 2 of those are inside other harnesses' mapping files.
- `C5.4a` → `OBSOLETE (2026-08-17)` — all 20 `Skill` sites are the English noun. Its genuine insight (deepagents exposes no skill-invoke tool; use `read_file`) survives in `src/rudra/skills/rudra_tools.py`.
- `C5.4b` → `OBSOLETE as written (2026-08-17)` — 3-way merge against upstream contradicts S11a.3's freeze. The scripted-transform requirement survives in `transform.py`.
- `C5.4c` → `OBSOLETE (2026-08-17)` — the 58 deferred sites in `writing-skills` and `subagent-driven-development` do not exist.

- [ ] **Step 2: Mark the closed rows**

- `C5.2` → `DONE` — vendored as a frozen bundle at `src/rudra/skills/bundles/superpowers/`, pinned by `MANIFEST.sha256` rather than a git SHA (S11a.2: the upstream copy is a released plugin package, not a git checkout). 14 skills, 37 supporting files, 472K.
- `C5.2c` → `DONE` — 26 cross-references rewritten to `/skills/library/superpowers/<name>/SKILL.md` by `transform._rewrite_cross_refs`.
- `C5.7` → `DONE` — `NOTICE` generated from the registry by `src/rudra/skills/notice.py`, with §5's correction to the row's MIT reasoning.
- `C5.6` → amend the row: the `update` subcommand is **dropped** per S11a.3 (the corpus is frozen; the owner updates it by hand). `list` / `validate` / `rebuild` remain, in 11c.
- `A4.7` → amend: NOTICE half **DONE**; CONTRIBUTING still `PENDING`.

- [ ] **Step 3: Supersede §0.3's rewrite measurement**

In `TODO.md` §0.3, under `#### D10 rewrite scope — measured` (around line 141), add a dated superseding note directly beneath the heading. Do not delete the subsection — it records the measurement that was believed and why.

```markdown
> **SUPERSEDED 2026-08-17.** Measured against superpowers **6.3.0**, the 101
> sites below are English prose, not tool references: `Write` is the verb,
> `Task` is a numbered plan item (`Task <N>: complete`), `Skill` is the noun
> ("What is a Skill?"). Applying the mapping would corrupt the corpus.
> Upstream now speaks in actions and resolves them per harness through
> `references/<harness>-tools.md`, indexed under `## Platform Adaptation`
> (`using-superpowers/SKILL.md:52`). Rudra adds one such file instead.
> Full evidence: `docs/superpowers/specs/2026-08-17-step11a-skill-corpus-design.md` §1.
> `C5.4`, `C5.4a`, `C5.4b` and `C5.4c` are OBSOLETE.
```

- [ ] **Step 4: Record the decomposition in §E**

In `### Stage IV — Make it smart`, replace the single **11** row with **11a**, **11b** and **11c** rows, following the format Steps 9a/9b/9c and 10a/10b/10c already use in Stage III. Mark **11a COMPLETE** with: the spec and plan paths, rows closed (`C5.2`, `C5.2c`, `C5.7`, `A4.7` NOTICE half), rows obsoleted (`C5.4`, `C5.4a`, `C5.4b`, `C5.4c`), the S11a.1–S11a.7 decisions, the measured test count, and the statement that **11b (`C5.1`, `C5.2a`, `C5.2b`, `C5.3`, `C5.9`) is unblocked**. Carry forward the two 11b design questions from spec §9 verbatim: whether subagents get their own `skills=` sources, and whether `~/.rudra/skills/` and `<project>/.rudra/skills/` are bundles or plain sources.

- [ ] **Step 5: Verify and commit**

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest -q 2>&1 | tail -3
git add TODO.md
git commit -m "docs: Step 11a ledger — C5.2/C5.2c/C5.7 done, C5.4* obsolete

D10's tool-name rewrite does not apply to superpowers 6.3.0: the 101
counted sites are English prose. Recording this is the point of the commit
-- a fresh session reading C5.4 would otherwise run a rename that corrupts
the corpus."
```

---

## Verification

Run before declaring 11a complete:

```bash
uv run ruff check src/ tests/          # All checks passed!
uv run ruff format --check src/ tests/ # clean
uv run pytest -q                        # baseline + 45, zero failures
uv run rudra --version                  # Rudra v0.2.0 -- not 0.0.0+unknown
git diff --stat main..HEAD -- src/ tests/ NOTICE pyproject.toml
```

The by-eye acceptance in Task 5 Step 6 is part of this, not optional. It is
the only check that the rendered prose reads correctly to a human, and the
corpus is prose a model must follow.

Nothing in 11a is reachable by an agent. `[skills]` remains reserved in
`config/schema.py:44`, no `/skills/` route exists, and no agent is
constructed with `skills=`. That wiring is 11b.
