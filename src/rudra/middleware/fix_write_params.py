"""FixWriteParamsMiddleware — auto-correct common file tool parameter mistakes.

Fixes applied (in order):
1. Parameter rename: `filename` or `path` → `file_path` for write/edit tools
2. Markdown fence stripping: removes ```lang ... ``` wrappers from file content
3. Split dot-segment repair: `/. rudra/x` → `/.rudra/x` on every path arg
4. Sandbox prefix stripping: removes known LLM-hallucinated path prefixes
   (/testbed/, /workspace/, /home/user/, etc.) from all file tool paths

It also REFUSES three calls rather than repairing them, the first two on the
SHAPE of the content rather than on the path, and the third on both:

* a write whose only purpose is to bring a directory into being (OPEN-22).
  There is nothing to repair there -- the model wants a directory and
  `write_file` makes files -- so the only useful answer is an error it can
  act on.
* a write whose content is the model's closing summary rather than the file
  (OPEN-97). Refused and not repaired for the same reason, and refused
  rather than annotated because a note arrives after the bytes are already
  on disk and the bytes are the damage.
* a write of a file at the project root whose name and one-sentence content
  both say "finished" -- `COMPLETION`, `DONE`, `task_complete.txt`
  (OPEN-104). OPEN-42's reflex, measured again four runs later, and out of
  the second rule's reach because that rule needs a known suffix.

Neither rewrites content. Both are held to `_is_directory_placeholder`'s
bar: a content rule must not be able to fire on something a person would
write by hand.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

from rudra.compat.path_constants import SANDBOX_PREFIXES
from rudra.compat.virtual_paths import virtual_to_relative

logger = logging.getLogger(__name__)

# Info string is anything up to the newline — matches the coverage that
# compat/overwrite_backend.py used to provide before U.3 deleted it (its
# regex was `^```[^\n]*\n(.*)\n```$`; the file is gone, so no line is cited).
# See TODO.md U.15.
_FENCE_RE = re.compile(r"^```([^\n]*)\n?(.*?)```\s*$", re.DOTALL)

# Info strings that mark the outer fence as a *wrapper* around a whole
# document rather than one code block inside it. A markup info string is
# the only thing that distinguishes "model wrapped my README in ```markdown"
# from "this README's own first line is a ```bash block" -- both shapes open
# and close with a fence and carry fences inside. See TODO.md OPEN-3.
_MARKUP_INFO = {"markdown", "md", "mdx", "rst", "text", "txt", ""}

# Tools that use file_path but model sometimes passes filename/path instead
_FILE_TOOLS = {"write_file", "edit_file"}

# All tools that carry a path argument (superset of _FILE_TOOLS)
_PATH_TOOLS = {"write_file", "edit_file", "read_file", "ls", "glob"}

# Path-bearing argument names to clean across all path tools
_PATH_ARG_KEYS = ("file_path", "path", "pattern")


def _strip_fences(content: str) -> str:
    """Remove an outer ```lang ... ``` wrapper, but never real fenced content.

    Strip only when the interior carries no fence of its own, or when the
    info string names a markup type -- that is exactly what separates a
    wrapper the model added from a Markdown file whose own content opens
    and closes with fences (TODO.md OPEN-3).
    """
    m = _FENCE_RE.match(content.strip())
    if m is None:
        return content
    info, inner = m.group(1).strip().lower(), m.group(2)
    first = info.split()[0] if info else ""
    if "```" in inner and first not in _MARKUP_INFO:
        return content
    return inner


# A path segment that is exactly `.` followed by whitespace and then a
# name: `/. rudra/AGENTS.md` for `/.rudra/AGENTS.md` (OPEN-8). Anchored to
# `^` or `/` so a dot INSIDE a segment is never touched, and requiring a
# non-space, non-slash after the whitespace so there is actually a segment
# to rejoin.
#
# Narrow on purpose. Stripping whitespace from paths generally would break
# `My Documents`, which is a real directory on every desktop OS; what makes
# THIS safe is that `. rudra` -- a directory whose name is dot, space, name
# -- is not a thing anyone creates, while a model splitting `.rudra` into
# two tokens is measured behaviour.
_SPLIT_DOT_RE = re.compile(r"(^|/)\.[ \t]+(?=[^/\s])")


def _repair_split_dot_segment(path: str) -> str:
    """Rejoin a dotfile segment the model split with whitespace."""
    return _SPLIT_DOT_RE.sub(r"\1.", path)


def _strip_sandbox_prefix(path: str) -> str:
    """Strip known sandbox/training-env prefixes from an absolute POSIX path."""
    if not path.startswith("/"):
        return path
    for prefix in SANDBOX_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


_DIRECTORY_PLACEHOLDER = (
    "REJECTED: `{path}` has no file extension and its content is only a "
    "comment, which is how an agent writes a file when what it wants is a "
    "directory. Directories here are created implicitly -- writing "
    "`{path}/<name>.py` creates `{path}/` on the way. Write the file you "
    "actually want; do not write a placeholder to make its directory."
)


def _is_directory_placeholder(path: str, content: object) -> bool:
    """Is this a write whose only purpose is to create a directory?

    Held to the same bar as `_repair_split_dot_segment`: it must not be able
    to fire on something a person would write by hand. Three conditions, all
    required.

    * No suffix -- `tests`, not `tests.py`.
    * Not a dotfile -- `.gitignore` and `.env` have no suffix either, and a
      `.gitignore` of nothing but comments is useless but legal.
    * Content is at most three lines and every one of them is blank or a
      `#` comment.

    `LICENSE`, `Makefile` and `Dockerfile` are all suffix-less and all carry
    real content, so none of them match. What matches is the measured shape:
    `write_file('/tests', '# This is a placeholder to create the directory')`
    (OPEN-22), which produced a 47-byte FILE named `tests` and doomed the
    five later tasks that needed `tests/` to be a directory.
    """
    if not isinstance(content, str):
        return False
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if not name or name.startswith(".") or "." in name:
        return False
    lines = [line.strip() for line in content.strip().splitlines()]
    if len(lines) > 3:
        return False
    return bool(lines) and all(not line or line.startswith("#") for line in lines)


# One required marker per structural file type: syntax a file of this type
# cannot be written without. A file of this type containing NONE of them is
# not that type, at any size. Closed on purpose -- an unlisted suffix
# declines, because the cost of a false refusal is the model's real write.
#
# `.md`, `.txt` and `.rst` are deliberately absent and must stay absent: a
# document of nothing but English sentences is exactly what they are for.
_REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    ".html": ("<",),
    ".htm": ("<",),
    ".xml": ("<",),
    ".svg": ("<",),
    ".css": ("{", "@"),
    ".json": ("{", "[", '"'),
    ".py": ("import ", "def ", "class ", "=", "#", ":"),
    ".js": ("{", "=", ";", "function", "const ", "let ", "import "),
    ".ts": ("{", "=", ";", "function", "const ", "let ", "import "),
}

# Anything saying "this file is generated from a template", where the
# structural markers above may legitimately be absent -- a Jinja fragment, a
# `.html.j2`, a Handlebars partial. Scope note 2 of OPEN-97: these are the
# real false-positive risk, so their presence declines outright.
_TEMPLATE_MARKERS = ("{{", "{%", "<%", "${", "$(")

# The longest content this rule will look at. A closing summary is short; a
# file is not. Well above the measured 329 bytes and far below any real
# document.
_PROSE_MAX_CHARS = 1000

# Lines and words. Three lines because a summary is occasionally two or
# three sentences on separate lines; eight words because fewer is not a
# sentence about anything and could be a legitimate one-liner.
_PROSE_MAX_LINES = 3
_PROSE_MIN_WORDS = 8

_PROSE_NOT_CONTENT = (
    "REJECTED: `{path}` is a {suffix} file and this content contains none of "
    "that syntax -- it reads as a summary of the work, not the work. Nothing "
    "was written; the file on disk is unchanged.\n\n"
    "If you are finished, say so in your REPLY and call no tool: that is what "
    "ends your turn, and your reply is the only thing the caller sees. A file "
    "cannot announce anything -- it only replaces what was there.\n\n"
    "If you did mean to write this file, write its actual content."
)


def _suffix_of(path: str) -> str:
    """The lower-cased suffix of the file `path` names, or "".

    Read off the NAME rather than the whole string: `src.d/README` carries a
    dot and has no suffix, and `.gitignore` is a dotfile whose leading dot is
    not one either -- `PurePosixPath` answers both the way the rest of the
    project does (`content_paths.is_interpreted_file`).
    """
    name = PurePosixPath(path.replace("\\", "/").rstrip("/")).name
    return PurePosixPath(name).suffix.lower()


def _is_prose_not_content(path: str, content: object) -> bool:
    """Is this a write of the model's closing summary rather than a file?

    Held to `_is_directory_placeholder`'s bar: it must not be able to fire on
    something a person would write by hand. FOUR conditions, all required,
    over a CLOSED suffix table that declines anything it does not know.

    * **Short** -- at most 1,000 characters.
    * **At most three lines.**
    * **Carrying none of its own type's syntax.** The load-bearing one:
      there is no valid HTML document, fragment or snippet with no "<" in
      it, at any size. A template marker declines here too, since a Jinja
      partial legitimately carries no markup of its own.
    * **Prose-shaped** -- eight or more words, opening with a capital and
      closing on ".", "!" or "?".

    The measured shape (OPEN-97, run `2cde3406f7d6` at=936.0): 329 bytes,
    one line, no "<", reading "Done. Created /src/iphone15.html with all
    required elements: ..." -- written over 24,800 bytes of finished markup,
    which `write_file` replaced and answered "Updated file".

    An empty write is NOT this rule: truncation to zero is a different
    intention and possibly a legitimate one.
    """
    if not isinstance(content, str):
        return False
    markers = _REQUIRED_MARKERS.get(_suffix_of(path))
    if markers is None:
        return False
    body = content.strip()
    if not body or len(body) > _PROSE_MAX_CHARS:
        return False
    if len(body.splitlines()) > _PROSE_MAX_LINES:
        return False
    if any(marker in body for marker in markers):
        return False
    if any(marker in body for marker in _TEMPLATE_MARKERS):
        return False
    return len(body.split()) >= _PROSE_MIN_WORDS and body[0].isupper() and body[-1] in ".!?"


COMPLETION_FILE_NOTICE = "completion-file"
"""The `name` on the NOTICE, and what a maintainer greps `debug-<id>.jsonl`
for. One spelling, as a module constant (`CLAUDE.md` §8a)."""

# Words a completion marker's NAME is built from. A name qualifies when every
# word in it is from `_MARKER_WORDS` and at least one is from
# `_COMPLETION_WORDS` -- `DONE`, `COMPLETION`, `task_complete`, `ALL_DONE`,
# `tasks-completed`. A category read off the name rather than a list of names
# seen so far, for OPEN-42 §10's reason: the model invents the name, and run7's
# `DONE` would not have covered this run's `COMPLETION`.
_COMPLETION_WORDS = frozenset(
    {"done", "complete", "completed", "completion", "finish", "finished", "success", "succeeded"}
)
_MARKER_WORDS = _COMPLETION_WORDS | {"task", "tasks", "all", "work", "job"}

# The suffixes a marker has been written with: none (`DONE`, `COMPLETION`) and
# the two a model reaches for when it wants a "text file" (`task_complete.txt`).
# Anything else declines -- `done.py` and `completion.sh` are code.
_MARKER_SUFFIXES = frozenset({"", ".txt", ".md"})

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_NAME_WORD_SEPARATORS = re.compile(r"[_\-.\s]+")

_COMPLETION_FILE = (
    "REJECTED: `{path}` would be a file whose only content says the work is "
    "finished. Nothing was written.\n\n"
    "A file announces nothing: it stays in the user's project as though they "
    "had asked for it, and your turn is not over. If you are finished, reply "
    "with one or two lines saying what you did and call no tool -- that is "
    "what ends your turn, and your reply is the only thing the caller sees."
)


def _is_completion_name(name: str) -> bool:
    """Is this file NAME built only of completion-marker words?"""
    if not name or name.startswith("."):
        return False
    pure = PurePosixPath(name)
    if pure.suffix.lower() not in _MARKER_SUFFIXES:
        return False
    stem = _CAMEL_BOUNDARY.sub("_", pure.stem if pure.suffix else name)
    words = {word for word in _NAME_WORD_SEPARATORS.split(stem.lower()) if word}
    return bool(words) and words <= _MARKER_WORDS and bool(words & _COMPLETION_WORDS)


def _root_level_name(path: str, project_root: Path | None) -> str | None:
    """The file name `path` writes, when that file is at the project root.

    With a root, the answer is `virtual_to_relative`'s -- the one authority on
    which real file a spelling names (CR-B4) -- so the host spelling run
    `f845b496a2aa` used, `/private/tmp/<project>/COMPLETION`, is placed where
    the backend put it. Without one only a single-segment spelling can be
    placed at all, so anything longer declines: the degraded mode is the old
    accepting one, never a wider refusal.
    """
    if project_root is not None:
        relative = virtual_to_relative(path, project_root)
    else:
        relative = path.replace("\\", "/").lstrip("/")
    if not relative:
        return None
    relative = relative.rstrip("/")
    if not relative or "/" in relative:
        return None
    return relative


def _is_completion_announcement(path: str, content: object, project_root: Path | None) -> bool:
    """Is this a file written only to say the work is finished?

    Held to `_is_directory_placeholder`'s bar: it must not be able to fire on
    something a person would write by hand. Three conditions, all required.

    * **The name is a completion marker** -- every word in it one of
      `_MARKER_WORDS`, at least one a completion word, suffix none, `.txt` or
      `.md`, not a dotfile. `Makefile`, `LICENSE`, `NOTICE`, `CHANGELOG` and
      `.gitignore` carry no such word; `STATUS.md` and `TODO.md` neither.
    * **It is at the project root.** `build/COMPLETE` is somebody's artefact.
    * **The content is one short sentence-shaped announcement** -- at most
      1,000 characters and three lines, two or more words, opening with a
      capital and closing on ".", "!" or "?", no template marker. A build
      stamp holding nothing, a timestamp, a digest or JSON declines, and so
      does a one-line `complete -F` shell completion script.

    Not OPEN-97's predicate, deliberately: that one needs a suffix from its
    closed table and eight words, and the first measured write here is
    "All tasks complete." -- three words, no suffix.

    The measured shape (OPEN-104, run `f845b496a2aa`): `COMPLETION` at the
    project root, written twice -- "All tasks complete." then "Task t2
    complete: database.py implemented with SQLite CRUD operations ..." --
    and before it run7's `/DONE` and `/task_complete.txt` (OPEN-42).
    """
    if not isinstance(content, str) or not isinstance(path, str):
        return False
    name = _root_level_name(path, project_root)
    if name is None or not _is_completion_name(name):
        return False
    body = content.strip()
    if not body or len(body) > _PROSE_MAX_CHARS:
        return False
    if len(body.splitlines()) > _PROSE_MAX_LINES:
        return False
    if any(marker in body for marker in _TEMPLATE_MARKERS):
        return False
    return len(body.split()) >= 2 and body[0].isupper() and body[-1] in ".!?"


class FixWriteParamsMiddleware(AgentMiddleware):
    """Auto-correct file tool parameters: rename args, clean paths, strip fences.

    D4 splits this middleware in two:

    * **Always on** — markdown-fence stripping, `filename`/`path` ->
      `file_path` aliasing, and split dot-segment repair. Fence stripping is
      *required*, not a small-model workaround: 0.7.4's
      `FilesystemBackend.write()` no longer strips, so nothing else in the
      stack does (TODO.md U.3, U.15). The dot-segment repair is always on
      because it cannot fire on a path anyone would write by hand, and
      because the write path fails SILENTLY without it -- a mangled read
      errors, a mangled write creates a junk directory and reports success
      (TODO.md OPEN-8).
    * **Opt-in** — sandbox-prefix stripping, behind `[compat] sandbox_paths`.
      It was built for a training-sandbox path shape that a 32B model on a
      normal machine does not emit, and it rewrites legitimate absolute
      paths when it does fire.
    """

    def __init__(
        self,
        strip_sandbox_prefixes: bool = False,
        *,
        role: str | None = None,
        usage: Any = None,
        trace: Any = None,
        project_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.strip_sandbox_prefixes = strip_sandbox_prefixes
        # All three default to None for GutterIndentMiddleware's reason
        # (OPEN-92): this middleware is constructed by compat tests and by
        # callers outside a full run, and a stack that reported nowhere must
        # still fix parameters. Only the OPEN-97 and OPEN-104 refusals read
        # them.
        self.role = role
        self.usage = usage
        self.trace = trace
        # Only the OPEN-104 refusal reads it, to place a host-spelled path at
        # the project root. None narrows that rule; it never widens it.
        self.project_path = project_path

    def _fix_args(self, request):
        name = request.tool_call.get("name")
        if name not in _FILE_TOOLS and name not in _PATH_TOOLS:
            return request

        args = dict(request.tool_call.get("args", {}))

        if name in _FILE_TOOLS:
            # Fix parameter name: filename/path → file_path
            if "filename" in args and "file_path" not in args:
                args["file_path"] = args.pop("filename")
            elif "path" in args and "file_path" not in args:
                args["file_path"] = args.pop("path")
            # Strip markdown fences from file content
            if isinstance(args.get("content"), str):
                # isinstance, not `in`: a model emitting a list for
                # `content` raised AttributeError inside wrap_tool_call
                # instead of the tool returning a validation error it could
                # correct. The sandbox branch below already guards this way
                # (CR-E11).
                args["content"] = _strip_fences(args["content"])

        # Always on, and after the alias above so it cleans the key the
        # model's path actually ended up under. `content` is deliberately
        # not in _PATH_ARG_KEYS: file content is data, and a line reading
        # `. rudra` inside a document is not a path.
        for key in _PATH_ARG_KEYS:
            if key in args and isinstance(args[key], str):
                args[key] = _repair_split_dot_segment(args[key])

        # Strip sandbox prefixes from all path-bearing args (second defense
        # layer). Opt-in per D4 — see the class docstring.
        if self.strip_sandbox_prefixes:
            for key in _PATH_ARG_KEYS:
                if key in args and isinstance(args[key], str):
                    cleaned = _strip_sandbox_prefix(args[key])
                    if cleaned != args[key]:
                        args[key] = cleaned

        request.tool_call["args"] = args
        return request

    def _announce_prose(self, path: str) -> None:
        """Say that a write was refused for carrying prose (CLAUDE.md 8a).

        What this guard prevents is bytes that never reach disk, and bytes
        that never reach disk leave no mark on calls, seconds, tokens or
        tool results -- so without its own number "did it fire?" is
        answerable only by a hand-written parser.

        Swallows its own failure: a run that did its work must not be
        reported failed because a log line could not be written.
        """
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_write_rejected_as_prose(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("prose write refusal not counted", exc_info=True)
        try:
            if self.trace is not None:
                self.trace.notice(
                    f"refused a write to {path}: its content reads as a summary "
                    "of the work rather than the work, and the file on disk was "
                    "left unchanged",
                    role=role,
                    name="prose-write",
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("prose write refusal not announced", exc_info=True)

    def _announce_completion(self, path: str) -> None:
        """Say that a completion-marker write was refused (CLAUDE.md 8a).

        `_announce_prose`'s reason exactly: the file never reaches disk, so
        nothing else in the run records that the model tried. Swallows its
        own failure for the same rule.
        """
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_completion_file_refused(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("completion file refusal not counted", exc_info=True)
        try:
            if self.trace is not None:
                self.trace.notice(
                    f"refused a write to {path}: its only content announces that "
                    "the work is finished, so nothing was written",
                    role=role,
                    name=COMPLETION_FILE_NOTICE,
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("completion file refusal not announced", exc_info=True)

    def _refusal(self, request):
        """A ToolMessage refusing this write on the shape of its content, or None.

        THREE independent rules, none of which can fire on another's shape:
        `_is_directory_placeholder` wants `#` comments, `_is_prose_not_content`
        wants a suffix in its closed table, and `_is_completion_announcement`
        wants a sentence under a marker name with no suffix, `.txt` or `.md`.

        Both lead with `REJECTED:` and neither with `Error:`, deliberately:
        `trace/stream.py::looks_like_error` counts a leading `Error:` and
        `subagents/runner.py` halts an invocation on three in a row, which is
        OPEN-94 exactly.
        """
        call = request.tool_call
        if call.get("name") != "write_file":
            return None
        args = call.get("args", {})
        path = args.get("file_path")
        if not isinstance(path, str):
            return None
        content = args.get("content")

        if _is_directory_placeholder(path, content):
            text = _DIRECTORY_PLACEHOLDER.format(path=path.rstrip("/"))
        elif _is_prose_not_content(path, content):
            text = _PROSE_NOT_CONTENT.format(path=path, suffix=_suffix_of(path))
            self._announce_prose(path)
        elif _is_completion_announcement(path, content, self.project_path):
            text = _COMPLETION_FILE.format(path=path)
            self._announce_completion(path)
        else:
            return None

        return ToolMessage(
            content=text,
            tool_call_id=call.get("id", ""),
            name="write_file",
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        request = self._fix_args(request)
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        request = self._fix_args(request)
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)
