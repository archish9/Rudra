# Step 11a — The Vendored Skill Corpus and Its Transform (C5.2, C5.2c, C5.7)

**Date:** 2026-08-17
**Status:** design approved, not implemented
**Ledger rows:** closes `C5.2`, `C5.2c`, `C5.7`, and the standing half of `A4.7`
(NOTICE). **Obsoletes `C5.4`, `C5.4a`, `C5.4b`, `C5.4c`** — see §1, which is the
load-bearing finding of this design.
**Depends on:** nothing in code. The corpus is inert until 11b builds a cache.
**Blocks:** Step 11b (the cache builder renders what this defines), Step 11c
(the CLI lists it and the live run measures it).

---

## 0. Decomposition

The owner split Step 11 three ways on 2026-08-17, the same way Steps 9 and 10
were split. Step 11 carried 13 rows across three separable subsystems.

| Sub-step | Rows | Why separable |
|---|---|---|
| **11a** (this spec) | `C5.2`, `C5.2c`, `C5.7`, `A4.7` (NOTICE half) | The frozen corpus, the transform that renders it, its attribution. Fully unit-testable with no model backend and no agent wiring |
| **11b** | `C5.1`, `C5.2a`, `C5.2b`, `C5.3`, `C5.9` | Cache builder, `/skills/` route, `skills=` wiring, `[skills]` config, bootstrap injection. The step where an agent first sees a skill |
| **11c** | `C5.5`, `C5.6`, `C5.8` | `rudra skills` CLI, the session-start hook, and the live 32B selection-quality acceptance |

11a is first for the same reason 9a and 10a were: it is the only one of the three
whose acceptance needs no live model. Nothing an agent can see changes here.

---

## 1. What this replaces — D10 is obsolete against superpowers 6.3.0

TODO.md §0.3 (D10, dated 2026-08-05) specifies a rewrite of **101 Claude Code
tool-name sites** across the 14 skills: 34 `Write`, 33 `Task`, 20 `Skill`, 13
`Read`, 1 `Edit`. `C5.4`, `C5.4a`, `C5.4b` and `C5.4c` all exist to execute,
sequence, script and defer that rewrite.

**Measured against the 6.3.0 corpus on 2026-08-17, that rewrite must not happen.**

The naive count reproduces almost exactly — a word-boundary grep over the 14
`SKILL.md` files returns **103** (`Task` 34, `Write` 35, `Read` 13, `Edit` 1,
`Skill` 20). Reading the hits shows what they are:

| Token | What it actually is | Example |
|---|---|---|
| `Write` | the English verb | `test-driven-development/SKILL.md:10` — "Write the test first" |
| `Task` | a numbered plan item | `subagent-driven-development/SKILL.md` — `` `Task <N>: complete` `` |
| `Skill` | the English noun | `writing-skills/SKILL.md` — "## What is a Skill?", "## Skill Types" |
| `Read` | the English verb | throughout |

Applying D10's mapping would turn `## What is a Skill?` into
`## What is a read_file?`. The rewrite does not reduce ambiguity; it manufactures
corruption.

**Literal tool references in the entire 51-file corpus: three.**

```
using-superpowers/references/hermes-tools.md:56   TodoWrite
using-superpowers/references/pi-tools.md:16       TodoWrite
brainstorming/visual-companion.md:68              the Bash tool
```

Two are inside *other harnesses'* mapping files, where naming that harness's tool
is the point. One is a genuine Claude Code reference in an optional companion doc.

### Why the corpus changed

Superpowers de-Claude-Code-ified itself upstream. 6.3.0 skills speak in **actions**
— "dispatch a subagent", "read a file", "create a todo" — and ship a per-harness
mapping file that resolves those actions to that harness's tools:

```
using-superpowers/references/{codex,pi,gemini,antigravity,hermes}-tools.md
```

`using-superpowers/SKILL.md:52-59` is the dispatcher:

```
## Platform Adaptation

If your harness appears here, read its reference file for special instructions:

- Codex: `references/codex-tools.md`
- Pi: `references/pi-tools.md`
- Antigravity: `references/antigravity-tools.md`
- Hermes Agent: `references/hermes-tools.md`
```

