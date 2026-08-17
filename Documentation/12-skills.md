# 12. Skills and Vendored Code

Rudra ships a methodology library — [superpowers](https://github.com/obra/superpowers) by Jesse Vincent — vendored into the package so it works offline and never changes under you.

> ### Status: live, for some agents
>
> The three planning stages, the coder and the tester carry the skill index and can read any of the fourteen skills by path. The reviewer and the general-purpose agent do not — neither is choosing a method, and both would pay tokens for descriptions they cannot act on.
>
> Select a different set with `[skills] enabled` in `config.toml`, or turn skills off entirely with `enabled = []`. You can also write your own — see [Adding your own](#adding-your-own).

---

## What a skill is

A skill is a written procedure the model reads when it recognises a situation — not code, not a tool. `test-driven-development` explains how to write a failing test first; `systematic-debugging` explains how to find a root cause instead of guessing at fixes.

They work by **progressive disclosure**. Only each skill's name and one-line description sit in the model's prompt. When one matches the task at hand, the model reads the full document with `read_file`. Fourteen skills cost a handful of lines of context until one is actually needed.

That is why shipping a large library is cheap: the cost is disk, not context.

---

## What ships

**superpowers 6.3.0** — 14 skills, 37 supporting files, 472K, MIT licensed, Copyright (c) 2025 Jesse Vincent.

Nine are enabled by default. The other five are on disk and readable but stay out of the prompt index until there is evidence they help rather than crowd it.

| Skill | Use it when | Default |
|---|---|:-:|
| `using-superpowers` | starting any conversation — this is the bootstrap that makes the model reach for the rest | ✅ |
| `brainstorming` | before any creative work: features, components, new behaviour | ✅ |
| `writing-plans` | you have requirements for a multi-step task, before touching code | ✅ |
| `executing-plans` | you have a written plan to work through with review checkpoints | ✅ |
| `test-driven-development` | implementing any feature or bugfix, before writing implementation code | ✅ |
| `systematic-debugging` | any bug, test failure or surprise, **before** proposing a fix | ✅ |
| `verification-before-completion` | about to claim something works, before committing | ✅ |
| `requesting-code-review` | finishing a task or feature, before merging | ✅ |
| `receiving-code-review` | acting on review feedback, especially when it seems questionable | ✅ |
| `using-git-worktrees` | feature work needing isolation from your working tree | — |
| `finishing-a-development-branch` | implementation done, deciding how to integrate | — |
| `subagent-driven-development` | executing a plan with independent tasks in one session | — |
| `dispatching-parallel-agents` | 2+ independent tasks with no shared state | — |
| `writing-skills` | authoring or editing skills themselves | — |

The nine map onto what Rudra already does: plan before coding, test, verify, review, fix. That is not a coincidence — they were chosen against those requirements.

---

## Frozen on purpose

**Rudra never updates the vendored corpus.** No version check, no update fetch, no network call at run time, ever. What was vendored is what runs, permanently, unless the maintainer replaces it by hand and re-runs the suite.

This is a deliberate design decision, not a missing feature. A methodology library that silently changed under you would change how your agent behaves between two runs of the same command, with nothing in your project to explain why.

There is consequently **no `rudra skills update`**. There is nothing to update to.

The freeze is enforced, not promised: `MANIFEST.sha256` records a hash for every one of the 52 vendored files, and a test verifies it on every run. Editing a vendored file — by anyone, for any reason — fails the suite.

---

## How it is laid out

Vendored code lives in **bundles**. Each bundle is one upstream project, self-describing, so adding a second one is a directory plus one line in a registry rather than a redesign.

```
src/rudra/skills/
├── registry.py                    the one list of what ships
├── transform.py                   renders bundles into what an agent reads
├── rudra_tools.py                 Rudra's own tool mapping (below)
└── bundles/superpowers/
    ├── BUNDLE.toml                upstream URL, version, license, copyright
    ├── LICENSE                    MIT, byte-identical
    ├── MANIFEST.sha256            52 hashes — the pin
    └── src/skills/                the 14 skills, byte-identical to upstream
```

The vendored tree is **byte-identical to upstream**. Rudra's adaptations are applied when the corpus is rendered into a cache at run time, never to the files in the repository. So the copy you can read here is exactly what was published, and a future update diffs cleanly against upstream with no Rudra edits tangled in.

Rendering produces two trees:

- **`library/superpowers/<skill>/`** — all 14, readable by path. Cross-references point here, so a reference stays valid whether or not that skill is enabled.
- **`active/<skill>/`** — the enabled 9, flat. This is the set whose names and descriptions enter the prompt.

Enabling a skill later moves a copy between them and rewrites no references.

---

## What Rudra changes, and why

Two adaptations, both applied at render time.

**1. Cross-references become paths.** Upstream skills refer to each other in plugin-namespace form — `superpowers:test-driven-development`. Rudra has no plugin namespace and no skill-invoke tool, so all 26 references are rewritten to `/skills/library/superpowers/test-driven-development/SKILL.md`, which the model can simply `read_file`.

**2. A Rudra tool mapping is added.** Superpowers 6.3.0 deliberately speaks in *actions* — "dispatch a subagent", "read a file", "create a todo" — and resolves them per harness through a reference file listed under its "Platform Adaptation" section. It already ships mappings for Codex, Pi, Gemini, Antigravity and Hermes. Rudra adds its own, `references/rudra-tools.md`, and one line to that list.

That is the whole adaptation. **No skill text is rewritten for Rudra's tool names**, because there is nothing to rewrite — the corpus does not name Claude Code tools. An earlier Rudra design called for renaming 101 sites; measuring them showed they were English prose (`Write` the verb, `Task` a numbered plan item, `Skill` a noun in headings), and the rename would have turned "What is a Skill?" into "What is a read_file?".

### The part that matters most

`rudra-tools.md` is not just a lookup table, because several skills instruct behaviour Rudra **structurally forbids**. `verification-before-completion` and `executing-plans` both tell the agent to mark work complete — and in Rudra no tool can. The mapping says so plainly:

> `add_tasks` and `drop_task` add and remove work. **Neither can mark a task complete, and no other tool can either.** Rudra's Python loop decides that, and only when a deterministic gate passes. A skill instructing you to "mark the task complete" is describing a harness you are not running on. Do the work; the gate decides.

It also tells the model that the reviewer has no write tools, that a denied command is an answer rather than an obstacle to route around, and that the browser-based companion some skills describe cannot start here. Without that, the library argues with the architecture inside the model's context.

See [Tools](11-tools.md) for the full tool list this maps onto.

---

## Licensing

Rudra is Apache-2.0. superpowers is MIT. Both are permissive and combine without relicensing; the vendored `LICENSE` is preserved byte-identical and attribution lives in [`NOTICE`](../NOTICE) at the repository root.

`NOTICE` is **generated** from each bundle's metadata rather than hand-maintained, so it cannot drift as bundles are added. It states plainly that what Rudra distributes is unmodified upstream, and that the modified copy exists only in the user's cache — along with exactly what the two modifications are.

MIT requires that the copyright and permission notice survive in copies. It does not require a statement of modification — that is Apache-2.0 §4(b) — but stating it is honest and costs nothing.

---

## Choosing which skills are active

```toml
[skills]
enabled = ["brainstorming", "test-driven-development", "systematic-debugging"]
```

Leave the section out to get the shipped nine. Name a subset to narrow it — including any of the five that are off by default, since all fourteen are vendored and enabling one is a config change, not a rebuild. Set `enabled = []` and no agent receives skills at all.

A name that is not a vendored skill is an **error**, not a warning. The skill loader silently skips a skill it cannot find, so accepting a typo would leave you believing a skill is active when it never loads and nothing ever says so.

Changing `enabled` changes the cache key, so you land in a different rendered directory rather than a stale one.

## Adding your own

Put a skill in your project and Rudra picks it up:

```
<project>/.rudra/skills/deploy-checklist/SKILL.md
```

```markdown
---
name: deploy-checklist
description: Use when shipping this project to production
---

# Deploy checklist

1. Run the full suite.
2. Check the migration ran.
```

Two rules the loader enforces: **`name` must equal the directory name**, and
`description` must be non-empty — it is the only thing the model sees when
deciding whether to reach for the skill.

`~/.rudra/skills/` works the same way and applies to every project.

### Check it before trusting it

```bash
rudra skills validate
```

This is worth running. A malformed `SKILL.md` is **skipped in silence** — no
error, the skill simply never appears and the model never uses it. `validate`
is the only place that failure becomes visible. It exits non-zero and names
each problem.

One case it catches that looks fine: writing `description:` with nothing after
it. YAML reads that as null, and the description becomes the literal string
`"None"`. The skill loads, indexes, and tells the model nothing.

```bash
rudra skills list       # every skill, its source, whether it is in the prompt
rudra skills rebuild    # re-render the bundled cache, drop stale copies
```

### Which one wins

Same name in two places? Later source wins:

```
bundled  →  ~/.rudra/skills/  →  <project>/.rudra/skills/
```

So a project skill overrides a bundled one of the same name. `rudra skills
list` marks the loser as `shadowed by project`, so this is visible rather than
mysterious.

---

## Related

- **[Tools](11-tools.md)** — every tool the model can call, which is what skills resolve down to
- **[How It Works](05-how-it-works.md)** — the loop skills would inform
- **[Project Status](08-project-status.md)** — what is wired and what is not
- **[Development](07-development.md)** — the package layout, including `src/rudra/skills/`
