"""Orchestrator agent for Rudra — coordinates planner and coder agents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from deepagents import create_deep_agent

from rudra.config import config
from rudra.filesystem import VirtualFileSystem
from rudra.state import ProjectContext


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

    command: str = "auto"
    file_path: Optional[str] = None
    issue: Optional[str] = None

    planner_model: str = ""
    coder_model: str = ""


@dataclass
class AgentResult:
    """Result of an agent execution."""

    success: bool
    message: str
    files_created: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    iterations: int = 0
    todo_summary: str = ""


def _parse_pending_files(plan_content: str) -> list[str]:
    return [
        line.strip().replace("- [ ]", "").strip()
        for line in plan_content.splitlines()
        if "- [ ]" in line
    ]


def _check_off_file(plan_path: Path, filename: str) -> None:
    content = plan_path.read_text(encoding="utf-8")
    updated = content.replace(f"- [ ] {filename}", f"- [x] {filename}", 1)
    if updated != content:
        plan_path.write_text(updated, encoding="utf-8")


class RudraAgent:
    """Orchestrates planner + coder agents for Rudra."""

    def __init__(
        self,
        context: AgentContext,
        planner_agent,
        session_id: str,
        db_conn,
        coder_config: dict,
        plan_path: Path,
    ):
        self.context = context
        self.planner_agent = planner_agent
        self.session_id = session_id
        self.console = context.console
        self._db_conn = db_conn
        self._coder_config = coder_config
        self.plan_path = plan_path
        self.iterations = 0

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

    def _log_single_message(self, msg: Any, index: int, prefix: str = "") -> None:
        msg_type = type(msg).__name__
        tag = f"[{prefix}] " if prefix else ""

        if msg_type == "AIMessage":
            tool_calls = getattr(msg, "tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get("name", "?")
                    args = str(tc.get("args", {}))[:400]
                    self._log_always(
                        f"[bold cyan]{tag}→ [{index}] CALL[/bold cyan] [yellow]{name}[/yellow]  {args}"
                    )
            else:
                content = str(getattr(msg, "content", ""))[:300].replace("\n", " ")
                self._log_always(f"[bold cyan]{tag}← [{index}] AI[/bold cyan]  {content}")

        elif msg_type == "ToolMessage":
            content = str(getattr(msg, "content", ""))
            tool_name = getattr(msg, "name", "?")
            first_line = content.split("\n")[0] if content else ""
            is_error = (
                first_line.startswith("Error:")
                or first_line.startswith("Cannot write to")
                or "Error:" in first_line
                or "Traceback" in first_line
                or "Errno" in first_line
                or "not a valid tool" in content
                or "Input should be a valid string" in content
                or "BLOCKED:" in first_line
            )
            if is_error:
                self._log_always(
                    f"[bold red]{tag}✗ [{index}] ERROR from {tool_name}:[/bold red]\n[red]{content}[/red]"
                )
            else:
                self._log_always(f"[green]{tag}✓ [{index}] {tool_name}:[/green] {content}")

        elif msg_type == "HumanMessage":
            content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
            self._log_always(f"[dim]{tag}[{index}] USER: {content}[/dim]")

        else:
            content = str(getattr(msg, "content", ""))[:200].replace("\n", " ")
            self._log_always(f"[dim]{tag}[{index}] {msg_type}: {content}[/dim]")

    async def _stream_planner(self, messages: list[dict], thread_id: str) -> bool:
        """Stream the planner agent. Returns False if halted by guards."""
        lg_config = {"configurable": {"thread_id": thread_id}}
        processed = 0
        consecutive_failures = 0
        planning_tool_calls: dict[str, int] = {}
        MAX_PLANNING_CALLS = 4
        _halt = False

        async for chunk in self.planner_agent.astream(
            {"messages": messages},
            lg_config,
            stream_mode="values",
            subgraphs=True,
        ):
            if _halt:
                break

            namespace, event = (
                chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
            )
            msgs = event.get("messages", [])

            while processed < len(msgs):
                msg = msgs[processed]
                msg_type = type(msg).__name__

                if namespace:
                    self._log_always(f"[dim](planner subagent {':'.join(namespace)})[/dim]")

                self._log_single_message(msg, processed + 1, prefix="planner")

                if msg_type == "AIMessage":
                    for tc in getattr(msg, "tool_calls", []):
                        name = tc.get("name", "")
                        if name in ("write_file", "write_task_assignment"):
                            planning_tool_calls.clear()
                        elif name in ("update_plan", "read_plan"):
                            planning_tool_calls[name] = planning_tool_calls.get(name, 0) + 1
                            if planning_tool_calls[name] >= MAX_PLANNING_CALLS:
                                self._log_always(
                                    f"[bold yellow]!! Planner loop guard: '{name}' called "
                                    f"{planning_tool_calls[name]}x — stopping.[/bold yellow]"
                                )
                                _halt = True
                                break
                    if _halt:
                        break

                elif msg_type == "ToolMessage":
                    content = str(getattr(msg, "content", ""))
                    first_line = content.split("\n")[0] if content else ""
                    is_error = (
                        first_line.startswith("Error:")
                        or first_line.startswith("Cannot write to")
                        or "Error:" in first_line
                        or "Traceback" in first_line
                        or "Errno" in first_line
                    )
                    if is_error:
                        consecutive_failures += 1
                        if consecutive_failures >= 3:
                            self._log_always("[bold yellow]!! 3 consecutive planner failures — stopping.[/bold yellow]")
                            _halt = True
                    else:
                        consecutive_failures = 0

                if _halt:
                    break
                processed += 1

        return not _halt

    async def _stream_coder(self, coder_agent, messages: list[dict], thread_id: str) -> bool:
        """Stream a coder agent. Returns False if halted by guards."""
        lg_config = {"configurable": {"thread_id": thread_id}}
        processed = 0
        consecutive_failures = 0
        repeated_tool_calls: dict[tuple, int] = {}
        MAX_REPEATED_CALLS = 3
        _halt = False

        async for chunk in coder_agent.astream(
            {"messages": messages},
            lg_config,
            stream_mode="values",
            subgraphs=True,
        ):
            if _halt:
                break

            namespace, event = (
                chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)
            )
            msgs = event.get("messages", [])

            while processed < len(msgs):
                msg = msgs[processed]
                msg_type = type(msg).__name__

                if namespace:
                    self._log_always(f"[dim](coder subagent {':'.join(namespace)})[/dim]")

                self._log_single_message(msg, processed + 1, prefix="coder")

                if msg_type == "AIMessage":
                    for tc in getattr(msg, "tool_calls", []):
                        name = tc.get("name", "")
                        if name in ("write_file", "edit_file", "read_file"):
                            args = tc.get("args", {})
                            key_arg = args.get("file_path", args.get("path", ""))
                            call_key = (name, key_arg)
                            repeated_tool_calls[call_key] = repeated_tool_calls.get(call_key, 0) + 1
                            if repeated_tool_calls[call_key] >= MAX_REPEATED_CALLS:
                                self._log_always(
                                    f"[bold yellow]!! Coder loop guard: '{name}' on '{key_arg}' "
                                    f"repeated {repeated_tool_calls[call_key]}x — stopping.[/bold yellow]"
                                )
                                _halt = True
                                break
                    if _halt:
                        break

                elif msg_type == "ToolMessage":
                    content = str(getattr(msg, "content", ""))
                    first_line = content.split("\n")[0] if content else ""
                    is_error = (
                        first_line.startswith("Error:")
                        or first_line.startswith("Cannot write to")
                        or "Error:" in first_line
                        or "Traceback" in first_line
                        or "Errno" in first_line
                        or "not a valid tool" in content
                        or "Input should be a valid string" in content
                        or "BLOCKED:" in first_line
                    )
                    if is_error:
                        consecutive_failures += 1
                        if self.context.stop_on_error and consecutive_failures >= 3:
                            self._log_always("[bold yellow]!! 3 consecutive coder failures — stopping.[/bold yellow]")
                            _halt = True
                    else:
                        consecutive_failures = 0

                if _halt:
                    break
                processed += 1

        return not _halt

    async def _run_planner_phase(self) -> bool:
        """Phase 1: run planner to create PLAN.md and first task assignment."""
        self._log_always(
            f"\n[bold blue]🔵 Phase 1: Planning (model: {self.context.planner_model})[/bold blue]"
        )
        self._status("Analyzing task and creating plan...")
        return await self._stream_planner(
            [{"role": "user", "content": self.context.task}],
            thread_id=f"{self.session_id}-planner",
        )

    async def _request_task_assignment(self, filename: str) -> bool:
        """Ask the planner (continuing same thread) to write current_task.md for a file."""
        msg = (
            f"Write the task assignment for: `{filename}`\n\n"
            "Call write_task_assignment() now with:\n"
            f"- file_path: {filename}\n"
            "- instructions: complete, detailed spec for this file\n"
            "- context_files: any existing files the coder should read first\n\n"
            "STOP after write_task_assignment() returns."
        )
        return await self._stream_planner(
            [{"role": "user", "content": msg}],
            thread_id=f"{self.session_id}-planner",
        )

    async def _run_coder_for_file(self, filename: str, index: int, attempt: int = 0) -> bool:
        """Create a fresh coder agent and write the file specified in current_task.md.

        Each attempt gets a distinct thread ID so retries start with a clean context.
        """
        from rudra.agent.coder_agent import create_coder_agent

        coder = create_coder_agent(**self._coder_config)
        thread_id = f"{self.session_id}-coder-{index}-{attempt}"
        coder_task = (
            f"Read .rudra/current_task.md and write the file specified there: `{filename}`.\n"
            f"Call write_file(file_path='{filename}', content='...') then stop."
        )
        return await self._stream_coder(
            coder,
            [{"role": "user", "content": coder_task}],
            thread_id=thread_id,
        )

    async def run(self) -> AgentResult:
        """Orchestrate planner → coder loop."""
        try:
            if self.context.dry_run:
                self._status("Dry run — no files written.")
                return AgentResult(
                    success=True,
                    message="Dry run completed (no files written)",
                    files_created=[],
                    files_modified=[],
                )

            self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")

            # Phase 1: Planning
            await self._run_planner_phase()

            if not self.plan_path.exists():
                return AgentResult(
                    success=False,
                    message="Planner did not create PLAN.md — cannot proceed.",
                )

            plan_content = self.plan_path.read_text(encoding="utf-8")
            pending_files = _parse_pending_files(plan_content)
            total = len(pending_files)

            if total == 0:
                return AgentResult(
                    success=False,
                    message="PLAN.md has no pending files.",
                )

            self._log_always(f"\n[bold]📋 Plan: {total} file(s) to generate[/bold]")

            MAX_CODER_RETRIES = 2

            # Phase 2: Coding loop
            files_created: list[str] = []
            for i, filename in enumerate(pending_files, start=1):
                self._log_always(
                    f"\n[bold green]🟢 Coding [{i}/{total}]: {filename} "
                    f"(model: {self.context.coder_model})[/bold green]"
                )

                # For files after the first, ask planner to write current_task.md
                if i > 1:
                    self._log_always(f"[dim]  Requesting task assignment for {filename}...[/dim]")
                    await self._request_task_assignment(filename)

                # Verify current_task.md exists before running coder
                task_assignment_path = self.plan_path.parent / "current_task.md"
                if not task_assignment_path.exists():
                    self._log_always(f"[red]🔴 No current_task.md for {filename} — skipping[/red]")
                    continue

                file_written = False
                for attempt in range(1 + MAX_CODER_RETRIES):
                    if attempt > 0:
                        self._log_always(
                            f"[yellow]🔄 Retry {attempt}/{MAX_CODER_RETRIES} for {filename}[/yellow]"
                        )

                    await self._run_coder_for_file(filename, i, attempt=attempt)

                    file_on_disk = self.context.project_path / filename
                    if file_on_disk.exists():
                        _check_off_file(self.plan_path, filename)
                        files_created.append(filename)
                        self._log_always(f"[green]✅ {filename} written[/green]")
                        file_written = True
                        break
                    else:
                        self._log_always(
                            f"[red]🔴 {filename} not found on disk (attempt {attempt + 1})[/red]"
                        )

                if not file_written:
                    self._log_always(
                        f"[bold red]⛔ {filename} failed after {1 + MAX_CODER_RETRIES} attempts — skipping[/bold red]"
                    )

            self._log_always(
                f"\n[bold]🏁 Complete: {len(files_created)}/{total} files generated[/bold]"
            )

            return AgentResult(
                success=len(files_created) > 0,
                message=f"{len(files_created)}/{total} files generated",
                files_created=files_created,
                files_modified=[],
                iterations=total,
            )

        except Exception:
            import traceback
            self._log_always("[bold red]\n!! Agent crashed — full traceback:[/bold red]")
            self._log_always(traceback.format_exc())
            raise


def _ensure_agents_md(rudra_dir: Path, project_context: Optional[ProjectContext]) -> None:
    """Create a starter AGENTS.md if one does not already exist."""
    agents_md = rudra_dir / "AGENTS.md"
    if agents_md.exists():
        return

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
    rudra_dir: Path,
    project_context: Optional[ProjectContext],
) -> str:
    """Write project tech stack to .rudra/tech_stack.md. Returns the content."""
    tech_stack_path = rudra_dir / "tech_stack.md"

    if project_context and project_context.primary_language:
        lines = [
            "# Project Tech Stack\n\n",
            f"**Primary Language:** {project_context.primary_language}\n\n",
            f"**Framework:** {project_context.framework or 'Not specified'}\n\n",
            f"**Database:** {project_context.database or 'Not specified'}\n\n",
        ]
        if project_context.additional_context:
            lines.append(f"## Architecture Rules\n\n{project_context.additional_context}\n")
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
            "\nIf the stack is truly ambiguous, infer the most likely one and proceed.\n",
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
) -> RudraAgent:
    """Factory function — creates the planner + stores coder config for orchestration."""
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
        planner_model=config.ollama.model_planner,
        coder_model=config.ollama.model_coder,
        **kwargs,
    )

    import aiosqlite
    import uuid
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from rudra.compat.deepagents_path import install_path_normalizer
    from deepagents.backends.filesystem import FilesystemBackend

    install_path_normalizer(project_path, plan_path=project_path / ".rudra" / "PLAN.md")

    # 0.7.4's FilesystemBackend.write() creates or overwrites (O_TRUNC +
    # O_NOFOLLOW), which is the only reason OverwriteFilesystemBackend
    # existed. Markdown-fence stripping now lives solely in
    # FixWriteParamsMiddleware, wired into both agents. See TODO.md U.3.
    filesystem_backend = FilesystemBackend(
        root_dir=str(project_path),
        virtual_mode=True,
    )

    rudra_dir = project_path / ".rudra"
    rudra_dir.mkdir(parents=True, exist_ok=True)

    _ensure_agents_md(rudra_dir, project_context)
    tech_stack_content = _write_tech_stack_file(rudra_dir, project_context)

    checkpoints_db = str(rudra_dir / "checkpoints.db")
    db_conn = await aiosqlite.connect(checkpoints_db)
    checkpointer = AsyncSqliteSaver(conn=db_conn)
    await checkpointer.setup()

    session_id = uuid.uuid4().hex[:12]

    from rudra.agent.planner_agent import create_planner_agent

    planner = create_planner_agent(
        task=task,
        project_path=project_path,
        vfs=vfs,
        tech_stack_content=tech_stack_content,
        filesystem_backend=filesystem_backend,
        checkpointer=checkpointer,
        console=console,
    )

    coder_config = {
        "project_path": project_path,
        "vfs": vfs,
        "tech_stack_content": tech_stack_content,
        "filesystem_backend": filesystem_backend,
        "checkpointer": checkpointer,
    }

    plan_path = rudra_dir / "PLAN.md"

    return RudraAgent(
        context=context,
        planner_agent=planner,
        session_id=session_id,
        db_conn=db_conn,
        coder_config=coder_config,
        plan_path=plan_path,
    )
