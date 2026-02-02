"""Main supervisor agent for RudraAnvil."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Optional

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from rich.console import Console

from rudraanvil.agent.prompts import SUPERVISOR_PROMPT, COMMAND_PROMPTS
from rudraanvil.agent.sub_agents import SubAgentPool, SubAgentType, get_recommended_agent_type
from rudraanvil.config import config
from rudraanvil.filesystem import VirtualFileSystem, FileSyncManager, SyncMode
from rudraanvil.state import TodoList, TodoStatus, CheckpointManager
from rudraanvil.tools import create_file_tools, create_code_tools


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


class MainAgent:
    """Main supervisor agent that orchestrates the coding workflow."""
    
    def __init__(self, context: AgentContext):
        """Initialize the main agent.
        
        Args:
            context: Agent execution context
        """
        self.context = context
        self.console = context.console
        
        # Initialize LLM
        self.llm = ChatOllama(
            model=config.ollama.model,
            base_url=config.ollama.base_url,
            temperature=config.ollama.temperature,
        )
        
        # Initialize tools
        self.tools = self._create_tools()
        
        # Sub-agent pool
        self.sub_agent_pool = SubAgentPool(max_agents=config.agent.max_agents)
        
        # Message history
        self.messages: list = []
        
        # Iteration counter
        self.iterations = 0
    
    def _create_tools(self) -> list:
        """Create all available tools."""
        tools = []
        tools.extend(create_file_tools(self.context.vfs))
        tools.extend(create_code_tools(self.context.vfs))
        return tools
    
    def _get_tool_descriptions(self) -> str:
        """Get formatted tool descriptions for the prompt."""
        descriptions = []
        for tool in self.tools:
            descriptions.append(f"- {tool.name}: {tool.description}")
        return "\n".join(descriptions)
    
    def _build_system_prompt(self) -> str:
        """Build the system prompt with current context."""
        return SUPERVISOR_PROMPT.format(
            project_path=self.context.project_path,
            project_tree=self.context.vfs.get_tree(),
            todo_list=self.context.todo_list.summary(),
            available_tools=self._get_tool_descriptions(),
        )
    
    def _build_command_prompt(self) -> str:
        """Build the command-specific prompt."""
        command = self.context.command
        
        if command not in COMMAND_PROMPTS:
            command = "build"
        
        template = COMMAND_PROMPTS[command]
        
        # Build format kwargs based on command
        kwargs = {"task": self.context.task}
        
        if command == "build":
            existing = "This is an existing project." if self.context.vfs.files else "Starting from scratch."
            kwargs["existing_context"] = existing
            
        elif command == "chat":
            kwargs["chat_history"] = self._format_chat_history()
            kwargs["user_input"] = self.context.task
            
        elif command == "fix":
            kwargs["issue"] = self.context.issue or self.context.task
            kwargs["error_context"] = ""  # Could be populated with stack traces
            
        elif command == "edit":
            kwargs["file_path"] = self.context.file_path
            kwargs["instruction"] = self.context.task
            content = self.context.vfs.read_file(self.context.file_path) if self.context.file_path else ""
            kwargs["file_content"] = content or "File not found"
            
        elif command == "review":
            kwargs["scope"] = f"Reviewing project at {self.context.project_path}"
            
        elif command == "suggest":
            kwargs["project_context"] = self.context.vfs.get_tree()
        
        return template.format(**kwargs)
    
    def _format_chat_history(self) -> str:
        """Format chat history for context."""
        if not self.context.chat_history:
            return "No previous messages"
        
        lines = []
        for msg in self.context.chat_history[-10:]:  # Last 10 messages
            role = msg.get("role", "user")
            content = msg.get("content", "")[:200]  # Truncate long messages
            lines.append(f"{role.upper()}: {content}")
        return "\n".join(lines)
    
    def _log(self, message: str, style: str = "") -> None:
        """Log a message if verbose mode is enabled."""
        if self.context.verbose:
            if style:
                self.console.print(f"[{style}]{message}[/{style}]")
            else:
                self.console.print(message)
    
    def _status(self, message: str) -> None:
        """Show a status message."""
        self.console.print(f"[dim]→ {message}[/dim]")
    
    async def _call_llm(self, messages: list) -> str:
        """Call the LLM and return the response."""
        try:
            response = await asyncio.to_thread(
                self.llm.invoke,
                messages
            )
            return response.content
        except Exception as e:
            return f"Error calling LLM: {str(e)}"
    
    async def _execute_tool(self, tool_name: str, tool_args: dict) -> str:
        """Execute a tool by name with given arguments."""
        for tool in self.tools:
            if tool.name == tool_name:
                try:
                    result = await asyncio.to_thread(tool.invoke, tool_args)
                    return str(result)
                except Exception as e:
                    return f"Error executing {tool_name}: {str(e)}"
        return f"Unknown tool: {tool_name}"
    
    async def plan(self) -> None:
        """Generate an initial plan and populate the todo list."""
        self._status("Planning...")
        
        # Build planning prompt
        messages = [
            SystemMessage(content=self._build_system_prompt()),
            HumanMessage(content=f"""Create a detailed plan for the following task. 
            
Break it down into specific, actionable todo items. For each item, specify:
1. A clear title
2. A brief description
3. Priority (0-100, higher = more urgent)

Task: {self.context.task}

{self._build_command_prompt()}

Respond with a JSON array of todo items, like:
[
    {{"title": "Set up project structure", "description": "Create directories and initial files", "priority": 90}},
    ...
]

