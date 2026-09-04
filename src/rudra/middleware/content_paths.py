"""ContentPathMiddleware — answer a project-absolute path written INTO a file.

**The defect (OPEN-93).** Run `2cde3406f7d6`, the reported one: the tester
wrote `HTML_PATH = "/src/iphone15.html"` into `tests/test_iphone15.py`. The
gate then ran `python3 -m pytest` with the project's own interpreter, to which
`/src` is the MACHINE's root. 8 of 8 tests failed, forever, against 480 lines
of correct HTML that had been complete on disk since t≈143 s. The run spent
2,132 s and finished 0 tasks of 2. It failed on a string literal.

**Why nothing caught it.** Rudra builds every backend `virtual_mode=True`, so
there are two path universes and the prompt documents only two of the three
consumers:

    read_file/write_file/edit_file/ls/glob/grep   "/src/x" -> <project>/src/x
    execute                                       "/src/x" -> the machine's
    code the agent WRITES, run later by a real
    interpreter                                   "/src/x" -> the machine's

The third row is the bug. `_PATH_RULES` calls the virtual spelling "correct"
without qualification -- and it IS correct, as a tool argument; it is what
`ls` and `glob` answer in, which OPEN-81 shipped deliberately.
`_COMMAND_RULES` states the duality precisely and scopes every sentence of it
to `execute`. The tester obeyed that for `execute` -- at `at=4.8` it ran
`cat /src/iphone15.html`, got `No such file or directory`, and corrected
itself to the real absolute path -- and then, 170 s later, wrote the spelling
it had just disproved into Python. Its prompt had no rule for file content at
all.

**The seam this uses is the tool's own answer**, which is what OPEN-25,
OPEN-81, `machine_paths.py` and `gutter_indent.py` all measured working: an
explanation delivered at the moment of the mistake, costing one round trip,
rather than a fourth prompt paragraph arguing with the three already there.
A companion block in `_PATH_RULES` ships with this item, and unlike OPEN-17
and OPEN-36 it is admissible because nothing in any Rudra prompt currently
says anything, in any direction, about paths inside written content -- there
is no incumbent for it to lose to (`_FINISH_RULES`' own test for this).

**It never rewrites `content`.** `fix_write_params.py` excludes content from
path repair deliberately and says why: file content is data, and a blanket
rewrite corrupts the legitimate absolute paths a real config holds. This
appends a sentence to a SUCCESSFUL result and nothing else.

**Every uncertain case declines**, which is `gutter_indent.py`'s rule. It
declines when the first segment is not a real top-level entry of this project
(read from disk, never guessed -- so `/api/v1/users` and `/usr/bin/env` are
left alone), when the file is not one a process later interprets (not
`.html`, not `.md`, not `.json`), when the line carries a URL, and when the
literal sits in a comment.

**APPENDED, never prepended.** `subagents/runner.py`'s
MAX_CONSECUTIVE_FAILURES and `repeat_guard.py::_is_error` both key on content
that starts with `Error`, so rewriting the front of a result silently disarms
two other guards -- which is OPEN-94, filed on the same board as this.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)

CONTENT_PATH_NOTICE = "content-path"
"""The `name` a hint carries in the trace and the debug log.

