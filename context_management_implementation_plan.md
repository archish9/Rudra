# RudraAnvil: Filesystem Context Management — Full Implementation Plan

## Project Overview

**Repo:** `/home/a/code/ai-ml/agent/RudraAnvil`
**Source root:** `src/rudraanvil/`

**Goal:** Replace all in-memory/system-prompt context patterns with filesystem-backed equivalents inside the `.rudraanvil/` directory. This prevents context window bloat, enables infinite planning memory, and creates the foundation for future MCP server integration.

**Core principle (from `filesystem-context.txt`):** The filesystem IS the memory. The agent reads/writes files instead of keeping state in its context window.

---

## The `.rudraanvil/` Directory (Canonical Layout After All Phases)

```
<project_root>/
└── .rudraanvil/
    ├── project.json          # Already exists — ProjectContext (tech stack config)
    ├── PLAN.md               # NEW Phase 1 — agent's living todo list (replaces write_todos)
    ├── tech_stack.md         # NEW Phase 2 — human-readable tech context for the agent
    ├── checkpoints/          # Already exists — session checkpoints
    └── logs/
        └── cmd_output.txt    # NEW Phase 3 — stdout/stderr scratchpad for run_command
```

---

## Phase 1: `PLAN.md` — Filesystem-Based Planning Tool

### Problem
The deepagents built-in `write_todos` tool stores the agent's plan directly in the LLM's **message history**. A 30-item plan = 30 objects permanently living in the context window for the entire session. This bloats every subsequent API call.

### Solution
Replace `write_todos` with a custom `update_plan(plan_markdown: str)` tool that writes a markdown checklist to `.rudraanvil/PLAN.md`. The agent reads its next step via `read_file` and checks off items with `edit_file`. The plan never lives in the context window — only the agent's thought about what it's currently doing does.

### Files to Create / Modify

---

#### [NEW] `src/rudraanvil/tools/planning_tools.py`

Create this file from scratch:

```python
"""Planning tools for filesystem-based context management."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem


def create_planning_tools(vfs: VirtualFileSystem) -> list:
    """Create planning tools that write to .rudraanvil/PLAN.md.

    Args:
        vfs: The virtual filesystem anchored to the project root

    Returns:
        List of LangChain tools
    """

    @tool
    def update_plan(plan_markdown: str) -> str:
        """Write or overwrite the agent's plan to .rudraanvil/PLAN.md.

        Use this INSTEAD of write_todos to track your task checklist.
        Write a Markdown checklist. Use `- [ ] task` for pending items
        and `- [x] task` for completed items.

        After writing the plan, use read_file('.rudraanvil/PLAN.md') to
        check your next pending item.

        Args:
            plan_markdown: Full markdown content of the plan/checklist

        Returns:
            Confirmation message
        """
        plan_path = ".rudraanvil/PLAN.md"
        vfs.write_file(plan_path, plan_markdown)
        return (
            f"Plan written to {plan_path}. "
            "Use read_file('.rudraanvil/PLAN.md') to review it. "
            "Use edit_file to check off items as you complete them."
        )

    @tool
    def read_plan() -> str:
        """Read the current plan from .rudraanvil/PLAN.md.

        Returns:
            Current plan content, or a message if no plan exists yet
        """
        content = vfs.read_file(".rudraanvil/PLAN.md")
        if content is None:
            return "No plan found. Use update_plan() to create one first."
        return content

    return [update_plan, read_plan]
```

---

#### [MODIFY] `src/rudraanvil/tools/__init__.py`

**Current content:**
```python
# (check actual content with view_file, likely exports create_code_tools)
```

**Change:** Add export for `create_planning_tools`.

```python
from rudraanvil.tools.code_tools import create_code_tools
from rudraanvil.tools.file_tools import create_file_tools
from rudraanvil.tools.planning_tools import create_planning_tools

__all__ = ["create_code_tools", "create_file_tools", "create_planning_tools"]
```

---

#### [MODIFY] `src/rudraanvil/agent/main_agent.py`

**Change 1 — Import planning tools** (top of file, near other imports):
```python
# ADD this import alongside `from rudraanvil.tools import create_code_tools`
from rudraanvil.tools.planning_tools import create_planning_tools
```

**Change 2 — Add planning tools to custom_tools** (around line 603):
```python
# BEFORE (line ~603):
custom_tools = create_code_tools(vfs)

# AFTER:
custom_tools = create_code_tools(vfs) + create_planning_tools(vfs)
```