`pi-tools.md:3` states the contract outright: *"Skills speak in actions … On Pi
these resolve to the tools below."*

**So D10's goal is reachable the way upstream now intends: Rudra adds one
reference file and one list entry, instead of forking 14 files.** That is
`rudra-tools.md` (§4), and it is the single highest-value artifact in this step.

### What survives from D10's analysis

`C5.2c` — cross-reference rewriting — is real, and is a different transform than
a tool rename. All **26** inter-skill references use the plugin-namespace form:

```
superpowers:test-driven-development         ×5
superpowers:finishing-a-development-branch  ×5
superpowers:subagent-driven-development     ×4
superpowers:using-git-worktrees             ×3
superpowers:systematic-debugging            ×2
superpowers:requesting-code-review          ×2
superpowers:executing-plans                 ×2
superpowers:brainstorming                   ×1
superpowers:verification-before-completion  ×1
superpowers:writing-skills                  ×1
```

Rudra has no plugin namespace and no `Skill` tool, so these must become paths the
agent can `read_file`. §4 specifies the rewrite.

---

## 2. Owner decisions

| # | Decision | Consequence |
|---|---|---|
| **S11a.1** | Step 11 splits into 11a / 11b / 11c | Each sub-step ships its own spec, plan and acceptance |
| **S11a.2** | Vendor from the local plugin cache, `superpowers 6.3.0` | `.claude-plugin/plugin.json` names `github.com/obra/superpowers` as repository and Jesse Vincent as author, so this is obra's content at release 6.3.0. No git SHA exists on disk (the cache is not a git checkout), so a **per-file sha256 manifest** substitutes as the immutable pin |
| **S11a.3** | **The vendored copy is frozen.** No version check, no update fetch, no network at runtime, ever. The owner updates it by hand and re-tests | `C5.6`'s `update` subcommand is **dropped** — it was specified as "re-runs the transform against a newer pinned upstream ref", which is precisely the auto-update being ruled out. `C5.4b`'s scripted-transform requirement survives with a changed rationale: not 3-way merges against upstream, but so a hand-dropped corpus needs one command rather than hand-edits |
| **S11a.4** | The transform runs at **cache-build time**, not vendor time | The repo holds exactly one tree, byte-identical to 6.3.0. A hand-update diffs clean upstream against clean upstream, with no Rudra edits tangled in. `<transform version>` was already in D13's cache key, which only makes sense for generated output |
| **S11a.5** | Vendored code is organised as **bundles**, not one vendor root | A second default-shipped upstream (the owner raised this explicitly) is a new directory plus one line in `registry.py`. No root-level file grows a second owner |
| **S11a.6** | **Skill-name uniqueness across bundles is the owner's contract** — the collision case will not occur | No resolution machinery, no `bundle:skill` config vocabulary. Encoded as an **invariant**: the transform raises on a duplicate active-skill name, naming both bundles. A declared invariant that fails loudly is ~5 lines; silent shadowing would be unfindable |
| **S11a.7** | The enabled set stays at D11's **8**, revisited in 11c | The rewrite cost that deferred 5 skills is now zero (§1) and `C3.5`/`C6.2` both shipped, so 4 of the 5 are technically unblocked. But selection quality across a longer index at 32B is exactly what `C5.8` exists to measure, and D11 stays locked until evidence overturns it |

---

## 3. Architecture

```
src/rudra/skills/
├── __init__.py
├── registry.py                     BUNDLES — the one list of what ships
├── transform.py                    render(bundles, enabled, dest) -> RenderReport
└── bundles/
    └── superpowers/
        ├── BUNDLE.toml             metadata + per-bundle transform declarations
        ├── LICENSE                 MIT, Jesse Vincent — byte-identical
        ├── MANIFEST.sha256         per-file hashes; the immutable pin
        └── src/                    byte-identical upstream subtree
            ├── skills/             14 SKILL.md + 37 supporting files (472K)
            └── hooks/session-start copied now, wired in 11c (C5.5)
```

`registry.py` mirrors `stacks/registry.py`, a pattern the repo already uses for
"the one list of the things we ship."

