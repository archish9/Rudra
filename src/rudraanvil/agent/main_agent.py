"""Main supervisor agent for RudraAnvil using deepagents framework."""

from __future__ import annotations

import asyncio
import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any

from rich.console import Console
from deepagents import create_deep_agent

from rudraanvil.config import config
from rudraanvil.filesystem import VirtualFileSystem
from rudraanvil.state import TodoList, CheckpointManager, ProjectContext
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

Project structure:
{vfs.get_tree()}

You can:
- Plan and decompose tasks using the write_todos tool
- Read, write, and edit files using built-in file system tools
- Spawn sub-agents for specialized tasks

CRITICAL: Do NOT attempt to execute code, run tests, or install packages. Your sole purpose is to generate and edit files.
"""

    if project_context and project_context.primary_language:
        base_prompt += f"""
Project Tech Stack Context:
- Primary Language: {project_context.primary_language}
- Frameworks/Libraries: {project_context.framework or 'Not specified'}
- Database: {project_context.database or 'Not specified'}
- Additional Rules/Architecture: {project_context.additional_context or 'None'}

Please ensure all generated code adheres STRICTLY to the above tech stack.
"""
    
    # Add command-specific guidance
    if command == "build":
        existing = "This is an existing project." if vfs.files else "Starting from scratch."
        base_prompt += f"""
Task: {task}
{existing}

Your ONLY job at this stage is to PLAN AND DELEGATE. Do NOT write code directly in your response.
Your MUST use the `task` tool to spawn the `coder-subagent` to write the necessary files.
You are a project manager. You plan the files needed and then delegate the actual creation to the subagent.

Required steps:
1. **Plan**: Call write_todos with exactly the schema below to plan the files that need to be created.
2. **Delegate**: Call the `task` tool with `subagent_type: "coder-subagent"` and give it clear instructions on what code to write.
3. **Verify**: Check the files using `ls` and `read_file`.
4. **Iterate**: If the files are incorrect or missing, use the `task` tool again to fix them.

IMPORTANT: When delegating to the `coder-subagent`, you MUST explicitly specify the primary language and frameworks defined in the "Project Tech Stack Context" above. Do NOT let the subagent default to an incorrect language.

CRITICAL: You MUST NEVER output code directly. You MUST ALWAYS use the `task` tool to spawn the `coder-subagent` so it executes `write_file` or `edit_file`.

write_file and edit_file are handled by the subagent, NOT you.

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
- CRITICAL: NEVER start a path with a slash (e.g., NEVER use "/main.py").
- write_file creates a brand new file — use edit_file to modify an existing file

