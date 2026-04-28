# Dual-Model Architecture: Planner + Coder (v2)

## Goal

Split Rudra's single-model architecture into two specialized models communicating via the filesystem:

- **Planner** (`OLLAMA_MODEL_PLANNER`): Analyzes tasks, creates detailed `PLAN.md`, writes per-file task assignments to `current_task.md`
- **Coder** (`OLLAMA_MODEL_CODER`): Reads the task assignment, writes the code file, stops

No verification phase for now — will be added later with a custom language-agnostic tool.

---

## Design Decisions (Confirmed)

| Decision | Choice | Rationale |
|---|---|---|
| Model names | Fully configurable via env vars, no hardcoded model names | User switches models frequently |
| Orchestration | Sequential (planner → coder loop), NOT subagent delegation | Clean context windows, filesystem as communication channel |
| Iteration limits | Use deepagents default (`recursion_limit=1000`) | No custom per-file limits needed; existing loop guards handle runaways |
| Verification | **Dropped for now** | Will be added later with custom language-agnostic verification tools |
| Retry logging | Rich CLI output showing phase transitions and coder retries | User wants visibility into failures |

---

## Proposed Changes

### Component 1: Configuration

---

#### [MODIFY] [config.py](file:///c:/laragon/www/AI-ML/RudraAnvil/src/rudra/config.py)

Add `model_planner` and `model_coder` fields to `OllamaConfig`. Keep `model` as a backward-compatible fallback:

```python
@dataclass
class OllamaConfig:
    base_url: str = field(default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"))
    # Legacy single-model fallback
    model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    # Dual-model config — falls back to OLLAMA_MODEL if not set
    model_planner: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL_PLANNER") or os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    model_coder: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL_CODER") or os.getenv("OLLAMA_MODEL", "qwen3:14b"))
    temperature: float = ...
    timeout: int = ...
    num_predict: int = ...
```

Fallback logic: `OLLAMA_MODEL_PLANNER` → `OLLAMA_MODEL` → `"qwen3:14b"`. Same for coder. This means if a user only sets `OLLAMA_MODEL`, both agents use the same model (backward compatible). If they set the specific vars, those take precedence.

#### [MODIFY] [.env.example](file:///c:/laragon/www/AI-ML/RudraAnvil/.env.example)

```diff
 # Ollama Configuration
 OLLAMA_BASE_URL=http://localhost:11434
 OLLAMA_MODEL=qwen3:14b
+# Dual-model: set these to use different models for planning vs coding
+# Falls back to OLLAMA_MODEL if not set
+OLLAMA_MODEL_PLANNER=qwen3:14b
+OLLAMA_MODEL_CODER=qwen3:14b
 OLLAMA_TEMPERATURE=0.7
```

#### [MODIFY] [.env](file:///c:/laragon/www/AI-ML/RudraAnvil/.env)

Add the two new variables (initially both pointing to whatever models the user wants).

---

### Component 2: Planner Agent

---

#### [NEW] [src/rudra/agent/planner_agent.py](file:///c:/laragon/www/AI-ML/RudraAnvil/src/rudra/agent/planner_agent.py)

Creates a **planning-only** deep agent. This module contains:

1. **`build_planner_prompt()`** — System prompt for the planner persona:
   - Senior architect that ONLY plans, never writes code files
   - Scans existing project tree and reads key files for context
   - Writes `PLAN.md` with exact file paths (not task descriptions)
   - Writes `.rudra/current_task.md` with specific instructions per file
   - Can read any project file for context, but cannot `write_file` to project files
   - CAN write to `.rudra/` context files (PLAN.md, current_task.md)

2. **`create_planner_agent()`** — Factory function:
   - Model: `config.ollama.model_planner` (reads from env, no hardcoded name)
   - Tools: `update_plan`, `read_plan`, `read_file`, `list_directory`, `grep`, `glob` + a new `write_task_assignment` tool
   - Middleware: `TaskAnchorMiddleware` only
   - Backend: `OverwriteFilesystemBackend` (shared, same project root)
   - Checkpointer: shared SQLite, thread = `{session_id}-planner`

