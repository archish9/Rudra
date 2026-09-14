# 9. Permissions

What Rudra may do without asking, and how to change it.

- [The three modes](#the-three-modes)
- [The approval prompt](#the-approval-prompt)
- [Allow and deny rules](#allow-and-deny-rules)
- [The deny floor](#the-deny-floor)
- [Running unattended](#running-unattended)
- [The audit log](#the-audit-log)
- [What this does and doesn't protect](#what-this-does-and-doesnt-protect)

---

## The three modes

```toml
[permissions]
mode = "ask"        # ask | auto | plan
```

| Mode | Behaviour |
|---|---|
| `ask` *(default)* | Every write, edit, delete and command stops for approval, with a diff |
| `auto` | Approves everything without prompting. Also `--auto` or `--yolo` |
| `plan` | Shows you the plan — the facts Rudra established and the tasks it declared — then stops. No project files touched, no commands run. Also `--plan` |

Reading is never gated in any mode. A single run reads dozens of files, and
none of them can destroy anything.

**Rudra's own bookkeeping is never gated either.** `add_tasks`, `drop_task`,
`ask_user` and `record_fact` write only inside `.rudra/` — the task ledger and
the fact store — so gating them would mean `ask` mode prompting you about
Rudra's own note-taking, and `plan` mode denying the very thing `--plan` exists
to show you. They are allowed in every mode, and they cannot touch your project.

There is a second, separate gate that is *not* about permissions at all: before
any code is written you are shown the plan and asked to approve, revise or
cancel it. That one runs in `ask` mode and is skipped by `--auto`; see
[How It Works](05-how-it-works.md).

**`ask` needs a terminal.** Piped or redirected input exits `2` immediately
rather than hanging on a prompt nobody can answer:

```
$ rudra "add tests" < /dev/null
Error: permissions.mode = "ask" needs an interactive terminal,
but stdin is not a TTY.

  --auto                      approve everything (unattended)
  permissions.mode = "auto"   same, persisted in .rudra/config.toml
```

That check runs before the model is contacted, so it costs nothing and
leaves nothing behind.

---

## The approval prompt

```
╭─ approval required ────────────────────────────────╮
│ write_file  src/app.py    +12 -3  (overwrite)      │
╰────────────────────────────────────────────────────╯
  @@ -8,6 +8,15 @@
  - def run(argv):
  -     pass
  + def run(argv):
  +     args = parse(argv)
  … 8 more changed lines

[a]pprove  [r]eject  [A]lways (write_file:src/app.py)  [!]auto-accept  [d]iff (full)
```

| Key | Effect |
|---|---|
| `a` | Approve this one call |
| `r` | Reject it. Rudra is told not to retry and picks another approach |
| `A` | Approve, and stop asking about this tool and pattern for the rest of the session |
| `!` | Approve, and stop asking about **anything** for the rest of the session |
| `d` | Show the whole diff, then ask again |

What you see depends on the operation:

- **Overwriting a file** — a unified diff, capped at 20 changed lines with a
  `+N/-M` header. `d` shows all of it.
- **A new file** — its size and first ten lines. There is nothing to diff
  against.
- **`edit_file`** — a diff of just the replaced text.
- **`delete`** — the path and how many lines are about to go.
- **A command** — the command string and the directory it runs in.

Binary content, or anything over about 1 MB, reports its size rather than
rendering.

### `!` — auto-accept

`!` is the answer for "I have seen enough, stop asking". From that point on
Rudra runs without prompting you, and prints one line saying so.

**It is not `--auto`, and the difference is deliberate.** `!` silences the
question; it does not lift a refusal. Every deny rule you wrote still
refuses, and so does Rudra's built-in floor — `git-dir`, `rudra-state` and
`catastrophic-command` — exactly as before you pressed it. Nothing you have
forbidden becomes permitted. What changes is that everything Rudra would
have *asked* about now proceeds.

Unlike `--auto`, `!` leaves `execute` and MCP calls working: `--auto` blocks
them unless you opt in with `--allow-shell` / `--allow-mcp`, because nobody
is reading commands in an unattended run. At an approval prompt somebody
plainly is, and that person just said yes.

Every call is still written to the audit log, with `source:
"session-grant-all"`.

There is no key that turns it back off. Restart Rudra. A run that has
already written files cannot un-write them, and a switch that implies
otherwise is worse than no switch.

### `A` and `!` last for the session, not the run

Both live in memory until the process exits — across every task you type at
the REPL prompt, not just the one you were running when you answered.
Neither is ever written to your config. A session can widen what it may do
for its own lifetime and can never quietly widen what you have saved.

A single-shot `rudra "…"` run is one run and one session, so there the two
are the same thing.

For a command, the grant covers the first word — approving `pytest -q` once
also covers `pytest -q tests/unit`. For a file, it covers that exact path.

---

## Allow and deny rules

```toml
[permissions]
allow = ["execute:pytest*", "execute:git status"]
deny  = ["execute:rm -rf *", "write_file:.env", "write_file:**/.git/**"]
```

An entry is either a bare tool name, meaning every call to it, or
`tool:pattern`.

**Tool names are the real ones:** `read_file`, `ls`, `glob`, `grep`,
`write_file`, `edit_file`, `delete`, `execute`, `task`, `call_mcp_tool`.

**Patterns** are globs:

- Starting with `/` — matched against the absolute path.
- Otherwise — matched against the path relative to your project. So
  `write_file:.env` means *this project's* `.env`, not any file anywhere with
  that name.
- For `execute`, the pattern matches the command string. A command is not a
  path, so `execute:pytest*` does match `pytest -q tests/x`.
- For `call_mcp_tool`, the pattern matches the MCP tool's id — `server__tool`,
  from your `.mcp.json`. So `deny = ["call_mcp_tool:*__system_bootstrap"]`
  blocks that tool on every server, and
  `allow = ["call_mcp_tool:kala__*"]` permits one server's whole tool set.
  Like a command, an id is not a path and is never resolved as one.

Paths are resolved before matching, so `src/../.env` is recognised as `.env`.

### Which rule wins

Evaluated top down; first match decides:

```
1. the deny floor          always, every mode
2. permissions.deny
3. grants from `A` this run
4. permissions.allow
5. the mode default
```

**Deny beats allow.** `allow` and `deny` are separate lists, so declaration
order can't arbitrate between them — if you write `allow = ["execute:git*"]`
and `deny = ["execute:git push*"]`, `git push` is denied.

A malformed rule is rejected when Rudra starts, not when the rule is first
hit, so a typo fails immediately rather than mid-run.

---

## The deny floor

Four rules that apply in **every** mode, including `--auto`:

| Rule | Blocks |
|---|---|
| `outside-root` | Writing or deleting outside your project |
| `git-dir` | Writing or deleting inside `.git/` |
| `rudra-state` | Writing or deleting inside `.rudra/` — Rudra's own state, including the `config.toml` that decides the next run's permissions |
| `catastrophic-command` | `rm -rf /`, `mkfs`, `dd of=/dev/*` |

Rudra itself still writes `.rudra/` — facts, the task ledger, `AGENTS.md`,
logs — but none of that goes through an agent's file tools, so `rudra-state`
blocks only the agent.

**Agents cannot read `.rudra/` either.** Its `config.toml` may hold an
`api_key`, so every agent's `read_file`, `ls`, `glob` and `grep` treat the
directory as if it did not exist — `grep "api_key" /` does not find it. That
is not a floor rule and cannot be switched off: it is how the file tools are
built. A shell command can still read it; see
[What this does and doesn't protect](#what-this-does-and-doesnt-protect).

Three of them can be switched off if you have a real reason:

```toml
[permissions]
floor_disable = ["git-dir"]
```

The run then prints a warning naming every disabled rule, and calls the rule
*would* have blocked are still written to the audit log. Turning a rule off
changes what Rudra blocks, not what it tells you.

`outside-root` is deliberately **not** in that list, and naming it is a
configuration error. Writes are confined to your project by the file-access
layer itself, not by this rule — so disabling it would change nothing and
silently redirect the write back into your project. Rejecting the setting is
more honest than accepting one that does nothing.

---

## Running unattended

```bash
rudra --auto "add type hints to utils.py"
```

`--auto` approves every file operation without asking. **Commands stay
disabled** unless you say otherwise:

```bash
rudra --auto --allow-shell "run the test suite and fix what fails"
```

```toml
[tools]
shell_in_auto = true    # the same thing, persisted
```

**This is why `--auto` alone runs no tests.** Rudra's `run_tests` tool is a
shell command underneath and is gated the same way — which is right, because
`pytest` executes the test files the model itself just wrote. So an
unattended run without `--allow-shell` writes code it cannot verify. If you
want Rudra to check its own work, opt in; if you would rather it never ran
anything unsupervised, leave it off and read the diff yourself.

Git works the same way. Rudra resolves the real command and asks about
*that*, so the audit log records `git checkout -b rudra/add-auth` rather
than an opaque tool name, and your existing rules apply to it unchanged:

```toml
[permissions]
allow = ["execute:pytest*"]                    # tests need no approval
deny  = ["execute:git push*"]                  # but never push
```

The split exists because the two are not equally contained. Writes, edits and
deletes are confined to your project directory whatever the model asks for. A
shell command is not — `echo x > /anywhere` does exactly what it says.

This is not hypothetical. In testing, a model told to write outside the
project was denied twice on `write_file`, and then wrote the file through the
shell instead. It was not trying to evade anything; it was routing around an
error, which is what these models do. Hence the separate opt-in.

Naming a command explicitly also counts as opting in — with
`allow = ["execute:pytest*"]`, `pytest` runs under `--auto` without the flag,
because you named it.

`ask` mode is unaffected by any of this. You read each command before it
runs.

### MCP servers work the same way

If you have configured an [MCP server](14-mcp.md), the agent can call its tools —
and an MCP server is a separate program Rudra does not confine, exactly like a
shell command. So it gets its own opt-in:

```bash
rudra --auto --allow-mcp "check this page against our design system"
```

```toml
[mcp]
mcp_in_auto = true      # the same thing, persisted
```

Without it, an unattended run refuses MCP calls and tells the model why, rather
than failing the run. In `ask` mode you approve each call, seeing the server, the
tool and the arguments:

```
╭─ approval required ────────────────────────────────╮
│ MCP  kala → system_bootstrap                       │
╰────────────────────────────────────────────────────╯
  {
    "dir": "/home/you/project",
    "brief": "a calm invoicing tool",
    "choice": 1
  }
```

Naming one tool counts as opting in for that tool, the same way naming a command
does:

```toml
[permissions]
allow = ["call_mcp_tool:kala__system_status"]   # this one, even unattended
deny  = ["call_mcp_tool:*__system_bootstrap"]   # this one, never
```

Looking up what a server offers — `list_mcp_tools`, `describe_mcp_tool` — is never
gated. Those read a server's description of itself and change nothing.

There is a second, narrower control in `[mcp]`: `allow`, `deny` and `readonly` there
decide which tools an agent can *see at all*. That is how the reviewer is kept to
read-only MCP tools. It never grants permission — the rules on this page still
decide every call.

---

## The audit log

`.rudra/run/logs/permissions.jsonl`, one line per decision, written in every
mode:

```json
{"ts":"2026-08-10T14:22:01Z","tool":"execute","arg":"pytest -q",
 "rule":null,"mode":"ask","decision":"approve","source":"prompt"}
{"ts":"...","tool":"write_file","arg":"/etc/hosts",
 "rule":"<floor:outside-root>","mode":"auto","decision":"deny","source":"floor"}
```

Recorded: everything denied, everything you approved or rejected, session
grants, and every mutation under `--auto`. Not recorded: reads that were
allowed by default, which would otherwise bury the signal under hundreds of
`read_file` lines.

The `source` field says *why*: `prompt`, `floor`, `deny`, `allow`,
`session-grant`, `session-grant-all` (you pressed `!`), `auto-shell`,
`mode-default`, or `floor-disabled`.

It lives under `run/`, which Rudra's own `.gitignore` excludes.

---

## What this does and doesn't protect

**It does:**

- Stop any file operation outside your project, in every mode
- Show you a diff before an existing file is overwritten
- Keep commands out of unattended runs unless you opt in
- Keep your API keys out of the environment handed to commands — anything
  matching `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD` or `AWS_*`, plus
  whatever `api_key_env` names, is stripped
- Keep `.rudra/` — and an `api_key` in `.rudra/config.toml` — out of every
  agent's file tools, and stop an agent rewriting it
- Leave a record of everything it allowed

**It does not:**

- Sandbox commands. With `--allow-shell` or in `ask` mode, an approved
  command runs with your user's full access. Real containment needs OS-level
  isolation, which Rudra does not do.
- Stop a command reading files your account can read, including credentials
  on disk — `cat .rudra/config.toml` among them. If that matters, keep the key
  in `~/.config/rudra/config.toml` or name an environment variable with
  `api_key_env`, rather than writing it into the project.
- Verify that generated code is correct. That is `rudra verify`'s job, not
  the permission layer's — the gate decides whether a task is done; this
  layer only decides whether a tool call is allowed to happen.

The practical advice hasn't changed: work on a branch, and read what it
writes.

---

## The security model, stated plainly

The section above is the user-facing summary. This one is the same truth
written as a threat model, and it is what [SECURITY.md](../SECURITY.md) points
at. It is blunt on purpose.

### The trust boundary

Since Step 7, Rudra's backend is
`CompositeBackend(default=LocalShellBackend(...))`. The `execute` tool
therefore runs arbitrary commands **as you**, with your account's full
authority: your files, your network, your SSH keys, whatever is in your
environment.

**There is no sandbox.** Rudra's permission engine decides *whether* a command
runs. It has no say in what that command can reach once it does.

### What the deny floor actually covers

The floor's path rules (`outside-root`, `git-dir`, `rudra-state`) are evaluated against a
resolved filesystem path — and only `write_file`, `edit_file` and `delete`
carry one. `execute` is checked against exactly one floor rule,
`catastrophic-command`, which matches shapes like `rm -rf /`, `mkfs*` and
`dd of=/dev/*`.

So: **the floor's path protection does not apply to shell commands at all.**

### An agent found the way around it, unprompted

This is not hypothetical. During the Step 7 acceptance run, in auto mode, the
model was denied twice on `write_file` outside the project root. It then ran a
shell redirect and succeeded. The audit log is the whole story:

```
deny  floor  write_file  /var/.../escaped.txt
allow        execute     pwd
deny  floor  write_file  ../tmp.../escaped.txt
allow        execute     echo "hello" > /var/.../escaped.txt
```

Two denials, then success by another route, in a single run, with no user
involvement and no prompting toward it.

**This cannot be fixed by pattern-matching commands.** Redirection, `tee`,
`cp`, `mv`, `python -c`, and any script the agent writes and then runs are all
equivalent. Real containment is an operating-system concern — a container,
seccomp, or a genuinely isolated `SandboxBackendProtocol` backend — not a
regular expression.

### What does contain the filesystem tools

Not the floor. `virtual_mode=True` on the backend confines `write_file`,
`edit_file` and `delete` to the project root, and that is the real mechanism.

That difference is exactly why `--auto` ships the way it does: the filesystem
tools are genuinely confined, and `execute` is not.

### The `--auto` rule

`--auto` alone runs the filesystem tools only. `execute` is denied unless you
opt in, in one of three ways:

- `--allow-shell` on the command line, or
- `[tools] shell_in_auto = true` in config, or
- an explicit allow rule naming the command, such as
  `allow = ["execute:pytest*"]` — naming a command *is* consent, which is why
  this check sits **after** the allow rules in `PermissionEngine.decide`.

**The consequence people trip over:** `--auto` alone cannot run its own tests,
because `run_tests` is gated as `execute`. A loop-engineering run wants
`--auto --allow-shell`.

`ask` mode is materially safer and this exposure never existed there — you
read every command string before it runs.

### The compensating control, and its limit

Every gated decision is written to `.rudra/run/logs/permissions.jsonl`, in
every mode, including `--auto`.

It is a record, not a control. It tells you what happened, after it happened.

### Credentials in the run record

Redaction happens where a trace event is **built**, not where it is rendered.
That is what makes the run transcript at `.rudra/run/transcripts/<id>.jsonl`
safe to write by default. Moving redaction into the renderer would silently
refill that file with credentials — which is not theoretical either: a live API
key reached the console during Step 15b's own acceptance run, and the fix was
made at the event boundary for this reason.

It is pattern matching, so it is not a guarantee. A credential with no
recognisable shape, in an innocuously named variable, still passes. Read a log
before you attach it to anything.

### If you need real isolation

Run Rudra in a container or VM that holds nothing you are not willing to lose,
with credentials scoped to the job. Nothing in Rudra's permission layer is a
substitute for that, and it does not claim to be.

---

**Back to:** [README](../README.md) · [Configuration](02-configuration.md) · [CLI Reference](04-cli-reference.md)