### What is vendored, and what is not

The `skills/` tree is copied **wholesale and byte-identical**, including parts
that do not apply to Rudra — upstream's own development artifacts
(`systematic-debugging/CREATION-LOG.md`, `test-pressure-{1,2,3}.md`) and a Node
browser companion (`brainstorming/scripts/server.cjs` and four siblings) that
Rudra has no runtime for.

Three reasons, in order of weight: S11a.3's freeze rule wants a clean
upstream-vs-upstream diff on a hand-update; dropping files bakes judgment calls
into the copy that the next upstream version invalidates; and D2's rule is "don't
strip anything that can help Rudra." The cost is 472K of disk and **zero
context** — only `name` and `description` ever enter a prompt
(`middleware/skills.py:3`, progressive disclosure). Where content genuinely does
not apply, `rudra-tools.md` says so (§4); the transform never deletes.

Not vendored: `.github/`, `tests/`, `scripts/`, `package.json`, the
`.{codex,cursor,devin,pi}-plugin/` directories, and the four non-Rudra
`AGENTS.md` / `GEMINI.md` / `CLAUDE.md` / `README.md`. That is marketplace
packaging for other harnesses, and `A4.3` is already an open row about repo
noise.

### `BUNDLE.toml`

Self-describing, so the transform is not superpowers-shaped:

```toml
name = "superpowers"
upstream = "https://github.com/obra/superpowers"
version = "6.3.0"
copied_on = "2026-08-17"
source = "~/.claude/plugins/cache/claude-plugins-official/superpowers/6.3.0"
license = "MIT"
license_file = "LICENSE"
copyright = "Copyright (c) 2025 Jesse Vincent"
frozen = true

skills_root = "src/skills"

# Per-bundle transform declarations. A bundle that needs neither declares neither.
cross_ref_prefix = "superpowers:"
bootstrap_skill = "using-superpowers"
platform_ref_section = "## Platform Adaptation"
```

`NOTICE` is generated from these fields across the registry, so adding a bundle
does not mean hand-maintaining an attribution file (§5).

---

## 4. The transform

One pure function. Imports nothing from Rudra beyond bundle metadata — the same
boundary rule `facts/store.py` and `loop/ledger.py` already hold.

```python
def render(
    bundles: Sequence[Bundle],
    enabled: frozenset[str],
    dest: Path,
) -> RenderReport: ...
```

Four operations, in order:

**1. Copy to `library/`.** Each bundle's skills → `library/<bundle>/<skill>/`,
every file, byte-for-byte except where (2) and (3) apply.

**2. Rewrite cross-references.** `superpowers:<name>` →
`/skills/library/superpowers/<name>/SKILL.md`. 26 sites. Driven by
`BUNDLE.toml`'s `cross_ref_prefix`; a bundle without that key gets no rewriting.

**3. Inject the platform reference.** Write `references/rudra-tools.md` into the
bundle's `bootstrap_skill`, and add a `- Rudra: \`references/rudra-tools.md\``
line to its `platform_ref_section` list. This is D10's replacement.

**4. Copy enabled skills flat** → `active/<skill>/`. Copies, not symlinks:
`FilesystemBackend` opens with `O_NOFOLLOW`, and the cache is disposable. Total
rendered size ≈ 700K.

### Why the two directories differ

| Cache dir | Namespaced by bundle? | Why |
|---|---|---|
| `library/<bundle>/<skill>/` | **yes** | Never indexed, only `read_file`d. No name constraint applies. Collision-free by construction, and cross-ref targets survive a second bundle |
| `active/<skill>/` | **no — flat** | `middleware/skills.py:347` requires the frontmatter `name` to equal the parent directory name. Namespacing here would mean rewriting vendored frontmatter |

So a collision can only bite among **enabled** skills, and per S11a.6 that is an
invariant, not a feature.

### Determinism and the cache key

`render()` must be deterministic: the same inputs produce byte-identical output.
11b's cache key is
`hash(rudra version + each bundle's MANIFEST.sha256 root + transform version +
sorted enabled list)`. A hand-update (S11a.3) changes the manifest, which changes
the key, which rebuilds every user's cache. No version negotiation anywhere.

