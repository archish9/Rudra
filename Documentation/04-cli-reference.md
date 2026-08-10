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

No subcommand, no mode to pick. Describe the outcome and Rudra plans the files and writes them.

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

**Be specific about filenames.** Rudra plans by filename, so naming what you want helps a lot:

```bash
rudra "write wordcount.py: an argparse CLI counting lines, words, characters"
```

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

## Global flags

| Flag | Short | Does |
|---|---|---|
| `--project-dir PATH` | `-d` | Work in that directory instead of the current one |
| `--verbose` / `--no-verbose` | `-V` | Show or hide detailed tool-call output |
| `--version` | `-v` | Print the version and exit |
| `--dry-run` | | **Not functional yet** — see below |
| `--auto` / `--yolo` | | Set permission mode to `auto` — **not enforced yet** |
| `--plan` | | Set permission mode to `plan` — **not enforced yet** |
| `--help` | | Show help |

```bash
rudra "add error handling" -d ~/projects/api
rudra "refactor the parser" --verbose
```

`--project-dir` decides which `.env` **and** which `.rudra/config.toml` are read, so a project's configuration follows the project rather than your shell's location.

> **`--auto`, `--yolo`, and `--plan` set a value nothing reads yet.** They record the permission mode and print a notice saying so. No approval prompts exist, so `--auto` grants nothing that the default doesn't already allow.

> **`--dry-run` does not preview anything.** It exits immediately, reporting `Dry run completed (no files written)`, without planning or writing. It is a placeholder. Don't rely on it to inspect what Rudra *would* do.

---

## Exit codes

| Code | Means |
|---|---|
| `0` | Success |
| `1` | Something failed |

Be aware of one rough edge: if a model call fails partway through a run, Rudra exits `1` even if files were already written successfully. Check `.rudra/run/PLAN.md` and your working directory before assuming nothing happened.

`rudra models test` and `rudra doctor` exit `1` when a check fails, which makes them usable in a setup script.

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
