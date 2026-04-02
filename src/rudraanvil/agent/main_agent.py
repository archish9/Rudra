"""Main supervisor agent for RudraAnvil using deepagents framework."""

from __future__ import annotations

import asyncio
import os
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from deepagents import create_deep_agent

from rudraanvil.config import config
from rudraanvil.filesystem import VirtualFileSystem
from rudraanvil.state import get_or_create_session_id, ProjectContext
from rudraanvil.tools.code_tools import create_code_tools
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

    # Execution settings
    dry_run: bool = False
    verbose: bool = False
    max_iterations: int = 100
    stop_on_error: bool = True  # If True, abort execution immediately on tool error

    # Chat history for chat mode
    chat_history: list[dict] = field(default_factory=list)

    # Command-specific context
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


def build_system_prompt(command: str, task: str, project_path: Path, vfs: VirtualFileSystem, project_context: Optional[ProjectContext] = None, **kwargs) -> str:
    """Build the system prompt for the agent based on command type."""
    
    # Base prompt
    base_prompt = f"""You are RudraAnvil, an expert autonomous coding agent.

Project root: {project_path}

## RULE #0 — MANDATORY BEFORE ANYTHING ELSE
Your very first tool call on every task MUST be update_plan().
Write a markdown checklist of every file you need to create.
Do NOT call the `task` tool or any other tool before update_plan has been called.
Violating this rule breaks the project's context management system.

Project structure:
{vfs.get_tree()}

You can:
- Plan and decompose tasks using the update_plan tool (writes to .rudraanvil/PLAN.md)
- Read your current plan with read_plan() or read_file('.rudraanvil/PLAN.md')
- Check off completed items with edit_file('.rudraanvil/PLAN.md', '- [ ] <task>', '- [x] <task>')
- Read, write, and edit files using built-in file system tools
- Spawn sub-agents for specialized tasks

CRITICAL: NEVER use write_todos — it bloats the context window. Always use update_plan instead.
CRITICAL: Do NOT attempt to execute code, run tests, or install packages. Your sole purpose is to generate and edit files.
"""

    if project_context and project_context.primary_language:
        base_prompt += f"""
## Project Tech Stack
- Primary Language: {project_context.primary_language}
- Frameworks/Libraries: {project_context.framework or 'Not specified'}
- Database: {project_context.database or 'Not specified'}
- Additional Rules/Architecture: {project_context.additional_context or 'None'}

Ensure all generated code adheres STRICTLY to the above tech stack.
"""
    else:
        base_prompt += """
## Project Context Unknown

If the current task requires language, framework, or database decisions:
1. Call ask_user() with ONE focused question relevant to this specific task.
   Example: ask_user("What programming language should I use for this project?")
2. Ask only what you need — do NOT ask generic questions if the task is self-evident.
3. After gathering answers, call save_project_context({"primary_language": "...", ...})
   to persist them so future sessions start with full context.

If the task needs no tech-stack knowledge (e.g. creating a .gitignore), skip asking entirely.
"""
    
    # Add command-specific guidance
    if command == "build":
        existing = "This is an existing project." if vfs.files else "Starting from scratch."
        base_prompt += f"""
Task: {task}
{existing}

You write ALL files yourself using the built-in file system tools. There is no subagent.

Required workflow:
1. **Plan**: Call update_plan() with a markdown checklist of every file to create/modify:
   update_plan("# Build Plan\n- [ ] Create main.py\n- [ ] Create requirements.txt\n- [ ] Create README.md")
2. **Write**: Use write_file() to create each file with complete, correct code.
3. **Verify**: Use ls() or read_file() to confirm the file was written correctly.
4. **Check off**: Use edit_file() to mark each item done in PLAN.md:
   edit_file('.rudraanvil/PLAN.md', '- [ ] Create main.py', '- [x] Create main.py')
5. **Iterate**: If a file is wrong or missing, use edit_file() or write_file() again to fix it.

File system tools available (DeepAgents built-ins):
- List directory:     ls(path=".")
- Read a file:        read_file(file_path="filename.txt")
- Create a new file:  write_file(file_path="filename.txt", content="...full file content...")
- Edit existing file: edit_file(file_path="filename.txt", old_string="old text", new_string="new text")
- Find files:         glob(pattern="**/*.py")
- Search content:     grep(pattern="search term", path=".")

CRITICAL — File Path Rules:
- Use RELATIVE paths only (e.g., "main.py", "app/routes.py", "requirements.txt")
- NEVER start a path with a slash (NEVER use "/main.py").
- write_file creates a brand-new file — use edit_file to modify an existing file.

CRITICAL: NEVER use write_todos. The plan lives in .rudraanvil/PLAN.md only.
"""
    
    elif command == "chat":
        user_input = kwargs.get("user_input", task)
        base_prompt += f"""
You are in interactive chat mode, helping with ongoing development.

User's request: {user_input}

Help the user with their request. Be conversational but efficient.
"""
    
    elif command == "fix":
        issue = kwargs.get("issue", task)
        base_prompt += f"""
You are debugging and fixing an issue.

Issue description: {issue}

Approach:
1. Analyze the error/issue carefully
2. Identify the root cause
3. Plan a minimal fix
4. Implement the fix
5. Review the code to ensure the fix is correct (Do NOT run tests or execute code)
"""
    
    elif command == "edit":
        file_path = kwargs.get("file_path", "")
        instruction = task
        content = vfs.read_file(file_path) if file_path else ""
        base_prompt += f"""
You are making a targeted edit to a specific file.

File: {file_path}
Instruction: {instruction}

Current file content:
{content or "File not found"}

Make the requested change precisely. Don't modify unrelated code.
"""
    
    elif command == "review":
        base_prompt += f"""
You are reviewing code for quality and issues.

Review the code and provide:
1. **Security Issues**: Any vulnerabilities or unsafe practices
2. **Performance Issues**: Inefficiencies or bottlenecks
3. **Code Quality**: Style, readability, maintainability
4. **Best Practices**: Violations of common patterns
5. **Suggestions**: Improvements that could be made

Format as a clear, actionable report. Do NOT make any changes, only report findings.
"""
    
    elif command == "suggest":
        base_prompt += f"""
You are suggesting improvements without applying them.

Task: {task}

Provide detailed suggestions including:
1. What changes would be beneficial
2. Why they would help
3. Example code snippets or diffs
4. Potential risks or trade-offs

Do NOT apply changes. Present them for the user to review.
"""

    else:  # "auto" — default when user runs: rudraanvil "some prompt"
        existing = "This is an existing project." if vfs.files else "Starting from scratch."
        base_prompt += f"""
Task: {task}
{existing}

Understand the user's intent and act accordingly:
- If creating or building something → plan with update_plan() then write files
- If fixing a bug → diagnose, plan a minimal fix, apply it
- If editing a file → make the targeted change only
- If asking a question → answer clearly without writing files

When writing or modifying files, follow this workflow:
1. **Plan**: Call update_plan() with a markdown checklist
2. **Write/Edit**: Use write_file() for new files, edit_file() for changes
3. **Verify**: Use ls() or read_file() to confirm changes
4. **Check off**: Mark items done in PLAN.md with edit_file()

File system tools:
- ls(path=".")
- read_file(file_path="filename.txt")
- write_file(file_path="filename.txt", content="...full content...")
- edit_file(file_path="filename.txt", old_string="old", new_string="new")
- glob(pattern="**/*.py")
- grep(pattern="term", path=".")

CRITICAL — Use RELATIVE paths only. NEVER start with a slash.
"""

    return base_prompt


