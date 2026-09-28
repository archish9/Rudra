"""The REPL's input layer: history, multiline, completion, `@`-mentions.

Lifted out of `cli.py` in Step 15b (C9.4), which was 1500+ lines and about
to gain three more concerns. Everything here is a plain function of
`(text, project_path)` -- none of it needs a terminal, which is what makes
`tests/test_repl_input.py` possible at all. Before this the REPL had no
tests of any kind.

**The session stays fresh per input (S15.4).** Nothing here holds state
between turns except the history file, which is a convenience and not a
conversation: `/compact` does not exist because there is nothing to
compact.

prompt_toolkit stays optional. Every entry point degrades to the plain
`rich.Prompt` path rather than raising, the way it did before.

**What a line MEANS is decided here too (OPEN-162)**, by `classify_input`,
so the dispatch is testable without a terminal. The REPL used to have no
resume path: run `1808f56c8329`'s user typed `continue` -- the word the
previous run had told them to use -- and it was planned as a request, and
four pending tasks were replaced. `continue` is now a command, and a new
request over pending work is asked about before anything is replaced.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.markup import escape

from rudra.filesystem import is_truncation_footer, project_tree
from rudra.state.paths import rudra_paths
from rudra.ui import Cancelled, Choice, Chosen, initial
from rudra.ui.prompt import ask as ask_selection

if TYPE_CHECKING:  # pragma: no cover - `rudra.loop` imports the engine
    from rudra.loop.ledger import Ledger

REPL_COMMANDS: dict[str, str] = {
    "/help": "Show this help message",
    "/tree": "Print the project file tree",
    "/continue": "Work the tasks the last run left pending (also: continue)",
    "/exit": "Quit Rudra",
}
"""The REPL's own commands. ONE table: `_print_help` renders it and
`build_completer` offers it, so a command cannot exist in one and be
missing from the other."""

_MENTION = re.compile(r"(?<![\w@])@([\w./\\-]+)")
"""`@path`, not preceded by a word character or a second `@`. The lookbehind
is what keeps `bob@example.com` out."""

MAX_COMPLETIONS = 30
"""A completion menu longer than a screen is a menu nobody reads."""


class ReplIntent(StrEnum):
    """What one line typed at the `rudra ❯` prompt is."""

    EMPTY = "empty"
    EXIT = "exit"
    HELP = "help"
    TREE = "tree"
    CONTINUE = "continue"
    UNKNOWN = "unknown"
    REQUEST = "request"


EXIT_WORDS = frozenset({"/exit", "/quit", "exit", "quit"})
"""Bare `exit` and `quit` have always worked without the slash."""

CONTINUE_WORDS = frozenset({"/continue", "/resume", "continue", "resume"})
"""The same shape as EXIT_WORDS, and for the same reason: the word a user
was told to type. `_provider_failure_message` and the REPL's cancel line
both say `continue`, and `--resume` is the flag's own alias. A CLOSED set
of whole lines, so "continue the auth work" is still a request."""


def classify_input(text: str) -> ReplIntent:
    """What the REPL should do with this line. Never reads the disk."""
    cmd = text.strip().lower()
    if not cmd:
        return ReplIntent.EMPTY
    if cmd in EXIT_WORDS:
        return ReplIntent.EXIT
    if cmd in CONTINUE_WORDS:
        return ReplIntent.CONTINUE
    if cmd == "/help":
        return ReplIntent.HELP
    if cmd == "/tree":
        return ReplIntent.TREE
    if cmd.startswith("/"):
        return ReplIntent.UNKNOWN
    return ReplIntent.REQUEST


def unfinished_ledger(project_path: Path) -> Ledger | None:
    """The ledger on disk when a new request would replace pending work.

    `Ledger.resumable()` is the test, PENDING only (A1.93) -- the same one
    `preserve_previous_ledger` and `_provider_failure_message` use, so the
    question is asked exactly when `plan()` would set work aside. That
    includes a plan that was never approved: OPEN-74's fifteen tasks had
    not one attempt between them, and `yes` replaced them all.

    None for a ledger this cannot read, as `preserve_previous_ledger` does:
    there is nothing to offer a resume of.
    """
    from rudra.loop.ledger import Ledger

    path = rudra_paths(project_path).ledger_json
    if not path.exists():
        return None
    try:
        ledger = Ledger.load(path)
    except Exception:  # noqa: BLE001 - an unreadable ledger is nothing to protect
        return None
    return ledger if ledger.resumable() else None


def asks_before_replacing(mode: str, *, interactive: bool) -> bool:
    """Whether the replace question can mean anything here.

    Not without a terminal: nobody can answer, and the question would read
    the next piped line as its answer -- so the REPL keeps OPEN-74's
    behaviour there, preserving and announcing. Not in plan mode, which
    leaves every plan PENDING by design and whose resume presents and stops
    as well: the question would fire on every request after the first.
    """
    return interactive and mode != "plan"


class ReplaceChoice(StrEnum):
    """The answer to "continue the pending work, or replace it?"."""

    CONTINUE = "continue"
    REPLACE = "replace"
    CANCEL = "cancel"


# First is the default an Enter picks, so it is the one that destroys
# nothing. `x` and not `c` for cancel, because `c` is the word the run
# told the user to type.
_REPLACE_CHOICES = (
    Choice(
        value=ReplaceChoice.CONTINUE,
        label="Continue",
        description="work the unfinished tasks; this request does not run",
        hotkey="c",
    ),
    Choice(
        value=ReplaceChoice.REPLACE,
        label="Replace",
        description="plan this request; the unfinished tasks are set aside",
        hotkey="r",
    ),
    Choice(value=ReplaceChoice.CANCEL, label="Cancel", description="do neither", hotkey="x"),
)


def ask_replace(ledger: Ledger, console: Any, *, reader: Any = None) -> ReplaceChoice:
    """Ask before a new request replaces pending work (OPEN-162).

    EOF and Ctrl-C are cancel, never replace -- the plan gate's rule
    (S10c.5), mapped HERE for the same reason `ask_approval` maps its own:
    a shared default could be the permissive one. Every exit prints a
    one-line record, because the inline driver erases its rows.
    """
    pending = len(ledger.resumable())
    plural = "" if pending == 1 else "s"
    console.print(
        f"\n[yellow]{pending} unfinished task{plural}[/yellow] "
        f"from [bold]{escape(ledger.request)}[/bold]"
    )
    console.print("[bold]Continue them, or replace them with this request?[/bold]")
    outcome = ask_selection(initial(_REPLACE_CHOICES), console=console, reader=reader)

    if isinstance(outcome, Cancelled) or not isinstance(outcome, Chosen) or not outcome.values:
        console.print("[dim]No answer — cancelled; nothing ran.[/dim]")
        return ReplaceChoice.CANCEL

    choice = ReplaceChoice(outcome.values[0])
    if choice is ReplaceChoice.CONTINUE:
        console.print(
            "[green]Continuing[/green] [dim]— this request did not run; "
            "type it again when these finish.[/dim]"
        )
    elif choice is ReplaceChoice.REPLACE:
        console.print("[dim]Replacing — planning this request.[/dim]")
    else:
        console.print("[dim]Cancelled — nothing ran.[/dim]")
    return choice


def history_path(project_path: Path) -> Path:
    """Where this project's prompt history lives.

    Per project, because a Rust project's prompts are not a Python
    project's. In the volatile subtree (D15) because it is convenience,
    not a fact about the project.

    Built through rudra_paths, never by hand -- CLAUDE.md §3: a `.rudra/`
    path assembled from a string is one that moves without its readers.
    """
    return rudra_paths(project_path).run / "repl_history"


def wants_more_input(text: str) -> bool:
    """Does this line end with a continuation marker?

    A trailing backslash, the shell convention. Esc+Enter also submits a
    multi-line buffer; this is the half that works when a terminal
    swallows Esc.
    """
    return text.endswith("\\")


def expand_mentions(text: str, project_path: Path) -> str:
    """Turn `@path` into a plain path, but only when the file is really there.

    Left alone otherwise, for two different reasons that happen to want
    the same behaviour:

    * `bob@example.com` is an email address, and rewriting it would
      corrupt the request the user typed.
    * `@../../etc/passwd` is not a project file reference. The backend is
      what confines the agent; this simply declines to helpfully rewrite
      an escape attempt into an absolute path first.
    """
    root = project_path.resolve()

    def replace(match: re.Match[str]) -> str:
        candidate = match.group(1)
        try:
            resolved = (root / candidate).resolve()
            resolved.relative_to(root)
        except (ValueError, OSError):
            return match.group(0)
        if not resolved.exists():
            return match.group(0)
        return candidate

    return _MENTION.sub(replace, text)


def _project_files(project_path: Path) -> list[str]:
    """Project-relative paths, capped, for the `@` completer.

    Reuses project_tree's walk so the completer and `/tree` agree about
    what counts as a project file -- ignores, build output and all.
    """
    try:
        listing = project_tree(project_path)
    except Exception:  # noqa: BLE001 -- completion must never break input
        return []
    # project_tree appends a non-path footer when it truncates
    # (filesystem/tree.py: "… N more entries omitted (cap: M)"), and it was
    # offered as a completion -- accepting it inserted that sentence into
    # the prompt, where expand_mentions left it and it went to the model
    # verbatim (CR-G11). The predicate lives beside the code that writes
    # the footer, so the PROJECT FILES block and this agree about which
    # lines are paths (OPEN-81) rather than each carrying the marker.
    return [line.strip() for line in listing.splitlines() if not is_truncation_footer(line)]


def build_completer(project_path: Path) -> Any:
    """A completer for `/`-commands and `@`-paths, and nothing else.

    Ordinary prose gets no menu: a completion popup mid-sentence is worse
    than no completion at all.
    """
    from prompt_toolkit.completion import Completer, Completion

    class _ReplCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor

            if text.startswith("/") and " " not in text:
                for command, description in REPL_COMMANDS.items():
                    if command.startswith(text):
                        yield Completion(
                            command,
                            start_position=-len(text),
                            display_meta=description,
                        )
                return

            match = re.search(r"@([\w./\\-]*)$", text)
            if match is None:
                return
            prefix = match.group(1)
            offered = 0
            for name in _project_files(project_path):
                if offered >= MAX_COMPLETIONS:
                    return
                if prefix and prefix not in name:
                    continue
                yield Completion(name, start_position=-len(prefix))
                offered += 1

    return _ReplCompleter()


def build_session(project_path: Path) -> Any:
    """A PromptSession with history, suggestion and completion, or None.

    None when prompt_toolkit is absent -- the caller falls back to
    `rich.Prompt`, which is what it did before any of this existed.
    """
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.history import FileHistory
        from prompt_toolkit.key_binding import KeyBindings
        from prompt_toolkit.styles import Style
    except ImportError:
        return None

    style = Style.from_dict({"prompt": "ansibrightcyan bold", "": "ansiwhite"})

    bindings = KeyBindings()

    @bindings.add("escape", "escape", "escape")
    def _(event):
        event.app.exit(result="/exit")

    history: Any = None
    try:
        path = history_path(project_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        history = FileHistory(str(path))
    except OSError:
        # A read-only project directory is a reason to lose history, never
        # a reason to lose the REPL.
        history = None

    return PromptSession(
        style=style,
        mouse_support=False,
        key_bindings=bindings,
        history=history,
        auto_suggest=AutoSuggestFromHistory(),
        completer=build_completer(project_path),
        complete_while_typing=False,
        # Esc+Enter submits; a trailing backslash continues. Both, because
        # some terminals eat Esc.
        multiline=False,
    )


__all__ = [
    "CONTINUE_WORDS",
    "EXIT_WORDS",
    "MAX_COMPLETIONS",
    "REPL_COMMANDS",
    "ReplIntent",
    "ReplaceChoice",
    "ask_replace",
    "asks_before_replacing",
    "build_completer",
    "build_session",
    "classify_input",
    "expand_mentions",
    "history_path",
    "unfinished_ledger",
    "wants_more_input",
]
