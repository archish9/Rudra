"""Main supervisor agent for RudraAnvil using deepagents framework."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from rich.console import Console
from deepagents import create_deep_agent

from rudraanvil.config import config
from rudraanvil.filesystem import VirtualFileSystem
from rudraanvil.middleware import BlockTaskToolMiddleware, ContinueAfterWriteMiddleware, TaskAnchorMiddleware
from rudraanvil.state import get_or_create_session_id, ProjectContext
from rudraanvil.tools.interaction_tools import create_interaction_tools
from rudraanvil.tools.planning_tools import create_planning_tools


@dataclass
class AgentContext:
    """Context passed to the agent during execution."""

    project_path: Path
    task: str
    vfs: VirtualFileSystem
    console: Console
    project_context: Optional[ProjectContext] = None

    dry_run: bool = False
    verbose: bool = False
    max_iterations: int = 100
    stop_on_error: bool = True

    chat_history: list[dict] = field(default_factory=list)

    command: str = "build"
    file_path: Optional[str] = None
    issue: Optional[str] = None


@dataclass
class AgentResult:
    """Result of an agent execution."""

    success: bool
    message: str
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    iterations: int = 0
    todo_summary: str = ""


def build_system_prompt(
    command: str,
    task: str,
    project_path: Path,
    vfs: VirtualFileSystem,
    project_context: Optional[ProjectContext] = None,
    tech_stack_content: str = "",
    **kwargs,
) -> str:
    """Build the system prompt for the agent based on command type."""

    base = f"""You are RudraAnvil, an expert autonomous coding agent.

## FILE PATH RULES
- Use RELATIVE paths only: "app.py", "src/models.py", "requirements.txt"
- NEVER use absolute paths or paths starting with "/" or a drive letter
- Correct:   write_file(file_path="models.py", ...)
- WRONG:     write_file(file_path="/home/user/project/models.py", ...)

## HARD CONSTRAINTS
- write_file content must be RAW source code — NEVER wrap it in ```markdown fences```
- Write COMPLETE, working code — never placeholders, stubs, or "TODO" comments
- NEVER use the `task` subagent tool — write all files yourself directly with write_file
- Write ONE file per message — call write_file() ONCE then stop and wait for the result
- Do NOT make multiple write_file() calls in the same response
- Plan your work with update_plan(); track progress by checking off items as you go

## WHAT "COMPLETE" MEANS — NO SHORTCUTS
- Models: every field defined with types, every method fully implemented, no bare `pass`
- REST APIs: ALL CRUD endpoints — GET (list + single), POST, PUT/PATCH, DELETE — each with
  full request parsing, DB interaction, and JSON response; no "Hello World" routes
- Requirements: all dependencies listed with version pins (e.g. Flask>=3.0.0)
- App: all blueprints registered, DB initialized, error handlers in place

Project structure:
{vfs.get_tree()}
"""

    # Tech stack: inlined directly so the model never needs to read it from disk
    if tech_stack_content:
        base += f"\n## Tech Stack — follow this STRICTLY\n{tech_stack_content}\n"
    else:
        base += (
            "\n## Tech Stack\n"
            "Infer the tech stack from the task description. "
            "Use the exact framework named in the task (e.g. FastAPI → Python + FastAPI).\n"
        )

    # Command-specific guidance
    if command in ("build", "auto"):
        existing = "Existing project — read relevant files before modifying." if vfs.files else "New project — start from scratch."
        base += f"""
## Task
{task}
{existing}

## Workflow
1. Call update_plan() ONCE with a checklist of FILENAMES (not task descriptions)
   !! Each item must be a real filename: '- [ ] main.py' NOT '- [ ] Create main.py'
2. Call task() with subagent_type='general-purpose' and a description that includes:
   - The exact task: "{task}"
   - Every filename from your plan (EXACT names — subagent must use these)
   - The full tech stack and framework
   - "Write COMPLETE, production-ready code for ALL files listed"
   The task subagent handles ALL file writing in its own isolated context.
   Do NOT call write_file yourself — that is the subagent's job.
