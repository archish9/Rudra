"""Main supervisor agent for RudraAnvil using deepagents framework."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from deepagents import create_deep_agent
from rich.console import Console

from rudraanvil.config import config
from rudraanvil.filesystem import VirtualFileSystem
from rudraanvil.state import TodoList, CheckpointManager
from rudraanvil.tools import create_code_tools


@dataclass
class AgentContext:
    """Context passed to the agent during execution."""
    
    project_path: Path
    task: str
    vfs: VirtualFileSystem
    todo_list: TodoList
    checkpoint_manager: CheckpointManager
    console: Console
    
    # Execution settings
    dry_run: bool = False
    verbose: bool = False
    max_iterations: int = 100
    
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


def build_system_prompt(command: str, task: str, project_path: Path, vfs: VirtualFileSystem, **kwargs) -> str:
    """Build the system prompt for the agent based on command type."""
    
    # Base prompt
    base_prompt = f"""You are RudraAnvil, an expert autonomous coding agent.

Project root: {project_path}

Project structure:
{vfs.get_tree()}

You can:
- Plan and decompose tasks using the write_todos tool
- Read, write, and edit files using built-in file system tools
- Run code, tests, and linting
- Spawn sub-agents for specialized tasks

"""
    
    # Add command-specific guidance
    if command == "build":
        existing = "This is an existing project." if vfs.files else "Starting from scratch."
        base_prompt += f"""
Task: {task}
{existing}

Your ONLY job at this stage is to WRITE CODE FILES. Do NOT install packages, run commands,
start servers, or execute any code. Installation, environment setup, and testing are handled
in separate stages. Just focus on generating complete, accurate code.

Required steps:
1. **Plan** (optional): Call write_todos with the exact schema below
2. **Write ALL files**: Call write_file for EVERY file the project needs
3. **Edit if needed**: Use edit_file to update existing files
4. **Keep working**: Do NOT stop until every file is written and complete

Write COMPLETE, production-quality code for ANY language (Python, JS, Go, Rust, etc.):
- No placeholders like "# TODO" or "pass" — every function must be fully implemented
- Include all imports, all logic, all error handling
- Include config files and dependency manifests (requirements.txt, package.json, go.mod, etc.)
- Include a README.md with setup and run instructions

CRITICAL — write_file and edit_file rules:
- write_file creates a NEW file — if the file already exists, use edit_file instead
- If write_file returns an "already exists" error, immediately retry with edit_file — do NOT stop
- To create files in subdirectories, just use the relative path directly:
  write_file(file_path="app/main.py", content="...")  — directories are created automatically

CRITICAL — write_todos exact schema (todos must be a LIST of objects):
  write_todos(todos=[
    {{"content": "Description of task 1", "status": "pending"}},
    {{"content": "Description of task 2", "status": "pending"}}
  ])
  Valid status values: "pending", "in_progress", "completed"
  Each item MUST have exactly: "content" (string) and "status" (string)

Tool Usage (CURRENT v0.4.x API):
- List directory:     ls(path=".")
- Read a file:        read_file(file_path="filename.txt")
- Create a new file:  write_file(file_path="filename.txt", content="file content here")
- Edit existing file: edit_file(file_path="filename.txt", old_string="old text", new_string="new text")
- Search files:       glob(pattern="**/*.py")
- Search content:     grep(pattern="search term", path=".")

CRITICAL — File Path Rules:
- Use RELATIVE paths (e.g., "main.py", "app/routes.py", "requirements.txt")
- Do NOT use absolute paths (e.g., "/home/user/project/main.py")
- write_file creates a brand new file — use edit_file to modify an existing file
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
5. Verify it works (run tests if applicable)
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
    
    return base_prompt


