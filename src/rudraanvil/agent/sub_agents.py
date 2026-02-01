"""Sub-agent creation and management."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Optional

from langchain_ollama import ChatOllama

from rudraanvil.agent.prompts import SUB_AGENT_PROMPTS
from rudraanvil.config import config

if TYPE_CHECKING:
    from rudraanvil.filesystem.virtual_fs import VirtualFileSystem
    from rudraanvil.state.todo import TodoItem


class SubAgentType(str, Enum):
    """Types of specialized sub-agents."""
    
    ARCHITECT = "architect"
    BACKEND = "backend"
    FRONTEND = "frontend"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    DEBUGGER = "debugger"
    SECURITY = "security"
    DEVOPS = "devops"


def get_recommended_agent_type(task_title: str) -> SubAgentType:
    """Recommend an agent type based on task title/description.
    
    Args:
        task_title: The task title or description
        
    Returns:
        Recommended sub-agent type
    """
    title_lower = task_title.lower()
    
    # Architecture and setup
    if any(kw in title_lower for kw in ["structure", "setup", "scaffold", "init", "architecture", "config"]):
        return SubAgentType.ARCHITECT
    
    # Testing
    if any(kw in title_lower for kw in ["test", "pytest", "coverage", "spec"]):
        return SubAgentType.TESTING
    
    # Documentation
    if any(kw in title_lower for kw in ["readme", "docs", "document", "comment", "example"]):
        return SubAgentType.DOCUMENTATION
    
    # Debugging
    if any(kw in title_lower for kw in ["fix", "bug", "error", "debug", "issue", "fail"]):
        return SubAgentType.DEBUGGER
    
    # Security
    if any(kw in title_lower for kw in ["security", "auth", "jwt", "password", "encrypt", "vulnerab"]):
        return SubAgentType.SECURITY
    
    # DevOps
    if any(kw in title_lower for kw in ["docker", "ci", "cd", "deploy", "pipeline", "kubernetes"]):
        return SubAgentType.DEVOPS
    
    # Frontend
    if any(kw in title_lower for kw in ["frontend", "ui", "component", "react", "vue", "css", "html"]):
        return SubAgentType.FRONTEND
    
    # Default to backend for general coding tasks
    return SubAgentType.BACKEND


def create_sub_agent(
    agent_type: SubAgentType,
    vfs: VirtualFileSystem,
    tools: list,
    assigned_tasks: list[TodoItem],
    error_context: Optional[str] = None,
) -> dict:
    """Create a sub-agent configuration for a specific role.
    
    Args:
        agent_type: Type of sub-agent to create
        vfs: Virtual filesystem for context
        tools: List of tools available to the agent
        assigned_tasks: Tasks assigned to this sub-agent
        error_context: Optional error context for debugger agent
        
    Returns:
        Configuration dict for the sub-agent
    """
    # Get the prompt template
    prompt_template = SUB_AGENT_PROMPTS.get(agent_type.value, SUB_AGENT_PROMPTS["backend"])
    
    # Build project context
    project_context = f"""Project root: {vfs.root_path}

Project structure:
{vfs.get_tree()}
"""
    
    # Build assigned tasks string
    tasks_str = "\n".join([
        f"- [{task.status.value}] {task.title}: {task.description}"
        for task in assigned_tasks
    ])
    
    # Format the prompt
    prompt = prompt_template.format(
        project_context=project_context,
        assigned_tasks=tasks_str or "No specific tasks assigned",
        error_context=error_context or "N/A",
    )
    
    # Create the LLM
    llm = ChatOllama(
        model=config.ollama.model,
        base_url=config.ollama.base_url,
        temperature=config.ollama.temperature,
    )
    
    return {
        "type": agent_type,
        "llm": llm,
        "prompt": prompt,
        "tools": tools,
        "tasks": assigned_tasks,
    }


class SubAgentPool:
    """Manages a pool of sub-agents for parallel execution."""
    
    def __init__(self, max_agents: int = 6):
        """Initialize the sub-agent pool.
        
        Args:
            max_agents: Maximum number of concurrent sub-agents
        """
        self.max_agents = max_agents
        self.active_agents: dict[str, dict] = {}
    
    def spawn(
        self,
        agent_type: SubAgentType,
        vfs: VirtualFileSystem,
        tools: list,
        tasks: list[TodoItem],
        error_context: Optional[str] = None,
    ) -> Optional[str]:
        """Spawn a new sub-agent if capacity allows.
        
        Args:
            agent_type: Type of sub-agent
            vfs: Virtual filesystem
            tools: Available tools
            tasks: Tasks to assign
            error_context: Optional error context
            
        Returns:
            Agent ID if spawned, None if at capacity
        """
        if len(self.active_agents) >= self.max_agents:
            return None
        
        agent_config = create_sub_agent(
            agent_type=agent_type,
            vfs=vfs,
            tools=tools,
            assigned_tasks=tasks,
            error_context=error_context,
        )
        
        # Generate agent ID
        agent_id = f"{agent_type.value}_{len(self.active_agents)}"
        self.active_agents[agent_id] = agent_config
        
        # Mark tasks as assigned
        for task in tasks:
            task.assigned_agent = agent_type.value
        
        return agent_id
    
    def release(self, agent_id: str) -> None:
        """Release a sub-agent from the pool.
        
        Args:
            agent_id: ID of the agent to release
        """
        if agent_id in self.active_agents:
            del self.active_agents[agent_id]
    
    def get_agent(self, agent_id: str) -> Optional[dict]:
        """Get a sub-agent configuration by ID.
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Agent configuration dict or None
        """
        return self.active_agents.get(agent_id)
    
    @property
    def available_capacity(self) -> int:
        """Get the number of available agent slots."""
        return self.max_agents - len(self.active_agents)
    
    @property
    def is_at_capacity(self) -> bool:
        """Check if the pool is at maximum capacity."""
        return len(self.active_agents) >= self.max_agents
