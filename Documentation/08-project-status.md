# 8. Project Status

An honest account of what Rudra does today, what it doesn't, and what's next. Last updated **2026-08-11** (after Step 8 — git tools and the test runner).

- [In one sentence](#in-one-sentence)
- [What works](#what-works)
- [What doesn't work yet](#what-doesnt-work-yet)
- [Known rough edges](#known-rough-edges)
- [Documented but not real](#documented-but-not-real)
- [Roadmap](#roadmap)
- [Should I use this?](#should-i-use-this)

---

## In one sentence

Rudra can plan a set of files, write them with your approval, run your test suite and read the result — against any model you point it at. What it can't do yet is act on that result by itself.

---

## What works

**Any model, any provider.** Ollama, OpenAI-compatible endpoints (OpenRouter, vLLM, LM Studio, Groq, Together), Anthropic, OpenAI, and Google. Switching is a configuration edit, not a code change. Verified end to end against both a local Ollama model and a hosted one.

**Different models per role.** The planner and coder can use different models, different providers, different settings.

**Configuration checking.** `rudra models test` verifies each role constructs, connects, and can call tools — before you commit to a task. `rudra doctor` checks the rest of the setup.

**Layered TOML configuration.** `.rudra/config.toml` for the project, `~/.config/rudra/config.toml` for you, environment variables and CLI flags on top. `rudra config list` shows every effective value **and which layer set it**, so a surprising setting is traceable rather than mysterious.

**Planning and writing.** Rudra produces a file checklist, then writes each file with a fresh agent, retrying up to three times per file.

**Multi-language.** Python, Rust, Node, React/Next.js, and Angular projects are detected, in both empty folders and existing repositories.

**Approval before it acts.** By default every write, edit, delete and command stops and shows you a diff first. `a` approves, `A` stops asking about that file, `r` rejects. `--auto` skips the prompts for unattended runs.

**Shell access.** Rudra can run commands — its agents have a real shell rooted at your project, with your toolchain on `PATH`.

**Running your tests.** `run_tests` works out the right command from your project's own layout — your virtualenv's `pytest`, `cargo test`, or whatever `package.json` declares — runs it, and reports the exit code, pass/fail/skip counts, and the tail of the output. Full output goes to `.rudra/run/logs/tests.log`. It distinguishes four outcomes that look alike from a distance and are not: no test command declared, a command that wouldn't start, a suite that collected nothing, and tests that genuinely failed.

**Reading your diff.** `git_diff` shows the working-tree diff, capped so a large one can't swallow the context window.

**Working on its own branch.** `[tools] auto_branch = true` puts a run on `rudra/<slug>` instead of your branch. Off by default, and it only fires from a clean tree with a branch checked out — otherwise it says why and carries on where you are, without failing the run.

**Safe by construction.** File operations are confined to your project directory, whatever the model asks for. A small deny floor applies in every mode, including `--auto`: nothing writes into `.git/`, nothing runs `rm -rf /`. Every gated decision is logged to `.rudra/run/logs/permissions.jsonl`.

**Keys stay out of the shell.** Anything shaped like a secret — `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `AWS_*`, plus whatever `api_key_env` names — is stripped from the environment commands run in.

**Keys stay out of config.** `RUDRA_API_KEY_ENV` names a variable; the value lives in the environment. A test asserts a built model's `repr()` cannot leak it.

---

## What doesn't work yet

Ordered roughly by how much you'll miss them.

### The run can't resume

Each run starts a fresh task ledger. If a run stops early — a denied command, a dropped connection — the work already on disk stays, but there is no `--continue` to pick up the remaining tasks. Re-run and the planner starts over.

**One consequence to know now:** the gate's lint, typecheck and test stages are shell commands underneath and are gated like them, so **`--auto` on its own verifies nothing** — the gate escalates and the run stops on the first task. Pair it with `--allow-shell`. See [Permissions](09-permissions.md).

### A failed model call is not retried

There is no backoff around model invocation. A rate limit or a dropped connection stops the run where it is. Tasks already marked `done` genuinely passed the gate; the rest are reported as never attempted.

### Planning is staged, but you do not approve it

Rudra clarifies, architects, then breaks the work down, each as its own stage,
and records every fact and decision with the reason behind it. What is missing
is the last step: you do not get to see the plan and approve it before the coder
starts. `--plan` today writes nothing and runs nothing, but it does not present
anything either. That is the next step (`C6.9`).

### Little memory between runs

Each invocation starts fresh apart from what's on disk. Project facts do
persist — `.rudra/facts.json` is durable, so a stack established last week is
still known — but conversations, plans and progress are not: the ledger is
per-run and checkpoints are never resumed (`C7.2`), and `AGENTS.md` is written
once and never updated (`A1.9`, `C7.3`).

### No MCP, no skills, no plugins

Designed, not implemented.

### No command sandbox

Approved commands run with your user's full access. Rudra confines *file* operations to your project, but a shell command is not confined — `echo x > /anywhere` does what it says.

That's why commands are disabled under `--auto` unless you pass `--allow-shell`: in an unattended run nobody reads the command first. In `ask` mode you do, which is the actual protection. Real containment would need OS-level isolation, and Rudra doesn't do that.

---

## Known rough edges

Things that work, but not the way you'd hope.

| Issue | What happens | Workaround |
|---|---|---|
| **A failed model call ends everything** | Rate limit or dropped connection stops the run, even if earlier tasks finished | Check `.rudra/run/ledger.json` — tasks marked `done` genuinely passed the gate. Re-run to continue |
| **`--dry-run` does nothing** | Exits immediately with no plan and no preview | Use `--plan`, which really does write a plan and touch nothing else |
| **Ollama truncates silently** | Default 4096-token window, no warning, and the agent forgets its plan | Set `RUDRA_CONTEXT_TOKENS` |
| **Free hosted models are unreliable** | `429` daily caps and transient `502`s mid-run | Retry, or use a paid or local model |
| **Coder may write the wrong file** | Nothing enforces that the file written matches the file assigned | Review output; keep tasks small |

---

## Documented but not real

Older versions of the README described commands that never existed or were removed. To be explicit:

**`build`, `chat`, `fix`, `edit`, `review`, `suggest`, `resume`, `watch` do not exist.** The only commands are a bare prompt, interactive mode, and `models test`. See [CLI Reference](04-cli-reference.md#commands-that-no-longer-exist).

**These settings are ignored:** `MAX_AGENTS`, `MAX_ITERATIONS`, `CHECKPOINT_INTERVAL`, `TAVILY_API_KEY`, `USE_DUCKDUCKGO`.

**There's no interactive tech-stack questionnaire.** The old four-question prompt (language / framework / database / notes) is gone. Rudra detects your stack from the files present.

**`qwen3:14b` is no longer supported.** The minimum is 32B.

---

## Roadmap

Built in order, because each depends on the last.

| Next | What it brings |
|---|---|
| **The real agent loop** | Subagents, a reviewer, a tester, and a completion gate that means *tests pass* rather than *file exists*. This is the one that matters, and the test runner it consumes is now in place |
| **Better planning** | Clarifying questions when a request is ambiguous, and a plan mode |
| **Skills** | A methodology layer the agent can draw on |
| **Context management** | Checkpoints, living project notes, token budgets |
| **MCP support** | Model Context Protocol servers |
| **Long-term memory** | Knowledge that persists across sessions |
| **Release polish** | Streaming, cost reporting, PyPI |

`TODO.md` in the repository root is the live ledger — every item, its status, and the evidence behind it.

---

## Should I use this?

**Good fit right now**

- Scaffolding small projects in an empty directory
- Generating a first draft you intend to review
- Working in an existing repo, in `ask` mode, on a branch — you see every change before it lands
- Trying out a local-first coding agent
- Contributing to Rudra itself

**Not yet**

- Anything where generated code might go unreviewed
- Workflows needing tests to actually pass
- Unattended runs with `--allow-shell` against anything you care about
- Machines with no model available and no budget for one

The honest summary: Rudra is a capable file generator that asks permission, runs commands, and can now run your tests and tell you what happened — on a solid provider-agnostic foundation. It is not yet the autonomous test-and-fix agent it's aiming to be, because nothing makes it *act* on a failing suite. Treat its output as a first draft from a fast junior developer who will run the tests if you ask, and will still hand you the branch when they fail.

---

**Back to:** [README](../README.md) · [Getting Started](01-getting-started.md)
