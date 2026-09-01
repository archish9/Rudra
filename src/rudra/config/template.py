"""The commented scaffold `rudra init` writes.

Kept as a literal rather than generated from DEFAULTS: the comments are the
point, and no TOML writer in the standard library preserves them. That is
also why `rudra config set` does not exist — see TODO.md S6.1.
"""

CONFIG_TEMPLATE = """\
# Rudra configuration.
#
# Load order, later overriding earlier:
#   1. built-in defaults
#   2. ~/.config/rudra/config.toml
#   3. this file
#   4. RUDRA_* environment variables (and .env)
#   5. command-line flags
#
# `rudra config list` shows every effective value and which layer set it.

[model.default]
# ollama | openai_compatible | anthropic | openai | google
provider    = "ollama"
base_url    = "http://localhost:11434"
model       = "qwen3:32b"
temperature = 0.3

# Your API key, if this provider needs one. Two ways, and api_key wins
# where both are set.
#
#   api_key     = "your-key-here"      the key itself
#   api_key_env = "OPENROUTER_API_KEY" the NAME of an environment variable
#                                      holding it
#
# Put api_key in ~/.config/rudra/config.toml, not here. That file is in
# your home directory and in no repository; THIS file is meant to be
# committed, so a key written here gets committed with it. Rudra warns at
# run start if it finds one. Either way the value is never printed: `rudra
# config list` masks it and no error message contains it.
# api_key_env = "RUDRA_API_KEY"

# Your model's real context window. Without it, history compaction never
# triggers for local models. See Documentation/02-configuration.md.
# context_tokens = 32768

# Per-role overrides. Anything omitted is inherited from [model.default].
[model.planner]
model = "qwen3:32b"

[model.coder]
model = "qwen3-coder:32b"

[agent]
# Show assistant prose and untruncated tool payloads in the run trace.
# false still shows every tool call, result and error -- `--verbose` on
# the command line turns this on for one run, `--no-verbose` drops to
# errors only.
verbose = false

# Stream the model's prose token by token into the trace. Off until it has
# been measured against a 32B (D6) -- `--stream` turns it on for one run.
stream_tokens = false

# How many times the fix loop retries one task before giving up. It
# usually stops sooner: two attempts that fail identically count as no
# progress and stop immediately.
max_fix_attempts = 3

# How many clarifying questions the planner may ask across one run.
# Set to 0 to never ask; unattended runs (--auto) never ask regardless.
max_questions = 5

# Write the complete record of every run to
# .rudra/run/logs/debug-<id>.jsonl — every trace event whatever `verbose`
# shows, with payloads uncapped, plus every log record and traceback. This
# is the file to attach to a bug report. One file per run, newest 20 kept,
# and .rudra/run/ is gitignored. `--no-debug` skips it for a single run.
debug_log = true

# When a run ends, copy its evidence — usage.json, the ledger, the debug
# log and the transcript — into $XDG_STATE_HOME/rudra/runs/<project>/<run>/
# (~/.local/state/rudra/runs/... by default). Everything above is written
# inside this project, so deleting the project deletes the record of what
# Rudra did in it; this keeps a copy somewhere that outlives it. Newest 20
# runs per project, and never more than 2 GiB of them.
run_archive = true

[permissions]
# ask | auto | plan
#
#   ask   prompt before each write, edit, delete, or command
#   auto  approve everything (same as --auto / --yolo)
#   plan  write no project files and run no commands
mode  = "ask"

# Rules are "tool" or "tool:pattern", using real tool names:
#   read_file  ls  glob  grep  write_file  edit_file  delete  execute  task
# A pattern starting with / matches the absolute path; otherwise it matches
# the project-relative path. For execute, it matches the command string.
# deny beats allow.
allow = []
deny  = []

# Rudra itself never commits. The agent can still reach git through the shell,
# so uncomment this if you would rather it could not:
# deny = ["execute:git commit*", "execute:git push*", "execute:git reset --hard*"]
#
# What a deny pattern like that catches. It is matched against every command
# in a chain, and against canonicalised spellings of each -- so a respelling
# does not get past it. All of these are caught:
#   git push ...              git  push ...            (extra whitespace)
#   /usr/bin/git push ...     git.exe push ...         (path, .exe)
#   sh -c 'git push ...'      env FOO=1 git push       (wrapper, env(1))
#   GIT_DIR=.git git push     echo hi && git push      (assignment, chaining)
#   git -C . push ...         git --no-pager push      (flag before subcmd)
#   git --git-dir=.git push   git -C . --no-pager push (either, or both)
# Flags are dropped for DENY rules only. An allow rule keeps every flag on
# purpose: dropping them would make an allow = ["execute:git status"] match
# `git -C /other/repo status`, which is a different repository -- so that
# spelling still asks rather than being authorised.
# A patternless deny = ["execute"] blocks the shell outright, and
# [tools] shell = false removes the tool.

# Built-in rules denied in every mode, including --auto. Name one here to
# switch it off; the run prints which are disabled, and calls they would
# have blocked are still written to the audit log.
#   git-dir               write or delete under .git/
#   catastrophic-command  rm -rf /, mkfs, dd of=/dev/*
# ("outside-root" is not listed and is rejected here: writes are confined to
#  the project by the backend, not by this rule, so there is nothing to
#  disable. Shell commands are not confined by it either.)
floor_disable = []

[tools]
# false removes the execute tool entirely: no tests, no linters, no git.
shell = true

# Whether --auto may run commands. Off by default: in unattended mode nobody
# reads the command before it runs, and a shell command can write anywhere
# you can — Rudra's confinement covers the file-writing tools, not the shell.
# Turn it on if you want unattended test runs, knowing that.
shell_in_auto = false

# Create a `rudra/<slug>` branch before a run, named after your task, so the
# agent's work is not on your branch. Only fires inside a git repository, from
# a clean tree, with a branch checked out — otherwise it says why and carries
# on where you are. It never fails a run.
auto_branch = false

# How long a test command may run before Rudra kills it, in seconds. Angular's
# Karma builder hangs forever when no browser is installed, and inside a fix
# loop a hang stalls the loop instead of failing a round.
test_timeout = 600

[compat]
# Workarounds kept for small models. Both off by default.
task_anchor   = false
sandbox_paths = false

# [skills]
# Which of the bundled superpowers skills enter an agent's prompt index.
# Leave the section out for the shipped set; `enabled = []` turns skills off
# for every agent. A name that is not a vendored skill is an error, not a
# silent skip. See Documentation/12-skills.md.
# enabled = ["brainstorming", "test-driven-development", "systematic-debugging"]

[mcp]
enabled     = true       # false disables MCP entirely
mcp_in_auto = false      # may an unattended run call MCP tools? (--allow-mcp)
timeout     = 60         # seconds per MCP call
# allow / deny are server__tool glob patterns; deny beats allow.
# allow = ["kala__*"]
# deny  = ["*__system_bootstrap"]
# Ids a read-only subagent (the reviewer) may see. Visibility only —
# the permission engine still decides every call.
# readonly = ["kala__verify", "kala__system_status"]
# disabled_servers = []  # names from .mcp.json to leave unloaded

# Servers themselves live in .mcp.json, in Claude Code's schema, so an
# existing config can be pasted in unchanged. `rudra mcp add` writes it.

[memory]
# Long-term memory: what this project decided, finished, was blocked by, and
# prefers. Local, no API key, and read back into every agent's prompt.
# `chroma` is the default and the tested one; the rest are MemPalace backends
# passed straight through. One key only — memory cannot be switched off, and
# the palace always lives at .rudra/memory/palace/. See
# Documentation/15-memory.md.
backend = "chroma"       # chroma | sqlite | milvus | qdrant | pgvector
"""

__all__ = ["CONFIG_TEMPLATE"]