### `rudra-tools.md` — the substantive artifact

This is not a lookup table. Several enabled skills instruct behavior Rudra
**structurally forbids**: `verification-before-completion` and `executing-plans`
both direct the agent to mark work complete, while Rudra's S9c.1 split means only
`loop/engine.py` writes `DONE`, and only on `VerifyReport.passed`. Left
unreconciled, the corpus argues with the architecture inside the model's context.

The file states, in the action vocabulary the corpus actually uses:

| Skills ask for | Rudra |
|---|---|
| read / write / edit a file | `read_file(file_path=…, limit=1000)` · `write_file` · `edit_file` |
| run a command | `execute` — gated; denied under `--auto` unless `--allow-shell` |
| dispatch a subagent | `task`, `subagent_type` ∈ `coder` / `tester` / `reviewer` / `general-purpose` |
| create or track todos | `add_tasks` / `drop_task` — **and no tool can mark work done.** Python decides that |
| verify work | `run_tests`; the gate is `rudra verify`, deterministic, not the agent's judgment |
| record a decision | `record_fact`, with its `why` |
| ask the user | `ask_user`, budgeted by `[agent] max_questions` |
| invoke a skill | no such tool — `read_file` the path shown in the skill index |
| browser / visual companion | unavailable: no browser, no Node runtime |

The `read_file(file_path=…, limit=1000)` shape is not Rudra's invention:
`middleware/skills.py:721` (`SKILLS_SYSTEM_PROMPT`) already prescribes exactly
that, including the `limit` argument and the reason for it. `rudra-tools.md`
agrees with the middleware rather than competing with it.

---

## 5. Attribution (C5.7), with one correction

`C5.7` reads: *"Must state that the skills were **modified** — MIT requires the
notice."* MIT requires no modification notice; that is Apache-2.0 §4(b). MIT
requires only that the copyright and permission notice survive in copies.

Two facts make the honest statement different from the row's:

1. Because the transform runs at cache-build time (S11a.4), what Rudra
   **distributes** is byte-identical upstream. The modification exists only in the
   user's `~/.cache`, produced by a program the user ran.
2. Rudra is Apache-2.0 and MIT is permissive and compatible, so the combination
   needs no relicensing — only preserved notices.

Ships:

- **`NOTICE`** at the repo root, closing the standing half of `A4.7`. Names Rudra
  (Apache-2.0), then each bundle: upstream URL, version, license, copyright
  holder, path to its license file, and a plain statement that Rudra renders a
  **modified** copy at runtime and exactly what it changes — cross-references
  rewritten, one reference file added.
- **`src/rudra/skills/bundles/superpowers/LICENSE`** — byte-identical MIT.
- `NOTICE` is **generated** from `BUNDLE.toml` across the registry, so a second
  bundle updates attribution by existing.

---

## 6. Packaging

`pyproject.toml:81-82` uses hatchling with `packages = ["src/rudra"]`, which
includes non-Python files under the package directory by default. The corpus
ships `.md`, `.sh`, `.js`, `.cjs`, `.ts`, `.dot`, `.html` and **no `.py`**, so
`ruff check src/` has nothing to lint and pytest nothing to collect.

Two guards, both cheap:

- An explicit ruff exclude for `src/rudra/skills/bundles/`, so a future bundle
  shipping Python does not silently enter Rudra's lint gate.
- A test that the built wheel contains a vendored `SKILL.md` (§7). Packaging
  silently dropping 472K of non-Python data is a failure that would otherwise
  surface only for a pipx user, long after merge.

---

## 7. Testing

The freeze rule becomes mechanical rather than a promise.

