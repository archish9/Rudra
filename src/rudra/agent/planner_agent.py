"""Planner agent — analyzes tasks, creates PLAN.md, writes task assignments."""

from __future__ import annotations

from pathlib import Path

from deepagents import create_deep_agent
from rich.console import Console

from rudra.filesystem import project_tree
from rudra.llm import build_model
from rudra.middleware import (
    FixWriteParamsMiddleware,
    TaskAnchorMiddleware,
)
from rudra.tools.interaction_tools import create_interaction_tools
from rudra.tools.planning_tools import create_planning_tools


def build_planner_prompt(
    task: str,
    project_path: Path,
    tech_stack_content: str = "",
) -> str:
    base = f"""You are a senior software architect and planning agent for Rudra.

Your ONLY job: ANALYZE tasks and CREATE plans. You NEVER write project code files directly.

## PROJECT STRUCTURE
{project_tree(project_path)}
"""
    if tech_stack_content:
        base += f"\n## Tech Stack\n{tech_stack_content}\n"

    base += f"""
## TASK
{task}

## WORKFLOW

### On first invocation (new task):
1. Read relevant files with read_file() to understand existing context if needed
2. Decide ALL files that need to be created for this task
3. Call update_plan() ONCE with a markdown checklist of EXACT filenames:
   CORRECT: '- [ ] main.rs'    WRONG: '- [ ] Create main.rs'
4. Call write_task_assignment() for the FIRST pending file with complete, detailed instructions
5. STOP — the orchestrator runs the coder, then asks you for the next file's assignment

### When asked to write a task assignment for a specific file:
1. Think about what that file needs to contain given the overall architecture
2. Call write_task_assignment() with:
   - file_path: exact relative path (e.g. "src/models.ts")
   - instructions: detailed spec — imports, classes, functions, endpoints, fields, logic
   - context_files: comma-separated files the coder should read first for context
3. STOP immediately after write_task_assignment() returns

## RULES
- NEVER call write_file() on project source files — the coder handles that
- NEVER write code yourself — describe what code to write in task assignments
- Use relative paths only (e.g. "src/main.rs", not absolute paths)
- Be specific in task assignments: name every import, class, method, endpoint
- Do NOT call ask_user() if the task already specifies a framework or language
"""
    return base


def create_planner_agent(
    task: str,
    project_path: Path,
    tech_stack_content: str,
    filesystem_backend,
    checkpointer,
    console: Console,
):
    """Create the planner deep agent."""
    model = build_model("planner")

    custom_tools = create_planning_tools(project_path, task=task) + create_interaction_tools(
        console, project_path
    )

    return create_deep_agent(
        model=model,
        tools=custom_tools,
        system_prompt=build_planner_prompt(task, project_path, tech_stack_content),
        backend=filesystem_backend,
        checkpointer=checkpointer,
        memory=[".rudra/AGENTS.md"],
        middleware=[
            # First in the list: cleans tool args before anything else sees
            # them. The planner previously got fence-stripping from
            # OverwriteFilesystemBackend, which U.3 deletes. See TODO.md U.14.
            FixWriteParamsMiddleware(),
            TaskAnchorMiddleware(task),
        ],
    )