Named here because it is what a maintainer greps for -- `"name":
"content-path"` in `debug-<id>.jsonl`, counted per role as
`content_paths_flagged` in `usage.json`, is the number that says whether this
item paid for itself (CLAUDE.md 8a).
"""

# Which argument of each tool carries the bytes that land on disk. `edit_file`
# is judged on `new_string` alone: `old_string` is text already IN the file,
# so flagging it would complain about a line the model is removing.
_CONTENT_ARGS: dict[str, tuple[str, ...]] = {
    "write_file": ("content",),
    "edit_file": ("new_string",),
}

# Files a real process later interprets, where a leading "/" is resolved by
# something with no idea this project exists.
#
# `.html`, `.md`, `.json`, `.txt` and every other data or markup type are
# deliberately absent. An `href="/src/x"` is resolved by a browser against a
# document root, and a link in a README by a reader -- both are legitimate
# spellings this has no business commenting on. The reported run's own
# deliverable was `.html`, and it was never wrong.
_INTERPRETED_SUFFIXES = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".mjs",
        ".cjs",
        ".sh",
        ".bash",
        ".zsh",
        ".rb",
        ".go",
        ".rs",
        ".java",
        ".php",
        ".pl",
        ".lua",
        ".mk",
    }
)

# Extensionless files that are nonetheless run. Matched on the exact name.
_INTERPRETED_NAMES = frozenset({"Makefile", "makefile", "GNUmakefile", "Dockerfile"})

# A simple quoted literal: no embedded quote, no newline. Anything more
# permissive starts guessing at a language's string grammar, which is the
# shape that produces a false positive -- and a false positive here is a
# sentence about a line that was right.
#
# A backslash IS allowed inside, so a Windows-shaped `\\src\\index.html`
# is seen on a mac (CLAUDE.md 1.8: recognise by shape, never by host OS). It
# does not widen the false-positive surface, because the gate is not the
# quoting -- it is whether the first segment names a directory this project
# actually has.
_LITERAL = re.compile(r"""(?P<q>['"])(?P<value>[^'"\n]*)(?P=q)""")


def _first_segment(value: str) -> str | None:
    """The first path segment of a root-anchored spelling, or None.

    Answered by SHAPE and never by host OS -- `compat/virtual_paths.py`'s
    rule, and the reason `\\src\\index.html` is recognised on macOS
    (CLAUDE.md 1.8). A model guessing from training data makes the same
    mistake whatever machine it is running on.
    """
    plain = value.replace("\\", "/")
    if not plain.startswith("/"):
        return None
    parts = PurePosixPath(plain).parts
    if len(parts) < 2:
        return None
    return parts[1]


def _comment_at(line: str) -> int:
    """Index where a comment begins on this line, or len(line).

    `#` and `//` cover every language in `_INTERPRETED_SUFFIXES`. It is read
    literally, so a `#` inside an earlier string ends the scan early -- a
    false DECLINE, which is the direction this whole module errs in.
    """
    marks = [i for i in (line.find("#"), line.find("//")) if i != -1]
    return min(marks) if marks else len(line)


def find_project_absolute_literals(content: str, top_level: Iterable[str]) -> list[str]:
    """Every quoted literal naming this project with a leading "/".

    `top_level` is what the project ACTUALLY holds, read from disk by
    `project_top_level`. That is what separates the mistake from the two
    things it reads like: `/api/v1/users` is a URL route and `/usr/bin/env`
    is a real machine path, and neither names a directory this project has.
    An empty set therefore declines everything, which is the right answer for
    a project we could not read.

    Returned in source order, once each -- the note names one and counts the
    rest, and a list with the same string three times would say "3 paths".
    """
    names = frozenset(top_level)
    if not names:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        # A line carrying a URL is a line where "/x" is a URL path. Cheap,
        # and it is the one false positive worth spending a whole line on.
        if "://" in line:
            continue
        cutoff = _comment_at(line)
        for match in _LITERAL.finditer(line):
            if match.start() >= cutoff:
                break
            value = match.group("value")
            segment = _first_segment(value)
            if segment is None or segment not in names or value in seen:
                continue
            seen.add(value)
            found.append(value)
    return found


# A bare path token in prose: a leading "/" and one or more segments of the
# characters a filename actually uses. Anchored on a non-word, non-slash
# character so `https://example.com/src/x` is not read as `/src/x` -- the
# scheme's own "//" is what disqualifies it.
_MENTION = re.compile(r"(?<![\w/])(/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*)")

# What a message ends with rather than what a path ends with. `.` is included
# because a sentence ends in one and a filename does not end in one either.
_TRAILING = ".,:;\"'`)]}"


def find_project_absolute_mentions(text: str, top_level: Iterable[str]) -> list[str]:
    """Every bare mention of this project's root-anchored spelling in prose.

    The same question `find_project_absolute_literals` asks, of a different
    kind of text. That one reads SOURCE and so requires a quoted literal,
    because in source an unquoted `/src/x` is a division. This one reads a
    gate's DIAGNOSTIC output, where there is no grammar to lean on and the
    run's own pytest tail quotes the path on one line and not the next:

        E   AssertionError: HTML file not found at /src/iphone15.html
        E   FileNotFoundError: [Errno 2] No such file or directory: '/src/iphone15.html'

    Both are the same defect and only the second is a literal. Keeping them
    two functions rather than one permissive one is deliberate: loosening the
    source scanner to match bare tokens would flag every `a / b` in arithmetic.

    Callers still gate on the file EXISTING at the relative spelling, which
    is what makes a claim built on this true rather than plausible.
    """
    names = frozenset(top_level)
    if not names:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for match in _MENTION.finditer(text):
        value = match.group(1).rstrip(_TRAILING)
        segment = _first_segment(value)
        if segment is None or segment not in names or value in seen:
            continue
        seen.add(value)
        found.append(value)
    return found


def is_interpreted_file(file_path: str) -> bool:
    """Will something later RUN this file?

    The whole scope of this middleware. A path inside a file nobody executes
    is resolved by a browser, a reader or a human, and none of them are the
    interpreter this item is about.
    """
    if not file_path:
        return False
    name = PurePosixPath(file_path.replace("\\", "/")).name
    if name in _INTERPRETED_NAMES:
        return True
    return PurePosixPath(name).suffix.lower() in _INTERPRETED_SUFFIXES


def project_top_level(project_path: Any) -> frozenset[str]:
    """The names directly inside the project root, or an empty set.

    Read fresh rather than cached: `tests/` is frequently created BY the run,
    and a set captured at construction would decline the very write that made
    the directory. One `scandir` against a model call is free.

    An unreadable project answers with the empty set, which declines
    everything downstream -- never raises. Bookkeeping may not end a run
    (CLAUDE.md 8a).
    """
    if project_path is None:
        return frozenset()
    try:
        with os.scandir(Path(project_path)) as entries:
            return frozenset(entry.name for entry in entries)
    except OSError:
        return frozenset()


_HINT = (
    '\n\nNote: this file now contains the literal "{spelling}". The file tools '
    'resolve a leading "/" against this project, but that string is not a tool '
    "argument -- it is data inside a file, and whatever runs this file later "
    '(python, node, a shell, make) reads "/{segment}" as the MACHINE\'s root, '
    "where this project's files are not. Nothing between here and that process "
    "will rewrite it.\n"
    'Write it relative to the project root ("{relative}"), or build it at run '
    "time from the file's own location."
)

_MORE = "\n{count} other literal(s) in this file have the same problem: {rest}."


def _relative(spelling: str) -> str:
    """The spelling that means the same thing to every consumer."""
    return spelling.replace("\\", "/").lstrip("/")


class ContentPathMiddleware(AgentMiddleware):
    """Append an explanation when a write puts a virtual path into real source.

    Fires only on a SUCCESSFUL write to a file something later interprets,
    and only for a literal whose first segment is a real top-level entry of
    this project. It appends; it never rewrites the model's bytes.
    """

    def __init__(
        self,
        role: str | None = None,
        *,
        project_path: Any = None,
        usage: Any = None,
        trace: Any = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.project_path = project_path
        self.usage = usage
        self.trace = trace

    def _flagged(self, name: str, args: dict) -> list[str]:
        """The literals to explain, or an empty list."""
        target = args.get("file_path") or args.get("path")
        if not isinstance(target, str) or not is_interpreted_file(target):
            return []
        top_level = project_top_level(self.project_path)
        if not top_level:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for key in _CONTENT_ARGS.get(name, ()):
            value = args.get(key)
            if not isinstance(value, str):
                continue
            for literal in find_project_absolute_literals(value, top_level):
                if literal not in seen:
                    seen.add(literal)
                    found.append(literal)
        return found

    def _announce(self, spelling: str) -> None:
        """Count and say that a result was annotated (TODO.md lesson 5).

        Both halves swallow their own failure: a run that did its work must
        not be reported failed because a log line could not be written
        (CLAUDE.md 8a).
        """
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_content_path_flagged(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("content-path hit not counted", exc_info=True)
        try:
            if self.trace is not None:
                self.trace.notice(
                    f'explained that the literal "{spelling}" written into this '
                    f"file is resolved by a real interpreter, to which a leading "
                    f'"/" is the machine\'s root and not this project',
                    role=role,
                    name=CONTENT_PATH_NOTICE,
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("content-path hit not announced", exc_info=True)

    def _hint(self, literals: list[str]) -> str:
        first = literals[0]
        segment = _first_segment(first) or ""
        text = _HINT.format(spelling=first, segment=segment, relative=_relative(first))
        if len(literals) > 1:
            rest = ", ".join(f'"{value}"' for value in literals[1:])
            text += _MORE.format(count=len(literals) - 1, rest=rest)
        return text

    def _is_failure(self, content: str) -> bool:
        """deepagents reports a filesystem failure as ordinary content leading
        with `Error` (backends/filesystem.py:447), which is also what
        `repeat_guard._is_error` reads. Never annotate one: the note would be
        about bytes that are not on disk."""
        return content.lstrip().startswith("Error")

    def _annotate(self, request, result: Any) -> Any:
        """`result` with the explanation appended, or `result` unchanged.

        A result that is neither a string nor a message carrying one is
        returned untouched -- `machine_paths.py`'s rule, and the same reason:
        a `Command` carries state updates this has no business rewriting.
        """
        call = request.tool_call
        name = call.get("name") or ""
        if name not in _CONTENT_ARGS:
            return result

        content = getattr(result, "content", result)
        if not isinstance(content, str) or self._is_failure(content):
            return result

        literals = self._flagged(name, call.get("args") or {})
        if not literals:
            return result

        self._announce(literals[0])
        hint = self._hint(literals)
        if result is content:
            return content + hint
        return result.model_copy(update={"content": content + hint})

    def wrap_tool_call(self, request, handler):
        return self._annotate(request, handler(request))

    async def awrap_tool_call(self, request, handler):
        return self._annotate(request, await handler(request))


__all__ = [
    "CONTENT_PATH_NOTICE",
    "ContentPathMiddleware",
    "find_project_absolute_literals",
    "find_project_absolute_mentions",
    "is_interpreted_file",
    "project_top_level",
]
