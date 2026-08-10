# 8. Project Status

An honest account of what Rudra does today, what it doesn't, and what's next. Last updated **2026-08-10**.

- [In one sentence](#in-one-sentence)
- [What works](#what-works)
- [What doesn't work yet](#what-doesnt-work-yet)
- [Known rough edges](#known-rough-edges)
- [Documented but not real](#documented-but-not-real)
- [Roadmap](#roadmap)
- [Should I use this?](#should-i-use-this)

---

## In one sentence

Rudra can plan a set of files and write them, against any model you point it at — and that's genuinely all it does so far.

---

## What works

**Any model, any provider.** Ollama, OpenAI-compatible endpoints (OpenRouter, vLLM, LM Studio, Groq, Together), Anthropic, OpenAI, and Google. Switching is a configuration edit, not a code change. Verified end to end against both a local Ollama model and a hosted one.

**Different models per role.** The planner and coder can use different models, different providers, different settings.

**Configuration checking.** `rudra models test` verifies each role constructs, connects, and can call tools — before you commit to a task.

**Planning and writing.** Rudra produces a file checklist, then writes each file with a fresh agent, retrying up to three times per file.

**Multi-language.** Python, Rust, Node, React/Next.js, and Angular projects are detected, in both empty folders and existing repositories.

**Safe by construction.** File operations are rooted at your project directory. API keys are read from named environment variables and never stored in configuration.

**Keys stay out of config.** `RUDRA_API_KEY_ENV` names a variable; the value lives in the environment. A test asserts a built model's `repr()` cannot leak it.

---

## What doesn't work yet

Ordered roughly by how much you'll miss them.

### Rudra can't run anything

No shell access. It cannot run your tests, linter, build, or git. It writes files and stops.

This is the single biggest limitation, and everything below follows from it.

### "Done" only means "the file exists"

Rudra ticks an item off when the file is present on disk — not when it compiles, not when tests pass. It will cheerfully report `3/3 files generated` for three files that don't run.

**Always review generated code.**

### No test → review → fix loop

The headline idea — write code, test it, review it, fix it, repeat — isn't built. Rudra currently does the first step only.

### No self-review

Nothing inspects the generated code before reporting success.

### No clarifying questions

Rudra won't ask what you meant. Ambiguous requests get a guess. Be specific, and name the files you want.

### No memory between runs

Each invocation starts fresh apart from what's on disk. It doesn't remember previous sessions or accumulate knowledge about your codebase.

### No MCP, no skills, no plugins

Designed, not implemented.

### No TOML configuration

`.rudra/config.toml` and `~/.config/rudra/config.toml` are designed but not implemented. Environment variables are the only configuration surface today.

### No permission prompts

There's no approval gate before Rudra writes a file. It writes within your project directory without asking. Point it at scratch directories, not repositories you can't afford to have edited.

---

## Known rough edges

Things that work, but not the way you'd hope.

| Issue | What happens | Workaround |
|---|---|---|
| **A failed model call ends everything** | Rate limit or dropped connection raises an error and exits `1`, even if files were already written | Check `.rudra/PLAN.md` — ticked items were genuinely written. Re-run to continue |
| **Silently dropped plan items** | A `PLAN.md` line that doesn't parse as a filename is skipped, and the count reports only survivors — `2/2` can hide a missing third | Read `PLAN.md`; ask again naming the file explicitly |
| **`--dry-run` does nothing** | Exits immediately with no plan and no preview | Use a scratch directory instead |
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
| **TOML configuration** | Config files with layering, permission modes |
| **Shell access + permissions** | Rudra can run commands — and asks before doing anything risky. These land together, never separately |
| **Git and test-runner tools** | Running your test suite becomes possible |
| **The real agent loop** | Subagents, a reviewer, a tester, and a completion gate that means *tests pass* rather than *file exists* |
| **Better planning** | Clarifying questions when a request is ambiguous, and a plan mode |
| **Skills** | A methodology layer the agent can draw on |
| **Context management** | Checkpoints, living project notes, token budgets |
| **MCP support** | Model Context Protocol servers |
| **Long-term memory** | Knowledge that persists across sessions |
| **Release polish** | Streaming, cost reporting, a LICENSE file, PyPI |

`TODO.md` in the repository root is the live ledger — every item, its status, and the evidence behind it.

---

## Should I use this?

**Good fit right now**

- Scaffolding small projects in an empty directory
- Generating a first draft you intend to review
- Trying out a local-first coding agent
- Contributing to Rudra itself

**Not yet**

- Production codebases you can't afford to have edited
- Anything where generated code might go unreviewed
- Workflows needing tests to actually pass
- Machines with no model available and no budget for one

The honest summary: Rudra is a capable file generator with a solid provider-agnostic foundation, and it is not yet the autonomous test-and-fix agent it's aiming to be. Treat its output as a first draft from a fast junior developer who cannot run the code.

---

**Back to:** [README](../README.md) · [Getting Started](01-getting-started.md)
