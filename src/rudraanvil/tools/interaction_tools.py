"""Interaction tools — allow the agent to ask the user clarifying questions
and persist gathered project context for future sessions."""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, tool
from rich.console import Console
from rich.prompt import Prompt

from rudraanvil.state import ProjectConfigManager


def create_interaction_tools(console: Console, project_path: Path) -> list[BaseTool]:
    """Create interaction tools bound to the given console and project path.

    Args:
        console: Rich console for terminal output.
        project_path: Root directory of the project (used to locate project.json).

    Returns:
        List of LangChain tools: [ask_user, save_project_context]
    """

    @tool
    def ask_user(question: str) -> str:
        """Ask the user a clarifying question and return their answer.

        Use this when you need information about the project that is not available
        in the codebase or file tree — for example, the preferred language,
        framework, or database. Ask ONE focused, specific question at a time.

        Args:
            question: A clear, specific question to ask the user.

        Returns:
            The user's text response.
        """
        console.print(f"\n[bold cyan]?[/bold cyan] {question}")
        try:
            answer = Prompt.ask("[bold yellow]Answer[/bold yellow]", default="")
        except EOFError:
            return "(stdin is not interactive; skipping this question)"
        return answer.strip() or "(no answer provided)"

    @tool
    def save_project_context(context: dict) -> str:
        """Save project tech stack information to .rudraanvil/project.json.

        Call this after using ask_user() to gather the project's tech stack.
        Only supply the fields you have confirmed answers for — existing fields
        that are not included will be left unchanged.

        Supported fields:
            primary_language (str): e.g. "Python", "TypeScript", "Go"
            framework        (str): e.g. "FastAPI", "Next.js", "Django"
            database         (str): e.g. "PostgreSQL", "SQLite", "MongoDB"
            additional_context (str): architecture rules or constraints

        Args:
            context: Dict containing any subset of the supported fields above.

        Returns:
            Confirmation string listing what was saved.
        """
        valid_fields = {"primary_language", "framework", "database", "additional_context"}
        config_manager = ProjectConfigManager(project_path)
        existing = config_manager.load()

        updates: dict[str, str] = {}
        for key, value in context.items():
            if key in valid_fields and isinstance(value, str) and value.strip():
                updates[key] = value.strip()

        for field_name, value in updates.items():
            setattr(existing, field_name, value)

        if updates:
            config_manager.save(existing)
            saved_pairs = ", ".join(f"{k}={v!r}" for k, v in updates.items())
            return f"Project context saved: {saved_pairs}"

        return "No valid fields provided; nothing was saved."

    return [ask_user, save_project_context]