| Test | Catches |
|---|---|
| `MANIFEST.sha256` matches the vendored tree, file for file | Any edit to the frozen copy, by anyone. **This is S11a.3, enforced** |
| Every rendered `active/<skill>` parses under deepagents' own `_parse_skill_metadata` | A skill silently dropped from the index — `middleware/skills.py` warns and skips on bad frontmatter or an over-length description, invisible at runtime |
| `name` == parent directory for all 14 rendered skills | The `middleware/skills.py:347` constraint, broken by a future transform change |
| No `superpowers:` token survives in rendered output; every rewritten target exists on disk | Dangling cross-references — what §0.3's reference-graph analysis existed to avoid |
| `render()` run twice → byte-identical output | Non-determinism, which would thrash 11b's cache key |
| Duplicate active-skill name across bundles → raises, naming both | S11a.6's invariant |
| `rudra-tools.md` is present in the rendered bootstrap skill, and the Platform Adaptation list contains a Rudra line | The one operation that carries D10's replacement |
| Built wheel contains `bundles/superpowers/src/skills/brainstorming/SKILL.md` | §6's packaging risk |

Gates unchanged and absolute: `ruff check src/ tests/` prints `All checks
passed!`, `ruff format --check` clean, and `uv run pytest -q` never goes down
from its current count.

---

## 8. Acceptance

11a has no live-model acceptance, by design — nothing an agent can see changes.
Its acceptance is the test table in §7 plus one manual check:

Render the corpus to a temporary directory with the D11 enabled set, then read
`library/superpowers/using-superpowers/SKILL.md` and confirm by eye that the
Platform Adaptation section names Rudra, that `references/rudra-tools.md` exists
beside it, and that a cross-reference such as
`superpowers:test-driven-development` now reads as a `/skills/library/...` path.
The corpus is prose that a model must follow; a passing regex is not the same as
a human confirming it reads correctly.

---

## 9. Out of scope

**Deferred to 11b:** the cache directory and XDG paths, key computation,
staleness and rebuild logic (`render()` takes a destination and writes there),
the `/skills/` composite route, `skills=` passed to any agent, `[skills]`
un-reserved in `config/schema.py:44`, and the bootstrap's prompt injection
(`C5.3`).

**Deferred to 11c:** `rudra skills` CLI, the session-start hook (`C5.5`), and the
live 32B selection-quality run (`C5.8`).

**Two 11b design questions, flagged here so they are not lost:**

1. **Do subagents get their own `skills=` sources, or only the planner?**
   `subagents/build.py:153` makes per-subagent scoping a one-line change, and the
   enabled 8 split cleanly by role — `test-driven-development` and
   `systematic-debugging` belong to the coder, `brainstorming` and `writing-plans`
   to the planner, `receiving-code-review` to whoever consumes the reviewer's
   output. Scoping costs prompt tokens per agent either way; the question is which
   agent pays for which skill.
2. **Are `~/.rudra/skills/` and `<project>/.rudra/skills/` (from `C5.1`)
   user-authored *bundles*, or plain extra sources?** Bundles would give
   user skills the same metadata and attribution machinery; plain sources would be
   simpler and match deepagents' own layering semantics
   (`middleware/skills.py:955`, later sources win).

---

## 10. Ledger impact

| Row | Disposition |
|---|---|
| `C5.2` | Closed here — vendored as a bundle, provenance in `BUNDLE.toml` + `MANIFEST.sha256` rather than a git SHA (S11a.2) |
| `C5.2c` | Closed here — cross-reference rewriting, reshaped from tool-rename to namespace-to-path (§1, §4) |
| `C5.7` | Closed here, with §5's correction to its MIT reasoning |
| `A4.7` | NOTICE half closed here. CONTRIBUTING remains open |
| `C5.4` | **OBSOLETE** — §1. The sites it counts are prose |
| `C5.4a` | **OBSOLETE** — the `Skill` tool has 20 counted sites, all the English noun. Its genuine insight (deepagents exposes no skill-invoke tool; use `read_file`) is preserved in `rudra-tools.md` |
| `C5.4b` | **OBSOLETE as written** (3-way merge against upstream). Its scripted-transform requirement survives in §4 under S11a.3's rationale |
| `C5.4c` | **OBSOLETE** — the 58 deferred sites in `writing-skills` and `subagent-driven-development` do not exist |
| `C5.6` | `update` subcommand **dropped** per S11a.3; `list` / `validate` / `rebuild` survive to 11c |
| TODO.md §0.3 | The "D10 rewrite scope — measured" subsection is superseded by §1 and must be marked as such, not deleted — it records why the measurement was believed |
