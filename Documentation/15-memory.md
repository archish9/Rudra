# 15. Long-term memory — MemPalace

**In one sentence:** Rudra remembers what it learned about *your* project — decisions,
finished work, what blocked it, your preferences — and hands the relevant bits back to
itself on the next run, without you re-explaining anything.

The store is **[MemPalace](https://github.com/MemPalace/mempalace)** (MIT), a local
searchable memory library. It ships with Rudra, runs entirely on your machine, needs no
API key, and cannot be turned off.

**Contents**

1. [Why this exists](#1-why-this-exists)
2. [Five-minute tour](#2-five-minute-tour)
3. [The mental model: palace, wing, room, drawer](#3-the-mental-model-palace-wing-room-drawer)
4. [What gets remembered, and by whom](#4-what-gets-remembered-and-by-whom)
5. [What is never remembered](#5-what-is-never-remembered)
6. [How a memory comes back: recall](#6-how-a-memory-comes-back-recall)
7. [The two tools the model can call](#7-the-two-tools-the-model-can-call)
8. [Memory vs `facts.json` vs `AGENTS.md`](#8-memory-vs-factsjson-vs-agentsmd)
9. [The `rudra memory` commands](#9-the-rudra-memory-commands)
10. [Keeping it: export, import, committing](#10-keeping-it-export-import-committing)
11. [Settings](#11-settings)
12. [Privacy, offline use, and the one download](#12-privacy-offline-use-and-the-one-download)
13. [Under the hood](#13-under-the-hood)
14. [When memory breaks](#14-when-memory-breaks)
15. [Limits and gotchas](#15-limits-and-gotchas)
16. [Troubleshooting](#16-troubleshooting)

---

## 1. Why this exists

An agent with no memory starts every run as a stranger to your project. You explain the
same constraint on Monday and again on Thursday; it re-derives a decision you already
made; it walks into the same wall that blocked it last week.

Rudra fixes that with three files and one store, and this page is about the store.

**Without memory:**

```
Run 1  →  you: "use uv, not pip"           →  code uses uv
Run 2  →  (new process, nothing recalled)  →  code uses pip
```

**With memory:**

```
Run 1  →  you: "use uv, not pip"  →  recorded in `preferences`
Run 2  →  planner prompt already contains:
             ## WHAT THIS PROJECT HAS LEARNED
             - [preferences] The user runs everything through uv, never pip  (agent)
          →  code uses uv
```

Nothing about that is magic and nothing about it is a chat log. It is a small set of
short statements, each tagged with who recorded it, searched by meaning and pasted into
the prompt.

---

## 2. Five-minute tour

**Step 1 — do some work.** Memory is written as work finishes, so it needs one run:

```bash
cd ~/my-project
rudra init
rudra "add a JSON config loader with tests"
```

`rudra init` also fetches the embedding model the first time — ~167 MB, once per
machine, shared by every project. It tells you before it starts. See
[§12](#12-privacy-offline-use-and-the-one-download).

**Step 2 — look at what it kept:**

```bash
rudra memory list
```

```
                       Memory — my-project
┏━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Room      ┃ By    ┃ Recorded   ┃ Memory                                       ┃
┡━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ tasks     │ rudra │ 2026-08-20 │ Completed: add a JSON config loader. Files:  │
│           │       │            │ config.py, test_config.py.                   │
│ decisions │ rudra │ 2026-08-20 │ language = Python (inferred: the request     │
│           │       │            │ names a .py file)                            │
│ prefer…   │ agent │ 2026-08-20 │ The user runs everything through uv, never   │
│           │       │            │ pip.                                         │
└───────────┴───────┴────────────┴──────────────────────────────────────────────┘
```

**Step 3 — ask it something.** Search is by *meaning*, not by keyword, so this finds the
`uv` line without the word `uv` appearing in your query:

```bash
rudra memory search "how do we install dependencies"
```

**Step 4 — throw away what you don't want.** Irreversible, and it says so:

```bash
rudra memory forget --added-by agent
```

That's the whole loop: it writes as it works, you can read it, search it, and prune it.

---

## 3. The mental model: palace, wing, room, drawer

MemPalace borrows the [memory-palace](https://en.wikipedia.org/wiki/Method_of_loci)
metaphor, and Rudra keeps the words because the CLI prints them.

| Term | In Rudra, it means |
|---|---|
| **Palace** | The store on disk: `<project>/.rudra/memory/palace/` |
| **Wing** | Your project. Named after the project directory (`my-project`), so two projects never mix |
| **Room** | The category. Exactly four, fixed — see below |
| **Drawer** | One memory: a sentence or two, plus who recorded it and when |

**The four rooms, and nothing else:**

| Room | Holds | Example |
|---|---|---|
| `decisions` | A choice and the reasoning behind it | `language = Python (inferred: the request names a .py file)` |
| `tasks` | Work that finished and passed the gate, with the files git says changed | `Completed: add a JSON config loader. Files: config.py, test_config.py.` |
| `blockers` | What stopped a task, and why | `Blocked: make the parser handle quoted commas. Reason: two identical failures.` |
| `preferences` | How you want work done | `The user runs everything through uv, never pip.` |

The set is closed on purpose. Rooms are a **search filter** (`--room decisions`), and a
taxonomy a model invents fresh each run filters nothing.

---

## 4. What gets remembered, and by whom

Every memory carries an **`added_by`** tag, and it is the most useful column in the
table because it tells you how much to trust the line.

| Tag | Meaning |
|---|---|
| **`rudra`** | Written by Python from something it can prove — a task that passed the verification gate, a plan you approved. No model judged it worth keeping; the file list comes from git, not from anything a model said |
| **`agent`** | A model called the `remember` tool because it judged something worth keeping. Often useful, sometimes noise, and always a model's opinion |

### The four automatic write points

These need no model decision and no tool call. They happen in the run loop, right beside
the code that updates `AGENTS.md`:

| When | Room | Written as |
|---|---|---|
| You **approve a plan** (or `--auto` approves it for you) | `decisions` | One entry per fact: `key = value (source: why)` |
| A task **passes the verification gate** | `tasks` | `Completed: <task>. Files: <from git>.` |
| A task is **blocked** — two identical failures | `blockers` | `Blocked: <task>. Reason: <blocker>.` |
| A task **runs out of fix attempts** | `blockers` | same shape |

Two consequences worth knowing:

- **A plan you cancel is never recorded.** The write happens after approval, because a
  plan you rejected is not a decision this project made. `rudra --plan` therefore writes
  no memory at all.
- **`rudra memory list` empty after a run means no task reached `done`.** Memory records
  finished work, not attempts.

### The one model-driven write point

The coder, tester and general-purpose subagents hold a `remember` tool
([§7](#7-the-two-tools-the-model-can-call)). Everything it writes is tagged `agent`,
which is exactly what makes `rudra memory forget --added-by agent` a safe prune: it
removes the opinions and keeps the proven record.

---

## 5. What is never remembered

**Conversation transcripts.** Rudra never mines the dialogue between you and the model,
and never stores the model's reasoning. MemPalace ships a transcript miner; Rudra does
not call it.

**Your code.** No file contents, no diffs. A `tasks` entry names files; it does not
contain them.

**Anything from a run you cancelled.** No approval, no memory.

What you see in `rudra memory list` is the complete contents. There is no second,
hidden store.

---

## 6. How a memory comes back: recall

Two paths, and the first one is the one that matters.

### The injected block — recall you don't have to ask for

Every time Rudra builds an agent, it searches this project's palace using **the task
text as the query**, takes the best matches, and pastes them into the system prompt:

```markdown
## WHAT THIS PROJECT HAS LEARNED

Recorded during earlier runs on this project, most relevant first. Each line
reads: [room] content (recorded by). `rudra` means Rudra recorded it
deterministically from a finished task; `agent` means a model judged it worth
keeping, so weigh it accordingly.

- [preferences] The user runs everything through uv, never pip.  (agent)
- [decisions] language = Python (inferred: the request names a .py file)  (rudra)
```

This is deliberate rather than lazy. A model only calls a search tool when it thinks to,
and measurement during earlier steps put that at roughly one run in five — recall cannot
depend on the model choosing to look.

**Who gets the block:**

| Agent | Block? | Why |
|---|---|---|
| planner — `clarify`, `architect`, `breakdown` | ✅ one per stage | Planning is where prior decisions pay off most |
| `coder` | ✅ | It benefits most from what earlier runs settled |
| `tester` | ✅ | Same |
| `general-purpose` | ✅ | What earlier runs established is part of an answer about the codebase |
| `reviewer` | ❌ | It reads a diff and reports on it; project history does not change what the diff says, and the block would be paid for on every review |

**How big it is.** The block is capped at **a fiftieth of the role's context window**
(minimum 300 tokens), truncated by whole entries so you never get half a memory. At a
32k window that is ~640 tokens.

Set against the tool-result eviction threshold, which is a *tenth* of the window, that
looks small — and it is, on purpose. An evicted tool result is transient. This block is
paid on **every model call for the whole run**, and nothing sheds it.

> **The fraction is not a config key.** It is a constant in
> `src/rudra/context/budget.py`, because an unmeasured knob is worse than a constant
> somebody can change with evidence. `usage.json` records `recall_chars` per role so
> that evidence can be collected.

**One number drives three things.** `[model.<role>] context_tokens` sets the
summarisation trigger, the tool-result eviction threshold, *and* this recall budget.
**If a role declares no `context_tokens`, it gets no recall block at all** — guessing a
budget for a window of unknown size is how a recall block crowds out the actual task.
Set it. See [Context and Memory](13-context-and-memory.md).

### The tool — recall mid-task

The block is built once, when the agent starts. When a model needs history the block
doesn't carry — *"why did we pick this parser?"* — it calls `search_memory` itself.

---

## 7. The two tools the model can call

| Tool | Who has it | Gated? |
|---|---|---|
| `remember(content, room)` | coder · tester · general-purpose | **never** — control plane. It writes under `.rudra/` only, never into your project |
| `search_memory(query, room=None)` | coder · tester · general-purpose | **never** — it only reads `.rudra/memory/`, and reads are never gated |

The planner stages have neither: they get the injected block, and their job is to settle
and break down work, not to file notes.

**`remember`** takes one specific thing in a sentence or two, and its instructions ask
for the *why*, not only the *what*. Completed and blocked tasks are recorded
automatically, and the tool's description says not to repeat them — though a model will
sometimes do it anyway, which is what `forget --added-by agent` is for.

**`search_memory`** returns up to five hits, each labelled with its room and its
`added_by`. An unknown room name comes back as a `REJECTED:` sentence naming the four
valid rooms, so the model can retry rather than fail.

Neither tool can fail a task. If the palace is unreachable, `remember` returns *"Memory
is unavailable for this run, so nothing was recorded. This is not your error — carry on
with the task."*

Full tool reference: **[Tools](11-tools.md)**.

---

## 8. Memory vs `facts.json` vs `AGENTS.md`

Rudra remembers across runs in three places, and they are not redundant.

| | `facts.json` | `AGENTS.md` | The palace |
|---|---|---|---|
| **Holds** | Settled facts about this project: `{key: {value, why, source}}` | A running notebook: session log (last 20 entries) + prose architecture notes | Every decision, task, blocker and preference, unbounded |
| **Shape** | Small key/value JSON | Markdown you can read and edit | Binary vector store |
| **Reaches the model by** | Pasted into every agent's prompt, in full | Pasted into the planner's prompt, in full | Semantic search, budgeted, most relevant first |
| **Grows?** | Only as facts are established | Capped at 20 log entries | Unbounded — that is its job |
| **Commit it?** | Yes | Yes | No — commit the **export** instead ([§10](#10-keeping-it-export-import-committing)) |
| **Edit by hand?** | Yes, one line | Yes | No — use `rudra memory forget` / `import` |

The split that makes this work: **`AGENTS.md` is the recent window, the palace is the
long history.** They are written from the same place in the code, one line apart, so
they cannot drift. An entry ageing off the 20-entry cap is already in the palace — which
turns that cap from data loss into what it was meant to be, a prompt budget.

Neither is generated from the other, deliberately: a durable, committable text file must
not depend on a binary store that rewrites itself on every write.

---

## 9. The `rudra memory` commands

All five run without a model. None of them need network.

### `rudra memory list`

```bash
rudra memory list                       # everything, newest first
rudra memory list --room decisions      # decisions | tasks | blockers | preferences
rudra memory list --added-by agent      # rudra | agent
rudra memory list --limit 200           # default 50
rudra memory list -d ~/other-project    # another project's palace
```

Columns: **Room · By · Recorded · Memory**. Newest first.

### `rudra memory search`

```bash
rudra memory search "package manager"
rudra memory search "what blocked us" --room blockers
rudra memory search "test framework" --limit 10      # default 5
```

Semantic, not textual: it matches meaning, so a query and a memory that share no words
can still match. Adds a **Score** column — higher is closer.

### `rudra memory forget`

```bash
rudra memory forget --added-by agent    # drop the model's opinions, keep the record
rudra memory forget --room blockers     # a stale wall you've since knocked down
rudra memory forget --all               # everything for this project
rudra memory forget --all --yes         # skip the confirmation (scripts, CI)
```

Three safety rules, all deliberate:

1. **A bare `rudra memory forget` is an error**, not a synonym for `--all`. You name a
   target: `--room`, `--added-by`, or `--all`.
2. **It prints what it will delete first** — up to 20 rows, then a count of the rest.
3. **It refuses on a non-terminal unless you pass `--yes`.** A piped stdin cannot answer
   a confirmation prompt, and this deletion cannot be undone, so it exits `2` rather
   than guessing.

There is no undo. There is an export — see next.

### `rudra memory export` / `rudra memory import`

```bash
rudra memory export                     # → .rudra/memory/export/
rudra memory export --out ~/backups/proj-memory
rudra memory import .rudra/memory/export
```

Covered in [§10](#10-keeping-it-export-import-committing).

### Where memory shows up elsewhere

**`rudra doctor`** reports four memory rows:

| Row | Tells you |
|---|---|
| `memory` | How many memories this project holds, the palace path, and the backend — or `fail — unreadable at …` when the palace cannot be opened |
| `memory model` | Whether the embedding model is on disk, and where — or that ~167 MB will download on first use |
| `mempalace config` | *Only if you have one:* that your personal `~/.mempalace/config.json` is deliberately ignored ([§13](#13-under-the-hood)) |
| `memory export` | How many room files the durable export holds, or where it would go |

**`rudra init`** warms the embedding model, printing the size before it starts.

---

## 10. Keeping it: export, import, committing

The palace is a binary store that rewrites itself on every write. That makes it a bad
thing to commit and a bad thing to be the only copy of anything. So Rudra writes a
second, durable copy in plain markdown:

```bash
rudra memory export
```

```
.rudra/memory/export/
  decisions.md
  tasks.md
  blockers.md
  preferences.md
```

One file per room. Inside, each entry is a plain paragraph preceded by an HTML comment
carrying its metadata — invisible in a rendered diff, trivial to parse back:

```markdown
# preferences

<!-- rudra-memory added_by=agent filed_at=2026-08-20T11:04:12.881204 -->
The user runs everything through uv, never pip.

<!-- rudra-memory added_by=rudra filed_at=2026-08-20T11:02:55.130991 -->
build_tool = uv (asked: the user said so in the request)
```

**Restoring is exact**, not approximate: same rooms, same authors. And it is
**idempotent** — memory ids are derived from the content, so importing the same tree
twice leaves one copy of each entry, not two.

```bash
rudra memory import .rudra/memory/export
```

**A hand-edited export still restores.** These are text files, and people edit text
files. An entry that no longer validates (empty, or over the 8000-character cap) is
skipped and the rest of the file is imported.

### What to commit

Rudra writes `.rudra/.gitignore`, scoped only to its own directory:

```
run/
memory/palace/
```

So out of the box: **the export is committed, the palace is not.** `config.toml`,
`AGENTS.md` and `facts.json` are likewise kept. Rudra never touches your project's own
`.gitignore` — whether you commit `.rudra/` at all is your call.

Committing the export means a teammate cloning the repo gets your project's history:
`rudra memory import .rudra/memory/export` and their next run starts where yours left
off.

---

## 11. Settings

`[memory]` has exactly one key:

```toml
[memory]
backend = "chroma"      # chroma | sqlite | milvus | qdrant | pgvector
```

`chroma` is the default and the one Rudra ships tested; the others are MemPalace
backends passed straight through. Anything outside the five is a config error naming the
valid values.

**Two keys that are deliberately absent:**

- **No `enabled`.** Long-term memory is not optional. A key that cannot be turned off is
  worse than no key at all.
- **No `recall_fraction`.** The recall budget is a constant
  ([§6](#6-how-a-memory-comes-back-recall)) until a measurement says what it should be.

**The palace location is not configurable either.** It is always
`<project>/.rudra/memory/palace/`, because that fixed layout is what lets
`.rudra/.gitignore` separate the durable files from the volatile ones.

**The setting that actually affects memory most is not in `[memory]`.** It is
`[model.<role>] context_tokens`, which decides whether the recall block is injected at
all, and how big it may be.

---

## 12. Privacy, offline use, and the one download

**Nothing is transmitted anywhere.** Embedding runs locally, on your CPU, with no API
key and no account. Memory never reaches your model provider except as text inside the
prompt of the run you asked for.

**Each project is an island.** `<project>/.rudra/memory/palace/` — a memory recorded in
one project is invisible to another, by design: a preference established in a Rust
project may not hold in a Python one.

**One network access, once per machine:** the embedding model, ~167 MB, cached at
`~/.cache/chroma/onnx_models/all-MiniLM-L6-v2` and shared by every project you ever use
Rudra in. `rudra init` fetches it up front — after telling you the size — so no run
discovers a 167 MB download mid-task.

**Air-gapped installs:** `rudra doctor` prints the cache path so you can pre-seed it
from a machine that has network. Warmth is decided by the model file itself, not by the
directory, so a half-finished download is correctly reported as cold rather than
discovered mid-run.

**To purge everything:**

```bash
rudra memory forget --all       # empties the store
rm -rf .rudra/memory            # palace and export, gone
```

**Disk cost:** a few MB per project, plus the one shared model.

---

## 13. Under the hood

Skip this section unless something surprised you.

**The library.** `mempalace >= 3.7.1`, a required dependency, never an extra. Exactly
one file in Rudra imports it — `src/rudra/memory/store.py` — and it imports it *inside
functions*, not at module scope, because MemPalace pulls ChromaDB, which pulls
onnxruntime, grpcio and opentelemetry. At module scope that import cost would land on
`rudra --version`. Two tests enforce both the single import site and its laziness.

**Embedding and search.** Text is embedded with ChromaDB's bundled ONNX
`all-MiniLM-L6-v2`; search is vector similarity over this project's wing. Entries longer
than 800 characters are split into chunks before embedding, because one vector for a
long document matches nothing well.

**Ids are content-addressed.** A memory's id is a hash of (wing, room, content), which
is what makes writes idempotent and imports safe to repeat.

**Every stored memory carries:** `wing`, `room`, `added_by`, `filed_at`, `source_file`,
`chunk_index`, and the id recipe used.

**Isolation is by explicit argument, not environment.** Every call into MemPalace passes
`palace_path`, `collection_name` (`rudra_memory`) and `backend` directly. This is
required, not stylistic:

- `collection_name` is read from `~/.mempalace/config.json` and has **no environment
  override at all**;
- `backend` is read from that file **before** the environment, so `MEMPALACE_BACKEND`
  loses to a user's global setting.

If you use MemPalace yourself, your personal config would otherwise silently redirect
every Rudra read and write to a different collection on a different backend — a bug that
reproduces on exactly one machine. `rudra doctor` says plainly that your config exists
and is ignored.

**One path could not be scoped away, and it is accepted:** `~/.mempalace/locks/`. A
cross-process write lock has to live somewhere stable, the lock file is named from a
hash of the resolved palace path (so two projects cannot contend), and it holds a
process id rather than any memory content. A test asserts that this is the *only* thing
that appears in your home MemPalace directory — so if a future MemPalace starts writing
anything else there, it fails and names the file.

**Rudra writes both halves of the export/import round trip** rather than calling
MemPalace's own exporter, which resolves the collection and backend from that same
global config file and would happily export a collection Rudra never wrote to, reporting
success while doing it.

---

## 14. When memory breaks

Two rules govern every failure, and they hold together:

1. **A memory failure never fails a task.** Every store operation degrades to a safe
   default — a write returns `False`, a search returns `[]`, a count returns `0`. Work
   that genuinely finished is not undone by bookkeeping.
2. **`Ctrl-C` is never swallowed.** `KeyboardInterrupt` and `SystemExit` pass straight
   through.

The first failure is kept rather than the last, because once a palace fails to open,
every later call fails too and the fifth message explains nothing the first did not.

**What you will actually see.** A project whose directory name has no usable characters
for a wing prints `Long-term memory is off for this run: …` before anything starts, and
the run continues.

Any other failure — a corrupt `chroma.sqlite3`, an unwritable directory, a palace path
occupied by a file — is reported at three points, and never by failing your work:

| Where | What it says |
|---|---|
| **The run summary** | `Long-term memory failed this run — nothing was recorded.` with the underlying error beneath it. Printed beside the token usage, so an empty `rudra memory list` afterwards is not a surprise |
| **`rudra doctor`** | The `memory` row reads `fail — unreadable at <path> — <error>` instead of a count |
| **`rudra memory` commands** | `Memory is unavailable: <error>`, and the command exits `1`. They no longer answer "nothing recorded" about a store they could not read — `export` least of all, since a silent empty export is the one failure that costs you the durable copy |

Only the *first* failure is reported. Once a palace fails to open, every later call fails
too, and the fifth message explains nothing the first did not.

---

## 15. Limits and gotchas

| Limit | Detail |
|---|---|
| **One entry ≤ 8000 characters** | Long enough for an architecture summary, short enough that a transcript cannot be smuggled in |
| **Rooms are fixed at four** | They are a search filter; you cannot add a fifth |
| **No expiry, no automatic pruning** | The palace grows forever. Pruning is `rudra memory forget` and it is manual — by design, since silently forgetting is worse than growing |
| **Models duplicate the automatic record** | The `remember` tool asks a model not to re-record completed tasks; a 31B model measurably does it anyway. `forget --added-by agent` is the cure |
| **No cross-project memory** | Deliberate. Export from one project and import into another if you really want it shared |
| **The reviewer never sees memory** | It reviews a diff; history does not change what the diff says |
| **`--plan` records nothing** | The plan-facts write happens after approval |
| **Recall size is not configurable** | A constant until measured |
| **A role without `context_tokens` gets no recall block** | The single most common reason memory "does nothing" |

---

## 16. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `rudra memory list` is empty after a run | No task reached `done` — memory records finished work, not attempts | Check the run summary for blocked tasks |
| Memory exists but behaviour never changes | The role declares no `context_tokens`, so no recall block is injected | Set `context_tokens` in `[model.default]`; role inheritance carries it |
| `doctor` says the embedding model is not fetched | First install, or `rudra init` was never run here | Run `rudra init`, or let it download on first use |
| First memory write pauses for a while | The one-time ~167 MB model download | Expected once per machine. `rudra init` gets it out of the way |
| `doctor` warns your `~/.mempalace/config.json` is ignored | You use MemPalace yourself; Rudra deliberately does not read your settings | Nothing to fix — Rudra passes its own settings explicitly so yours cannot change its behaviour |
| `Long-term memory is off for this run: …` | The project directory name has no characters a wing can use (e.g. `___`) | Rename the directory, or run from one with letters or digits in its name |
| `[memory] backend must be one of chroma, …` | Only five values are accepted | Use `chroma` unless you have a reason not to |
| Memories from another project appear | They cannot — each project has its own palace | Check which directory you ran in |
| The model re-records finished tasks | Known: the tool asks it not to; models sometimes ignore that | `rudra memory forget --added-by agent` |
| `Refusing: this deletes data and stdin is not a terminal` | `forget` in a script or pipe | Add `--yes` |
| A restored export looks right but search finds nothing | Should not happen — an integration test covers exactly this | File an issue with the export directory |

---

**Related:** [Context and Memory](13-context-and-memory.md) · [Tools](11-tools.md) ·
[Configuration](02-configuration.md) · [CLI Reference](04-cli-reference.md) ·
[How It Works](05-how-it-works.md)

**Back to:** [README](../README.md)
