# RudraAnvil

> **Rudra** (रुद्र) — The fierce, storm-like form of Shiva; the howler/roarer  
> **Anvil** — The blacksmith's forge where raw metal is hammered into perfection  
>  
> *The roaring storm that hammers and purifies code*

**RudraAnvil** is an autonomous coding agent CLI that helps you build, debug, and maintain software projects. It uses AI (via Ollama) to understand your requests, plan the work, and execute it—all while you stay in control.

---

## Table of Contents

- [What is RudraAnvil?](#what-is-rudraanvil)
- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Commands](#commands)
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

## What is RudraAnvil?

RudraAnvil is a **CLI-based autonomous coding agent** that acts as your AI pair programmer. Unlike simple code generators, it:

- **Understands context** — Scans your existing project before making changes
- **Plans before acting** — Creates a dynamic todo list and works through it systematically
- **Uses tools** — Reads/writes files, runs tests, lints code, manages git
- **Learns from errors** — If something fails, it adds fix tasks and iterates
- **Saves progress** — Checkpoints let you resume interrupted sessions

Think of it as having a tireless developer who can work on your codebase while you review the results.

### What Can It Do?

| Task | Command |
|------|---------|
| Create a new FastAPI app from scratch | `rudraanvil build "Build a FastAPI app with JWT auth and PostgreSQL"` |
| Add a feature to existing code | `rudraanvil build "Add pagination to the todos endpoint"` |
| Fix a bug | `rudraanvil fix "Login returns 500 when password is wrong"` |
| Have an ongoing development conversation | `rudraanvil chat` |
| Review code for security issues | `rudraanvil review --focus security` |

---

## Features

| Feature | Description |
|---------|-------------|
| 🔨 **Build** | Create new projects or enhance existing ones |
| 💬 **Chat** | Interactive REPL for ongoing development |
| 🔧 **Fix** | Debug and fix specific issues |
| ✏️ **Edit** | Targeted file modifications |
| 🔍 **Review** | Code quality and security audits |
| 💡 **Suggest** | Improvement proposals without applying |
| ▶️ **Resume** | Continue interrupted sessions |
| 👁️ **Watch** | Real-time file monitoring |

---

## Installation

### Prerequisites

- **Python 3.12+**
- **Ollama** running locally with the `gpt-oss:20b` model
- **Git** (for cloning the repo)

### Step 1: Clone the Repository

```bash
git clone https://github.com/archish/RudraAnvil.git
cd RudraAnvil
```

### Step 2: Choose Your Installation Method

#### Option A: pipx (Recommended - Global Access, No Activation)

[pipx](https://pypa.github.io/pipx/) installs Python CLI tools in isolated environments while making them globally available. This is the cleanest approach for CLI tools.

**On Debian/Ubuntu/Linux Mint:**

If you get an `externally-managed-environment` error, install pipx via apt:

```bash
# Install pipx from system package manager
sudo apt install pipx

# Ensure pipx path is configured
pipx ensurepath

# Restart your terminal or run:
source ~/.bashrc

# Install RudraAnvil in editable mode
pipx install -e .

# Verify installation
rudraanvil --version
```

**On macOS/Other Systems:**

```bash
# Install pipx
pip install --user pipx
pipx ensurepath
# Restart your terminal

# Install RudraAnvil
pipx install -e .

# Verify installation
rudraanvil --version
```

✅ **Benefits:**
- Works from any directory
- No virtual environment activation needed
- Isolated from system Python
- Perfect for development (editable mode)

#### Option B: Virtual Environment (Traditional)

If you prefer traditional virtual environments or don't have sudo access:

```bash
# Create virtual environment
python3 -m venv .venv

# Activate it
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in editable mode
pip install -e .

# Verify installation
rudraanvil --version
```

⚠️ **Important:** With venv, you must activate the environment in each new terminal session:
```bash
source ~/path/to/RudraAnvil/.venv/bin/activate
```

### Step 3: Set Up Ollama

RudraAnvil requires Ollama to be running with the appropriate model:

```bash
# Start Ollama server (in a separate terminal)
ollama serve

# Pull the required model
ollama pull gpt-oss:20b

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

### Installation Complete! 🎉

Test your installation:

```bash
# Check version
rudraanvil --version

# Try a simple command
cd ~/my-test-project
rudraanvil build "Create a hello world Python script"
```

---

## Quick Start

```bash
# Navigate to your project (or create a new folder)
cd my-project

# Build something new
rudraanvil build "Create a Flask REST API with SQLite for a todo app"

# Or enhance an existing project
rudraanvil build "Add user authentication with JWT tokens"

# Start interactive mode for ongoing work
rudraanvil chat
```

---

## Commands

### build

**Create new projects or enhance existing ones.**

This is the primary "workhorse" command. Give it a task description, and it will plan, execute, and deliver.

```bash
rudraanvil build "Your task description here"
```

**Options:**
| Flag | Description |
|------|-------------|
| `--project-dir`, `-d` | Target directory (defaults to current) |
| `--dry-run` | Preview changes without writing files |
| `--verbose`, `-V` | Show detailed progress |
| `--max-agents` | Limit concurrent sub-agents (default: 6) |

**Examples:**
```bash
# Create a new FastAPI app
rudraanvil build "Build a FastAPI app with JWT authentication, PostgreSQL database, and CRUD endpoints for a todo list"

# Add to existing project
rudraanvil build "Add rate limiting to all endpoints using slowapi"

# Preview what would be created
rudraanvil build "Add email verification" --dry-run
```

---

### chat

**Interactive mode for ongoing development.**

Enter a REPL-like session where you can have an ongoing conversation with the agent. It remembers context across messages.

```bash
rudraanvil chat
```

**In-session commands:**
| Command | Action |
|---------|--------|
| `/exit` or `/quit` | Leave chat mode |
| `/status` | Show current todo list |
| `/save` | Force save checkpoint |
| `/tree` | Show project file tree |

**Example session:**
```
$ rudraanvil chat
💬 RudraAnvil Chat
Path: /home/user/my-project

rudraanvil> Add pagination to the GET /todos endpoint, 20 per page
→ Planning...
→ Working on: Add pagination parameters
→ Working on: Update query logic
✓ Done

rudraanvil> Tests are failing now — please fix
→ Working on: Debug test failures
→ Found issue in test_todos.py
✓ Fixed

rudraanvil> /exit
```

---

### fix

**Debug and fix specific issues.**

Focused on diagnosing and repairing bugs. More targeted than `build`.

```bash
rudraanvil fix "Description of the issue"
```

**Options:**
| Flag | Description |
|------|-------------|
| `--file`, `-f` | Limit scope to a specific file |
| `--dry-run` | Preview fix without applying |

**Examples:**
```bash
# General fix
rudraanvil fix "JWT token refresh returns 401 unauthorized"

# Scoped to a file
rudraanvil fix "Login validation not working" --file app/routers/auth.py
```

---

### edit

**Make targeted modifications to a specific file.**

Use this for precise, surgical edits instead of full rebuilds.

```bash
rudraanvil edit <file> "What to change"
```

**Options:**
| Flag | Description |
|------|-------------|
| `--preview`, `-p` | Show diff before applying |

**Examples:**
```bash
rudraanvil edit app/routers/todos.py "Add limit and offset query parameters for pagination"

rudraanvil edit models/user.py "Add email_verified boolean field with default False"

# Preview first
rudraanvil edit main.py "Add CORS middleware" --preview
```

---

### review

**Analyze code for quality, security, and best practices.**

Outputs a report without making any changes.

```bash
rudraanvil review
```

**Options:**
| Flag | Description |
|------|-------------|
| `--focus`, `-f` | Focus area: `security`, `performance`, `style` |

**Examples:**
```bash
# Full review
rudraanvil review

# Security-focused
rudraanvil review --focus security

# Performance audit
rudraanvil review --focus performance
```

**Output includes:**
- Security vulnerabilities
- Performance bottlenecks
- Code quality issues
- Best practice violations
- Suggested improvements

---

### suggest

**Propose improvements without applying them.**

Like `build`, but outputs suggestions/diffs instead of modifying files.

```bash
rudraanvil suggest "What improvement to consider"
```

**Examples:**
```bash
rudraanvil suggest "Make the API more secure against SQL injection"

rudraanvil suggest "Refactor the authentication module for better testability"

rudraanvil suggest "Add caching to improve performance"
```

---

### resume

**Continue an interrupted session.**

RudraAnvil automatically saves checkpoints. Use this to pick up where you left off.

```bash
# List available sessions
rudraanvil resume --list

# Resume a specific session
rudraanvil resume abc123def
```

**Output:**
```
Available Sessions
┏━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━
┃ Session ID  ┃ Task                         ┃ Updated           
┡━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━
│ abc123def   │ Build a FastAPI app with...  │ 2026-02-01T16:30  
│ xyz789ghi   │ Add authentication...        │ 2026-02-01T14:15  
└─────────────┴──────────────────────────────┴───────────────────
```

---

### watch

**Monitor your project for changes in real-time.**

Watches files and provides instant feedback (linting, syntax checks).

```bash
rudraanvil watch
```

**Options:**
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
│        "Build a FastAPI app with JWT authentication"        │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Main Agent (Supervisor)                  │
│  • Parses request                                           │
│  • Creates todo list                                        │
│  • Delegates to sub-agents                                  │
│  • Manages checkpoints                                      │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
   ┌────────────┐  ┌────────────┐  ┌────────────┐
   │ Architect  │  │  Backend   │  │  Testing   │
   │ Sub-Agent  │  │ Sub-Agent  │  │ Sub-Agent  │
   └────────────┘  └────────────┘  └────────────┘
          │               │               │
          └───────────────┴───────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Virtual Filesystem                         │
│  • All changes happen in memory first                        │
│  • Synced to disk after completion                           │
│  • Respects .gitignore                                       │
└─────────────────────────────────────────────────────────────┘
```

### Workflow

1. **Request** — You give a task via CLI
2. **Planning** — Agent creates a structured todo list
3. **Delegation** — Complex tasks spawn 2-6 specialized sub-agents
4. **Execution** — Agents work through todos, using tools (file ops, code execution, git)
5. **Iteration** — If errors occur, new fix tasks are added automatically
6. **Sync** — Changes are written to disk (with backup)
7. **Commit** — Optionally commits to git

### Sub-Agent Types

| Type | Role |
|------|------|
| Architect | Project structure, scaffolding |
| Backend | Server-side logic, APIs |
| Frontend | UI components, client code |
| Testing | Unit tests, integration tests |
| Documentation | README, docstrings |
| Debugger | Error analysis, fixes |
| Security | Vulnerability review |
| DevOps | Docker, CI/CD |

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and customize:

```bash
# Ollama Configuration
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:20b
OLLAMA_TEMPERATURE=0.7
OLLAMA_TIMEOUT=300

# Agent Configuration
MAX_AGENTS=6          # Max concurrent sub-agents
MAX_ITERATIONS=100    # Prevent infinite loops
CHECKPOINT_INTERVAL=5 # Save every N iterations

# Optional: Web Search
TAVILY_API_KEY=       # For research tool
USE_DUCKDUCKGO=true   # Fallback search
```

### Project-Specific

RudraAnvil stores checkpoints in `.rudraanvil/` inside your project. Add to `.gitignore`:

```
.rudraanvil/
```

---

## Development Workflow

### Making Code Changes

Since you installed with `-e` (editable mode), code changes are reflected **immediately** without reinstalling:

```bash
# 1. Edit any source file
vim src/rudraanvil/cli.py

# 2. Test immediately - changes are live!
rudraanvil --version
```

The `-e` flag creates a symlink to your source code, so Python imports directly from your development directory.

### When to Reinstall

You **only** need to reinstall if you modify:

#### 1. Dependencies (pyproject.toml or requirements.txt)

```bash
# With pipx
cd ~/path/to/RudraAnvil
pipx install -e . --force

# With venv
source .venv/bin/activate
pip install -e .
```

#### 2. Entry Points (CLI command definitions)

If you change the `[project.scripts]` section in `pyproject.toml`:

```bash
# Same as above - reinstall
pipx install -e . --force  # or pip install -e .
```

### Typical Development Cycle

```bash
# Edit code
vim src/rudraanvil/agent/main.py

# Test immediately (no reinstall needed)
rudraanvil chat

# Add a new dependency
echo "new-package==1.0.0" >> requirements.txt

# Now reinstall to pick up new dependency
pipx install -e . --force
```

### Running Tests

```bash
# With pipx (from anywhere)
cd ~/path/to/RudraAnvil
pytest

# With venv (activate first)
source .venv/bin/activate
pytest
```

---

## Troubleshooting

### "command not found: rudraanvil"

**With pipx:**
```bash
# Ensure path is configured
pipx ensurepath
source ~/.bashrc  # or restart terminal

# Verify installation
pipx list
```

**With venv:**
```bash
# Activate the virtual environment
source ~/path/to/RudraAnvil/.venv/bin/activate

# Verify installation
which rudraanvil
```

### "externally-managed-environment" error

This is common on Debian/Ubuntu systems. **Solution:**

```bash
# Install pipx via apt instead of pip
sudo apt install pipx
pipx ensurepath
source ~/.bashrc

# Then install RudraAnvil
cd ~/path/to/RudraAnvil
pipx install -e .
```

**Alternative:** Use the venv method (no sudo required).

### "Error calling LLM" or "Connection refused"

1. **Check Ollama is running:**
   ```bash
   # Start Ollama server
   ollama serve
   ```

2. **Verify model is available:**
   ```bash
   ollama list
   # Should show gpt-oss:20b
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

**Never use `sudo pip`** - it can break your system Python.

**Solutions:**
- Use `sudo apt install pipx` (for pipx itself only)
- Use venv method (no sudo needed)
- Use `pip install --user` (not recommended for development)

### Changes not reflecting after editing code

**Check installation mode:**
```bash
# With pipx
pipx list --verbose
# Should show "editable" next to rudraanvil

# With venv
pip show rudraanvil
# Should show: Editable project location: /path/to/RudraAnvil
```

**If not editable, reinstall:**
```bash
pipx install -e . --force
# or
pip install -e .
```

### Slow on first run

The first run may be slower as dependencies are loaded and Ollama initializes the model. Subsequent runs are much faster.

### Model download is slow

The `gpt-oss:20b` model is large (~12GB). Download time depends on your internet connection:

```bash
# Check download progress
ollama pull gpt-oss:20b
```

---

## License

Apache 2.0

---

<p align="center">
  <i>Built with 🔥 by the storm that purifies code</i>
</p>