3. After task() returns, verify every file was written:
   - For each filename in your plan, call read_file(file_path='<filename>') to check it exists
   - If any file returns "not found" or an error: call task() AGAIN with ONLY those missing files
   - Do NOT call task() more than 3 times total
4. Update .rudraanvil/AGENTS.md using edit_file to record:
   - Tech stack confirmed
   - Files created and what each does
   - Key architecture decisions
"""

    elif command == "chat":
        user_input = kwargs.get("user_input", task)
        base += f"""
## Chat Request
{user_input}

Respond conversationally. Make targeted file changes only when explicitly requested.
You may use ask_user() if you need clarification from the user.
"""

    elif command == "fix":
        issue = kwargs.get("issue", task)
        base += f"""
## Bug / Issue
{issue}

Workflow: diagnose root cause → plan a minimal fix → apply with edit_file() → review for correctness.
Do NOT run tests or execute code.

After fixing: update `.rudraanvil/AGENTS.md` Session Log with what was fixed and why.
"""

    elif command == "edit":
        file_path = kwargs.get("file_path", "")
        content = vfs.read_file(file_path) if file_path else ""
        base += f"""
## Targeted Edit
File: {file_path}
Instruction: {task}

Current content:
{content or "(file not found)"}

Make the requested change precisely with edit_file(). Do not touch unrelated code.
"""

    elif command == "review":
        base += """
## Code Review
Provide a clear, actionable report covering:
1. Security issues
2. Performance problems
3. Code quality / readability
4. Best-practice violations
5. Concrete suggestions

Do NOT modify any files — report only.
"""

    elif command == "suggest":
        base += f"""
## Suggestions (read-only)
Task: {task}

