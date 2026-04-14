# Rudra

> **Rudra** (रुद्र) — The fierce, storm-like form of Shiva; the howler/roarer
> **Anvil** — The blacksmith's forge where raw metal is hammered into perfection
>
> *The roaring storm that hammers and purifies code*

**Rudra** is an autonomous coding agent CLI that helps you build, debug, and maintain software projects. It uses AI (via Ollama) to understand your requests, plan the work, and execute it—all while you stay in control.

Just describe what you want:

```bash
rudra "Build a FastAPI app with JWT authentication"
```

No need to pick a subcommand. Rudra reads your intent and acts.

---

## Table of Contents

- [What is Rudra?](#what-is-rudra)
- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
  - [Direct Prompt (Recommended)](#direct-prompt-recommended)
  - [Interactive Mode](#interactive-mode)
  - [Explicit Subcommands (Advanced)](#explicit-subcommands-advanced)
- [Subcommand Reference](#subcommand-reference)
  - [build](#build)
  - [chat](#chat)
  - [fix](#fix)
  - [edit](#edit)
  - [review](#review)
  - [suggest](#suggest)
  - [resume](#resume)
  - [watch](#watch)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## What is Rudra?

Rudra is a **CLI-based autonomous coding agent** that acts as your AI pair programmer. Unlike simple code generators, it:

- **Understands context** — Scans your existing project before making changes
- **Plans before acting** — Creates a dynamic checklist and works through it systematically
- **Uses tools** — Reads/writes files, searches code, manages project structure
- **Learns from errors** — If something fails, it adds fix tasks and iterates
- **Saves progress** — Checkpoints let you resume interrupted sessions
- **Understands intent** — No need to specify a mode; just describe what you want

Think of it as having a tireless developer who can work on your codebase while you review the results.

### What Can It Do?

| Task | Command |
|------|---------|
| Create a new FastAPI app from scratch | `rudra "Build a FastAPI app with JWT auth and PostgreSQL"` |
| Add a feature to existing code | `rudra "Add pagination to the todos endpoint"` |
| Fix a bug | `rudra "Login returns 500 when password is wrong"` |
| Have an ongoing conversation | `rudra` (interactive mode) |
| Review code for security issues | `rudra review --focus security` |
| Make a targeted file edit | `rudra edit app/main.py "Add CORS middleware"` |

---

## Features

| Feature | Description |
|---------|-------------|
| **Direct Prompt** | Just run `rudra "your task"` — no subcommand needed |
| **Interactive REPL** | Run `rudra` with no args for a persistent chat session |
| **Auto Intent Detection** | The LLM understands whether to build, fix, edit, or explain |
| **Persistent Sessions** | Checkpoints let you resume exactly where you left off |
| **Project Context** | Scans your project tree and respects your tech stack |
| **Explicit Subcommands** | Power users can still use `build`, `fix`, `edit`, etc. |

---

## Installation

### Prerequisites

- **Python 3.12+**
- **Ollama** running locally with the `qwen3:14b` model
- **Git** (for cloning the repo)

### Step 1: Clone the Repository

```bash
git clone https://github.com/archish/Rudra.git
cd Rudra
```

### Step 2: Choose Your Installation Method

#### Option A: pipx (Recommended — Global Access, No Activation)

[pipx](https://pypa.github.io/pipx/) installs Python CLI tools in isolated environments while making them globally available. This is the cleanest approach for CLI tools.

**On Debian/Ubuntu/Linux Mint:**

```bash
# Install pipx from system package manager
sudo apt install pipx

# Ensure pipx path is configured
pipx ensurepath

# Restart your terminal or run:
source ~/.bashrc

# Install Rudra in editable mode
pipx install -e .

# Verify installation
rudra --version
```

**On macOS/Other Systems:**

```bash
# Install pipx
pip install --user pipx
pipx ensurepath
# Restart your terminal

# Install Rudra
pipx install -e .

# Verify installation
rudra --version
```

Benefits of pipx:
- Works from any directory
- No virtual environment activation needed
- Isolated from system Python
- Perfect for development (editable mode)

#### Option B: Virtual Environment (Traditional)

```bash
# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in editable mode
pip install -e .

# Verify installation
rudra --version
```

> **Note:** With venv, you must activate the environment in each new terminal session:
> ```bash
> source ~/path/to/Rudra/.venv/bin/activate
> ```

### Step 3: Set Up Ollama

Rudra requires Ollama to be running with the appropriate model:

```bash
# Start Ollama server (in a separate terminal)
ollama serve

# Pull the required model
ollama pull qwen3:14b

# Verify model is available
ollama list
```

### Step 4: Configure Environment (Optional)

```bash
# Copy example configuration
cp .env.example .env

# Edit if needed (defaults work for most cases)
nano .env
```

### Installation Complete!

Test your installation:

```bash
# Check version
rudra --version

# Try a simple task
cd ~/my-test-project
rudra "Create a hello world Python script"
```

---

## Quick Start

```bash
# Navigate to your project (or create a new folder)
cd my-project

# Run a task directly
rudra "Create a Flask REST API with SQLite for a todo app"

# Or start interactive mode for ongoing work
rudra
```

---

## Usage

### Direct Prompt (Recommended)

The simplest way to use Rudra — just describe what you want:

```bash
rudra "your task or question here"
```

Rudra will automatically determine the right approach:
- If you're asking it to **create or build** something, it will plan and write files
- If you're asking it to **fix** something, it will diagnose and repair
- If you're asking it to **edit** something, it will make targeted changes
- If you're asking a **question**, it will answer without touching files

**Examples:**

```bash
# Build something new
rudra "Create a FastAPI app with JWT authentication and PostgreSQL"

# Add a feature
rudra "Add rate limiting to all API endpoints using slowapi"

# Fix a bug
rudra "The login endpoint returns 500 when the password is wrong"

# Ask about the code
rudra "Explain how the authentication middleware works"

# Refactor
rudra "Refactor the database module to use SQLAlchemy async"
```

**Common options:**

| Flag | Description |
|------|-------------|
| `--project-dir`, `-d` | Target directory (defaults to current) |
| `--dry-run` | Preview changes without writing files |
| `--verbose`, `-V` | Show detailed agent trace |
| `--max-agents` | Limit concurrent sub-agents (default: 6) |

```bash
# Work on a different directory
rudra "Add tests for the auth module" --project-dir ~/projects/myapp

# Preview without writing
rudra "Add email verification" --dry-run

# Show detailed output
rudra "Refactor the user model" --verbose
```

---

### Interactive Mode

Run `rudra` with no arguments to enter an interactive REPL — useful for ongoing work where you want to have a back-and-forth conversation with the agent.

```bash
rudra
```

The agent remembers context across messages within the session. Use this when:
- You're iterating on a feature across multiple steps
- You want to ask follow-up questions
- You're doing exploratory work where the next step depends on the previous result

**Example session:**

```
$ rudra
Rudra — Interactive mode
Path: /home/user/my-project

rudra> Add pagination to the GET /todos endpoint, 20 per page
→ Planning...
→ Working on: Add pagination parameters
→ Working on: Update query logic
✓ Done

rudra> Tests are failing now — please fix
→ Analyzing test failures...
→ Found issue in test_todos.py
✓ Fixed

rudra> /tree
src/
├── app/
│   ├── main.py
│   └── routers/
│       └── todos.py
└── tests/
    └── test_todos.py

rudra> /exit
Goodbye!
```

**In-session commands:**

| Command | Action |
|---------|--------|
| `/exit` or `/quit` | Leave interactive mode |
| `/tree` | Show project file tree |

---

### Explicit Subcommands (Advanced)

For users who want precise control, all original subcommands are still available. These are useful when you want to:
- Guarantee a specific mode (e.g., always `--dry-run` for a review)
- Use mode-specific flags (e.g., `--focus` for `review`, `--file` for `fix`)
- Script Rudra in CI or shell pipelines

```bash
rudra build "Create a FastAPI app"
rudra fix "Login returns 500" --file app/auth.py
rudra edit app/main.py "Add CORS middleware"
rudra review --focus security
```

See [Subcommand Reference](#subcommand-reference) below for full details.

---

## Subcommand Reference

### build

Create new projects or enhance existing ones.

```bash
rudra build "Your task description"
```

| Flag | Description |
|------|-------------|
| `--project-dir`, `-d` | Target directory (defaults to current) |
| `--dry-run` | Preview changes without writing files |
| `--verbose`, `-V` | Show detailed progress |
| `--max-agents` | Limit concurrent sub-agents (default: 6) |

**Examples:**

```bash
# Create a new project
rudra build "Build a FastAPI app with JWT authentication, PostgreSQL, and CRUD endpoints"

# Enhance existing project
rudra build "Add rate limiting to all endpoints using slowapi"

# Preview without writing
rudra build "Add email verification" --dry-run
```

---

### chat

Interactive REPL for ongoing development. Equivalent to running `rudra` with no arguments, but scoped to the `chat` system prompt.

```bash
rudra chat
```

| Command | Action |
|---------|--------|
| `/exit` or `/quit` | Leave chat mode |
| `/tree` | Show project file tree |

---

### fix

Debug and fix a specific issue. More targeted than `build` — focuses on diagnosing and repairing a single problem.

```bash
rudra fix "Description of the issue"
```

| Flag | Description |
|------|-------------|
| `--file`, `-f` | Limit scope to a specific file |
| `--dry-run` | Preview fix without applying |

**Examples:**

```bash
# General fix
rudra fix "JWT token refresh returns 401 unauthorized"

# Scoped to a file
rudra fix "Login validation not working" --file app/routers/auth.py
```

---

### edit

Make a targeted modification to a specific file. Use this for surgical edits instead of full task runs.

```bash
rudra edit <file> "What to change"
```

| Flag | Description |
|------|-------------|
| `--preview`, `-p` | Show diff before applying |

**Examples:**

```bash
rudra edit app/routers/todos.py "Add limit and offset query parameters for pagination"

rudra edit models/user.py "Add email_verified boolean field with default False"

# Preview first
rudra edit main.py "Add CORS middleware" --preview
```

---

### review

Analyze code for quality, security, and best practices. Outputs a report — does not modify any files.

```bash
rudra review
```

| Flag | Description |
|------|-------------|
| `--focus`, `-f` | Focus area: `security`, `performance`, `style` |

**Examples:**

```bash
# Full review
rudra review

# Security-focused
rudra review --focus security

# Performance audit
rudra review --focus performance
```

**Output includes:**
- Security vulnerabilities
- Performance bottlenecks
- Code quality issues
- Best practice violations
- Suggested improvements

---

### suggest

Propose improvements without applying them. Like `build`, but outputs suggestions and code snippets instead of writing files.

```bash
rudra suggest "What improvement to consider"
```

**Examples:**

```bash
rudra suggest "Make the API more secure against SQL injection"

rudra suggest "Refactor the authentication module for better testability"

rudra suggest "Add caching to improve performance"
```

---

### resume

Rudra automatically saves checkpoints after every session. Resuming is automatic — just run any command in the same project directory and the agent continues from where it left off.

The `resume` command shows the current session status:

```bash
rudra resume
```

Session state is stored in `.rudra/checkpoints.db` and identified by a stable session ID in `.rudra/session_id.txt`.

---

### watch

Monitor your project for changes in real-time. Watches files and provides instant feedback (linting, syntax checks).

```bash
rudra watch
```

| Flag | Description |
|------|-------------|
| `--auto-apply` | Automatically apply suggested fixes (use with caution) |

Press `Ctrl+C` to stop watching.

---

## How It Works

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      User Request                           │
│          rudra "Build a FastAPI app with JWT"          │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    CLI Layer (cli.py)                        │
│  • Parses prompt or subcommand                              │
│  • Loads project context (.rudra/project.json)         │
│  • Prompts for tech stack if not yet configured             │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                 Main Agent (main_agent.py)                   │
│  • Builds system prompt (auto / build / fix / edit / ...)   │
│  • Connects to Ollama LLM                                   │
│  • Opens SQLite checkpoint (resumes prior session)          │
│  • Streams tool calls in real time                          │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   ┌────────────┐  ┌────────────┐  ┌────────────┐
   │ update_plan│  │ write_file │  │  edit_file │
   │  read_plan │  │  read_file │  │  ls / glob │
   └────────────┘  └────────────┘  └────────────┘
          │               │               │
          └───────────────┴───────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Virtual Filesystem                         │
│  • All changes anchored to project root                     │
│  • Respects .gitignore                                      │
│  • Tracks created and modified files                        │
└─────────────────────────────────────────────────────────────┘
```

### Workflow

1. **Request** — You give a task via prompt or subcommand
2. **Context** — Agent reads the project file tree and your tech stack config
3. **Planning** — Agent calls `update_plan()` to create a checklist in `.rudra/PLAN.md`
4. **Execution** — Agent works through the plan, using file tools to read/write/edit
5. **Verification** — Agent reads back files it wrote to confirm correctness
6. **Checkpointing** — Progress is saved to SQLite after every step; interrupted sessions resume automatically

### Session Persistence

Rudra uses a stable session ID (derived from your project path) to maintain continuity across CLI invocations. Every `build`, `fix`, `edit`, or interactive session in the same directory shares the same checkpoint thread. This means:

- The agent remembers what it did in previous sessions
- You never lose progress if a run is interrupted
- `rudra resume` shows you the current session status

Session files are stored in `.rudra/` inside your project directory. Add this to `.gitignore`:

```
.rudra/
```

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and customize:

```bash
# Ollama Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:14b
OLLAMA_TEMPERATURE=0.7
OLLAMA_TIMEOUT=300

# Agent Configuration
MAX_AGENTS=6          # Max concurrent sub-agents
MAX_ITERATIONS=100    # Prevent infinite loops
CHECKPOINT_INTERVAL=5 # Save every N iterations
VERBOSE=false         # Show detailed agent trace by default

# Optional: Web Search
TAVILY_API_KEY=       # For research tool
USE_DUCKDUCKGO=true   # Fallback search
```

### Project Context

On first use in a project directory, Rudra will ask you for your tech stack:

```
Project Context Required
Primary Programming Language: Python
Frameworks/Libraries (optional): FastAPI
Database (optional): PostgreSQL
Any additional architecture rules? (optional):
```

This is saved to `.rudra/project.json` and used in every subsequent session to ensure generated code matches your stack. You can delete this file to re-configure.

---

## Development Workflow

### Making Code Changes

Since you installed with `-e` (editable mode), code changes are reflected **immediately** without reinstalling:

```bash
# 1. Edit any source file
vim src/rudra/cli.py

# 2. Test immediately — changes are live
rudra --version
```

### When to Reinstall

You **only** need to reinstall if you modify:

**Dependencies (`pyproject.toml` or `requirements.txt`):**

```bash
# With pipx
cd ~/path/to/Rudra
pipx install -e . --force

# With venv
source .venv/bin/activate
pip install -e .
```

**Entry Points (`[project.scripts]` in `pyproject.toml`):**

```bash
pipx install -e . --force  # or pip install -e .
```

### Running Tests

```bash
# With pipx (from anywhere)
cd ~/path/to/Rudra
pytest

# With venv (activate first)
source .venv/bin/activate
pytest
```

---

## Troubleshooting

### "command not found: rudra"

**With pipx:**
```bash
pipx ensurepath
source ~/.bashrc  # or restart terminal
pipx list         # verify installation
```

**With venv:**
```bash
source ~/path/to/Rudra/.venv/bin/activate
which rudra
```

### "externally-managed-environment" error

Common on Debian/Ubuntu. Fix:

```bash
sudo apt install pipx
pipx ensurepath
source ~/.bashrc
cd ~/path/to/Rudra
pipx install -e .
```

**Alternative:** Use the venv method (no sudo required).

### "Error calling LLM" or "Connection refused"

1. **Start Ollama:**
   ```bash
   ollama serve
   ```

2. **Verify model is available:**
   ```bash
   ollama list
   # Should show qwen3:14b
   ```

3. **Check configuration:**
   ```bash
   cat .env | grep OLLAMA
   # Should show: OLLAMA_BASE_URL=http://localhost:11434
   ```

4. **Test Ollama directly:**
   ```bash
   curl http://localhost:11434/api/tags
   ```

### "Permission denied" during install

Never use `sudo pip` — it can break your system Python.

- Use `sudo apt install pipx` (for pipx itself only)
- Use venv method (no sudo needed)

### Changes not reflecting after editing code

Check installation mode:

```bash
# With pipx
pipx list --verbose
# Should show "editable" next to rudra

# With venv
pip show rudra
# Should show: Editable project location: /path/to/Rudra
```

If not editable, reinstall:

```bash
pipx install -e . --force
# or
pip install -e .
```

### Slow on first run

The first run may be slower as dependencies are loaded and Ollama initializes the model. Subsequent runs are much faster.

### Model download is slow

The `qwen3:14b` model is large (~12GB). Download time depends on your connection:

```bash
ollama pull qwen3:14b
```

---

## License

Apache 2.0

---

<p align="center">
  <i>Built with fire by the storm that purifies code</i>
</p>