**Change 3 — Update the system prompt** in `build_system_prompt()` function (lines 64–77):

In the `base_prompt` string, replace this line:
```
- Plan and decompose tasks using the write_todos tool
```
With:
```
- Plan and decompose tasks using the update_plan tool (writes to .rudraanvil/PLAN.md)
- Read your current plan with read_plan() or read_file('.rudraanvil/PLAN.md')
- Check off items with edit_file('.rudraanvil/PLAN.md', '- [ ] <task>', '- [x] <task>')
- NEVER use write_todos — it bloats context. Always use update_plan instead.
```

**Change 4 — Update the `build` command instructions** (around line 102):

Find:
```
1. **Plan**: Call write_todos with exactly the schema below to plan the files that need to be created.
```
Replace with:
```
1. **Plan**: Call update_plan() with a markdown checklist of all files to be created. Example:
   update_plan("# Build Plan\n- [ ] Create main.py\n- [ ] Create requirements.txt")
```

Also remove the `write_todos exact schema` block (lines 114–120) entirely — it is now obsolete.

---

### Phase 1 Verification

1. Run: `cd /home/a/code/ai-ml/agent/RudraAnvil && rudraanvil build "Create a hello world Python script" --project-dir /tmp/test_phase1`
2. After completion, check: `cat /tmp/test_phase1/.rudraanvil/PLAN.md`
3. **Expected:** A markdown checklist exists with `- [x]` items checked off.
4. **Expected:** Agent logs show calls to `update_plan` but NOT `write_todos`.

---

## Phase 2: `tech_stack.md` — Evict Project Context from System Prompt

### Problem
In `build_system_prompt()` (`main_agent.py` lines 79–88), the entire `ProjectContext` is string-interpolated directly into the system prompt:
```python
base_prompt += f"""
Project Tech Stack Context:
- Primary Language: {project_context.primary_language}
- Frameworks/Libraries: {project_context.framework or 'Not specified'}
- Database: {project_context.database or 'Not specified'}
- Additional Rules/Architecture: {project_context.additional_context or 'None'}
...
"""
```
This text is prepended to **every single LLM API call** for the entire session. A long `additional_context` field = hundreds of wasted tokens, forever.

Also in `main_agent.py` around line 651–652, the same context is injected into the **subagent prompt**:
```python
if project_context and project_context.primary_language:
    subagent_prompt += f"\n\nSTRICT TECH STACK ENFORCEMENT:\n..."
```

### Solution
Write `tech_stack.md` to disk in `cli.py` before the agent starts. Replace all context injections with a single instruction: *"Read `.rudraanvil/tech_stack.md` to understand the architecture."*

### Files to Modify

---

#### [MODIFY] `src/rudraanvil/cli.py`

**Add a helper function** after `get_or_prompt_project_context()` (after line 78):

```python
def write_tech_stack_file(project_path: Path, context: ProjectContext) -> None:
    """Write project tech stack context to .rudraanvil/tech_stack.md.
    
    This offloads architecture context from the system prompt to a file,
    reducing token usage on every LLM call.
    
    Args:
        project_path: Root directory of the project
        context: The project context to serialize
    """
    rudraanvil_dir = project_path / ".rudraanvil"
    rudraanvil_dir.mkdir(parents=True, exist_ok=True)
    
    lines = [
        "# Project Tech Stack\n",
        f"**Primary Language:** {context.primary_language or 'Not specified'}\n\n",
        f"**Frameworks / Libraries:** {context.framework or 'Not specified'}\n\n",
        f"**Database:** {context.database or 'Not specified'}\n\n",
    ]
    
    if context.additional_context:
        lines.append("## Architecture Rules & Additional Context\n\n")
        lines.append(context.additional_context + "\n")
    
    tech_stack_path = rudraanvil_dir / "tech_stack.md"
    tech_stack_path.write_text("".join(lines), encoding="utf-8")
    console.print(f"[dim]✓ Tech stack context written to .rudraanvil/tech_stack.md[/dim]")
```

**Call this function in the `build` command**, right after loading context (after line 127):

```python
# BEFORE (line ~127-130):
project_context = get_or_prompt_project_context(project_path)

# Create and run agent
agent = create_main_agent(...)

# AFTER (insert one line):
project_context = get_or_prompt_project_context(project_path)
write_tech_stack_file(project_path, project_context)  # ← ADD THIS LINE

# Create and run agent
agent = create_main_agent(...)
```

