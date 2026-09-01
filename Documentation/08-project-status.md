# 8. Project Status

An honest account of what Rudra does today, what it doesn't, and what's next. Last updated **2026-08-17** (after Step 11c — the skills command, user skills, and the selection measurement).

- [In one sentence](#in-one-sentence)
- [What works](#what-works)
- [What doesn't work yet](#what-doesnt-work-yet)
- [Known rough edges](#known-rough-edges)
- [Documented but not real](#documented-but-not-real)
- [Roadmap](#roadmap)
- [Should I use this?](#should-i-use-this)

---

## In one sentence

Rudra asks what it can't work out, shows you a plan, writes code with your approval, checks its own work against a deterministic gate, and fixes what the gate rejects — drawing on a bundled methodology library, against any model you point it at. What it can't do yet is survive an interruption.

---

## What works

**Any model, any provider.** Ollama, OpenAI-compatible endpoints (OpenRouter, vLLM, LM Studio, Groq, Together, NVIDIA), Anthropic, OpenAI, and Google. Switching is a configuration edit, not a code change. Verified end to end against both a local Ollama model and a hosted one.

**Different models per role.** The planner and coder can use different models, different providers, different settings.

**Configuration checking.** `rudra models test` verifies each role constructs, connects, and can call tools — before you commit to a task. `rudra doctor` checks the rest of the setup.

**Layered TOML configuration.** `.rudra/config.toml` for the project, `~/.config/rudra/config.toml` for you, environment variables and CLI flags on top. `rudra config list` shows every effective value **and which layer set it**, so a surprising setting is traceable rather than mysterious.

**Planning in three stages, then your approval.** Rudra settles what the work depends on — asking you only what it cannot infer — decides how it should be built, then breaks it into units of *work*, not filenames. It shows you the result with each fact's source marked `asked` or `inferred`, and waits. You approve, revise it in a sentence (up to three times), or cancel. `--plan` stops there and writes nothing.

Each stage is a separate agent with its own tools, so a stage cannot do another stage's job: the clarify stage has no way to declare work, and the breakdown stage has no way to ask a question.

**Subagents with structural limits.** A `coder`, a `tester`, a `reviewer` and a `general-purpose` agent, each with its own model if you want. The differences are enforced by which tools exist for them rather than by instructions — the coder has no shell, and the reviewer has no write tools at all. See [Tools](11-tools.md).

**A completion gate that means something.** A task is done when `rudra verify` passes it: the code parses, lint and types hold, the tests run, and no stubs or placeholders are left behind. Six outcomes, not two. You can run the same gate yourself with `rudra verify`, with no model involved.

**It fixes what the gate rejects.** A failing gate sends the exact errors back to the coder for another attempt, up to `[agent] max_fix_attempts` (default 3). Two identical failures in a row stop that task rather than burning the budget, and the summary says which tasks were blocked and why.

**Only Python decides a task is done.** No tool the model can call is able to mark work complete — there is no `finish_task` and no status argument. The model decides what work exists; the gate decides when it is finished.

**Project facts that persist.** What Rudra establishes about your project is written to `.rudra/facts.json` with its value, *why* it believes it, and whether you said so or it inferred it. Every agent reads them, so the coder knows what the planner learned, and next week's run starts knowing your stack instead of asking again.

**Multi-language.** Python, Rust, Node, React/Next.js, and Angular projects are detected, in both empty folders and existing repositories.

**Approval before it acts.** By default every write, edit, delete and command stops and shows you a diff first. `a` approves, `A` stops asking about that file, `r` rejects. `--auto` skips the prompts for unattended runs.

**Shell access.** Rudra can run commands — its agents have a real shell rooted at your project, with your toolchain on `PATH`.

**Running your tests.** `run_tests` works out the right command from your project's own layout — your virtualenv's `pytest`, `cargo test`, or whatever `package.json` declares — runs it, and reports the exit code, pass/fail/skip counts, and the tail of the output. Full output goes to `.rudra/run/logs/tests.log`. It distinguishes four outcomes that look alike from a distance and are not: no test command declared, a command that wouldn't start, a suite that collected nothing, and tests that genuinely failed.

**Reading your diff.** `git_diff` shows the working-tree diff, capped so a large one can't swallow the context window.

**Working on its own branch.** `[tools] auto_branch = true` puts a run on `rudra/<slug>` instead of your branch. Off by default, and it only fires from a clean tree with a branch checked out — otherwise it says why and carries on where you are, without failing the run.

**Safe by construction.** File operations are confined to your project directory, whatever the model asks for. A small deny floor applies in every mode, including `--auto`: nothing writes into `.git/`, nothing runs `rm -rf /`. Every gated decision is logged to `.rudra/run/logs/permissions.jsonl`.

**Keys stay out of the shell.** Anything shaped like a secret — `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `AWS_*`, plus whatever `api_key_env` names — is stripped from the environment commands run in.

**Keys stay out of config.** `RUDRA_API_KEY_ENV` names a variable; the value lives in the environment. A test asserts a built model's `repr()` cannot leak it.

**A methodology library the agents can draw on.** The [superpowers](https://github.com/obra/superpowers) corpus (14 skills, MIT) ships inside the package, frozen and hash-verified. Nine are indexed by default for the three planning stages, the coder and the tester — `brainstorming` before designing, `test-driven-development` before implementing, `systematic-debugging` before guessing at a fix. Only names and descriptions sit in the prompt; the full text is read on demand. Narrow the set with `[skills] enabled`, or switch it off with `enabled = []`. The reviewer and general-purpose agent get none, by design.

**You can add your own**, in `<project>/.rudra/skills/` or `~/.rudra/skills/`, and a project skill overrides a bundled one of the same name. `rudra skills list` shows what exists and what is shadowed; `rudra skills validate` catches a malformed skill, which the loader otherwise skips in silence. See [Skills](12-skills.md).

**Nine skills ship enabled, and that is now measured rather than guessed.** Eight runs across two arms found that a thirteen-skill index made a 550B model erratic — enumerating the whole library for a trivial task, producing empty plans, and once regressing a test it had already fixed — where nine did not. The same measurement has not been run at the 32B floor.

---

## What doesn't work yet

Ordered roughly by how much you'll miss them.

### Resuming works, but only for the task list

`rudra --continue` picks up the tasks a stopped run never reached, without
re-planning or retyping the request. What it does *not* restore is the
conversation: the previous transcript is gone, so the coder starts a task with
the project on disk and the recorded facts, not with everything the last run
had discussed. That is deliberate — replaying transcripts would crowd out the
context window — but it means a task whose reasoning lived only in that
conversation may need re-explaining.

Blocked tasks are not retried, by design. If a run ended with everything
blocked, `--continue` refuses and tells you to change the request instead.

**One consequence to know now:** the gate's lint, typecheck and test stages are shell commands underneath and are gated like them, so **`--auto` on its own verifies nothing** — the gate escalates and the run stops on the first task. Pair it with `--allow-shell`. See [Permissions](09-permissions.md).

### A mid-stream provider failure still ends the run

Transient failures — `429`, `5xx`, dropped connections, timeouts — are retried
three times with backoff, which absorbs most of what free hosted tiers throw at
you. The gap is timing: retry only happens while nothing has been streamed back
yet. Once the model has begun answering, retrying would re-emit the whole turn,
so that failure ends the run.

Tasks already marked `done` genuinely passed the gate, and `rudra --continue`
works the rest, so this costs you a command rather than the run.

### Overlapping tasks can block work that is already done

Rudra clarifies, architects, breaks the work down, and shows you the plan before
any code is written — you approve, revise, or cancel it. What is still rough is
the planner occasionally declaring two tasks for one edit. The coder finishes
both under the first, the second legitimately changes nothing, and Rudra's
empty-diff guard treats that as a failed attempt: the run can end with a
non-zero exit and a `blocked` task while the feature works and its tests pass.

In `ask` mode you can see the overlap in the plan and revise it away before it
costs you anything. Under `--auto` you cannot, and re-running usually produces a
cleaner breakdown.

### Memory between runs is real, and now long-term

What persists on disk: **`.rudra/facts.json`**, so a stack established last week
is still known; **`.rudra/AGENTS.md`**, which gains a line per completed task and a
rewritten architecture summary at the end of each run; the **task ledger**, which
`rudra --continue` reads; and since Step 14 a **searchable long-term store** at
`.rudra/memory/`, holding every decision, finished task, blocker and stated preference,
and read back into each agent's prompt automatically. It runs locally, needs no API key,
and is documented in full in [Memory](15-memory.md).

What does not persist is the **conversation**. Checkpoints are written but never
replayed, on purpose — a stable per-project thread would have every run inherit
every earlier run's history. So Rudra remembers *what your project is and what
was done to it*, not *what it was thinking at the time*.

The `AGENTS.md` session log keeps its most recent 20 entries. It is re-read on
every planner call, so an unbounded one would be a tax that grows forever — and that cap
costs nothing now, because nothing ages out of the long-term store beside it.

One rough edge, stated plainly: a model will sometimes re-record a task the deterministic
half already recorded, and `rudra memory forget --added-by agent` prunes exactly that.

A palace that cannot be opened never fails your work — but it is no longer quiet about it
either. The run summary says so, `rudra doctor`'s memory row reads `unreadable` rather
than `0 memories`, and every `rudra memory` command exits `1` with the error instead of
reporting an empty store.

### MCP is real, and ships off

Model Context Protocol servers work: `.mcp.json` in Claude Code's schema, three
meta-tools instead of a bloated prompt, and per-tool gating. **No server ships enabled**,
so with no `.mcp.json` Rudra behaves as it always did. See [MCP](14-mcp.md).

### No command sandbox

Approved commands run with your user's full access. Rudra confines *file* operations to your project, but a shell command is not confined — `echo x > /anywhere` does what it says.

That's why commands are disabled under `--auto` unless you pass `--allow-shell`: in an unattended run nobody reads the command first. In `ask` mode you do, which is the actual protection. Real containment would need OS-level isolation, and Rudra doesn't do that.

---

## Known rough edges

Things that work, but not the way you'd hope.

| Issue | What happens | Workaround |
|---|---|---|
| **A mid-stream model failure ends the run** | Transient errors are retried three times with backoff, but only before the model starts answering; after that the run stops | Check `.rudra/run/ledger.json` — tasks marked `done` genuinely passed the gate — then `rudra --continue` |
| **Ollama truncates silently** | Default 4096-token window, no warning, and the agent forgets its plan | Set `context_tokens` for the role. `rudra doctor` warns when it is missing |
| **Free hosted models are unreliable** | `429` daily caps and transient `502`s mid-run | Rudra retries these automatically; if one lands mid-answer, `rudra --continue`. Or use a paid or local model |
| **Review is advisory only** | The reviewer runs once at the end and prints its findings; nothing gates on them, by design — an LLM verdict doesn't decide completion here | Read the findings yourself. The deterministic gate is what blocks |
| **A stray task can block a working feature** | Two plan tasks covering one edit: the coder finishes both under the first, the second changes nothing, and an empty diff counts as a failed attempt | Revise the plan when you see the overlap. Under `--auto`, re-run — the breakdown is usually cleaner |

---

## Documented but not real

Older versions of the README described commands that never existed or were removed. To be explicit:

**`build`, `chat`, `fix`, `edit`, `review`, `suggest`, `resume`, `watch` do not exist.** What does: a bare prompt, interactive mode, and `init`, `doctor`, `verify`, `models test`, `config list`, `config get`. See [CLI Reference](04-cli-reference.md#commands-that-no-longer-exist).

**There is no `rudra skills` command yet**, and no `config set` — configuration is read-only from the CLI, so you edit the TOML.

**These settings are ignored:** `MAX_AGENTS`, `MAX_ITERATIONS`, `CHECKPOINT_INTERVAL`, `TAVILY_API_KEY`, `USE_DUCKDUCKGO`.

**There's no interactive tech-stack questionnaire.** The old four-question prompt (language / framework / database / notes) is gone. Rudra detects your stack from the files present.

**`qwen3:14b` is no longer supported.** The minimum is 32B.

---

## Roadmap

Built in order, because each depends on the last.

**Shipped since this page last named them as "next":** the real agent loop — subagents, a tester, an advisory reviewer, and a completion gate that means *the tests pass* rather than *the file exists*; better planning, which turned out to mean three staged agents, dynamic clarifying questions, and a plan you approve before any code is written; the [skills](12-skills.md) library with user-authored skills and `rudra skills validate`; [context management](13-context-and-memory.md), which made summarisation, eviction and per-run token accounting real; [MCP](14-mcp.md); and [long-term memory](15-memory.md). Since then, release polish shipped too: the run trace with per-role latency, `--debug` logging, a Ctrl-C that stops at a task boundary and hands the task back to `--continue`, a REPL with history, multiline, `@`-file mentions and `/`-completion, and a per-run transcript that `rudra log` replays.

| Next | What it brings |
|---|---|
| **Packaging** | A PyPI release, so `uv tool install rudra` and `pipx install rudra` work. Until then, install from source — see [Getting Started](01-getting-started.md) |
| **Stack coverage** | An end-to-end acceptance case per stack — Rust, Python, Node, React, Angular — greenfield and brownfield. Detection itself already ships |

`TODO.md` in the repository root is the live ledger — every item, its status, and the evidence behind it.

---

## Should I use this?

**Good fit right now**

- Scaffolding small projects in an empty directory
- Generating a first draft you intend to review
- Working in an existing repo, in `ask` mode, on a branch — you see every change before it lands
- Trying out a local-first coding agent

**Also a good fit now**

- Work you want checked rather than merely generated — the gate is real, and `rudra verify` runs it without a model
- Tasks where you'd rather be asked than guessed at
- Loop-engineering runs with `--auto --allow-shell`, on a branch, in a repo you can throw away

**Not yet**

- Anything where generated code might go unreviewed
- Long runs where losing the *conversation* matters — `--continue` restores the task list, not the reasoning
- Unattended runs with `--allow-shell` against anything you care about
- Machines with no model available and no budget for one

The honest summary: Rudra now closes its own loop. It plans, asks, writes, verifies against a deterministic gate, and repairs what the gate rejects — stopping rather than thrashing when two attempts fail the same way. The foundation is provider-agnostic and the safety model is real, if not sandboxed.

It is durable in the way that matters and not in the way it doesn't. A dropped connection is retried, and if a run dies anyway the completed work stays on disk and `rudra --continue` takes the rest. What is *not* carried over is the conversation, deliberately. The vendored methodology library is wired in and the agents read from it. Treat its output as a reviewed first draft from a fast junior developer who runs the tests, fixes what they can, and tells you plainly which tasks they gave up on.

---

**Back to:** [README](../README.md) · [Getting Started](01-getting-started.md)
