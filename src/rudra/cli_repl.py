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
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rudra.filesystem import is_truncation_footer, project_tree
from rudra.state.paths import rudra_paths

REPL_COMMANDS: dict[str, str] = {
    "/help": "Show this help message",
    "/tree": "Print the project file tree",
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
    "MAX_COMPLETIONS",
    "REPL_COMMANDS",
    "build_completer",
    "build_session",
    "expand_mentions",
    "history_path",
    "wants_more_input",
]