Also call `write_tech_stack_file` in the **`chat` command** (after line 185) similarly.

---

#### [MODIFY] `src/rudraanvil/agent/main_agent.py`

**Change 1 — Remove context injection from main system prompt** (lines 79–88):

```python
# REMOVE this entire block:
if project_context and project_context.primary_language:
    base_prompt += f"""
Project Tech Stack Context:
- Primary Language: {project_context.primary_language}
...
"""
```

**Replace with:**
```python
# ADD this static instruction instead:
base_prompt += """
IMPORTANT: Before writing any code, use read_file('.rudraanvil/tech_stack.md') to understand 
the required tech stack, frameworks, and architecture rules for this project.
Always strictly follow what is specified in that file.
"""
```

**Change 2 — Remove context injection from subagent prompt** (lines 651–652):

```python
# REMOVE this block:
if project_context and project_context.primary_language:
    subagent_prompt += f"\n\nSTRICT TECH STACK ENFORCEMENT:\n- Primary Language: {project_context.primary_language}\n..."
```

**Replace with:**
```python
subagent_prompt += (
    "\n\nIMPORTANT: Before writing any code, use read_file to read "
    "'.rudraanvil/tech_stack.md' — it contains the PRIMARY LANGUAGE, FRAMEWORKS, "
    "and ARCHITECTURE RULES you MUST follow. Failure to read this file will result "
    "in incorrect code."
)
```

**Change 3 — Also remove the `build` command IMPORTANT note** about tech stack (line ~107):

```python
# REMOVE:
IMPORTANT: When delegating to the `general-purpose` subagent, you MUST explicitly specify 
the primary language and frameworks defined in the "Project Tech Stack Context" above. 
Do NOT let the subagent default to an incorrect language.

# REPLACE WITH:
IMPORTANT: When delegating to the `general-purpose` subagent, include in your instructions
a reminder that it MUST read `.rudraanvil/tech_stack.md` before writing code.
```

---

### Phase 2 Verification

1. Run `rudraanvil build "create a flask REST API" --project-dir /tmp/test_phase2`
2. Check: `cat /tmp/test_phase2/.rudraanvil/tech_stack.md`
3. **Expected:** The file exists with correct language/framework info.
4. **Expected:** Agent logs show a `read_file` call for `.rudraanvil/tech_stack.md` early in the trace.
5. **Expected:** The string `"Project Tech Stack Context"` does NOT appear in the raw agent messages (can grep the verbose output).

---

## Phase 3: `run_command` — Scratchpad for Large Tool Outputs

### Problem
`src/rudraanvil/tools/code_tools.py` currently returns an empty list — no execution capability exists. When we add `run_command`, naively returning stdout/stderr crashes the context window. A `pip install` log can be 2,000+ lines.

### Solution
Implement `run_command` that:
1. Executes the shell command anchored to `project_path`
2. Writes **full output** to `.rudraanvil/logs/cmd_output.txt`
3. Returns **only a 2-line summary** to the agent: exit code + file path

### Files to Modify

---

#### [MODIFY] `src/rudraanvil/tools/code_tools.py`

Replace the entire file:

