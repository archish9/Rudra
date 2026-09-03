"""GutterIndentMiddleware — repair an `old_string` carrying `read_file`'s gutter.

**The defect (OPEN-92).** `read_file` renders every line as
`f"{marker:>{marker_width}}  {line}"` (deepagents/backends/utils.py:243) --
the line number, **two spaces**, then the raw source line. Upstream's own
docstring calls that separator a load-bearing contract, and it is: two
downstream parsers match it exactly. It is not going away.

A model composing `edit_file`'s `old_string` from what it was shown carries
those two spaces into the content. Every line after the first comes out `+2`
against the file, and the first line comes out with no indent at all -- the
model reads everything before its first non-space character as the marker.
`edit_file` is an exact substring match (`content.count(old_string)`,
utils.py:523) with one recovery, for a trailing newline, so a `+2` string is
simply absent and `occurrences == 0`.

**Why the model then retries byte-identically.** The error hands it back its
own argument -- `Error: String not found in file: '<old_string>'`
(utils.py:552) -- and says nothing about indentation. Given its own string
returned unchanged, the model re-sends it. Three times, and then
`MAX_REPEATED_CALLS` kills the invocation. That guard is the only thing that
stops this and must not be weakened (`subagents/runner.py`, OPEN-60).

**Measured**, run `fc543fb2b82f`: `index.html` written once and never changed
again, seven coder `edit_file` calls against it, all zero-occurrence, two
guard halts. The delta was exactly `+2` on every line of both distinct edits.
The tester's two edits in the same run SUCCEEDED -- it was editing a file it
had written in the same invocation, so its `old_string` came from its own
`write_file` content and never round-tripped through the gutter.

That asymmetry is the general statement of the defect:

    `edit_file` works when an agent edits what it just wrote, and fails when
    it edits anything else.

Which is every fix-loop retry, every task after the first in a project with
shared files, and every `rudra --continue`. It is not an HTML problem and not
a 550B problem: any file whose lines are indented is unpatchable by an agent
that read it first.

**Why the repair returns the FILE's bytes rather than dedenting the model's.**
The obvious algorithm -- strip the minimum leading indent from `old_string`
and match that -- does not work, and was measured not to: against the seven
real failures it repairs **zero** of them. Dedenting to column zero produces a
block the file does not contain either, because the file's own block is
indented too. What does work is an anchored search on stripped line content,
which finds a unique window for all seven; the corrected `old_string` is then
that window's own bytes, which match by construction.

**Why every uncertain case declines.** A dedented string that matches twice is
ambiguous, and silently editing the wrong block is worse than the failure this
is fixing. Zero matches means the argument was wrong for some other reason and
the real error is the useful answer. A non-uniform delta is not this defect.
In all of them the call goes through untouched -- this middleware only ever
turns a certain failure into a success, never a failure into a different one.

The seam is `FixWriteParamsMiddleware`'s (`fix_write_params.py`): a correction
to what a model reliably gets wrong about an argument's format, applied before
the backend sees it. The disk read is `RepeatGuardMiddleware._disk_digest`'s
(`repeat_guard.py:383`), including its rule that None means "cannot confirm",
which is always the answer that PERFORMS the call.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from rudra.compat.virtual_paths import virtual_to_host

logger = logging.getLogger(__name__)

# The one tool whose arguments carry file bytes the model transcribed from a
# `read_file` display. `write_file`'s `content` is composed, not copied.
_EDIT_TOOL = "edit_file"


def _indent(line: str) -> int:
    """Leading SPACES only. A tab is content here, not indentation -- the
    gutter is spaces whatever the file indents with, so measuring spaces is
    what isolates the delta this repairs."""
    return len(line) - len(line.lstrip(" "))


def locate_by_content(content: str, old_string: str) -> list[str]:
    """Every window of `content` matching `old_string` line-for-line once
    indentation is set aside, as the file's OWN bytes.

    Used for two different jobs, which is why it is separate from the repair:
    the repair needs exactly one, and the error message wants to say how many
    there were. An empty list means the argument is wrong for some reason
    other than indentation, and upstream's own error -- which carries a
    trailing-newline recovery hint -- is then the useful one.
    """
    if not old_string:
        return []
    old_lines = old_string.split("\n")
    file_lines = content.split("\n")
    span = len(old_lines)
    if span < 2 or span > len(file_lines):
        return []
    return [
        "\n".join(file_lines[i : i + span])
        for i in range(len(file_lines) - span + 1)
        if all(file_lines[i + k].strip() == old_lines[k].strip() for k in range(span))
    ]


# The message the model gets when the repair declines but the text IS in the
# file. It has to do two things upstream's cannot: name INDENTATION as the
# difference, and quote the FILE's bytes rather than echo the model's own
# argument back at it. Given its own string returned unchanged, a model
# re-sends it -- measured three times per attempt in run fc543fb2b82f, twice,
# each ending in a killed invocation.
_AMBIGUOUS = (
    "Error: String not found in file, because the indentation does not match. "
    "The file contains this text {count} times at a different indentation, so "
    "it is ambiguous. The file's version of the first one, exactly:\n"
    "{quoted}\n"
    "Retry with old_string copied at the file's own indentation, and add "
    "surrounding context so the match is unique. Note that read_file prints a "
    "2-space gutter after each line number; that gutter is NOT part of the "
    "file and must not be included in old_string."
)


def repair_indent(content: str, old_string: str, new_string: str) -> tuple[str, str] | None:
    """`(old_string, new_string)` corrected to the file's indentation, or None.

    None means decline, and every decline passes the original call through.
    The five declines, each for its own reason:

    * `old_string` already occurs -- never touch the happy path.
    * fewer than two lines -- the model strips the first line's indent rather
      than over-indenting it, so a one-line argument carries no delta to infer.
    * no unique window matching on stripped line content -- zero is some other
      mistake, more than one is ambiguous.
    * the per-line delta is not uniform -- the gutter adds the same two spaces
      to every line, so a block that differs by different amounts is not this.
    * a non-blank replacement line shallower than the delta -- only leading
      spaces may be removed, and there are not enough to remove.
    """
    if not old_string or old_string in content:
        return None

    old_lines = old_string.split("\n")
    if len(old_lines) < 2:
        return None

    file_lines = content.split("\n")
    span = len(old_lines)
    starts = [
        i
        for i in range(len(file_lines) - span + 1)
        if all(file_lines[i + k].strip() == old_lines[k].strip() for k in range(span))
    ]
    if len(starts) != 1:
        return None
    start = starts[0]

    # The delta is measured on the lines AFTER the first: the first line is the
    # one the model strips entirely rather than shifting, so including it would
    # poison a value that must be uniform to be trusted. Blank lines have no
    # indent to measure.
    deltas = {
        _indent(old_lines[k]) - _indent(file_lines[start + k])
        for k in range(1, span)
        if old_lines[k].strip()
    }
    if len(deltas) != 1:
        return None
    delta = deltas.pop()
    if delta < 0:
        return None

    repaired_old = "\n".join(file_lines[start : start + span])
    # The anchored window is unique among windows; its TEXT still has to be
    # unique in the file, because that is what `edit_file` counts.
    if content.count(repaired_old) != 1:
        return None

    # The replacement is shifted the same way, or it lands at the wrong indent
    # and the edit "succeeds" into a corrupted file. First line takes the
    # file's own indent, since that is the one the model dropped; the rest give
    # back the gutter.
    base = file_lines[start][: _indent(file_lines[start])]
    new_lines = new_string.split("\n")
    repaired_new = [base + new_lines[0].lstrip(" ")]
    for line in new_lines[1:]:
        if not line.strip():
            repaired_new.append(line)
        elif line[:delta] == " " * delta:
            repaired_new.append(line[delta:])
        else:
            return None

    return repaired_old, "\n".join(repaired_new)


class GutterIndentMiddleware(AgentMiddleware):
    """Rewrite a gutter-indented `edit_file` argument before the tool runs.

    Pre-emptive rather than a retry after the tool's error, for two reasons:
    the check costs one file read against a ~46 s model round trip at the
    provider this was measured on, and reacting to `Error: String not found`
    would key behaviour on upstream's message text.
    """

    def __init__(
        self,
        role: str | None = None,
        *,
        usage: Any = None,
        trace: Any = None,
        project_path: Any = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.usage = usage
        self.trace = trace
        self.project_path = project_path

    def _content(self, target: str) -> str | None:
        """What is on disk at `target` now, or None for "cannot confirm".

        None is always the answer that performs the call unchanged: no project
        path, a backend route outside the project (`/artifacts/`, `/skills/`),
        a file that is gone, or bytes that will not decode.

        Line endings are normalised the way the backend normalises them on
        read (`backends/utils.py:500` -- "downstream tooling assumes LF"), so
        the content matched here is the content `edit_file` will count against.
        """
        if self.project_path is None or not target:
            return None
        try:
            host = virtual_to_host(target, Path(self.project_path))
            if host is None:
                return None
            raw = host.read_bytes().decode("utf-8")
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        return raw.replace("\r\n", "\n").replace("\r", "\n")

    def _announce(self, target: str) -> None:
        """Say that a model's argument was rewritten (TODO.md lesson 5).

        Swallows its own failure: a run that did its work must not be reported
        failed because a log line could not be written (CLAUDE.md §8a).
        """
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_edit_reindented(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("edit reindent not counted", exc_info=True)
        try:
            if self.trace is not None:
                self.trace.notice(
                    f"repaired the indentation of an edit to {target} "
                    f"(read_file's line-number gutter had been copied into old_string)",
                    role=role,
                    name="reindent",
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("edit reindent not announced", exc_info=True)

    def _apply(self, request):
        """Repair the call, or refuse it with something the model can act on.

        Returns a `ToolMessage` to answer WITHOUT running the tool, or None
        to run it -- possibly with rewritten arguments. Refusing outright is
        safe only because we have already read the file and know the match
        cannot succeed; every case where that is not certain runs the tool.
        """
        call = request.tool_call
        if call.get("name") != _EDIT_TOOL:
            return None

        args = call.get("args", {})
        old, new = args.get("old_string"), args.get("new_string")
        target = args.get("file_path")
        if not isinstance(old, str) or not isinstance(new, str) or not isinstance(target, str):
            return None

        content = self._content(target)
        if content is None:
            return None

        repaired = repair_indent(content, old, new)
        if repaired is not None:
            fixed = dict(args)
            fixed["old_string"], fixed["new_string"] = repaired
            call["args"] = fixed
            self._announce(target)
            return None

        # The repair declined. Say why ONLY when the text is demonstrably in
        # the file at another indentation -- otherwise upstream's own error is
        # the better one, since it carries a trailing-newline recovery hint
        # this must not shadow.
        if old in content:
            return None
        found = locate_by_content(content, old)
        if len(found) < 2:
            return None
        return ToolMessage(
            content=_AMBIGUOUS.format(count=len(found), quoted=found[0]),
            tool_call_id=call.get("id", ""),
            name=_EDIT_TOOL,
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        refusal = self._apply(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        refusal = self._apply(request)
        return refusal if refusal is not None else await handler(request)
