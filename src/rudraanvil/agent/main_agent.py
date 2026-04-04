"""Main supervisor agent for RudraAnvil using deepagents framework."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from rich.console import Console
from deepagents import create_deep_agent

from rudraanvil.config import config
from rudraanvil.filesystem import VirtualFileSystem
from rudraanvil.middleware import TaskAnchorMiddleware
from rudraanvil.state import get_or_create_session_id, ProjectContext
from rudraanvil.tools.interaction_tools import create_interaction_tools


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
- Write COMPLETE, working code — never placeholders, stubs, or "TODO" comments
- Do NOT execute code, run tests, or install packages — file generation only
- NEVER use the `task` subagent tool — write all files yourself directly with write_file

Project structure:
{vfs.get_tree()}
"""

    # Tech stack context
    if project_context and project_context.primary_language:
        base += f"""
## Project Tech Stack
- Language:  {project_context.primary_language}
- Framework: {project_context.framework or 'Not specified'}
- Database:  {project_context.database or 'Not specified'}
- Notes:     {project_context.additional_context or 'None'}

All generated code must strictly follow the above tech stack.
"""
    else:
        base += """
## Tech Stack
Infer the stack from the task description — do NOT ask the user:
- "Flask ..."       → Python + Flask
- "Django ..."      → Python + Django
- "FastAPI ..."     → Python + FastAPI
- "React ..."       → JavaScript / TypeScript + React
- "Next.js ..."     → TypeScript + Next.js
- "Express ..."     → Node.js + Express
- "Spring ..."      → Java + Spring Boot
- "Rails ..."       → Ruby on Rails
If the stack is truly ambiguous (no framework or language hint), use ask_user() ONCE.
"""

    # Command-specific guidance
    if command in ("build", "auto"):
        existing = "Existing project — read relevant files before modifying." if vfs.files else "New project — start from scratch."
        base += f"""
## Task
{task}
{existing}

## Workflow
1. Call write_todos() once with every file you need to create/modify
2. For each file in order: call write_file() with the COMPLETE file content
3. Mark each todo as completed immediately after writing the file
4. Do NOT spawn subagents — write all files yourself

## After completing all files
Update `.rudraanvil/AGENTS.md` using edit_file to record:
- Confirmed tech stack
- Files created and what each does
- Key architecture decisions
- Anything useful to know for the next session
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

            lg_config = {
                "configurable": {"thread_id": self.session_id},
            }

            async for chunk in self.agent.astream(
                {"messages": [{"role": "user", "content": self.context.task}]},
                lg_config,
                stream_mode="values",
                subgraphs=True,
            ):
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

                    if msg_type == "ToolMessage":
                        content = str(getattr(msg, "content", ""))
                        is_error = (
                            content.startswith("Error:")
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
                                    "[bold red]!! Halting: 3 consecutive tool failures[/bold red]"
                                )
                                raise RuntimeError(
                                    f"Repeated tool errors in {':'.join(namespace) or 'main agent'}:\n{content}"
                                )
                        else:
                            consecutive_failures = 0

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
                files_created = list(new_paths - original_paths)
                files_modified = [
                    p
                    for p in new_paths & original_paths
                    if new_vfs.files[p] != self.context.vfs.files.get(p)
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
        reasoning=False,
    )

    # ask_user / save_project_context are only safe in chat mode.
    # In build/auto/fix/edit the model loses task context after an ask_user
    # tool call and starts treating the user's one-word answer as a new task.
    # For those commands the tech stack is inferred from the task text instead.
    if command == "chat":
        custom_tools = create_interaction_tools(console, project_path)
    else:
        custom_tools = []

    system_prompt = build_system_prompt(
        command=command,
        task=task,
        project_path=project_path,
        vfs=vfs,
        project_context=project_context,
        **kwargs,
    )

    from deepagents.backends import FilesystemBackend
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from rudraanvil.compat.deepagents_path import install_path_normalizer

    install_path_normalizer(project_path)

    filesystem_backend = FilesystemBackend(
        root_dir=str(project_path),
        virtual_mode=True,
    )

    rudraanvil_dir = project_path / ".rudraanvil"
    rudraanvil_dir.mkdir(parents=True, exist_ok=True)

    # Ensure persistent memory file exists before MemoryMiddleware tries to load it.
    _ensure_agents_md(rudraanvil_dir, project_context)

    checkpoints_db = str(rudraanvil_dir / "checkpoints.db")
    db_conn = await aiosqlite.connect(checkpoints_db)
    checkpointer = AsyncSqliteSaver(conn=db_conn)
    await checkpointer.setup()

    if command == "chat":
        session_id = get_or_create_session_id(rudraanvil_dir)
    else:
        session_id = str(uuid.uuid4())

    # Subagent system prompt: explicit file-writing workflow.
    # The subagent does NOT get MemoryMiddleware — it receives full task context
    # from the main agent's delegation description instead.
    subagent_prompt = (
        "You are a file-generation assistant. Your ONLY job is to write complete project files.\n\n"
        "## WORKFLOW\n"
        "1. Call write_todos() once to list every file to create\n"
        "2. For EACH file in the todo list:\n"
        "   a. Think through the ENTIRE file content in your head first\n"
        "   b. Call write_file(file_path='...', content='...') with the COMPLETE final code\n"
        "   c. Mark the todo completed immediately after\n"
        "3. Repeat step 2 for every file — do NOT stop until all todos are completed\n\n"
        "## CRITICAL — ONE COMPLETE FILE PER write_file CALL\n"
        "- write_file CANNOT overwrite an existing file — if you write a partial/skeleton file,\n"
        "  you are stuck in an edit_file loop that almost always fails\n"
        "- NEVER write a skeleton, stub, or placeholder that you plan to expand later\n"
        "- Think through ALL the code before calling write_file — write it once, write it right\n\n"
        "## ARCHITECTURE — use separate files, never cram everything into one file\n"
        "For a Flask app, for example, create these separate files:\n"
        "  - requirements.txt  → all pip dependencies, one per line\n"
        "  - models.py         → all SQLAlchemy models\n"
        "  - routes.py         → all Flask routes / blueprints\n"
        "  - app.py            → Flask app factory + db.init_app + run block ONLY\n"
        "Write them in this order: requirements.txt → models.py → routes.py → app.py\n"
        "(app.py is last because it imports from the others)\n\n"
        "## FILE PATH RULES\n"
        "- Use RELATIVE paths only: 'app.py', 'src/models.py'\n"
        "- NEVER use absolute paths\n\n"
        "## IF edit_file IS EVER NEEDED\n"
        "- Always call read_file first to get the current file content\n"
        "- Copy the old_string CHARACTER-FOR-CHARACTER from the read_file output\n"
        "- Never reconstruct old_string from memory — one wrong space causes failure\n\n"
        "## HARD CONSTRAINTS\n"
        "- Write COMPLETE, working code — never stubs, TODOs, or '# ... rest of code'\n"
        "- Do NOT execute code, run tests, or install packages\n"
        "- Do NOT spawn further subagents\n\n"
        f"Task: {task}"
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
                "description": "Writes project files using write_file for each planned item.",
                "system_prompt": subagent_prompt,
                "middleware": [TaskAnchorMiddleware(task)],
            }
        ],
    )

    return RudraAnvilAgent(context, deep_agent, session_id, db_conn=db_conn)
