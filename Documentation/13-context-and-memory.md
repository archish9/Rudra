# 13. Context and Memory

Two different things share this page, because in practice you meet them together:

- **Context** — the agent's short-term memory, the window it can actually see. It fills up during a run.
- **Memory** — what survives *between* runs, so tomorrow's run knows something about yesterday's.

If you only read one thing: **set `context_tokens` for your model.** Everything else on this page is automatic.

---

## The one setting

Every model can only read so much at once. Rudra needs that number, and it cannot work it out for itself.

```toml
[model.default]
provider      = "ollama"
base_url      = "http://localhost:11434"
model         = "qwen3:32b"
context_tokens = 32768     # ← this one
```

Check whether it's set:

```bash
rudra doctor
```

```
│ context window │ ok │ Tool results evict at: default 3276, planner 3276, … │
```

If it says `warn`, it will name the roles that are missing it.

### Finding your model's number

| Where | How |
|---|---|
| **Local (Ollama)** | `ollama show <model>` — look for `context length` |
| **Hosted, any provider** | OpenRouter publishes it for thousands of models, free and without an API key — see the snippet below |
| **Your provider's model page** | Usually stated on the model card |

Looking one up, for any model OpenRouter lists — it does not have to be the provider you use:

```bash
curl -s https://openrouter.ai/api/v1/models \
  | python3 -c "import json,sys; [print(m['id'], m['context_length']) for m in json.load(sys.stdin)['data'] if 'nemotron' in m['id']]"
```

```
nvidia/nemotron-3-ultra-550b-a55b 512288
```

Swap `nemotron` for part of your model's name.

**Erring low is safe.** Too low just means Rudra tidies up earlier than it had to. Too high risks the model actually overflowing mid-task. If you're unsure, halve your best guess.

### What it actually controls

That one number feeds two thresholds. This is why it's worth setting:

| At | What happens |
|---|---|
| **85% of the window** | Rudra summarises the older part of the conversation and offloads the detail, so the agent keeps working instead of overflowing |
| **10% of the window**, never below 2,000 tokens | Any *single* tool result bigger than this is written to `.rudra/run/artifacts/` and replaced by a preview plus a pointer to read it |

The 2,000-token floor only matters on very small models: ten percent of a 4k
window would be smaller than an ordinary file read, and a threshold that evicts
everything has just replaced the context window with a filing cabinet.

That second one matters more than it sounds. A failing `pytest` run can produce an enormous wall of text. Without a threshold it sits in the conversation for the rest of the task, crowding out the code the agent is trying to fix.

> **Leave `context_tokens` unset and both fall back to defaults built for very large hosted models** — summarising at 170,000 tokens and evicting at 20,000. On a 32k model the first never fires at all, and the second lets one test transcript eat a third of everything the model can see.

---

## What Rudra does on its own

You don't configure or trigger any of this.

**It summarises when the conversation gets long.** At 85% of the window, older messages are condensed and the full text is kept in `.rudra/run/artifacts/` where the agent can go back and read it.

**It sets aside oversized tool results.** Past the 10% threshold, the agent gets a head-and-tail preview and a file path rather than the whole thing.

**The coder and tester can tidy up mid-task.** Both can call `compact_conversation` when they judge their own context is getting long — useful in a fix loop that has been round several times. It shows in your run summary as a *compaction*. The planner and reviewer don't get it: they're too short-lived to need it.

---

## What a run cost you

Every finished run reports its usage:

```
✅ Complete
Files created:  3
Files modified: 1
Tokens: planner  41,204 in / 3,118 out   (7 calls)
        coder    88,930 in / 12,455 out  (19 calls, 2 compactions)
        tester   14,002 in /  1,203 out  (4 calls)
```

The same numbers, machine-readable, in `.rudra/run/logs/usage.json`:

```json
{
  "planner": {"calls": 7, "input_tokens": 41204, "output_tokens": 3118, "compactions": 0},
  "coder":   {"calls": 19, "input_tokens": 88930, "output_tokens": 12455, "compactions": 2}
}
```

Two things worth knowing about these numbers:

- **`not reported` is not zero.** Some local endpoints don't send usage data. Rudra says so rather than printing `0`, which would look like a free run.
- **`compactions` counts only the deliberate kind** — the tool call above. Automatic summarisation happens outside Rudra's view and isn't in this figure.

---

## What survives between runs

Three things persist in `.rudra/`. Two are worth committing.

### `facts.json` — what Rudra established about your project

```json
{
  "language": {"value": "Rust", "why": "the user explicitly asked for a CLI in Rust", "source": "asked"}
}
```

Every fact carries its reasoning and whether you said it (`asked`) or Rudra worked it out (`inferred`). Next run starts knowing your stack instead of asking again. Wrong guess? Edit the line.

### `AGENTS.md` — the project's running notebook

Rudra creates this on first run and **keeps writing to it**. Each completed task adds a line:

```markdown
## Session Log

- 2026-08-19 t1 add a JSON config loader
  src/config.py, tests/test_config.py
- 2026-08-19 t2 wire the --config flag
  src/cli.py
```

The file list comes from **git**, not from the model, so it records what actually changed.

At the end of each run, one model call rewrites the *Architecture Notes* section into a short description of how the project is built:

```markdown
## Architecture Notes

Configuration loading lives in `src/config.py` and is intentionally
dependency-free. The CLI reads it once at startup and passes the resulting
object down rather than re-reading per command.
```

The planner reads this file at the start of every run. That's how a decision made last week reaches this week's work.

**The session log keeps its 20 most recent entries.** It's re-read on every planner call, so an unbounded one would be a bill you pay forever.

> If the summarising call fails — provider down, quota hit — you lose the prose, not the log. The per-task entries are written as work completes, before any model is involved.

### `run/ledger.json` — the task list

This is what `rudra --continue` reads. Regenerated per run and not worth committing.

---

## What does *not* survive: the conversation

Rudra writes checkpoints but never replays them, and this is a deliberate choice rather than a missing feature.

Replaying would mean every run in a project inherits every earlier run's transcript. Run fifty starts by reading forty-nine runs of history, and the window fills with old conversation before any work begins.

So Rudra remembers **what your project is and what was done to it** — not **what it was thinking at the time**.

The practical consequence: if you explained something in a run that died, and that explanation never became a *fact* or a *task*, you may need to say it again.

---

## Picking up after an interruption

```bash
rudra --continue
```

Full behaviour, the refusals, and what does and doesn't resume: **[CLI Reference](04-cli-reference.md#--continue)**.

The short version: it works the remaining tasks, reuses the original request so you don't retype it, skips planning entirely, and refuses rather than guessing if you hand it a different request or there's nothing pending.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Agent "forgets" its task mid-run | `context_tokens` unset, so summarisation never fires | Set it for the role |
| Ollama truncates silently | Ollama's own default is 4096 tokens | Set `context_tokens`; Rudra passes it through |
| `doctor` warns about context window | No role declares one | Add `context_tokens` to `[model.default]` — role inheritance carries it to the rest |
| Token counts say `not reported` | Your endpoint doesn't return usage data | Nothing to fix; it's the provider |
| `AGENTS.md` architecture notes are empty | No task reached `done`, or the summarising call failed | Check the session log — if entries are there, only the prose is missing |
| `.rudra/run/artifacts/` is filling up | Large tool results being offloaded, working as intended | Safe to delete; it's regenerated |

---

**Back to:** [README](../README.md) · [Configuration](02-configuration.md) · [How It Works](05-how-it-works.md)
