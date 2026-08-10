# 8. Project Status

An honest account of what Rudra does today, what it doesn't, and what's next. Last updated **2026-08-10** (after Step 7 — shell and permissions).

- [In one sentence](#in-one-sentence)
- [What works](#what-works)
- [What doesn't work yet](#what-doesnt-work-yet)
- [Known rough edges](#known-rough-edges)
- [Documented but not real](#documented-but-not-real)
- [Roadmap](#roadmap)
- [Should I use this?](#should-i-use-this)

---

## In one sentence

Rudra can plan a set of files, write them with your approval, and run commands — against any model you point it at. What it can't do yet is judge whether any of it worked.

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

**Safe by construction.** File operations are confined to your project directory, whatever the model asks for. A small deny floor applies in every mode, including `--auto`: nothing writes into `.git/`, nothing runs `rm -rf /`. Every gated decision is logged to `.rudra/run/logs/permissions.jsonl`.

**Keys stay out of the shell.** Anything shaped like a secret — `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, `AWS_*`, plus whatever `api_key_env` names — is stripped from the environment commands run in.

**Keys stay out of config.** `RUDRA_API_KEY_ENV` names a variable; the value lives in the environment. A test asserts a built model's `repr()` cannot leak it.

---

## What doesn't work yet

Ordered roughly by how much you'll miss them.

### "Done" only means "the file exists"

Rudra ticks an item off when the file is present on disk — not when it compiles, not when tests pass. It will cheerfully report `3/3 files generated` for three files that don't run.

This is now the single biggest limitation, and everything below follows from it.

**Always review generated code.**

### No test → review → fix loop

The headline idea — write code, run its tests, review it, fix it, repeat — isn't built. Rudra *can* run commands now, but nothing in the loop makes it run your test suite and act on the result. That's the next milestone.

There is also no dedicated test-runner tool yet: the agent has a raw shell, not a step that knows how to run pytest and parse the failures.

### No self-review

Nothing inspects the generated code before reporting success.

### No clarifying questions

Rudra won't ask what you meant. Ambiguous requests get a guess. Be specific, and name the files you want.

### No memory between runs

Each invocation starts fresh apart from what's on disk. It doesn't remember previous sessions or accumulate knowledge about your codebase.

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
| **A failed model call ends everything** | Rate limit or dropped connection raises an error and exits `1`, even if files were already written | Check `.rudra/run/PLAN.md` — ticked items were genuinely written. Re-run to continue |
| **Silently dropped plan items** | A `PLAN.md` line that doesn't parse as a filename is skipped, and the count reports only survivors — `2/2` can hide a missing third | Read `PLAN.md`; ask again naming the file explicitly |
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
| **Git and test-runner tools** | A step that knows how to run your suite and read the failures, rather than a raw shell |
| **The real agent loop** | Subagents, a reviewer, a tester, and a completion gate that means *tests pass* rather than *file exists*. This is the one that matters |
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

The honest summary: Rudra is a capable file generator that now asks permission and can run commands, on a solid provider-agnostic foundation — and it is not yet the autonomous test-and-fix agent it's aiming to be. Treat its output as a first draft from a fast junior developer who doesn't check their own work.

---

**Back to:** [README](../README.md) · [Getting Started](01-getting-started.md)