Only output the JSON array, no other text.
""")
        ]
        
        response = await self._call_llm(messages)
        
        # Parse the response and add to todo list
        try:
            import json
            # Try to extract JSON from the response
            start = response.find("[")
            end = response.rfind("]") + 1
            if start >= 0 and end > start:
                items = json.loads(response[start:end])
                for item in items:
                    self.context.todo_list.add(
                        title=item.get("title", ""),
                        description=item.get("description", ""),
                        priority=item.get("priority", 0),
                    )
                self._log(f"Created {len(items)} todo items", "green")
        except Exception as e:
            self._log(f"Could not parse plan: {e}", "yellow")
            # Fall back to a single task
            self.context.todo_list.add(
                title=self.context.task,
                description="Main task from user request",
                priority=50,
            )
    
    async def execute_iteration(self) -> bool:
        """Execute one iteration of the agent loop.
        
        Returns:
            True if should continue, False if done
        """
        self.iterations += 1
        
        if self.iterations > self.context.max_iterations:
            self._log("Max iterations reached", "yellow")
            return False
        
        # Check if todo list is complete
        if self.context.todo_list.is_complete():
            return False
        
        # Get next task
        task = self.context.todo_list.get_next_pending()
        if not task:
            return False
        
        task.mark_in_progress()
        self._status(f"Working on: {task.title}")
        
        # Build execution prompt
        messages = [
            SystemMessage(content=self._build_system_prompt()),
            HumanMessage(content=f"""Execute this todo item:

Title: {task.title}
Description: {task.description}

Use the available tools to complete this task. When done, summarize what you did.
If you encounter issues, describe them so we can add fix tasks.

Available tools: {', '.join(t.name for t in self.tools)}

To use a tool, format your response like:
TOOL: <tool_name>
ARGS: {{"arg1": "value1", ...}}

You can use multiple tools, one after another. When completely done, say TASK_COMPLETE.
""")
        ]
        
        # Add any previous context
        messages.extend(self.messages[-5:])  # Last 5 messages for context
        
        response = await self._call_llm(messages)
        self._log(f"LLM Response: {response[:200]}...", "dim")
        
        # Parse and execute tool calls
        lines = response.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith("TOOL:"):
                tool_name = line[5:].strip()
                
                # Look for ARGS on next line
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("ARGS:"):
                    args_line = lines[i + 1].strip()[5:].strip()
                    try:
                        import json
                        args = json.loads(args_line)
                        result = await self._execute_tool(tool_name, args)
                        self._log(f"Tool {tool_name}: {result[:100]}...", "blue")
                        self.messages.append(AIMessage(content=f"Tool {tool_name} result: {result}"))
                    except Exception as e:
                        self._log(f"Tool error: {e}", "red")
                    i += 1
            
            elif "TASK_COMPLETE" in line:
                task.mark_completed()
                self._log(f"Completed: {task.title}", "green")
                break
            
            i += 1
        
        # If no explicit completion, check if task seems done
        if task.status != TodoStatus.COMPLETED:
            task.mark_completed()  # Assume done for now
        
        # Save checkpoint periodically
        if self.iterations % config.agent.checkpoint_interval == 0:
            self.context.checkpoint_manager.update_todo_list(self.context.todo_list)
            self.context.checkpoint_manager.update_virtual_fs(self.context.vfs.to_dict())
            self.context.checkpoint_manager.save()
        
        return True
    
    async def run(self) -> AgentResult:
        """Run the full agent workflow.
        
        Returns:
            AgentResult with execution summary
        """
        try:
            # Plan
            await self.plan()
            
            # Execute loop
            while await self.execute_iteration():
                pass
            
            # Sync to disk if not dry run
            if not self.context.dry_run:
                sync_manager = FileSyncManager(self.context.vfs, self.console)
                result = sync_manager.sync(mode=SyncMode.BACKUP)
                
                return AgentResult(
                    success=True,
                    message="Completed successfully",
                    files_created=result.files_created,
                    files_modified=result.files_modified,
                    iterations=self.iterations,
                    todo_summary=self.context.todo_list.summary(),
                )
            else:
                # Dry run - just show what would be done
                sync_manager = FileSyncManager(self.context.vfs, self.console)
                sync_manager.preview_changes()
                
                return AgentResult(
                    success=True,
                    message="Dry run completed (no files written)",
                    files_created=self.context.vfs.get_new_files(),
                    files_modified=self.context.vfs.get_modified_files(),
                    iterations=self.iterations,
                    todo_summary=self.context.todo_list.summary(),
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
        self.context.task = user_input
        
        # Build chat messages
        messages = [
            SystemMessage(content=self._build_system_prompt()),
            HumanMessage(content=self._build_command_prompt()),
        ]
        
        response = await self._call_llm(messages)
        
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
) -> MainAgent:
    """Factory function to create a main agent.
    
    Args:
        project_path: Path to the project
        task: Task description
        command: CLI command being executed
        console: Rich console for output
        dry_run: If True, don't write files
        verbose: If True, show detailed output
        **kwargs: Additional context arguments
        
    Returns:
        Configured MainAgent instance
    """
    console = console or Console()
    project_path = project_path.resolve()
    
    # Initialize virtual filesystem
    vfs = VirtualFileSystem(project_path)
    if project_path.exists():
        vfs.load_from_disk()
    
    # Initialize checkpoint manager
    checkpoint_dir = project_path / ".rudraanvil"
    checkpoint_manager = CheckpointManager(checkpoint_dir)
    checkpoint = checkpoint_manager.create(task)
    
    # Initialize todo list
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
    
    return MainAgent(context)