CRITICAL: If the subagent fails to write files (e.g. returns text instead of tool calls, or tries to use absolute paths), you MUST RE-RUN the task tool with even more explicit instructions. You are responsible for ensuring the code actually reaches the disk.
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
            # Truncate long outputs but keep more for errors
            limit = 800 if is_error else 400
            preview = content[:limit].replace('\n', ' ↵ ')
            if is_error:
                self._log_always(
                    f"[bold red]✗ [{index}] ERROR from {tool_name}:[/bold red]\n[red]{content}[/red]"
                )
            else:
                self._log_always(
                    f"[green]✓ [{index}] {tool_name}:[/green] {preview}"
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
            
            if self.context.verbose:
                self._log("Invoking deepagents with recursion_limit=100", "cyan")
            
            # Using stream to show real-time progress
            self._log_always("\n[bold]Agent Live Trace[/bold]", "cyan")
            
            final_state = None
            processed_messages = 0
            last_tool_call = None
            consecutive_failures = 0
            
            # Run deepagents as an async generator using astream
            async for chunk in self.agent.astream(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self.context.task
                        }
                    ]
                },
                {"recursion_limit": 100},  # Allow agent to iterate through work
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
                                self._log_always(f"[bold red]!! Halting execution: Tool failure detected in {':'.join(namespace) or 'main agent'}[/bold red]", "red")
                                self._log_always(f"[bold red]Reason: {content[:1000]}[/bold red]", "red")
                                return AgentResult(
                                    success=False, 
                                    message=f"Execution halted on tool error: {content}", 
                                    iterations=self.iterations
                                )
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
        
        except Exception as e:
            import traceback
            traceback.print_exc()
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
        num_predict=config.ollama.num_predict,  # Increased from default (128) to allow complete tool call JSON generation
    )
    
    # Create empty custom tools list since execution is disabled
    custom_tools = create_code_tools(vfs)
    
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
    
    # Configure FilesystemBackend to write files to actual disk (not just memory)
    # virtual_mode=False is safer for general agent access as it won't crash on unanchored paths
    from deepagents.backends import FilesystemBackend
    
    filesystem_backend = FilesystemBackend(
        root_dir=str(project_path),  # Must be absolute path
        virtual_mode=True  # Enabling virtual mode anchors all paths to root_dir and prevents absolute path jumps
    )

    # Force the coder subagent to use write_file
    from deepagents.middleware.filesystem import FilesystemMiddleware
    fs_middleware = FilesystemMiddleware(backend=filesystem_backend)
    
    # We find the file tools to bind them
    file_tools = []
    for tools_group in fs_middleware.tools:
        if isinstance(tools_group, list):
             for t in tools_group:
                 name = getattr(t, 'name', '')
                 if name in ['write_file', 'edit_file', 'read_file', 'ls']:
                     file_tools.append(t)
        else:
             name = getattr(tools_group, 'name', '')
             if name in ['write_file', 'edit_file', 'read_file', 'ls']:
                  file_tools.append(tools_group)
             
    coder_model = model
    if file_tools:
        # Binding them explicitly ensures they are available and preferred.
        coder_model = model.bind_tools(file_tools)
    
    # Create subagent system prompt with context
    subagent_prompt = (
        "You are a code generation agent. Your sole purpose is to execute write_file and edit_file to create the necessary code for your given task. "
        "Use the ls and read_file tools to navigate the project path if necessary. "
        "CRITICAL: You MUST use purely RELATIVE paths (e.g. 'models/User.js') when creating files. NEVER use absolute paths or paths starting with '/' (e.g. NEVER use '/models/User.js'). ALL paths should be relative to the project root. "
        "CRITICAL 2: The 'content' argument for write_file and edit_file MUST be a valid UTF-8 string. NEVER pass objects, dictionaries, or lists as content – convert them to formatted code or JSON strings first. "
        "CRITICAL 3: Do NOT use the write_todos tool. Marking a task as completed in the todo list does NOT create a file. You MUST write all the needed code strictly via tool calls to write_file or edit_file. "
        "CRITICAL 4: You MUST ONLY output write_file and edit_file tool calls. NEVER output ordinary text, markdown, codeblocks, or explanations. You MUST write all the needed code strictly via tool calls."
    )
    
    if project_context and project_context.primary_language:
        subagent_prompt += f"\n\nSTRICT TECH STACK ENFORCEMENT:\n- Primary Language: {project_context.primary_language}\n- Frameworks: {project_context.framework or 'Not specified'}\n- Database: {project_context.database or 'Not specified'}\n\nEnsure ALL code you generate uses this stack."

    deep_agent = create_deep_agent(
        model=model,  # Pass configured ChatOllama instance
        tools=custom_tools,  # Add our custom code execution tools
        system_prompt=system_prompt,
        backend=filesystem_backend,  # Write files to actual project directory
        subagents=[
            {
                "name": "coder-subagent",
                "description": "An agent specialized in writing code and generating exact implementation files. Launch this subagent to generate files for a given module. The subagent will return only once it has written all files.",
                "system_prompt": subagent_prompt,
                "model": coder_model,
                "tools": [] # inherits default tools 
            }
        ]
    )
    
    # Wrap in RudraAnvil agent for compatibility
    return RudraAnvilAgent(context, deep_agent)
