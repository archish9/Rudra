# 6. Troubleshooting

Real error messages and what to do about them.

- [Start here](#start-here)
- [Installation problems](#installation-problems)
- [Configuration problems](#configuration-problems)
- [Connection problems](#connection-problems)
- [Model behaviour problems](#model-behaviour-problems)
- [Problems during a run](#problems-during-a-run)
- [Still stuck?](#still-stuck)

---

## Start here

Nine times out of ten this identifies the problem in one step:

```bash
rudra models test
```

Read the row left to right. The first column that isn't `ok` is your answer:

| First failing column | Problem is in |
|---|---|
| **Construct** | Your configuration — no network involved yet |
| **Reach** | Connection, URL, key, or model name |
| **Tools** | The model itself — it can't call tools |

---

## Installation problems

### `command not found: rudra`

Rudra isn't on your PATH.

**pipx:**
```bash
pipx ensurepath
source ~/.bashrc          # or restart the terminal
pipx list                 # rudra should be listed
```

**venv** — you need to activate it in each new terminal:
```bash
source /path/to/Rudra/.venv/bin/activate
```

**uv** — either activate, or prefix commands:
```bash
uv run rudra --version
```

### `rudra --version` prints `0.0.0+unknown`

The package isn't properly installed — Rudra reads its version from installed metadata. Reinstall:

```bash
pipx install -e . --force     # or: pip install -e .   /   uv sync
```

### `error: externally-managed-environment`

Debian, Ubuntu, and recent Fedora block `pip install` into system Python. Use pipx:

```bash
sudo apt install pipx
pipx ensurepath
source ~/.bashrc
cd /path/to/Rudra
pipx install -e .
```

Or use a virtualenv, which needs no `sudo` at all.

**Never run `sudo pip install`** — it can break system packages that depend on your OS Python.

### Code edits aren't taking effect

You installed non-editable. Check:

```bash
pip show rudra | grep -i editable      # want: Editable project location: ...
pipx list --verbose | grep -i editable
```

Fix by reinstalling with `-e`:

```bash
pipx install -e . --force
```

---

## Configuration problems

### `Unknown provider 'xyz'. Valid providers: anthropic, google, ollama, openai, openai_compatible.`

Typo in `RUDRA_PROVIDER`. It must be exactly one of those five.

Note that most services speaking OpenAI's API — OpenRouter, vLLM, LM Studio, Groq, Together, NVIDIA — want `openai_compatible`, not `openai`. Only OpenAI itself uses `openai`.

### `Role 'planner' needs an API key, but environment variable 'OPENROUTER_API_KEY' is not set.`

Rudra found the variable *name* in your config but no value in the environment.

```bash
echo $OPENROUTER_API_KEY        # probably empty
```

Add it to `.env` in your project:

```bash
OPENROUTER_API_KEY=sk-or-v1-your-actual-key
```

Or export it from your shell profile.

**The classic mix-up** — putting the key where the name goes:

```bash
# WRONG — this is the key, not a variable name
RUDRA_API_KEY_ENV=sk-or-v1-abc123

# RIGHT — two separate lines
RUDRA_API_KEY_ENV=OPENROUTER_API_KEY
OPENROUTER_API_KEY=sk-or-v1-abc123
```

With the wrong form, Rudra looks up an environment variable literally named `sk-or-v1-abc123`, finds nothing, and reports it missing.

### A setting isn't taking effect

Stop guessing — ask:

```bash
rudra config list
```

The `Source` column names the layer that set each value: `builtin`, `user`, `project`, `env`, or `cli`. If it says `env` and you expected your config file, an environment variable is winning. That's by design; later layers override earlier ones.

```
built-in  →  ~/.config/rudra/config.toml  →  .rudra/config.toml  →  RUDRA_* env  →  CLI flags
```

Find a stray variable with:

```bash
env | grep RUDRA_
```

### My `.env` or `config.toml` is being ignored

Both are read from the **project directory** — the folder you're in, or whatever `--project-dir` names. Not your home directory, not the Rudra source folder.

```bash
rudra doctor --offline
```

It prints which config files it found and which `.env` it read. If a path there isn't the one you edited, that's the answer.

### `Configuration error: ...` on startup

Rudra refuses to run on a config it can't trust, and names the problem:

| Message | Cause |
|---|---|
| `invalid TOML — Expected ']' (at line 2...)` | Syntax error; the line number is real |
| `Unknown key 'tempreature' ... Did you mean 'temperature'?` | Typo. Unknown keys are fatal on purpose — silently ignoring one means your setting never applies and nothing says so |
| `Unknown provider 'banana'` | Check the spelling against the five valid names it lists |
| `[memory] backend must be one of chroma, milvus, ...` | The only key `[memory]` takes is `backend`, and only those five values |
| `Unknown skill 'brainstorm' in [skills] enabled` | A skill name that isn't vendored. It's fatal rather than ignored because the loader silently skips a skill it can't find — see [Skills](12-skills.md) |

### Settings from an old guide do nothing

`MAX_AGENTS`, `MAX_ITERATIONS`, `CHECKPOINT_INTERVAL`, `TAVILY_API_KEY`, `USE_DUCKDUCKGO` were removed and are ignored. See [Configuration](02-configuration.md).

`OLLAMA_*` variables still work but print a deprecation warning naming their `RUDRA_*` replacement. Same for a bare `VERBOSE`, which is now `RUDRA_VERBOSE` — unprefixed, it collided with CI systems and build tools that set it for unrelated reasons.

### Rudra overwrote a file without asking

Check your mode: `rudra config get permissions.mode`. If it is `auto` — or you passed `--auto` or `--yolo` — that is what those do. The default, `ask`, stops before every write and shows a diff.

If you pressed `A` (always) earlier in the run, that grant covers the rest of the run for that file or command. It is not saved; the next run asks again.

### Rudra exits 2 without doing anything

```
Error: permissions.mode = "ask" needs an interactive terminal,
but stdin is not a TTY.
```

`ask` mode has to be able to ask. Piped, redirected, or CI input has no terminal to prompt on, so Rudra stops immediately rather than hanging — before contacting the model, and without creating anything. Use `--auto` for unattended runs, adding `--allow-shell` if it also needs to run commands.

### A command was denied and the agent gave up

Under `--auto`, commands are off unless you pass `--allow-shell`. The denial message says so, and the audit log records it as `source: "auto-shell"`:

```bash
cat .rudra/run/logs/permissions.jsonl
```

Rudra confines file writes to your project whatever the model asks; a shell command isn't confined, and in an unattended run nobody reads it first. See [Permissions](09-permissions.md#running-unattended).

### A write was denied and I don't know which rule did it

Every denial is logged with the rule that fired:

```bash
grep '"decision":"deny"' .rudra/run/logs/permissions.jsonl
```

`source` tells you where it came from — `floor` for the built-in rules, `deny` for one of yours, `auto-shell` for the unattended-command rule, `auto-mcp` for the unattended-MCP one, `mode-default` for plan mode.

### `floor_disable` won't accept `outside-root`

That is deliberate. Writes are confined to your project by the file-access layer, not by that rule, so switching it off would change nothing and quietly redirect the write back inside your project. Rejecting the setting is more honest than accepting one that does nothing. `git-dir`, `rudra-state` and `catastrophic-command` can be disabled.

---

## MCP problems

All of these assume you have configured a server — see [MCP](14-mcp.md). With no
`.mcp.json`, none of this applies and Rudra behaves as it always has.

### `No MCP servers configured`

There is no `.mcp.json` in your project root, or its `mcpServers` object is empty.

```bash
rudra mcp add kala -- npx -y -p kala-mcp@0.3.0 kala-mcp
rudra mcp list
```

Note the file lives in the **project root**, not in `.rudra/`.

### `rudra mcp test` says `fail` / `FileNotFoundError`

The program that starts the server isn't installed, or isn't on your `PATH`. The
detail column names it:

```
│ kala │ fail │ FileNotFoundError: ... 'npx'
```

Install it — for anything `npx`-based that means Node 20 or newer — then re-run
`rudra mcp test`. `rudra doctor` reports the same thing without starting anything:

```
│ mcp: kala │ missing │ 'npx' is not on PATH │
```

### `Permission denied: MCP tools are disabled in unattended ('auto') mode`

Expected. An MCP server is a separate program Rudra does not confine, so `--auto`
refuses MCP until you opt in:

```bash
rudra --auto --allow-mcp "..."
```

Or persist it with `[mcp] mcp_in_auto = true`, or allow one specific tool with
`[permissions] allow = ["call_mcp_tool:kala__system_status"]`.

### `'kala__system_bootstrap' is not available to you`

That tool is filtered out for the agent that asked. Check `[mcp] allow`, `[mcp]
deny`, and — if the message came from the reviewer — `[mcp] readonly`, which is the
only list the reviewer can see.

This is visibility, not permission. Widening it does not bypass `[permissions]`.

### `MCP call 'x__y' exceeded the 60s [mcp] timeout`

The server took too long. Raise it:

```toml
[mcp]
timeout = 180
```

Servers that launch a browser or fetch a package on first use are the usual cause.

### `Warning: MCP disabled — ... could not be read as JSON`

`.mcp.json` has a syntax error. The run continues without MCP rather than failing.
Check for a trailing comma or an unquoted key:

```bash
python3 -m json.tool .mcp.json
```

### The agent never uses my server

Ask for the outcome, not the tool — *"check this page against our design system"*
rather than *"call kala__verify"*. If it still doesn't, confirm the tools are
reachable with `rudra mcp test`, then check the agent that would need them has
access: the tester and the planner stages get no MCP at all
([Tools](11-tools.md#which-agent-gets-which-tool)).

### One server is down and I want the rest to keep working

They already do. A server that fails is reported once, remembered for the rest of
the run so it isn't retried, and every other server keeps answering.

---

## Connection problems

### `Connection refused` on Ollama

The server isn't running:

```bash
ollama serve                          # separate terminal
curl http://localhost:11434/api/tags  # should return JSON
```

Check your URL has no trailing slash and no `/v1` — Ollama wants a bare `http://localhost:11434`.

### `model "qwen3:32b" not found, try pulling it first`

```bash
ollama pull qwen3:32b
ollama list                # names must match exactly, tag included
```

### `Error code: 401` / `Incorrect API key provided`

The key is wrong, expired, or revoked. Regenerate it at your provider and update `.env`.

Also check you're pointing at the right service — an OpenRouter key sent to OpenAI's endpoint gives exactly this.

### `Error code: 404` on a hosted provider

Usually the `base_url`. These need the `/v1` suffix:

| Service | Correct `base_url` |
|---|---|
| OpenRouter | `https://openrouter.ai/api/v1` |
| vLLM | `http://localhost:8000/v1` |
| LM Studio | `http://localhost:1234/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| Ollama | `http://localhost:11434` — **no** `/v1` |

Or the model name is wrong. Copy it exactly from the provider's list.

### `Error code: 429 - Rate limit exceeded: free-models-per-day`

You've spent the day's free allowance on OpenRouter. Options: wait for the reset, add a small credit balance (the message says how much unlocks a larger daily cap), or switch to a paid model.

Not a Rudra problem — your configuration is fine, which is why **Construct** still shows `ok`.

### `502 - Upstream error: ResourceExhausted: Worker local total request limit reached`

The upstream provider behind a free model is momentarily saturated. Usually transient — retry, often successfully on the second attempt.

Full agent runs make far more calls than `rudra models test` does, so you may see `models test` pass and a real run fail. For dependable work, use a paid model or a local one.

### `404 ... /responses` on a self-hosted server

You set `RUDRA_PROVIDER=openai`, which enables OpenAI's Responses API. vLLM, LM Studio, Groq, and Together only serve `/chat/completions`.

```bash
RUDRA_PROVIDER=openai_compatible
```

---

## Model behaviour problems

### `Model 'x' reports tool_calling=False`

The model can't call tools, and Rudra can't work without that. Pick a different one — `rudra models test` will confirm.

### Tools column fails, everything else passes

The model answered but never emitted a tool call. Usually a base or instruct-tuned model without tool support, or one below the 32B floor. Try `qwen3:32b` locally, or a known tool-capable hosted model.

### Output is poor, plans are nonsense, files are half-written

Almost always model size. **32B is the minimum.** Below that, plans come out malformed and files land incomplete — Rudra removed the workarounds that used to paper over this.

Also try lowering the temperature:

```bash
RUDRA_TEMPERATURE=0.1
```

### The agent forgets its own plan mid-run

Context truncation. On Ollama especially:

```bash
RUDRA_CONTEXT_TOKENS=32768
```

Ollama allocates **4096 tokens** by default and silently truncates past that — no error, just an agent that loses the thread. Setting this raises the allocation, and it also tells Rudra when to compact its history.

Confirm it took effect in the `Ctx` column of `rudra models test`.

---

## Problems during a run

### Rudra goes quiet for minutes and I can't tell what it's doing

It shouldn't any more. Every agent — planner, coder, tester, reviewer —
prints each tool call, result and error as it happens. If you see planning
and then silence, you are either on a version before v0.2.1 or running with
`--no-verbose`, which drops the trace to errors only.

To see more rather than less:

```bash
rudra --verbose "…"     # adds the model's prose and untruncated payloads
rudra --stream "…"      # streams that prose token by token as it arrives
```

A long gap on a single line is the model thinking, not Rudra hanging. The
per-task timings in the final summary tell you afterwards where the time
actually went.

### My runs are slow

Nearly all of it is the model, not Rudra. In one measured 33-minute run,
208 model calls at 8.95s each were 91% of the wall clock; Rudra's own code
accounted for about 28 seconds.

That means a run's duration is **calls × latency**, and you can see both
numbers:

```bash
rudra models test           # the Latency column: one tool-call round trip
```

and, after any run, the `Tokens:` block in the final summary, which reports
`s/call` per role beside the call count. The same figures are written to
`.rudra/run/logs/usage.json`.

Read the role with the largest **total** seconds, not the largest `s/call`.
The coder usually wins by a wide margin — it retries per task and reads
before it writes — which makes `[model.coder]` the highest-leverage line in
your config. It does not have to be the same model as `[model.planner]`.
See [Configuration → Which role to change
first](02-configuration.md#which-role-to-change-first), and mind the 32B
floor: a smaller coder that needs more attempts costs more calls than it
saves in latency.

### The clock says an hour, Rudra says twenty minutes

Your machine went to sleep mid-run. An unattended `--auto` run holds no
wake lock, so a laptop left alone can suspend between one model call and
the next.

Every duration Rudra reports — `s/call`, per-task seconds, the trace
timeline — comes from a monotonic clock, which by design does not count
time the process was suspended. Those numbers stay correct. What they
stop matching is your stopwatch.

The final summary says so when it happens:

```
suspended 3645.4s of 4888.0s wall clock (machine asleep; the seconds above exclude it)
```

and `.rudra/run/logs/usage.json` carries the same three figures under its
`run` key: `wall_seconds`, `counted_seconds`, `suspended_seconds`.

The fix is to keep the machine awake for the run:

```bash
caffeinate -i rudra --auto --allow-shell "..."     # macOS
systemd-inhibit --what=idle rudra --auto ...       # Linux with systemd
```

Measured, on the run that produced this section: 81 minutes of wall clock
over 27 minutes of work, in three sleeps of 15, 15 and 30 minutes.

### Searching a large repo feels slow

Check `rudra doctor` for the `ripgrep` row. Without `rg` on your `PATH`,
searches fall back to a Python implementation that is noticeably slower on
big trees. Installing ripgrep is the whole fix — Rudra picks it up
automatically, with no configuration.

### I pressed Ctrl-C and lost the task it was working on

Fixed in v0.2.1. Ctrl-C now stops at the end of the current task, returns
that task to `pending`, and saves the ledger, so `rudra --continue` picks
it up. Single-shot exits `130`.

Before that, Ctrl-C killed the process wherever it happened to be and left
the task marked `in_progress` — a status `--continue` skips deliberately,
so the interrupted task was the one task a resume would never retry.

If a ledger from an older version has a stuck task, edit
`.rudra/run/ledger.json` and change that task's `"status": "in_progress"`
to `"pending"`.

### A traceback appears and Rudra exits, but files were written

A model call failed and could not be recovered — rate limit, dropped connection, upstream error. Rudra retries transient failures three times with backoff, but only while nothing has been streamed back yet; once the model has started answering, a retry would re-emit the whole turn, so the run ends there and exits `1` even though earlier work succeeded.

Check before assuming nothing happened:

```bash
cat .rudra/run/ledger.json  # tasks marked done genuinely passed the gate
ls
```

Then pick up where it stopped:

```bash
rudra --continue
```

That works the remaining tasks without re-planning and without retyping the
request. Running it *without* `--continue` starts a fresh plan instead, which is
what you want if the request itself has changed.

### The summary says every task is done, but a file I asked for is missing

Every task the planner declared appears in the summary with a status, and anything not `done` says why. If a task is `blocked`, read its reason: `no progress: the same failure twice` means two attempts failed identically, and `attempts exhausted` means the fix budget ran out — which is not the same as never converging; see the next section.

Check the plan, then ask again naming the file explicitly:

```bash
rudra "also write tests/test_parser.py covering the CSV edge cases"
```

### A task says `3 attempts exhausted`

The coder had `[agent] max_fix_attempts` tries (default 3) to get the task past the verification gate, and every try failed *differently* — two identical failures would have stopped it sooner, as `no progress`. Different is not always stuck: the coder cannot run anything itself, so each gate shows it only the next layer of a problem — a missing package, then a schema error, then a wrong assertion. A task can run out of attempts while its failures are still shrinking.

To tell which, read the attempts in order. Each retry's instructions in `.rudra/run/logs/debug-<id>.jsonl` are a `"kind": "user"` record for that task containing `Your previous attempt did not pass verification`, followed by the gate it was answering; the last gate is under the note in `ledger.json`. If every gate is a new failure and the counts are falling, the task was converging. If the same count keeps coming back in a different spelling, it was not.

A blocked task stays blocked: `rudra --continue` works only `pending` tasks, so it will not give this one another try. For a converging task, raise the budget and run the request again:

```toml
# .rudra/config.toml
[agent]
max_fix_attempts = 5
```

Raising it costs a coder invocation per extra attempt on every task that fails, stuck or not — across Rudra's own measured runs a third attempt passed about one time in twenty — so raise it for the run that needs it rather than for good.

A gate that failed **only** because a package the project does not declare is missing (`No module named ...`, or a library's own `pip install ...` hint) does not count against the budget: the coder cannot see the next missing package until the one in front of it is installed, and Rudra installs what the project declares before every gate. Each such gate prints `the gate failed only on packages the project does not declare (...)`, and the task's `dependency_gates` in `ledger.json` lists them. At most three are given back per task per run, and a package the coder does not then declare draws the same gate again and ends as `no progress`. So a task that still ends `attempts exhausted` with `dependency_gates` in its record spent its budget on its own code — or, past three, on more missing packages than the allowance: declare them in `requirements.txt` or `pyproject.toml` yourself and run again.

### The generated code doesn't run

A task marked `done` passed the verification gate: it parsed, type checked, its tests ran, and no placeholders were left in the files that task touched. So this should be rare — and when it happens it usually means one of:

- **The gate could not run.** Under `--auto` without `--allow-shell`, lint, typecheck and tests are all denied and the run stops on the first task. Check the summary for `unfinished` — the task the run stopped on, with the reason under it — and `never attempted` for the tasks after it.
- **The project has no tests.** The gate reports `not_applicable` rather than failing, so nothing exercised the code. Ask for tests explicitly.
- **The task was blocked, not done.** Read the summary: anything not `done` says why.

Run `rudra verify` yourself to see the same verdict. Always review generated code.

### `ERROR: Could not find an activated virtualenv (required).`

This is on purpose. Rudra sets `PIP_REQUIRE_VIRTUALENV=1` for every command,
so pip refuses to install into a Python that is not a virtualenv. Without it,
an agent's `pip install` changes packages on your machine, and one did
(OPEN-120).

For a Python project, Rudra installs the project's declared dependencies into
its `.venv` before running tests. Add a missing dependency to
`requirements.txt` or `pyproject.toml`. To install something by hand, install
into the venv:

```bash
.venv/bin/python -m pip install <package>
```

pip does not count a conda environment as a virtualenv, so it refuses there
too. Use `conda install`, or a venv.

### Rudra created a `.venv` in my project

This happens in a Python project that has no virtualenv of its own; see
[Your project's `.venv`](10-verification.md#project-venv). Rudra marks it with
`.venv/.rudra-sync.json` and gives it its own `.gitignore`, so `git status`
does not list it. You can delete it at any time.

### `run_tests` says "Running tests was not permitted"

You're in `--auto` without `--allow-shell`. Tests are a shell command underneath, and unattended shell is opt-in — nobody is reading the command before it runs.

```bash
rudra --auto --allow-shell "add a parser and make its tests pass"
```

Or allow the commands themselves, which is narrower. Every command Rudra was
refused is in `.rudra/run/logs/permissions.jsonl`, spelled exactly as the rule
is matched against it — the `arg` of each line reading `"decision": "deny"`.
For a Python project that is an absolute path. Add each one with a trailing
`*`:

```toml
[permissions]
allow = [
  "execute:/home/you/todo/.venv/bin/python -m pytest*",
  "execute:/home/you/.local/share/pipx/venvs/rudra/bin/python -m mypy*",
]
```

`execute:pytest*` does **not** do this: it matches a command that starts with
the word `pytest`, which an agent may type, and the gate never runs.

A rule for the tests alone is not enough:

- **The type-check runs first.** For a Python project it is mypy — Rudra's
  own, the second line above, unless your `.venv` holds one — and a denied
  type-check stops the run before the tests are reached. A denied lint stops
  nothing; lint is advisory.
- **The test command above needs `.venv`, and building it is a command too.**
  Rudra builds `.venv` with `python3 -m venv .venv` and fills it with
  `pip install`. While those are denied there is no `.venv`, and the tests run
  under your machine's `python3` instead. Allowing `pip install*` lets the run
  install whatever the project declares, which is most of what `--allow-shell`
  allows. The narrow route is to create `.venv` yourself, with pytest in it:
  Rudra leaves a virtualenv it did not build alone, and installs nothing into
  it — the dependencies the code needs are yours to add.

Each run records only the refusals it reached, so completing the list can take
more than one run. For anything past a quick check, `--allow-shell` is simpler.

### `run_tests` says "This project declares no test command"

Rudra looks for a test command in your project's own layout: a virtualenv's `pytest`, `cargo test`, or `scripts.test` in `package.json`. An empty directory with no project file has none of those, which is a real answer rather than a failure.

Add the project file the stack expects — `pyproject.toml`, `Cargo.toml`, `package.json` — and it will resolve.

### `run_tests` says "No tests were collected"

The suite ran and found nothing to execute. Usually the test files aren't written yet, or they don't match your runner's discovery pattern (`test_*.py` for pytest by default).

This is deliberately **not** reported as a failure: nothing ran, so nothing is verified either way, and calling it a failure would send the agent off to fix code that may be fine.

### `run_tests` says "The test command timed out"

The suite exceeded `[tools] test_timeout` (default 600 seconds) and was killed, along with any workers it spawned.

Either the suite is genuinely slow — raise the limit — or something hangs. Angular's default test builder runs Karma against Chrome and waits forever when no browser is installed, which is the usual culprit.

```toml
[tools]
test_timeout = 1800
```

### `No branch created: the working tree has uncommitted changes`

`auto_branch` only branches from a clean tree, because `git checkout -b` carries uncommitted work onto the new branch and fails outright where it would clobber. Commit or stash first.

Rudra's own `.rudra/` directory doesn't count — only your changes do. The run continues on your current branch either way; this is never fatal.

### `--dry-run has been removed`

Correct, and deliberately loud. The flag never previewed anything: it exited
before the planner ran and reported *success*, so a script that used it
believed it had inspected something. It now exits 2 and names its
replacement.

Use `--plan`. It runs the planner, prints the facts it established and the
tasks it declared, and writes nothing.

### First run is slow

Rudra imports a large dependency tree, and your model may need loading into memory. Later runs are quicker.

---

## Still stuck?

**Send the folder, not a checklist.** Everything an issue needs is already
written, and by the run itself rather than by you:

```bash
ls .rudra/run/logs/
# meta.json  debug-<run-id>.jsonl  usage.json  verify.log  permissions.jsonl
```

`meta.json` is written when the run starts and answers the first four
questions of any triage — which Rudra, which Python, which platform, which
permission mode, and which model each role used. It carries no API key.
`rudra models test` is still worth running if the problem is a provider one.

If the project is gone, the same evidence is at
`~/.local/state/rudra/runs/<project>/<run-id>/`, which also holds the run's
ledger, its facts and its transcript.

```bash
ls -t .rudra/run/logs/debug-*.jsonl | head -1     # the most recent run
```

One JSON object per line, covering both the trace and Rudra's internal log
records and tracebacks. It is the **complete** record: every event whatever
`--verbose` was set to, with payloads uncapped. One file per run, newest 20
kept.

Older versions wrote this only under `--debug` and filtered it to whatever
the console had printed, so `--no-verbose` produced a nearly empty file. If
you are on such a version, `rudra --debug --verbose` is the pair that gets
you a full one.

One more file is worth adding if the run got that far:
`.rudra/run/transcripts/<run-id>.jsonl` — the readable record, capped at
2000 characters per payload, which `rudra log --last` replays.

A failed model call carries what the provider actually said, and its
traceback, on its own line:

```bash
grep '"ok": false' .rudra/run/logs/debug-*.jsonl | head
```

If Langfuse is configured (`[telemetry]`), the same run is a link you can
share instead — one trace per run, named by the same run id. See
[Configuration](02-configuration.md#the-telemetry-section) for what leaves
the machine.

**Read the debug log before you post it.** It contains file paths and
whatever your model wrote, which may include contents of your project.

Rudra redacts credential-shaped values — `API_KEY=…`, `Bearer …`, and keys
carrying a known vendor prefix — before they reach the trace, the debug
log, or a transcript. That is pattern matching, not a guarantee: a secret
with no recognisable shape, in a variable with an innocuous name, will
pass straight through. Skim the file yourself.

Then open an issue at [github.com/archish9/Rudra/issues](https://github.com/archish9/Rudra/issues).

**Never paste an API key** into an issue, log, or screenshot. The command above masks anything ending in `KEY=`, but check the output before posting.

---

**Next:** [Project Status](08-project-status.md) · [Configuration](02-configuration.md)