3. **`write_task_assignment` tool** — New planning tool that writes `.rudra/current_task.md`:
   ```python
   @tool
   def write_task_assignment(file_path: str, instructions: str, context_files: str = "") -> str:
       """Write a coding task assignment for the coder agent.
       
       Args:
           file_path: The exact file path the coder should create (e.g. "src/main.py")
           instructions: Detailed instructions for what the file should contain
           context_files: Comma-separated list of existing files the coder should read first
       """
   ```
   This writes a structured markdown file to `.rudra/current_task.md` that the coder reads.

---

### Component 3: Coder Agent

---

#### [NEW] [src/rudra/agent/coder_agent.py](file:///c:/laragon/www/AI-ML/RudraAnvil/src/rudra/agent/coder_agent.py)

Creates a **code-writing-only** deep agent. This module contains:

1. **`build_coder_prompt()`** — System prompt for the coder persona:
   - Expert code generator that ONLY writes code, never plans
   - First reads `.rudra/current_task.md` — this is its assignment
   - Reads `.rudra/tech_stack.md` for technology constraints
   - Reads any context files listed in the task
   - Writes ONE complete file with `write_file()`, then stops
   - Code must be complete, production-ready — no stubs/TODOs/placeholders
   - Content must be raw source code — no markdown fences

2. **`create_coder_agent()`** — Factory function:
   - Model: `config.ollama.model_coder` (reads from env, no hardcoded name)
   - Tools: `read_file`, `write_file`, `edit_file`, `list_directory` — NO planning tools, NO `run_command`
   - Middleware: `FixWriteParamsMiddleware`, `TaskAnchorMiddleware` (with coder-specific anchor text)
   - Backend: `OverwriteFilesystemBackend` (shared, same project root)
   - Checkpointer: shared SQLite, thread = `{session_id}-coder-{N}` (fresh per file to keep context clean)

---

### Component 4: Orchestrator (Refactored main_agent.py)

---

#### [MODIFY] [src/rudra/agent/main_agent.py](file:///c:/laragon/www/AI-ML/RudraAnvil/src/rudra/agent/main_agent.py)

This is the largest change. The single-agent `create_main_agent()` becomes an orchestrator:

**What stays:**
- `AgentContext` dataclass (minor additions)
- `AgentResult` dataclass (unchanged)
- `RudraAgent` class shell (refactored internals)
- `_ensure_agents_md()` helper
- `_write_tech_stack_file()` helper

**What changes:**

1. **`create_main_agent()`** — Now creates the orchestrator instead of a single deep agent:
   - Creates shared infrastructure: VFS, SQLite checkpointer, filesystem backend
   - Creates the planner agent via `create_planner_agent()`
   - Stores coder factory config (model, backend, etc.) — coder agents are created on-demand per file
   - Returns a `RudraAgent` that orchestrates both

2. **`RudraAgent.run()`** — Becomes a multi-phase orchestration loop:

```
Phase 1: PLANNING
├── Log: "🔵 Phase 1: Planning (model: {planner_model})"
├── Run planner agent with user's task
├── Planner produces: PLAN.md + current_task.md for first file
├── Parse PLAN.md to get list of pending files
└── Log: "📋 Plan created: {N} files to generate"

Phase 2: CODING (loop per file)
├── For each pending file in PLAN.md:
│   ├── Log: "🟢 Coding [{i}/{N}]: {filename} (model: {coder_model})"
│   ├── Have planner write current_task.md for this file (if not first)
│   ├── Create fresh coder agent (new thread ID per file)
│   ├── Run coder agent
│   │   ├── Coder reads current_task.md
│   │   ├── Coder writes the file
│   │   ├── On failure: Log "🔴 Coder failed on {filename}, retrying..."
│   │   └── Deepagents handles retries internally (default limits)
│   ├── Verify file exists on disk (simple check, not content verification)
│   ├── Check off item in PLAN.md: "- [ ] file" → "- [x] file"
│   └── Log: "✅ {filename} written"
└── Log: "🏁 All {N} files generated"
```

3. **`_run_planner_phase()`** — Private method that streams the planner agent and processes its output, using the same logging infrastructure as current `_log_single_message()`.