```python
"""Code execution tools for agents — with scratchpad log offloading."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem

# Maximum characters of output to surface directly in the summary
_PREVIEW_CHARS = 300


def create_code_tools(vfs: VirtualFileSystem, timeout: int = 60) -> list:
    """Create code execution tools with log scratchpad offloading.

    All stdout/stderr is written to .rudraanvil/logs/cmd_output.txt.
    Only a short summary is returned to the agent to prevent context explosion.

    Args:
        vfs: The virtual filesystem anchored to the project root
        timeout: Maximum execution time in seconds (default 60)

    Returns:
        List of LangChain tools
    """

    @tool
    def run_command(command: str) -> str:
        """Run a shell command in the project directory.

        Full output is saved to .rudraanvil/logs/cmd_output.txt.
        Use read_file or grep_file to inspect specific errors.

        IMPORTANT: Only use this tool to run safe commands like:
        - Package installation (pip install, npm install)
        - Build/compile commands
        - Linters and formatters (black, eslint, tsc --noEmit)
        - Test runners (pytest, jest)
        Do NOT use for file I/O; use write_file/read_file tools instead.

        Args:
            command: Shell command to execute (runs in project root)

        Returns:
            Short summary: exit code + path to full log
        """
        project_root = Path(vfs.root_dir)
        log_dir = project_root / ".rudraanvil" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "cmd_output.txt"

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(project_root),
                timeout=timeout,
            )
            combined_output = (
                f"=== COMMAND ===\n{command}\n\n"
                f"=== EXIT CODE ===\n{result.returncode}\n\n"
                f"=== STDOUT ===\n{result.stdout}\n\n"
                f"=== STDERR ===\n{result.stderr}\n"
            )
            log_file.write_text(combined_output, encoding="utf-8")

            # Surface a short preview to orient the agent
            preview_src = result.stderr if result.returncode != 0 else result.stdout
            preview = preview_src[:_PREVIEW_CHARS].replace("\n", " ↵ ")
            status = "FAILED" if result.returncode != 0 else "SUCCESS"

            return (
                f"Command {status} (exit code {result.returncode}). "
                f"Full output saved to .rudraanvil/logs/cmd_output.txt\n"
                f"Preview: {preview}\n"
                f"Use read_file('.rudraanvil/logs/cmd_output.txt') or "
                f"grep_in_file to analyze the full output."
            )

        except subprocess.TimeoutExpired:
            msg = f"Command timed out after {timeout}s: {command}"
            log_file.write_text(msg, encoding="utf-8")
            return f"TIMEOUT: {msg}. Check .rudraanvil/logs/cmd_output.txt"

        except Exception as exc:
            msg = f"Error executing command: {exc}"
            log_file.write_text(msg, encoding="utf-8")
            return f"ERROR: {msg}"

    @tool
    def grep_in_file(file_path: str, pattern: str) -> str:
        """Search for a pattern in a file (like grep). Useful for scanning large log files.

        Args:
            file_path: Path to file (relative to project root)
            pattern: String to search for (case-insensitive substring match)

        Returns:
            All matching lines with line numbers, or a not-found message
        """
        content = vfs.read_file(file_path)
        if content is None:
            return f"Error: File not found: {file_path}"

        pattern_lower = pattern.lower()
        matches = [
            f"L{i+1}: {line}"
            for i, line in enumerate(content.splitlines())
            if pattern_lower in line.lower()
        ]

        if not matches:
            return f"No matches for '{pattern}' in {file_path}"

        output = "\n".join(matches[:50])  # cap at 50 lines to stay context-safe
        if len(matches) > 50:
            output += f"\n... and {len(matches) - 50} more matches (narrow your search)"
        return output

    return [run_command, grep_in_file]
```

> **Note:** `vfs.root_dir` must be accessible. If `VirtualFileSystem` doesn't expose `root_dir` as a public attribute, look at `src/rudraanvil/filesystem/virtual_fs.py` — the constructor stores the project path; expose it as `self.root_dir = str(root_dir)` if needed.

---

#### [MODIFY] `src/rudraanvil/agent/main_agent.py`

**Add `run_command` and `grep_in_file` to main agent's tool list.** These stay on the **main agent only** (not the coding subagent, which should only write files).

The `custom_tools` block (after Phase 1 changes) already reads:
```python
custom_tools = create_code_tools(vfs) + create_planning_tools(vfs)
```
No change needed — `create_code_tools` now returns `[run_command, grep_in_file]` automatically.

**Update system prompt** to mention the new tools in `base_prompt` (alongside existing file tools):
```
- Run shell commands with run_command (output is saved to .rudraanvil/logs/cmd_output.txt)
- Search files and logs with grep_in_file(file_path, pattern)
```

**IMPORTANT: Do NOT add `run_command` to the `general-purpose` subagent's tool list.** The subagent only writes files. Command execution stays on the main agent.

---

### Phase 3 Verification

1. Run: `rudraanvil build "create a python flask app and install dependencies" --project-dir /tmp/test_phase3`
2. Check: `cat /tmp/test_phase3/.rudraanvil/logs/cmd_output.txt`
3. **Expected:** The file exists with the output of `pip install flask`.
4. **Expected:** The agent trace shows `run_command` returning a short summary, NOT the full pip output.

---

## Phase 4: MCP Server Layer

> ⚠️ **This is the most complex phase. Implement only when Phases 1–3 are stable.**

### Problem
Currently, RudraAnvil's tools are LangChain `@tool` handlers — they only work with deepagents/Ollama. No external MCP client (Claude Desktop, Cursor, Cline) can use them.

