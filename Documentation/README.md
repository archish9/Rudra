# Rudra Documentation

Welcome. Start wherever fits — the guides are ordered but each stands alone.

| | Guide | Read this if you want to… |
|---|---|---|
| 1 | **[Getting Started](01-getting-started.md)** | Install Rudra, connect a model, run your first task |
| 2 | **[Configuration](02-configuration.md)** | Write a `config.toml`, understand the five layers, use different models per role |
| 3 | **[Choosing a Model](03-providers.md)** | Set up Ollama, OpenRouter, vLLM, Anthropic, OpenAI, or Google |
| 4 | **[CLI Reference](04-cli-reference.md)** | Look up a command, flag, or interactive shortcut |
| 5 | **[How It Works](05-how-it-works.md)** | See what happens between your prompt and the files on disk |
| 6 | **[Troubleshooting](06-troubleshooting.md)** | Decode an error message and fix it |
| 7 | **[Development](07-development.md)** | Work on Rudra itself — tests, layout, conventions |
| 8 | **[Project Status](08-project-status.md)** | Know what genuinely works and what doesn't |
| 9 | **[Permissions](09-permissions.md)** | Approval prompts, allow/deny rules, the deny floor, the audit log |
| 10 | **[Verification](10-verification.md)** | Run the deterministic gate, read its verdict, install the per-stack tools |
| 11 | **[Tools](11-tools.md)** | Look up what the agent can actually do — every tool, with examples and per-subagent grants |
| 12 | **[Skills](12-skills.md)** | Understand the vendored superpowers library, why it's frozen, and what Rudra adapts |
| 14 | **[MCP](14-mcp.md)** | Add an MCP server, control what it may do, and see a real one working |

---

## Suggested paths

**Never used Rudra** → [Getting Started](01-getting-started.md) → [Choosing a Model](03-providers.md) → [Project Status](08-project-status.md)

**About to run it unattended** → [Permissions](09-permissions.md) → [Tools](11-tools.md) → [MCP](14-mcp.md) if you configured a server

**Giving the agent tools Rudra doesn't ship** → [MCP](14-mcp.md)

**Wondering what the agent can actually do to your machine** → [Tools](11-tools.md)

**Something's broken** → [Troubleshooting](06-troubleshooting.md), or run `rudra models test` first — it usually names the problem for you

**Checking whether generated code is actually finished** → [Verification](10-verification.md)

**Tuning your setup** → [Configuration](02-configuration.md) → [Choosing a Model](03-providers.md)

**Curious how it works** → [How It Works](05-how-it-works.md) → [Development](07-development.md)

**Deciding whether to adopt it** → [Project Status](08-project-status.md), which is deliberately blunt

---

## The two-minute version

Rudra is a local-first autonomous coding agent for the terminal. You describe what you want; it plans the files and writes them.

```bash
rudra init                # scaffold .rudra/config.toml
rudra models test         # confirm the model works
rudra "write a Python CLI that reverses a string"
rudra verify              # the same gate the loop uses, run by hand
```

It works with any model — local via Ollama, or hosted via OpenRouter, Anthropic, OpenAI, Google, or any OpenAI-compatible endpoint. Switching takes one line of config.

Settings come from five layers — built-in defaults, your user config, the project config, environment variables, then CLI flags. `rudra config list` shows every effective value **and which layer set it**, so nothing has to be guessed at.

By default it asks before every write, edit, delete and command, and shows you a diff first. `--auto` skips the prompts for unattended runs — see [Permissions](09-permissions.md).

**It's alpha.** It writes files well, runs commands, and can run your test suite and report what failed — but an agent run does not yet act on the answer: a file counts as "done" when it exists on disk, not when it works. `rudra verify` will tell you the truth about the result ([Verification](10-verification.md)); wiring that verdict back into the run is the next step. Review everything it produces, and see [Project Status](08-project-status.md) before pointing it at anything you care about.