4. **`_run_coder_for_file()`** — Private method that:
   - Creates a fresh coder agent with a unique thread ID
   - Streams the coder agent
   - Logs tool calls and errors with coder-specific prefixes
   - Returns success/failure boolean

5. **Loop guard integration** — The existing loop guards (repeated tool calls, consecutive failures) are applied per-agent. The planner gets the planning-specific guards, the coder gets the write-specific guards.

**Orchestration flow diagram:**

```
User Task
    │
    ▼
┌─────────────────────────────────────────┐
│  PLANNER (model_planner)                │
│  Thread: {sid}-planner                  │
│                                         │
│  Tools: update_plan, read_plan,         │
│         write_task_assignment,           │
│         read_file, ls, glob, grep       │
│                                         │
│  Output: PLAN.md + current_task.md      │
└───────────────┬─────────────────────────┘
                │
    ┌───────────┴─── For each file ───────┐
    │                                      │
    ▼                                      │
┌─────────────────────────────────────┐   │
│  PLANNER writes current_task.md     │   │
│  (specific instructions for file)   │   │
└───────────────┬─────────────────────┘   │
                │                          │
                ▼                          │
┌─────────────────────────────────────┐   │
│  CODER (model_coder)                │   │
│  Thread: {sid}-coder-{N} (fresh)    │   │
│                                     │   │
│  Tools: read_file, write_file,      │   │
│         edit_file, ls               │   │
│                                     │   │
│  Input: reads current_task.md       │   │
│  Output: writes the code file       │   │
└───────────────┬─────────────────────┘   │
                │                          │
                ▼                          │
┌─────────────────────────────────────┐   │
│  ORCHESTRATOR (Python)              │   │
│  - Verify file exists on disk       │   │
│  - Check off in PLAN.md             │   │
│  - Log success/failure              │   │
└───────────────┬─────────────────────┘   │
                │                          │
                └──────────────────────────┘
                │
                ▼
           AgentResult
```

---

### Component 5: Planning Tools Update

---

#### [MODIFY] [src/rudra/tools/planning_tools.py](file:///c:/laragon/www/AI-ML/RudraAnvil/src/rudra/tools/planning_tools.py)

Add the `write_task_assignment` tool to the existing `create_planning_tools()` function:

```python
@tool
def write_task_assignment(file_path: str, instructions: str, context_files: str = "") -> str:
    """Write a task assignment for the coder agent to .rudra/current_task.md.
    
    Call this BEFORE the coder generates each file. The coder reads this 
    file to know exactly what to build.
    
    Args:
        file_path: Exact relative path of the file to create (e.g. "src/main.py")
        instructions: Detailed instructions for what the file should contain
        context_files: Comma-separated list of existing files the coder should read for context
    
    Returns:
        Confirmation message
    """
```

Return: `[update_plan, read_plan, write_task_assignment]`

---

### Component 6: CLI Logging Updates

---

#### [MODIFY] [cli.py](file:///c:/laragon/www/AI-ML/RudraAnvil/src/rudra/cli.py)

Minor updates to show dual-model info:

1. **Task panel** — Show which models are being used:
   ```
   ⚡ Task
   Build a FastAPI app with JWT auth
   Path: C:\Users\user\project
   Planner: qwen3:14b  │  Coder: qwen3-coder:30b
   ```

2. **Phase transitions** — The `RudraAgent.run()` already logs to console, but the CLI displays a clear header when phases change:
   ```
   🔵 Phase: Planning (qwen3:14b)
   → Planning and executing task...
   ✓ [1] update_plan: Plan saved...
   ✓ [2] write_task_assignment: Task written...
   📋 Plan: 5 files to generate

   🟢 Coding [1/5]: main.py (qwen3-coder:30b)
   → [1] CALL write_file  file_path='main.py'
   ✓ [1] write_file: main.py written
   ✅ main.py complete

   🟢 Coding [2/5]: models.py (qwen3-coder:30b)
   ...
   
   🏁 Complete: 5/5 files generated
   ```

3. **Retry visibility** — When the coder fails and deepagents retries internally, the existing `_log_single_message()` already shows `✗ ERROR` entries. The orchestrator adds a wrapper log:
   ```
   🔴 Coder error on models.py — agent retrying...
   ```

---

### Component 7: Module Exports

