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

from collections.abc import Callable
from pathlib import Path

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field
from rich.console import Console
from rich.markup import escape
from rich.prompt import Prompt

from rudra.facts import FactRejected, FactStore
from rudra.ui import Cancelled, Choice, Chosen, initial
from rudra.ui.prompt import ask as ask_selection

OTHER = "__rudra_other__"
"""The always-appended escape hatch. A sentinel rather than a label, so a
model that happens to offer a real option called "Other" cannot collide
with it."""


class _InputClosed(Exception):
    """stdin is gone. Distinct from a skipped question on purpose: `esc`
    skips ONE question and the rest are still worth asking, while a closed
    input means every remaining prompt would fail the same way."""


class AskOption(BaseModel):
    """One selectable answer."""

    label: str = Field(description='The answer, e.g. "SQLite".')
    description: str = Field(default="", description="One short line of explanation.")


class AskQuestion(BaseModel):
    """One question, its fact key, and its answers."""

    key: str = Field(description='Fact key for the answer, e.g. "language".')
    question: str = Field(description="The question, as the user will read it.")
    options: list[AskOption] = Field(
        default_factory=list,
        description="The answers to choose from. Leave empty for a free-text question.",
    )
    multi_select: bool = Field(
        default=False, description="True when more than one option may be chosen."
    )


def create_interaction_tools(
    console: Console,
    store: FactStore,
    path: Path,
    *,
    max_questions: int = 5,
    interactive: bool = True,
    reader: Callable[[], str] | None = None,
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
        reader: Injected line reader, forcing the terminal-free driver.
            Tests pass one; a real run leaves it None and gets arrow keys.

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
        # A1.72: an unattended run has no ask_user at all (S10a.5), so a
        # fact cannot have been asked. The model said otherwise in Step
        # 10a's acceptance run -- honestly enough, since the request it
        # read was written by the user -- and `asked` is the label a later
        # stage trusts as "settled, do not revisit". Python knows the
        # truth here; the prompt can only request it.
        #
        # An unknown source still falls through to store.record and is
        # rejected there: coercion must not launder bad input.
        #
        # A1.73: a fact the user answered in an EARLIER run keeps its
        # provenance when this run merely restates it. Demoting it made
        # the record contradict itself -- "source": "inferred" beside a
        # why reading "the user chose it last run" -- and demoted the one
        # signal a later stage trusts. Restating what the user said is
        # honest; claiming a *different* value was asked is not, so the
        # value must match too.
        if not interactive and source == "asked":
            known = store.get(key)
            restated = (
                known is not None
                and known.source == "asked"
                and isinstance(value, str)
                and known.value == value.strip()
            )
            if not restated:
                source = "inferred"

        try:
            fact = store.record(key, value, why, source)
        except FactRejected as exc:
            return f"REJECTED: {exc}"
        store.save(path)
        return f'Recorded {key} = "{fact.value}" ({fact.source}).'

    tools: list[BaseTool] = [record_fact]
    if not interactive or remaining["questions"] <= 0:
        return tools

    def _free_text() -> str | None:
        """A typed answer, or None when the user gave none."""
        try:
            raw = (
                reader()
                if reader is not None
                else Prompt.ask("[bold yellow]Answer[/bold yellow]", default="")
            ).strip()
        except EOFError as exc:
            raise _InputClosed from exc
        return raw or None

    def _collect(item: AskQuestion) -> str | None:
        """One question's answer, or None when it was skipped.

        Raises _InputClosed when stdin is gone, which stops the batch --
        `esc` on one question does not.
        """
        if not item.options:
            return _free_text()

        # "Other…" is appended for you, always: a user must never be
        # trapped inside a list the model happened to think of.
        choices = tuple(
            Choice(value=option.label, label=option.label, description=option.description)
            for option in item.options
        ) + (Choice(value=OTHER, label="Other…", description="type your own answer"),)

        outcome = ask_selection(
            initial(choices, multi=item.multi_select), console=console, reader=reader
        )
        if isinstance(outcome, Cancelled) or not isinstance(outcome, Chosen):
            return None
        if OTHER in outcome.values:
            return _free_text()
        # ", " rather than a list: a fact value is a string. An over-long
        # join is caught by the FactRejected handler below, same as any
        # other over-long value (facts/store.py MAX_TEXT).
        return ", ".join(outcome.values) or None

    @tool
    def ask_user(questions: list[AskQuestion]) -> str:
        """Ask the user up to 4 related questions and record the answers.

        Ask only what you cannot infer from the request or the codebase.
        Questions are shown ONE AT A TIME, so send the related ones
        together in a single call. Every answer is recorded as a fact
        automatically -- do not call record_fact for it.

        If you are offering the user a choice, put it in `options`. NEVER
        WRITE THE CHOICES INTO YOUR MESSAGE TEXT: the user cannot select
        prose, and a question you narrate is a question that was never
        asked. Leave `options` empty only for a genuinely open question.

        Set `multi_select` when more than one answer may apply. An
        "Other" row is added for you, so never include one yourself.

        Returns:
            A numbered question-and-answer block, or REJECTED and why.
        """
        if not questions:
            return "REJECTED: give at least one question."
        if remaining["questions"] <= 0:
            return (
                f"REJECTED: question budget spent ({max_questions}) — "
                "infer the rest from the request and record it with record_fact."
            )

        # Ask what fits rather than refusing the batch: refusing would
        # punish exactly the batching this tool exists to encourage.
        askable = list(questions)[: remaining["questions"]]
        skipped = len(questions) - len(askable)

        lines: list[str] = []
        for index, item in enumerate(askable, start=1):
            remaining["questions"] -= 1
            console.print(f"\n[bold cyan]?[/bold cyan] {escape(item.question)}")
            if len(askable) > 1:
                console.print(f"[dim]Question {index} of {len(askable)}[/dim]")

            try:
                answer = _collect(item)
            except _InputClosed:
                # stdin closed mid-batch. Everything answered so far is
                # already recorded; the rest simply did not happen.
                lines.append(f"{index}. {item.question} → (no answer)")
                break
            if answer is None:
                lines.append(f"{index}. {item.question} → (no answer)")
                continue
            try:
                store.record(item.key, answer, f"user answered: {item.question}", "asked")
            except FactRejected as exc:
                lines.append(f"{index}. {item.question} → {answer}  (NOT recorded: {exc})")
                continue
            store.save(path)
            # The permanent record, printed after the widget erases itself
            # (spec 4.3) so scrollback keeps the answer and not the menu.
            console.print(f"  [green]✓[/green] {escape(answer)}")
            lines.append(f"{index}. {item.question} → {answer}  (recorded as {item.key})")

        if skipped:
            lines.append(f"({skipped} question(s) not asked: the question budget is spent.)")
        return "\n".join(lines)

    return [record_fact, ask_user]


__all__ = ["create_interaction_tools"]