Provide detailed suggestions with example code snippets or diffs.
Do NOT apply any changes.
"""

    return base


class RudraAnvilAgent:
    """Wrapper around deepagents for RudraAnvil-specific functionality."""

    def __init__(self, context: AgentContext, deep_agent, session_id: str, db_conn=None):
        self.context = context
        self.agent = deep_agent
        self.session_id = session_id
        self.console = context.console
        self.iterations = 0
        self._db_conn = db_conn

    async def close(self) -> None:
        if self._db_conn is not None:
            await self._db_conn.close()
            self._db_conn = None

    def _log_always(self, message: str, style: str = "") -> None:
        if style:
            self.console.print(f"[{style}]{message}[/{style}]")
        else:
            self.console.print(message)

    def _status(self, message: str) -> None:
        self.console.print(f"[dim]→ {message}[/dim]")

    def _log_single_message(self, msg: Any, index: int) -> None:
        """Log a single message as it arrives in the stream."""
        msg_type = type(msg).__name__

        if msg_type == "AIMessage":
            tool_calls = getattr(msg, "tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "?")
                    args = str(tc.get("args", {}))[:400]
                    self._log_always(
                        f"[bold cyan]→ [{index}] CALL[/bold cyan] [yellow]{name}[/yellow]  {args}"
                    )
            else:
                content = str(getattr(msg, "content", ""))[:300].replace("\n", " ")
                self._log_always(f"[bold cyan]← [{index}] AI[/bold cyan]  {content}")

        elif msg_type == "ToolMessage":
            content = str(getattr(msg, "content", ""))
            tool_name = getattr(msg, "name", "?")
            is_error = (
                content.startswith("Error:")
                or "Error:" in content
                or "Traceback" in content
                or "Errno" in content
                or "not a valid tool" in content
                or "Input should be a valid string" in content
            )
            if is_error:
                self._log_always(
                    f"[bold red]✗ [{index}] ERROR from {tool_name}:[/bold red]\n[red]{content}[/red]"
                )
            else:
                self._log_always(f"[green]✓ [{index}] {tool_name}:[/green] {content}")

        elif msg_type == "HumanMessage":
            content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
            self._log_always(f"[dim][{index}] USER: {content}[/dim]")

        else:
            content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
            self._log_always(f"[dim][{index}] {msg_type}: {content}[/dim]")

    async def run(self) -> AgentResult:
        """Run the full agent workflow."""
        try:
            self._status("Planning and executing task...")
            self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")

            final_state = None
            processed_messages = 0
            consecutive_failures = 0
            _halt = False  # set to True to break out of both the while and async-for loops
            # Loop detection: count how many times the same (tool, file_path) is called
            repeated_tool_calls: dict[tuple, int] = {}
            MAX_REPEATED_CALLS = 5
            # Separate counter for planning tools — lower threshold since 3 calls is always a loop
            planning_tool_calls: dict[str, int] = {}
            MAX_PLANNING_CALLS = 3

            lg_config = {
                "configurable": {"thread_id": self.session_id},
            }

            async for chunk in self.agent.astream(
                {"messages": [{"role": "user", "content": self.context.task}]},
                lg_config,
                stream_mode="values",
                subgraphs=True,
            ):
                if _halt:
                    break

                namespace, event = (
                    chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
                )

                final_state = event
                messages = event.get("messages", [])

                while processed_messages < len(messages):
                    msg = messages[processed_messages]
                    msg_type = type(msg).__name__

                    if namespace:
                        self._log_always(f"[dim](subagent {':'.join(namespace)})[/dim]")

                    self._log_single_message(msg, processed_messages + 1)

                    if msg_type == "AIMessage":
                        tool_calls = getattr(msg, "tool_calls", [])
                        for tc in tool_calls:
                            name = tc.get("name", "")
                            if name in ("write_file", "edit_file", "read_file"):
                                args = tc.get("args", {})
                                key_arg = args.get("file_path", args.get("path", ""))
                                # PLAN.md is edited once per file (normal check-off) — skip it
                                if "PLAN.md" not in key_arg:
                                    call_key = (name, key_arg)
                                    repeated_tool_calls[call_key] = repeated_tool_calls.get(call_key, 0) + 1
                                    if repeated_tool_calls[call_key] >= MAX_REPEATED_CALLS:
                                        self._log_always(
                                            f"[bold yellow]!! Loop guard: '{name}' on '{key_arg}' "
                                            f"repeated {repeated_tool_calls[call_key]}x — stopping here, "
                                            f"keeping files written so far.[/bold yellow]"
                                        )
                                        _halt = True
                                        break  # break inner for-tc loop
                            if _halt:
                                break  # break inner for-tc loop (planning check)
                            if name in ("write_file", "task"):
                                # The model is progressing (writing files or delegating) —
                                # reset the planning loop counter.
                                planning_tool_calls.clear()
                            elif name in ("update_plan", "read_plan"):
                                planning_tool_calls[name] = planning_tool_calls.get(name, 0) + 1
                                if planning_tool_calls[name] >= MAX_PLANNING_CALLS:
                                    self._log_always(
                                        f"[bold yellow]!! Loop guard: '{name}' called "
                                        f"{planning_tool_calls[name]}x without writing files — stopping.[/bold yellow]"
                                    )
                                    _halt = True
                                    break

                    elif msg_type == "ToolMessage":
                        content = str(getattr(msg, "content", ""))
                        is_error = (
                            content.startswith("Error:")
                            or content.startswith("Cannot write to")
                            or "Error:" in content
                            or "Traceback" in content
                            or "Errno" in content
                            or "not a valid tool" in content
                            or "Input should be a valid string" in content
                        )
                        if is_error:
                            consecutive_failures += 1
                            if self.context.stop_on_error and consecutive_failures >= 3:
                                self._log_always(
                                    "[bold yellow]!! 3 consecutive tool failures — stopping.[/bold yellow]"
                                )
                                _halt = True
                        else:
                            consecutive_failures = 0

                    if _halt:
                        break  # break inner while loop
                    processed_messages += 1

            if not final_state:
                return AgentResult(
                    success=False, message="Agent stream returned no state.", iterations=0
                )

            messages = final_state.get("messages", []) if isinstance(final_state, dict) else []
            if messages:
                final_msg = messages[-1]
                response_content = (
                    final_msg.content if hasattr(final_msg, "content") else str(final_msg)
                )
            else:
                response_content = "No response"

            if not self.context.dry_run:
                new_vfs = VirtualFileSystem(self.context.project_path)
                if self.context.project_path.exists():
                    new_vfs.load_from_disk()

                original_paths = set(self.context.vfs.files.keys())
                new_paths = set(new_vfs.files.keys())
                # Exclude .rudraanvil/ state files (PLAN.md, AGENTS.md, etc.)
                files_created = [
                    p for p in (new_paths - original_paths)
                    if Path(p).parts[0] != ".rudraanvil"
                ]
                files_modified = [
                    p
                    for p in new_paths & original_paths
                    if new_vfs.files[p] != self.context.vfs.files.get(p)
                    and Path(p).parts[0] != ".rudraanvil"
                ]

                return AgentResult(
                    success=True,
                    message=response_content,
                    files_created=files_created,
                    files_modified=files_modified,
                    iterations=self.iterations,
                    todo_summary=response_content,
                )
            else:
                self._status("Dry run — no files written.")
                self.console.print(response_content)
                return AgentResult(
                    success=True,
                    message="Dry run completed (no files written)",
                    files_created=[],
                    files_modified=[],
                    iterations=self.iterations,
                    todo_summary=response_content,
                )

        except Exception:
            import traceback

            self._log_always("[bold red]\n!! Agent crashed — full traceback:[/bold red]")
            self._log_always(traceback.format_exc())
            raise

    async def chat_turn(self, user_input: str) -> str:
        """Handle a single turn in chat mode."""
        self.context.chat_history.append({"role": "user", "content": user_input})

        lg_config = {"configurable": {"thread_id": self.session_id}}
        result = await asyncio.to_thread(
            self.agent.invoke,
            {"messages": self.context.chat_history},
            lg_config,
        )

        result_messages = result.get("messages", [])
        if result_messages:
            final_msg = result_messages[-1]
            response = final_msg.content if hasattr(final_msg, "content") else str(final_msg)
        else:
            response = "No response"

        self.context.chat_history.append({"role": "assistant", "content": response})
        return response


def _ensure_agents_md(rudraanvil_dir: Path, project_context: Optional[ProjectContext]) -> None:
    """Create a starter AGENTS.md if one does not already exist.

    The file is the agent's persistent cross-session memory. MemoryMiddleware
    reads it at startup and injects it into every system prompt. The agent
    updates it via edit_file as it learns about the project.

    Only created on the very first run — never overwritten.
    """
    agents_md = rudraanvil_dir / "AGENTS.md"
    if agents_md.exists():
        return

    # Pre-populate tech stack from project.json if available
    stack_lines = []
    if project_context:
        if project_context.primary_language:
            stack_lines.append(f"- Language: {project_context.primary_language}")
        if project_context.framework:
            stack_lines.append(f"- Framework: {project_context.framework}")
        if project_context.database:
            stack_lines.append(f"- Database: {project_context.database}")
        if project_context.additional_context:
            stack_lines.append(f"- Notes: {project_context.additional_context}")

    stack_section = "\n".join(stack_lines) if stack_lines else "(not yet determined — agent will fill in)"

    agents_md.write_text(
        f"# Project Memory\n\n"
        f"This file is your persistent memory across sessions.\n"
        f"Update it using edit_file after completing any task.\n\n"
        f"## Tech Stack\n{stack_section}\n\n"
        f"## Project Structure\n(not yet built)\n\n"
        f"## Architecture Notes\n(none yet)\n\n"
        f"## Session Log\n(no sessions yet)\n",
        encoding="utf-8",
    )


def _write_tech_stack_file(
    rudraanvil_dir: Path,
    project_context: Optional[ProjectContext],
) -> None:
    """Write project tech stack context to .rudraanvil/tech_stack.md.

    Called once in create_main_agent() before the agent starts. Offloads
    tech stack info to disk so the agent reads it via read_file() rather
    than having it injected into every system prompt call.

    If no project_context is available, writes instructions for the agent
    to infer the stack from the task description.
    """
    tech_stack_path = rudraanvil_dir / "tech_stack.md"

    if project_context and project_context.primary_language:
        lines = [
            "# Project Tech Stack\n\n",
            f"**Primary Language:** {project_context.primary_language}\n\n",
            f"**Framework:** {project_context.framework or 'Not specified'}\n\n",
            f"**Database:** {project_context.database or 'Not specified'}\n\n",
        ]
        if project_context.additional_context:
            lines.append(
                f"## Architecture Rules\n\n{project_context.additional_context}\n"
            )
        lines.append("\nAll code you write MUST use this exact tech stack.\n")
    else:
        lines = [
            "# Project Tech Stack\n\n",
            "No tech stack configured. Infer from the task description:\n\n",
            "- 'Flask ...' → Python + Flask\n",
            "- 'Django ...' → Python + Django\n",
            "- 'FastAPI ...' → Python + FastAPI\n",
            "- 'React ...' → JavaScript / TypeScript + React\n",
            "- 'Next.js ...' → TypeScript + Next.js\n",
            "- 'Express ...' → Node.js + Express\n",
            "- 'Spring ...' → Java + Spring Boot\n",
            "- 'Rails ...' → Ruby on Rails\n",
            "\nIf the stack is truly ambiguous, use ask_user() ONCE.\n",
        ]

    content = "".join(lines)
    tech_stack_path.write_text(content, encoding="utf-8")
    return content


async def create_main_agent(
    project_path: Path,
    task: str,
    project_context: Optional[ProjectContext] = None,
    command: str = "build",
    console: Optional[Console] = None,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> RudraAnvilAgent:
    """Factory function to create a main agent using deepagents."""
    console = console or Console()
    project_path = project_path.resolve()

    vfs = VirtualFileSystem(project_path)
    if project_path.exists():
        vfs.load_from_disk()

    context = AgentContext(
        project_path=project_path,
        task=task,
        vfs=vfs,
        console=console,
        project_context=project_context,
        dry_run=dry_run,
        verbose=verbose,
        command=command,
        **kwargs,
    )

    from langchain_ollama import ChatOllama

    model = ChatOllama(
        model=config.ollama.model,
        base_url=config.ollama.base_url,
        temperature=config.ollama.temperature,
        num_predict=config.ollama.num_predict,
        reasoning=True,
    )

    # ask_user / save_project_context are only safe in chat mode.
    # In build/auto/fix/edit the model loses task context after an ask_user
    # tool call and starts treating the user's one-word answer as a new task.
    # For those commands the tech stack is inferred from the task text instead.
    if command == "chat":
        custom_tools = create_interaction_tools(console, project_path)
    else:
        # Planning tools (update_plan / read_plan) go on the MAIN AGENT only.
        # The subagent is write-only and gets deepagents' built-in file tools.
        # Code execution tools are intentionally excluded — this agent only generates files.
        custom_tools = create_planning_tools(vfs, task=task)

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from rudraanvil.compat.deepagents_path import install_path_normalizer
    from rudraanvil.compat.overwrite_backend import OverwriteFilesystemBackend

    install_path_normalizer(project_path)

    filesystem_backend = OverwriteFilesystemBackend(
        root_dir=str(project_path),
        virtual_mode=True,
    )

    rudraanvil_dir = project_path / ".rudraanvil"
    rudraanvil_dir.mkdir(parents=True, exist_ok=True)

    # Ensure persistent memory file exists before MemoryMiddleware tries to load it.
    _ensure_agents_md(rudraanvil_dir, project_context)

    # Write tech stack to disk (for reference) and inline into system prompt
    # so the model doesn't need to read it voluntarily.
    tech_stack_content = _write_tech_stack_file(rudraanvil_dir, project_context)

    system_prompt = build_system_prompt(
        command=command,
        task=task,
        project_path=project_path,
        vfs=vfs,
        project_context=project_context,
        tech_stack_content=tech_stack_content,
        **kwargs,
    )

    checkpoints_db = str(rudraanvil_dir / "checkpoints.db")
    db_conn = await aiosqlite.connect(checkpoints_db)
    checkpointer = AsyncSqliteSaver(conn=db_conn)
    await checkpointer.setup()

    # Stable session ID for all commands — gives the LangGraph checkpointer
    # continuity across CLI invocations in the same project directory.
    # Delete .rudraanvil/session_id.txt to start a completely fresh session.
    session_id = get_or_create_session_id(rudraanvil_dir)

    # Subagent system prompt: explicit file-writing workflow.
    # The subagent does NOT get MemoryMiddleware — it receives full task context
    # from the main agent's delegation description instead.
    subagent_prompt = (
        "You are a file-writing assistant. You receive a task description listing files to create.\n"
        "Your ONLY job: write every listed file with COMPLETE, production-ready code.\n\n"
        "## WORKFLOW — write every file in the task description\n"
        "For EACH file listed:\n"
        "  1. Think through the COMPLETE implementation before writing\n"
        "  2. Call write_file(file_path='filename', content='...full code...') — raw code, no markdown fences\n"
        "  3. Move immediately to the next file — do NOT stop between files\n"
        "Keep going until EVERY file in the task description is written.\n\n"
        "## HARD CONSTRAINTS\n"
        "- RAW source code only in content= — NEVER wrap in ```fences```\n"
        "- COMPLETE code only — no stubs, no 'pass', no 'TODO', no Hello World\n"
        "- ONE write_file call per file — write it right the first time\n"
        "- Do NOT run code, install packages, or spawn subagents\n"
        "- Do NOT stop or say 'let me know' until ALL files are written\n\n"
        "## WRITE ORDER — dependencies first\n"
        "Write files in this order so imports resolve correctly:\n"
        "  config/deps (requirements.txt) → data models → auth/utility code → route handlers → entry point (app.py/main.py)\n\n"
        "## FILENAMES — use EXACTLY what the task description gives you\n"
        "- If the task says 'app.py' — write 'app.py', NOT 'src/app.py' or 'application.py'\n"
        "- Do NOT reorganize files into subdirectories unless the task explicitly asks for it\n"
        "- Do NOT rename files or split one file into multiple files\n\n"
        "## CODE QUALITY\n"
        "- Every function must be fully implemented with real logic\n"
        "- Use the EXACT framework named in the task (FastAPI = FastAPI, not Flask)\n"
        "- Include all imports at the top of each file\n"
        "- JWT auth: use python-jose or PyJWT; hash passwords with passlib/bcrypt\n"
        "- Database models: include all fields with correct types\n"
        "- API routes: full CRUD where appropriate — not just Hello World endpoints\n"
    )

    deep_agent = create_deep_agent(
        model=model,
        tools=custom_tools,
        system_prompt=system_prompt,
        backend=filesystem_backend,
        checkpointer=checkpointer,
        # MemoryMiddleware: loads .rudraanvil/AGENTS.md and injects into every
        # system prompt. Silently skips if the file is missing (safe on first run).
        # The agent updates the file via edit_file as it learns about the project.
        memory=[".rudraanvil/AGENTS.md"],
        middleware=[TaskAnchorMiddleware(task)],
        subagents=[
            {
                "name": "general-purpose",
                "description": "Writes ALL project files with complete code. Use for any coding task.",
                "system_prompt": subagent_prompt,
                "tools": [],  # only deepagents' built-in tools (write_file, edit_file, etc.)
                "middleware": [TaskAnchorMiddleware(task), ContinueAfterWriteMiddleware(task=task)],
            }
        ],
    )

    return RudraAnvilAgent(context, deep_agent, session_id, db_conn=db_conn)