### Solution
Add a **Model Context Protocol (MCP) server** that:
- Exposes the `.rudraanvil/` filesystem as **MCP Resources** (read-only, lazy-loaded — not injected into context)
- Re-exposes the existing tools as **MCP Tools**
- Runs as a subprocess/stdio server that any MCP client can connect to

### Dependency to Add

```toml
# In pyproject.toml, under [project.dependencies]:
"mcp>=1.0.0",
```

Install: `pip install mcp`

---

### Files to Create / Modify

---

#### [NEW] `src/rudraanvil/mcp/__init__.py`

```python
"""MCP server for RudraAnvil."""
```

---

#### [NEW] `src/rudraanvil/mcp/server.py`

```python
"""RudraAnvil MCP Server.

Exposes RudraAnvil's filesystem context and tools via the Model Context Protocol.
Run with: python -m rudraanvil.mcp.server --project-dir /path/to/project

MCP Resources (read-only, fetched on-demand by clients):
  rudraanvil://plan           → .rudraanvil/PLAN.md
  rudraanvil://tech_stack     → .rudraanvil/tech_stack.md
  rudraanvil://project_tree   → live directory tree
  rudraanvil://logs/latest    → .rudraanvil/logs/cmd_output.txt

MCP Tools (callable actions):
  update_plan(plan_markdown)
  read_file(path)
  write_file(path, content)
  run_command(command)
  grep_in_file(file_path, pattern)
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions

from rudraanvil.filesystem import VirtualFileSystem
from rudraanvil.tools.file_tools import create_file_tools
from rudraanvil.tools.planning_tools import create_planning_tools
from rudraanvil.tools.code_tools import create_code_tools


def build_mcp_server(project_path: Path) -> Server:
    """Build and configure the MCP server for a given project path."""
    
    vfs = VirtualFileSystem(project_path)
    if project_path.exists():
        vfs.load_from_disk()

    app = Server("rudraanvil")
    rudraanvil_dir = project_path / ".rudraanvil"

    # ─── Resources: lazy, on-demand context — never injected automatically ────

    @app.list_resources()
    async def list_resources() -> list[types.Resource]:
        resources = [
            types.Resource(
                uri="rudraanvil://project_tree",
                name="Project File Tree",
                description="Live directory tree of the project",
                mimeType="text/plain",
            ),
        ]
        plan_file = rudraanvil_dir / "PLAN.md"
        if plan_file.exists():
            resources.append(types.Resource(
                uri="rudraanvil://plan",
                name="Current Plan",
                description="Agent's active task plan (PLAN.md)",
                mimeType="text/markdown",
            ))
        stack_file = rudraanvil_dir / "tech_stack.md"
        if stack_file.exists():
            resources.append(types.Resource(
                uri="rudraanvil://tech_stack",
                name="Tech Stack",
                description="Project tech stack and architecture rules",
                mimeType="text/markdown",
            ))
        log_file = rudraanvil_dir / "logs" / "cmd_output.txt"
        if log_file.exists():
            resources.append(types.Resource(
                uri="rudraanvil://logs/latest",
                name="Latest Command Output",
                description="stdout/stderr from last run_command call",
                mimeType="text/plain",
            ))
        return resources

    @app.read_resource()
    async def read_resource(uri: types.AnyUrl) -> str:
        uri_str = str(uri)
        if uri_str == "rudraanvil://project_tree":
            return vfs.get_tree()
        elif uri_str == "rudraanvil://plan":
            p = rudraanvil_dir / "PLAN.md"
            return p.read_text(encoding="utf-8") if p.exists() else "No plan yet."
        elif uri_str == "rudraanvil://tech_stack":
            p = rudraanvil_dir / "tech_stack.md"
            return p.read_text(encoding="utf-8") if p.exists() else "No tech stack defined."
        elif uri_str == "rudraanvil://logs/latest":
            p = rudraanvil_dir / "logs" / "cmd_output.txt"
            return p.read_text(encoding="utf-8") if p.exists() else "No command output yet."
        raise ValueError(f"Unknown resource URI: {uri_str}")

    # ─── Tools: bridge existing LangChain tools to MCP ───────────────────────

    all_lc_tools = (
        create_file_tools(vfs)
        + create_planning_tools(vfs)
        + create_code_tools(vfs)
    )
    # Build a lookup by tool name for fast dispatch
    lc_tool_map = {t.name: t for t in all_lc_tools}

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        mcp_tools = []
        for lc_tool in all_lc_tools:
            # Convert LangChain tool schema to MCP Tool schema
            schema = getattr(lc_tool, "args_schema", None)
            input_schema = schema.model_json_schema() if schema else {"type": "object", "properties": {}}
            mcp_tools.append(types.Tool(
                name=lc_tool.name,
                description=lc_tool.description,
                inputSchema=input_schema,
            ))
        return mcp_tools

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        lc_tool = lc_tool_map.get(name)
        if lc_tool is None:
            raise ValueError(f"Unknown tool: {name}")
        # LangChain tools are sync; run in executor to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lc_tool.invoke, arguments)
        return [types.TextContent(type="text", text=str(result))]

    return app


async def run_server(project_path: Path) -> None:
    """Run the MCP server over stdio."""
    app = build_mcp_server(project_path)
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="rudraanvil",
                server_version="0.1.0",
                capabilities=app.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="RudraAnvil MCP Server")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="Project root directory (default: current directory)",
    )
    args = parser.parse_args()
    asyncio.run(run_server(args.project_dir.resolve()))


if __name__ == "__main__":
    main()
```

