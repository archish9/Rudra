# 4. CLI Reference

Everything `rudra` accepts. It's a short list — that's deliberate.

- [Running a task](#running-a-task)
- [Interactive mode](#interactive-mode)
- [`rudra models test`](#rudra-models-test)
- [`rudra init`](#rudra-init)
- [`rudra config`](#rudra-config)
- [`rudra doctor`](#rudra-doctor)
- [Global flags](#global-flags)
- [Exit codes](#exit-codes)
- [Commands that no longer exist](#commands-that-no-longer-exist)

---

## Running a task

```bash
rudra "<what you want>"
```

No subcommand, no mode to pick. Describe the outcome. Rudra settles what it needs to know, decides how to build it, shows you the plan, and writes the files once you approve.

```bash
rudra "write a Python CLI that reverses a string"
rudra "create a Rust command-line tool that formats JSON, using clap and serde_json"
rudra "build an Express API with a /health endpoint and a Jest test"
```

Quotes are optional but recommended — without them the shell may eat characters:

```bash
rudra write a hello world script     # works
rudra "write a hello world script"   # safer
```

**Say what you want, not which files to create.** Rudra plans in units of *work* — "write a CSV parser that handles quoted commas" is a task; "parser.py" is not — and it decides the layout itself, recording why. Naming a file is still useful when you actually care which one it is:

```bash
rudra "write wordcount.py: an argparse CLI counting lines, words, characters"
```

**Answer its questions.** When something matters and cannot be inferred, Rudra asks — in one batch, up to `[agent] max_questions` for the whole run — and remembers the answers in `.rudra/facts.json` for next time.

Rudra supports Python, Rust, Node, React/Next.js, and Angular projects, and works in both empty folders and existing repositories.

---

## Interactive mode

```bash
rudra
```

Starts a session with no task. Type requests as you go.

```
rudra> write a CSV parser in parser.py
✓ Done

rudra> /tree
parser.py

rudra> /exit
```

| Command | Does |
|---|---|
| `/help` | List available commands |
| `/tree` | Print the project file tree |
| `/exit` or `/quit` | Leave |

`exit` and `quit` work without the slash. Pressing `Esc` three times also quits.

Each request is handled as its own task. Deep multi-turn memory across requests is still being built — see [Project Status](08-project-status.md).

---

## `rudra models test`

Checks that every configured model is reachable and can call tools.

```bash
rudra models test
rudra models test --role planner
```

| Flag | Does |
|---|---|
| `--role ROLE` | Test one role only. Default tests `planner` and `coder` |

Output:

```
                          Model check
┏━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ Role    ┃ Provider ┃ Model            ┃ Construct ┃ Reach ┃ Tools ┃ Ctx    ┃
┡━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ planner │ ollama   │ qwen3:32b        │ ok        │ ok    │ ok    │ 131072 │
│ coder   │ ollama   │ qwen3:32b        │ ok        │ ok    │ ok    │ 131072 │
└─────────┴──────────┴──────────────────┴───────────┴───────┴───────┴────────┘
```

| Stage | Proves | Network? |
|---|---|---|
| **Construct** | Config is valid, provider known, key variable present, package installed | No |
| **Reach** | The model answers | Yes |
| **Tools** | The model emits a real tool call | Yes |
| **Ctx** | The context window in effect | No |

The stages are reported separately because they fail independently. A green **Reach** with a failed **Tools** means your connection is perfect and the model simply can't drive Rudra — a distinction a single pass/fail would hide.

When something fails, the cell shows the error in place:

```
│ planner │ openai_com │ nvidia/... │ ok │ Error code: 429 - Rate limit    │ skipped │ 131072 │
│         │ patible    │            │    │ exceeded: free-models-per-day.  │         │        │
```

Read that as: configuration is correct, the quota is spent. Later stages are skipped once one fails.

---

## `rudra init`

Writes a commented `.rudra/config.toml` and creates the `.rudra/` directory
layout.

```bash
rudra init                  # project config
rudra init --global         # ~/.config/rudra/config.toml instead
rudra init --force          # overwrite an existing file
```

Refuses to overwrite without `--force`, so it is safe to re-run.

---

## `rudra config`

Read-only inspection of the effective configuration.

```bash
rudra config list                       # every value, with the layer that set it
rudra config list --role planner        # one model role
rudra config get model.planner.model    # one value
```

`list` prints a `Source` column — `builtin`, `user`, `project`, `env`, or
`cli`. When a setting isn't taking effect, that column tells you which layer
overrode it.

There is no `config set`; edit `.rudra/config.toml` in your editor. The
reasoning is in [Configuration](02-configuration.md).

---

## `rudra doctor`

Checks the whole setup and prints a table.

```bash
rudra doctor              # includes a live model check
rudra doctor --offline    # skip the network, check everything else
```

It reports which config files were found and in what order, which `.env` was
read, whether the `.rudra/` layout is intact, the pinned versus installed
`deepagents` version, and — prominently — that permission mode is **not
enforced** yet.

MCP servers, skills, and memory aren't checked because they don't exist yet.

---

## `rudra verify`

Runs the deterministic verification gate over the project — syntax, lint,
typecheck, tests, and a stub scan. No model is involved.

```bash
rudra verify                        # the files git reports as changed
rudra verify --all                  # every source file in the project
rudra verify --changed src/a.py     # name files explicitly (repeatable)
rudra verify --json                 # machine-readable
```

| Flag | Means |
|---|---|
| `-d`, `--project-dir PATH` | Project to verify. Defaults to the current directory |
| `--all` | Scan every source file, not just what git reports as changed |
| `--changed PATH` | Scope the stub scan to these files. Repeatable |
| `--json` | Machine-readable output instead of the table |

`--all` and `--changed` are mutually exclusive.

| Exit code | Means |
|---|---|
| `0` | Passed. The verdict still names any stage that produced no judgement |
| `1` | A blocking stage failed — syntax, typecheck, test, or stubs |
| `2` | Something needs you: a denied command, a missing tool, or an internal error |

Lint never fails a task. It is reported in full and ignored by the verdict.

Stages that run commands go through the permission gate as `execute`, so under
`--auto` without `--allow-shell` — and under `mode = "ask"` with no terminal —
they are denied and the run exits `2`.

See [Verification](10-verification.md) for the stages, the outcomes, and the
per-stack install matrix.

---

## Global flags

| Flag | Short | Does |
|---|---|---|
| `--project-dir PATH` | `-d` | Work in that directory instead of the current one |
| `--verbose` / `--no-verbose` | `-V` | Show or hide detailed tool-call output |
| `--version` | `-v` | Print the version and exit |
| `--dry-run` | | **Not functional yet** — see below. Use `--plan` |
| `--auto` / `--yolo` | | Approve every file operation without prompting |
| `--allow-shell` | | Let `--auto` run commands too. Off by default |
| `--allow-mcp` | | Let `--auto` call MCP tools too. Off by default |
| `--plan` | | Show the plan — the facts Rudra established and the tasks it declared — then stop. Writes nothing, runs nothing |
| `--continue` / `--resume` | | Work the remaining tasks from the last run instead of planning afresh |
| `--help` | | Show help |

```bash
rudra "add error handling" -d ~/projects/api
rudra "refactor the parser" --verbose
```

`--project-dir` decides which `.env` **and** which `.rudra/config.toml` are read, so a project's configuration follows the project rather than your shell's location.

> **`--auto` skips the approval prompts; `--allow-shell` is separate on purpose.** File operations are confined to your project whatever the model asks; a shell command is not. In an unattended run nobody reads the command first, so commands stay off until you say otherwise. See [Permissions](09-permissions.md#running-unattended).
>
> **This also means `--auto` alone runs no tests.** Rudra's `run_tests` is a shell command underneath and is gated the same way, so an unattended run without `--allow-shell` writes code it cannot check. Pair the flags when you want it to verify its own work.

> **`--allow-mcp` is the same idea for MCP servers.** An MCP server is a separate program Rudra does not confine, so unattended runs refuse MCP calls until you opt in — with the flag, or `[mcp] mcp_in_auto = true`. Naming one tool in `[permissions] allow` (`call_mcp_tool:kala__system_status`) is also consent, for that tool only. See [MCP](14-mcp.md#5-controlling-what-a-server-may-do).

> **The default mode needs a terminal.** `mode = "ask"` prompts before each write, so piped or redirected stdin exits `2` immediately rather than hanging. Use `--auto` for scripts.

### `--continue`

```bash
rudra --continue
```

Picks up where the last run in this directory stopped. It reads
`.rudra/run/ledger.json`, skips planning entirely, and hands the remaining
tasks straight to the coder — no clarifying questions, no re-architecting, no
replay of the previous conversation.

```
Resuming: 4 done · 3 pending · 0 blocked
```

**You don't need to retype the request.** The ledger records what it was
planned for, and `--continue` on its own reuses it.

**What resumes and what doesn't:**

| Task state | On `--continue` |
|---|---|
| `pending` | Worked. This is the point of the flag |
| `in_progress` | Not resumed — the process died mid-task, and the coder's partial work is already on disk for the next attempt to see |
| `blocked` | Not retried. It failed the same way twice already, so repeating it spends tokens to reach the same place |
| `done` · `dropped` | Finished. Left alone |

Attempt counts carry across, so `[agent] max_fix_attempts` still means what it
says either side of the interruption.

**It refuses rather than guessing.** Four cases, each exiting `2` with a
sentence:

```
Cannot continue: no previous run to continue — .rudra/run/ledger.json does not exist.

Cannot continue: that is a different request from the one this plan was built for.
  planned for: build a small JSON config loader with tests
  you asked:   build a YAML parser instead
  Run it without --continue to plan afresh, or drop the prompt to continue the original.

Cannot continue: nothing pending — 7 of 7 task(s) finished. There is nothing left to continue.

Cannot continue: nothing pending — 3 done, 2 blocked. A blocked task failed the
same way twice, so continuing would repeat it; change the request instead.
```

Every one of those lands *before* Rudra builds an agent or reads your API key,
so a mistaken `--continue` costs nothing.

`--resume` is an alias. There is only one thing to resume, so there is only one
behaviour behind both spellings.

> **What is resumed is the task list, not the conversation.** Rudra deliberately
> does not replay the previous transcript: a stable per-project thread would
> make every run inherit every earlier run's history and steadily crowd out the
> context window. What an interruption actually costs you is finished work, and
> the ledger is the record of that.

> **`--dry-run` does not preview anything.** It exits immediately, reporting `Dry run completed (no files written)`, without planning or writing. It is a placeholder. Don't rely on it to inspect what Rudra *would* do.

---

## Exit codes

| Code | Means |
|---|---|
| `0` | Success |
| `1` | Something failed |
| `2` | `mode = "ask"` was set but stdin is not a terminal — nothing ran. Also a `--continue` that cannot proceed. For `rudra verify`, also a denied command, a missing tool, or an internal error |

If a model call fails and cannot be retried, Rudra exits `1` even though earlier files were written successfully. Nothing is lost: tasks marked `done` in `.rudra/run/ledger.json` genuinely passed the gate, and **`rudra --continue` works the rest**.

`rudra models test` and `rudra doctor` exit `1` when a check fails, which makes them usable in a setup script.

---

## `rudra skills`

Inspect and validate the skill library. See [Skills](12-skills.md) for what
skills are and how to write one.

```bash
rudra skills list          # every skill, its source, whether it is in the prompt
rudra skills validate      # would these skills actually load?
rudra skills validate PATH # check one directory
rudra skills rebuild       # re-render the bundled cache, drop stale copies
```

`validate` exits **1** if any skill would fail to load, naming each one. That
matters because the loader skips a malformed skill *silently* — without this
command, a typo means a skill that never loads and never explains itself.

`list` marks a skill `invalid` rather than claiming it is in the prompt, and
marks `shadowed by project` when a project skill overrides a bundled one.

---

## `rudra mcp`

Attach outside tool servers. See [MCP](14-mcp.md) for what they are and how to use one.

```bash
rudra mcp add kala -- npx -y -p kala-mcp@0.3.0 kala-mcp   # a local program
rudra mcp add api --url https://mcp.example.com/mcp       # a hosted one
rudra mcp list                                            # what's configured
rudra mcp test                                            # start each one, list its tools
rudra mcp remove kala
```

Everything after `--` in `add` is the command that starts the server. The name you give
(`kala`) becomes the prefix on that server's tools: `kala__verify`.

`add` and `remove` edit `.mcp.json` in your project root — the same schema Claude Code
uses, so an existing config pastes in unchanged. Edit it by hand if you prefer.

`test` actually starts each server and asks what it offers:

```
│ kala   │ ok     │ 8 tools: system_status, verify, explain, …  │
│ ghost  │ fail   │ FileNotFoundError: 'ghost-server'           │
```

It exits **1** if any server fails, so it works in a setup script. `rudra doctor` reports
the same servers without starting them — it only checks that each command exists on
`PATH`.

---

## Commands that no longer exist

Earlier versions documented `build`, `chat`, `fix`, `edit`, `review`, `suggest`, `resume`, and `watch`. **None of them exist.** Use a plain prompt instead:

| You wanted | Do this |
|---|---|
| `rudra build "..."` | `rudra "..."` |
| `rudra chat` | `rudra` |
| `rudra fix "..."` | `rudra "fix: ..."` |
| `rudra edit file.py "..."` | `rudra "in file.py, ..."` |
| `rudra review` | Not available yet — see [Project Status](08-project-status.md) |
| `rudra suggest "..."` | Not available yet |
| `rudra resume` | Not available yet |
| `rudra watch` | Removed |

The single-prompt interface is the design: describe the outcome, let Rudra decide the approach.

---

**Next:** [How It Works](05-how-it-works.md) · [Troubleshooting](06-troubleshooting.md)
