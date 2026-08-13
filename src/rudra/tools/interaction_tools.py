"""Asking the user, and recording what the run establishes.

Both tools replace the static questionnaire TODO.md §0.5 inventories.
`save_project_context` accepted four field names and silently discarded
everything else (surface #2); `ask_user` mandated one question at a time
(surface #3), which is the opposite of what C6.8 needs.

Neither tool raises. A model reads what comes back and tries again, so a
rejection is a sentence explaining what would be acceptable -- the same
REJECTED idiom loop/tools.py uses.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import BaseTool, tool
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

from rudra.facts import FactRejected, FactStore


def create_interaction_tools(
    console: Console,
    store: FactStore,
    path: Path,
    *,
    max_questions: int = 5,
    interactive: bool = True,
) -> list[BaseTool]:
    """The interaction tools for one run.

    Args:
        console: Rich console for the questions.
        store: The live FactStore the loop also reads -- the same object,
            not a copy, for the reason the Ledger is shared.
        path: Where to persist after each successful record.
        max_questions: The run's whole clarification budget
            (`[agent] max_questions`). Counted in questions, not calls.
        interactive: False when nobody can answer. `ask_user` is then not
            registered at all rather than returning a refusal (S10a.5):
            the reviewer cannot write because its tools do not exist, and
            this follows that precedent.

    Returns:
        [record_fact], plus [ask_user] when a user can actually answer.
    """
    remaining = {"questions": max(0, int(max_questions))}

    @tool
    def record_fact(key: str, value: str, why: str, source: str) -> str:
        """Record one thing you have established about this project.

        Record everything you rely on, whether the user told you or you
        worked it out -- the coder, the tester and the reviewer all read
        these facts, and a fact you keep to yourself is one they do not
        have.

        Args:
            key: A short lowercase name: "language", "cli_framework",
                "min_python_version". Invent whatever fits; there is no
                fixed list.
            value: What you established, e.g. "Rust".
            why: Why you believe it, e.g. "the user asked for a Rust CLI".
            source: "asked" if the user told you, "inferred" if you worked
                it out from the request, "detected" if you read it off the
                project.

        Returns:
            Confirmation, or REJECTED and what would be acceptable.
        """
        try:
            fact = store.record(key, value, why, source)
        except FactRejected as exc:
            return f"REJECTED: {exc}"
        store.save(path)
        return f'Recorded {key} = "{fact.value}" ({fact.source}).'

    tools: list[BaseTool] = [record_fact]
    if not interactive or remaining["questions"] <= 0:
        return tools

    @tool
    def ask_user(questions: list[str], keys: list[str]) -> str:
        """Ask the user a batch of related questions and record the answers.

        Ask only what you cannot infer from the request or the codebase.
        Send related questions together in ONE call rather than one per
        call. Every answer is recorded as a fact automatically, so you do
        not need to call record_fact for it.

        Args:
            questions: The questions, in the order to ask them.
            keys: One fact key per question, same order and same length:
                ["language", "cli_framework"].

        Returns:
            A numbered question-and-answer block, or REJECTED and why.
        """
        if not questions:
            return "REJECTED: give at least one question."
        if len(keys) != len(questions):
            return (
                f"REJECTED: give one key per question; got {len(questions)} "
                f"question(s) and {len(keys)} key(s)."
            )
        if remaining["questions"] <= 0:
            return (
                f"REJECTED: question budget spent ({max_questions}) — "
                "infer the rest from the request and record it with record_fact."
            )

        # Ask what fits rather than refusing the batch: refusing would
        # punish exactly the batching this tool exists to encourage.
        askable = list(zip(keys, questions))[: remaining["questions"]]
        skipped = len(questions) - len(askable)

        lines: list[str] = []
        for index, (key, question) in enumerate(askable, start=1):
            remaining["questions"] -= 1
            console.print(f"\n[bold cyan]?[/bold cyan] {escape(question)}")
            try:
                answer = Prompt.ask("[bold yellow]Answer[/bold yellow]", default="").strip()
            except EOFError:
                # stdin closed mid-batch. Everything answered so far is
                # already recorded; the rest simply did not happen.
                lines.append(f"{index}. {question} → (no answer)")
                break
            if not answer:
                lines.append(f"{index}. {question} → (no answer)")
                continue
            try:
                store.record(key, answer, f"user answered: {question}", "asked")
            except FactRejected as exc:
                lines.append(f"{index}. {question} → {answer}  (NOT recorded: {exc})")
                continue
            store.save(path)
            lines.append(f"{index}. {question} → {answer}  (recorded as {key})")

        if skipped:
            lines.append(f"({skipped} question(s) not asked: the question budget is spent.)")
        return "\n".join(lines)

    return [record_fact, ask_user]


__all__ = ["create_interaction_tools"]