---

#### [MODIFY] `pyproject.toml`

**Add MCP entrypoint** under `[project.scripts]`:
```toml
[project.scripts]
rudraanvil = "rudraanvil.cli:app"
rudraanvil-mcp = "rudraanvil.mcp.server:main"   # ← ADD THIS
```

**Add MCP dependency** under `[project.dependencies]`:
```toml
"mcp>=1.0.0",
```

---

#### MCP Client Config (for Claude Desktop / Cursor)

After `pip install mcp` and the package is installed, users add this to `claude_desktop_config.json` or equivalent:

```json
{
  "mcpServers": {
    "rudraanvil": {
      "command": "rudraanvil-mcp",
      "args": ["--project-dir", "/path/to/your/project"]
    }
  }
}
```

---

### Phase 4 Verification

1. Install the updated package: `pip install -e /home/a/code/ai-ml/agent/RudraAnvil`
2. In a test project directory with a `.rudraanvil/` dir: `rudraanvil-mcp --project-dir /tmp/mcp_test &`
3. Use the MCP inspector tool: `npx @modelcontextprotocol/inspector rudraanvil-mcp --project-dir /tmp/mcp_test`
4. **Expected:** Inspector shows resources `rudraanvil://plan`, `rudraanvil://tech_stack`, `rudraanvil://project_tree`.
5. **Expected:** Tools `update_plan`, `read_file`, `write_file`, `run_command` are listed and callable.

---

## Summary Table

| Phase | What Changes | Files Affected | Key Benefit |
|---|---|---|---|
| **1 — PLAN.md** | Replace `write_todos` with `update_plan` tool writing to `.rudraanvil/PLAN.md` | `planning_tools.py` [NEW], `main_agent.py`, `tools/__init__.py` | Planning memory never bloats context window |
| **2 — tech_stack.md** | Remove `ProjectContext` string interpolation from prompts; write to file in `cli.py` | `cli.py`, `main_agent.py` | Tech stack context paid once (file read), not every call |
| **3 — run_command** | Implement `run_command` that logs to `.rudraanvil/logs/cmd_output.txt` | `code_tools.py` | Scratchpad for large output; agent gets 2-line summaries |
| **4 — MCP Server** | Add `mcp/server.py` exposing resources + tools via MCP protocol | `mcp/server.py` [NEW], `mcp/__init__.py` [NEW], `pyproject.toml` | Any MCP client (Cursor, Claude Desktop) can use RudraAnvil as backend |

---

## Implementation Notes for New Chats

- **Phases are independent** — each can be implemented without the others, but implement in order (1 → 2 → 3 → 4).
- **`vfs.root_dir`**: Check `src/rudraanvil/filesystem/virtual_fs.py` — if `root_dir` isn't a public attribute, add `self.root_dir = str(root)` in its `__init__`.
- **`tools/__init__.py`**: Always check its current content before editing — it likely imports `create_code_tools`.
- **No existing tests**: The `tests/` directory is currently empty. Manual verification steps (listed per phase) are the validation method.
- **deepagents `write_todos`**: This is a built-in tool provided by the `deepagents` library, not one we wrote. We are NOT removing it from the library — we are just instructing the agent in the system prompt to use `update_plan` instead, and not listing `write_todos` in custom tools documentation.