class RudraAnvilAgent:
    """Wrapper around deepagents for RudraAnvil-specific functionality."""
    
    def __init__(self, context: AgentContext, deep_agent):
        """Initialize RudraAnvil agent wrapper.
        
        Args:
            context: Agent execution context
            deep_agent: The deepagents agent instance
        """
        self.context = context
        self.agent = deep_agent
        self.console = context.console
        self.iterations = 0
    
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
        Controlled by VERBOSE=false in .env to silence.
        """
        if not self.context.verbose:
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
                limit = 800 if is_error else 400
                preview = content[:limit].replace('\n', ' ↵ ')
                if is_error:
                    self._log_always(
                        f"[bold red]✗ [{i+1}] ERROR from {tool_name}:[/bold red] {preview}"
                    )
                else:
                    self._log_always(
                        f"[green]✓ [{i+1}] {tool_name}:[/green] {preview}"
                    )

            # ── HumanMessage ──
            elif msg_type == "HumanMessage":
                content = str(getattr(msg, 'content', ''))[:200].replace('\n', ' ')
                self._log_always(f"[dim][{i+1}] USER: {content}[/dim]")

            else:
                content = str(getattr(msg, 'content', ''))[:200].replace('\n', ' ')
                self._log_always(f"[dim][{i+1}] {msg_type}: {content}[/dim]")
    
    async def run(self) -> AgentResult:
        """Run the full agent workflow.
        
        Returns:
            AgentResult with execution summary
        """
        try:
            self._status("Planning and executing task...")
            
            if self.context.verbose:
                self._log("Invoking deepagents with recursion_limit=100", "cyan")
            
            # Run deepagents with proper LangGraph configuration
            # recursion_limit allows the agent to loop through todos and execute work
            result = await asyncio.to_thread(
                self.agent.invoke,
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self.context.task
                        }
                    ]
                },
                {"recursion_limit": 100}  # Allow agent to iterate through work
            )
            
            # Log all messages with full detail
            messages = result.get("messages", [])
            self._log_messages(messages)

            if self.context.verbose:
                self._log(f"Result state keys: {list(result.keys())}", "yellow")
            
            # Extract final response
            # deepagents returns a dict with 'messages' key containing AIMessage objects
            messages = result.get("messages", [])
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
        
        except Exception as e:
            return AgentResult(
                success=False,
                message=f"Error: {str(e)}",
                iterations=self.iterations,
            )
    
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
        
        # Run agent with recursion limit for chat mode
        result = await asyncio.to_thread(
            self.agent.invoke,
            {"messages": messages},
            {"recursion_limit": 100}  # Allow agent to execute work in chat mode too
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


def create_main_agent(
    project_path: Path,
    task: str,
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
    
    # Initialize checkpoint manager
    checkpoint_dir = project_path / ".rudraanvil"
    checkpoint_manager = CheckpointManager(checkpoint_dir)
    checkpoint = checkpoint_manager.create(task)
    
    # Initialize todo list (for display purposes)
    todo_list = TodoList()
    
    # Create context
    context = AgentContext(
        project_path=project_path,
        task=task,
        vfs=vfs,
        todo_list=todo_list,
        checkpoint_manager=checkpoint_manager,
        console=console,
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
        num_predict=config.ollama.num_predict,  # Increased from default (128) to allow complete tool call JSON generation
    )
    
    # Create custom code execution tools (deepagents has file tools built-in)
    custom_tools = create_code_tools(vfs)
    
    # Build system prompt
    system_prompt = build_system_prompt(
        command=command,
        task=task,
        project_path=project_path,
        vfs=vfs,
        **kwargs
    )
    
    # Create deep agent with configured Ollama model and REAL filesystem backend
    # Deepagents will handle:
    # - Built-in file system tools (read_file, write_file, edit_file, ls)
    # - Built-in planning tool (write_todos)
    # - Subagent spawning
    # - Tool calling orchestration
    
    # Configure FilesystemBackend to write files to actual disk (not just memory)
    # virtual_mode=True enables path sand boxing and normalization under root_dir
    from deepagents.backends import FilesystemBackend
    
    filesystem_backend = FilesystemBackend(
        root_dir=str(project_path),  # Must be absolute path
        virtual_mode=True  # Enables path-based security and normalization
    )
    
    deep_agent = create_deep_agent(
        model=model,  # Pass configured ChatOllama instance
        tools=custom_tools,  # Add our custom code execution tools
        system_prompt=system_prompt,
        backend=filesystem_backend,  # Write files to actual project directory
    )
    
    # Wrap in RudraAnvil agent for compatibility
    return RudraAnvilAgent(context, deep_agent)
