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

# The NAME of an environment variable holding your key — never the key
# itself. Rudra reads the variable at run time and never stores its value.
# api_key_env = "RUDRA_API_KEY"

# Your model's real context window. Without it, history compaction never
# triggers for local models. See TODO.md A1.17 / C1.4a.
# context_tokens = 32768

# Per-role overrides. Anything omitted is inherited from [model.default].
[model.planner]
model = "qwen3:32b"

[model.coder]
model = "qwen3-coder:32b"

[agent]
verbose = true

# How many times the fix loop retries one task before giving up. It
# usually stops sooner: two attempts that fail identically count as no
# progress and stop immediately.
max_fix_attempts = 3

# How many clarifying questions the planner may ask across one run.
# Set to 0 to never ask; unattended runs (--auto) never ask regardless.
max_questions = 5

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
# Workarounds kept for small models. Both off by default — see TODO.md D4.
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