---

#### [MODIFY] [agent/__init__.py](file:///c:/laragon/www/AI-ML/RudraAnvil/src/rudra/agent/__init__.py)

```python
from rudra.agent.main_agent import create_main_agent, RudraAgent, AgentContext, AgentResult
from rudra.agent.planner_agent import create_planner_agent
from rudra.agent.coder_agent import create_coder_agent
```

---

## What's NOT Changing

| File | Reason |
|---|---|
| `filesystem/virtual_fs.py` | Already serves as shared context layer |
| `filesystem/sync.py` | File sync utilities, no changes needed |
| `state/checkpoint.py` | Session ID management works as-is |
| `state/project_config.py` | Tech stack persistence works as-is |
| `tools/code_tools.py` | `run_command` + `grep_in_file` stay (planner can use them later for verification) |
| `tools/interaction_tools.py` | `ask_user` stays on planner |
| `compat/deepagents_path.py` | Path normalization shared by both |
| `compat/overwrite_backend.py` | Overwrite backend shared by both |
| `middleware/fix_write_params.py` | Used by coder agent |
| `middleware/block_premature_ask.py` | Used by planner agent |
| `middleware/task_anchor.py` | Used by both (different anchor text each) |
| `middleware/block_task_tool.py` | Used by coder agent (prevents it from trying to delegate) |
| `middleware/continue_after_write.py` | **May be removed** — coder writes one file then stops; the orchestrator handles continuation. Will evaluate during implementation. |

---

## Files Summary

| Action | File | Purpose |
|---|---|---|
| **MODIFY** | `config.py` | Add `model_planner` + `model_coder` with env fallback |
| **MODIFY** | `.env.example` | Add `OLLAMA_MODEL_PLANNER` + `OLLAMA_MODEL_CODER` |
| **MODIFY** | `.env` | Add the new variables |
| **NEW** | `agent/planner_agent.py` | Planner agent factory + system prompt |
| **NEW** | `agent/coder_agent.py` | Coder agent factory + system prompt |
| **MODIFY** | `agent/main_agent.py` | Refactor into orchestrator (largest change) |
| **MODIFY** | `tools/planning_tools.py` | Add `write_task_assignment` tool |
| **MODIFY** | `cli.py` | Show model names + phase logging |
| **MODIFY** | `agent/__init__.py` | Export new modules |

---

## Verification Plan

### Smoke Test

```bash
# Set both models to the same one first (verify orchestration works)
set OLLAMA_MODEL_PLANNER=qwen3:14b
set OLLAMA_MODEL_CODER=qwen3:14b
rudra "Create a hello world Python script" --project-dir C:\temp\test_dual --verbose
```

**Expected:**
- CLI shows "Planner: qwen3:14b | Coder: qwen3:14b"
- Phase 1 (Planning) logs show `update_plan` + `write_task_assignment` calls
- Phase 2 (Coding) logs show `write_file` call for the script
- `.rudra/PLAN.md` exists with `[x]` items
- `.rudra/current_task.md` exists
- The Python script exists and contains complete code

### Multi-File Test

```bash
rudra "Build a Flask REST API with SQLite for a todo app" --project-dir C:\temp\test_flask --verbose
```

**Expected:**
- Plan has 5+ files (not just 3)
- Each file is written in a separate coder phase with its own log section
- All planned files exist on disk
- Phase transitions are clearly visible in output

### Dual-Model Test

```bash
set OLLAMA_MODEL_PLANNER=qwen3:14b
set OLLAMA_MODEL_CODER=qwen3-coder:30b
rudra "Build a FastAPI app with JWT auth" --project-dir C:\temp\test_dual_model --verbose
```

**Expected:**
- CLI shows different model names for each phase
- Planner runs on qwen3:14b, coder runs on qwen3-coder:30b
- No cross-contamination of context between models

---

## Implementation Order

```
Step 1: config.py + .env changes
Step 2: planning_tools.py — add write_task_assignment
Step 3: planner_agent.py — new file
Step 4: coder_agent.py — new file
Step 5: main_agent.py — refactor into orchestrator
Step 6: cli.py — display updates
Step 7: agent/__init__.py — exports
Step 8: End-to-end testing
```