class RudraAnvilAgent:
    """Wrapper around deepagents for RudraAnvil-specific functionality."""

    def __init__(self, context: AgentContext, deep_agent, session_id: str, db_conn=None):
        """Initialize RudraAnvil agent wrapper.

        Args:
            context: Agent execution context
            deep_agent: The deepagents agent instance
            session_id: LangGraph thread_id for this project's checkpoint thread
            db_conn: aiosqlite connection to close on cleanup
        """
        self.context = context
        self.agent = deep_agent
        self.session_id = session_id
        self.console = context.console
        self.iterations = 0
        self._db_conn = db_conn

    async def close(self) -> None:
        """Close the underlying database connection."""
        if self._db_conn is not None:
            await self._db_conn.close()
            self._db_conn = None
    
    def _log(self, message: str, style: str = "") -> None:
        """Log a debug message — only shown if verbose=True."""
        if self.context.verbose:
            if style:
                self.console.print(f"[{style}]{message}[/{style}]")
            else:
                self.console.print(message)

    def _log_always(self, message: str, style: str = "") -> None:
        """Log a message that is ALWAYS shown (not gated by verbose)."""
        if style:
            self.console.print(f"[{style}]{message}[/{style}]")
        else:
            self.console.print(message)

    def _status(self, message: str) -> None:
        """Show a status message."""
        self.console.print(f"[dim]→ {message}[/dim]")

    def _log_messages(self, messages: list) -> None:
        """Log all agent messages with full detail — always shown by default.
        
        Shows tool names, arguments, full outputs, and highlights errors.
        Controlled by VERBOSE=false in .env to silence (except for real-time streaming).
        """
        if not self.context.verbose and not getattr(self, "_force_log", False):
            return

        self._log_always(f"\n[bold]Agent trace — {len(messages)} messages[/bold]", "cyan")
        for i, msg in enumerate(messages):
            msg_type = type(msg).__name__
            role = getattr(msg, 'type', msg_type).upper()

            # ── AIMessage ──
            if msg_type == "AIMessage":
                tool_calls = getattr(msg, 'tool_calls', [])
                if tool_calls:
                    for tc in tool_calls:
                        name = tc.get('name', '?')
                        args = tc.get('args', {})
                        args_str = str(args)[:400]
                        self._log_always(
                            f"[bold cyan]→ [{i+1}] CALL[/bold cyan] [yellow]{name}[/yellow]  {args_str}"
                        )
                else:
                    content = str(getattr(msg, 'content', ''))[:300].replace('\n', ' ')
                    self._log_always(f"[bold cyan]← [{i+1}] AI[/bold cyan]  {content}")

            # ── ToolMessage ──
            elif msg_type == "ToolMessage":
                content = str(getattr(msg, 'content', ''))
                tool_name = getattr(msg, 'name', '?')
                is_error = (
                    "Error" in content or "error" in content
                    or "Errno" in content or "Traceback" in content
                    or "not a valid tool" in content
                )
                # Truncate long outputs but keep more for errors
                #limit = 800 if is_error else 400
                #preview = content[:limit].replace('\n', ' ↵ ')
                if is_error:
                    self._log_always(
                        f"[bold red]✗ [{i+1}] ERROR from {tool_name}:[/bold red] {content}"
                    )
                else:
                    self._log_always(
                        f"[green]✓ [{i+1}] {tool_name}:[/green] {content}"
                    )

            # ── HumanMessage ──
            elif msg_type == "HumanMessage":
                content = str(getattr(msg, 'content', ''))[:200].replace('\n', ' ')
                self._log_always(f"[dim][{i+1}] USER: {content}[/dim]")

            else:
                content = str(getattr(msg, 'content', ''))[:200].replace('\n', ' ')
                self._log_always(f"[dim][{i+1}] {msg_type}: {content}[/dim]")
                
    def _log_single_message(self, msg: Any, index: int) -> None:
        """Log a single message as it arrives in the stream."""
        msg_type = type(msg).__name__
        
        # ── AIMessage ──
        if msg_type == "AIMessage":
            tool_calls = getattr(msg, 'tool_calls', [])
            if tool_calls:
                for tc in tool_calls:
                    name = tc.get('name', '?')
                    args = tc.get('args', {})
                    args_str = str(args)[:400]
                    self._log_always(
                        f"[bold cyan]→ [{index}] CALL[/bold cyan] [yellow]{name}[/yellow]  {args_str}"
                    )
            else:
                content = str(getattr(msg, 'content', ''))[:300].replace('\n', ' ')
                self._log_always(f"[bold cyan]← [{index}] AI[/bold cyan]  {content}")

        # ── ToolMessage ──
        elif msg_type == "ToolMessage":
            content = str(getattr(msg, 'content', ''))
            tool_name = getattr(msg, 'name', '?')
            is_error = (
                content.startswith("Error:") or "Error:" in content or "Traceback" in content
                or "Errno" in content or "not a valid tool" in content
                or "Input should be a valid string" in content
            )                        
            
            if is_error:
                self._log_always(
                    f"[bold red]✗ [{index}] ERROR from {tool_name}:[/bold red]\n[red]{content}[/red]"
                )
            else:
                self._log_always(
                    f"[green]✓ [{index}] {tool_name}:[/green] {content}"
                )

        # ── HumanMessage ──
        elif msg_type == "HumanMessage":
            content_str = str(getattr(msg, 'content', ''))
            content = content_str[:200].replace('\n', ' ')
            self._log_always(f"[dim][{index}] USER: {content}[/dim]")

        else:
            content_str = str(getattr(msg, 'content', ''))
            content = content_str[:200].replace('\n', ' ')
            self._log_always(f"[dim][{index}] {msg_type}: {content}[/dim]")
    
    async def run(self) -> AgentResult:
        """Run the full agent workflow.
        
        Returns:
            AgentResult with execution summary
        """
        try:
            self._status("Planning and executing task...")
            
            # Using stream to show real-time progress
            self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")
            
            final_state = None
            processed_messages = 0
            last_tool_call = None
            consecutive_failures = 0
            
            # Build the user message. For build/auto commands, prepend explicit
            # step-by-step instructions so local LLMs don't skip write_file.
            user_content = self.context.task
            if self.context.command in ("build", "auto"):
                user_content = (
                    "Follow these steps IN ORDER. Do not skip any step.\n\n"
                    "STEP 1 — Call update_plan() ONCE with a checklist of every file to create:\n"
                    "  update_plan('# Plan\\n- [ ] Create app.py\\n- [ ] Create models.py\\n...')\n\n"
                    "STEP 2 — Work through the plan ONE file at a time:\n"
                    "  a) Call write_file(file_path='...', content='...COMPLETE file content...') to create the file\n"
                    "  b) ONLY AFTER writing the file, check it off:\n"
                    "     edit_file('.rudraanvil/PLAN.md', '- [ ] Create app.py', '- [x] Create app.py')\n"
                    "  c) Repeat for the next file\n\n"
                    "CRITICAL RULES:\n"
                    "- NEVER mark an item done unless you have ALREADY called write_file() for it\n"
                    "- NEVER call update_plan() a second time — use edit_file() to check off items\n"
                    "- write_file() content must be COMPLETE, working code — not a placeholder\n\n"
                    f"YOUR TASK: {self.context.task}"
                )

            # Run deepagents as an async generator using astream.
            # LangGraph checkpointer requires thread_id in the configurable dict
            # so it knows which checkpoint thread to save/load from.
            lg_config = {
                "recursion_limit": 100,
                "configurable": {"thread_id": self.session_id},
            }
            async for chunk in self.agent.astream(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": user_content
                        }
                    ]
                },
                lg_config,
                stream_mode="values",
                subgraphs=True
            ):
                namespace, event = chunk if isinstance(chunk, tuple) and len(chunk) == 2 else ((), chunk)

                final_state = event
                messages = event.get("messages", [])
                
                # If there are new messages, log them and check for errors
                while processed_messages < len(messages):
                    msg = messages[processed_messages]
                    msg_type = type(msg).__name__
                    
                    # Display the namespace if inside a subagent
                    if namespace:
                        self._log_always(f"[dim](subagent {':'.join(namespace)})[/dim]")
                    
                    self._log_single_message(msg, processed_messages + 1)
                    
                    # Error and Loop Detection
                    if msg_type == "AIMessage" and hasattr(msg, 'tool_calls') and msg.tool_calls:
                        last_tool_call = str(msg.tool_calls)
                    
                    elif msg_type == "ToolMessage":
                        content = str(getattr(msg, 'content', ''))
                        is_error = (
                            content.startswith("Error:") or "Error:" in content or "Traceback" in content
                            or "Errno" in content or "not a valid tool" in content
                            or "Input should be a valid string" in content
                        )
                        
                        if is_error:
                            consecutive_failures += 1
                            if self.context.stop_on_error:
                                self._log_always(f"[bold red]!! Halting execution: Tool failure detected in {':'.join(namespace) or 'main agent'}[/bold red]")
                                self._log_always(f"[bold red]{content}[/bold red]")
                                raise RuntimeError(f"Tool error in {':'.join(namespace) or 'main agent'}:\n{content}")
                            if consecutive_failures >= 3:
                                return AgentResult(
                                    success=False,
                                    message=f"Execution aborted: Detected repeating failure loop.\nLast error: {content}",
                                    iterations=self.iterations
                                )
                        else:
                            consecutive_failures = 0 # Reset on success
                            
                    processed_messages += 1

            if not final_state:
                 return AgentResult(success=False, message="Agent stream returned no state.", iterations=0)

            if self.context.verbose and isinstance(final_state, dict):
                self._log(f"Result state keys: {list(final_state.keys())}", "yellow")
            
            # Extract final response
            # deepagents returns a dict with 'messages' key containing AIMessage objects
            messages = []
            if isinstance(final_state, dict):
                 messages = final_state.get("messages", [])
            if messages:
                final_message = messages[-1]
                # AIMessage has a .content attribute, not .get() method
                response_content = final_message.content if hasattr(final_message, 'content') else str(final_message)
            else:
                response_content = "No response"
            
            self._log(f"Final response preview: {response_content[:200]}...", "dim")
            
            # Sync files if not dry run
            if not self.context.dry_run:
                # deepagents writes files directly to disk via FilesystemBackend.
                # Reload the VFS from disk to pick up all files written by deepagents,
                # then diff against the original snapshot to report accurate counts.
                new_vfs = VirtualFileSystem(self.context.project_path)
                if self.context.project_path.exists():
                    new_vfs.load_from_disk()
                
                # Determine created vs modified files by comparing with original VFS
                original_paths = set(self.context.vfs.files.keys())
                new_paths = set(new_vfs.files.keys())
                files_created = list(new_paths - original_paths)
                files_modified = [
                    p for p in new_paths & original_paths
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
                # Dry run - show what would be done
                self._status("Dry run - showing planned changes:")
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
        """Handle a single turn in chat mode.
        
        Args:
            user_input: The user's message
            
        Returns:
            Agent's response
        """
        # Add to chat history
        self.context.chat_history.append({"role": "user", "content": user_input})
        
        # Build messages list
        messages = []
        for msg in self.context.chat_history:
            messages.append(msg)
        
        # Run agent with recursion limit for chat mode.
        # LangGraph checkpointer requires thread_id in configurable dict.
        lg_config = {
            "recursion_limit": 100,
            "configurable": {"thread_id": self.session_id},
        }
        result = await asyncio.to_thread(
            self.agent.invoke,
            {"messages": messages},
            lg_config
        )
        
        # Extract response
        # deepagents returns a dict with 'messages' key containing AIMessage objects
        result_messages = result.get("messages", [])
        if result_messages:
            final_message = result_messages[-1]
            # AIMessage has a .content attribute, not .get() method
            response = final_message.content if hasattr(final_message, 'content') else str(final_message)
        else:
            response = "No response"
        
        # Add to history
        self.context.chat_history.append({"role": "assistant", "content": response})
        
        return response


async def create_main_agent(
    project_path: Path,
    task: str,
    project_context: Optional[ProjectContext] = None,
    command: str = "build",
    console: Optional[Console] = None,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs
) -> RudraAnvilAgent:
    """Factory function to create a main agent using deepagents.
    
    Args:
        project_path: Path to the project
        task: Task description
        command: CLI command being executed
        console: Rich console for output
        dry_run: If True, don't write files
        verbose: If True, show detailed output
        **kwargs: Additional context arguments
        
    Returns:
        Configured RudraAnvilAgent instance
    """
    console = console or Console()
    project_path = project_path.resolve()
    
    # Initialize virtual filesystem (for tracking/preview)
    vfs = VirtualFileSystem(project_path)
    if project_path.exists():
        vfs.load_from_disk()
    
    # Create context
    context = AgentContext(
        project_path=project_path,
        task=task,
        vfs=vfs,
        console=console,
        project_context=project_context,
        dry_run=dry_run,
        verbose=verbose,
        command=command,
        **kwargs
    )
    
    
    # Create Ollama model explicitly for proper configuration
    # Deepagents requires a configured model object, not just a string
    from langchain_ollama import ChatOllama
    
    model = ChatOllama(
        model=config.ollama.model,
        base_url=config.ollama.base_url,
        temperature=config.ollama.temperature,
        num_predict=config.ollama.num_predict,
        reasoning=False,  # Disable qwen3 thinking mode — with think=True the model
                          # puts all output in message.thinking and content is empty,
                          # so no tool_calls are ever generated and the graph exits immediately.
    )
    
    # Custom tools: code tools (currently empty) + filesystem planning tools
    custom_tools = (
        create_code_tools(vfs)
        + create_planning_tools(vfs)
        + create_interaction_tools(console, project_path)
    )
    
    # Build system prompt
    system_prompt = build_system_prompt(
        command=command,
        task=task,
        project_path=project_path,
        vfs=vfs,
        project_context=project_context,
        **kwargs
    )
    
    # Create deep agent with configured Ollama model and REAL filesystem backend
    # Deepagents will handle:
    # - Built-in file system tools (read_file, write_file, edit_file, ls)
    # - Built-in planning tool (write_todos)
    # - Subagent spawning
    # - Tool calling orchestration
    
    from deepagents.backends import FilesystemBackend
    import aiosqlite
    # langgraph 1.x: AsyncSqliteSaver is in `langgraph.checkpoint.sqlite.aio`
    # (package: langgraph-checkpoint-sqlite). Use for async astream() support.
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    filesystem_backend = FilesystemBackend(
        root_dir=str(project_path),  # Must be absolute path
        virtual_mode=True  # Anchors all paths to root_dir, prevents absolute path escapes
    )

    # Persistent async checkpointing. aiosqlite.connect() returns an open
    # connection (not a context manager) — it stays alive for the agent lifetime.
    rudraanvil_dir = project_path / ".rudraanvil"
    rudraanvil_dir.mkdir(parents=True, exist_ok=True)
    checkpoints_db = str(rudraanvil_dir / "checkpoints.db")
    db_conn = await aiosqlite.connect(checkpoints_db)
    checkpointer = AsyncSqliteSaver(conn=db_conn)
    await checkpointer.setup()  # Create tables if they don't exist yet


    # chat uses a stable session so multi-turn conversations persist.
    # All other commands (auto, build, fix, edit, …) get a fresh UUID per
    # invocation — prevents stale/broken checkpoint history from a prior run
    # from polluting the new request's context.
    if command == "chat":
        session_id = get_or_create_session_id(rudraanvil_dir)
    else:
        session_id = str(uuid.uuid4())

    # Main agent has full access to built-in file tools (read_file, write_file, edit_file,
    # ls, glob, grep) provided by FilesystemBackend — no subagent needed.
    deep_agent = create_deep_agent(
        model=model,
        tools=custom_tools,
        system_prompt=system_prompt,
        backend=filesystem_backend,
        checkpointer=checkpointer,
    )

    # Wrap in RudraAnvil agent, passing session_id for thread-based invocation
    # db_conn is passed so the agent can close it before the event loop shuts down
    return RudraAnvilAgent(context, deep_agent, session_id, db_conn=db_conn)
