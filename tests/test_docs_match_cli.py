"""Guards A4.1 — the README documented seven subcommands that never existed.

`build`, `chat`, `fix`, `edit`, `review`, `suggest` and `resume` were all
documented as working commands while `cli.py` registered exactly one. They
are gone now, but nothing stopped them coming back: a renamed command leaves
its old name in prose, and prose is checked by whoever happens to read it.

This closes that class structurally. It does NOT check whether documentation
is *true* -- a page can name only real commands and still describe them
wrongly (A1.96-A1.100 are all of that kind, and all were found by hand).
It checks that every command, subcommand and relative link the docs name
actually resolves.

Three things defeat the naive version of this test, so it is built around
them rather than against them:

  * `Documentation/04-cli-reference.md` carries a migration table listing
    retired command names *as retired* -- deleting it to satisfy a test
    would destroy real documentation.
  * Two guides state an absence deliberately and explain it: there is no
    `rudra config set` (02-configuration.md, the stdlib cannot write TOML
    without destroying the comments `rudra init` writes) and no
    `rudra skills update` (12-skills.md, the vendored corpus is frozen by
    design).
  * `rudra write a hello world script` is a *task*, not a subcommand.
    `TaskOrCommandGroup.parse_args` (cli.py:78-86) lets an unrecognized
    first token fall through to the callback, and the CLI reference
    documents that with a real example.

Hence DOCUMENTED_AS_ABSENT, and hence the fourth test that keeps it honest.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import typer

from rudra.cli import app

REPO = Path(__file__).resolve().parent.parent

DOCS = [
    REPO / "README.md",
    REPO / "CONTRIBUTING.md",
    REPO / "SECURITY.md",
    *sorted((REPO / "Documentation").glob("*.md")),
]

# Names the documentation mentions in order to say they do NOT exist. Each
# entry is a deliberate statement with a reason attached at the cited line;
# none is an oversight being tolerated.
DOCUMENTED_AS_ABSENT = frozenset(
    {
        # Documentation/04-cli-reference.md:529-536 -- the "if you were
        # expecting this command, here is the current form" migration table.
        "build",
        "chat",
        "fix",
        "edit",
        "review",
        "suggest",
        "resume",
        "watch",
        # Documentation/04-cli-reference.md:34 -- a natural-language task,
        # not a subcommand. `rudra write a hello world script` works because
        # cli.py:78-86 falls unrecognized tokens through to the callback.
        "write",
        # Documentation/02-configuration.md:178 and 12-skills.md:56 -- both
        # say "there is no rudra <x>" and explain why. Absence on purpose.
        "set",
        "update",
    }
)

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_INVOKE = re.compile(
    r"(?<![\w./-])rudra\s+(?:-{1,2}[\w-]+\s+)*([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?"
)


def _command_tree() -> click.Group:
    """The live CLI, as click sees it after Typer builds it."""
    return typer.main.get_command(app)


def _groups() -> dict[str, click.Group]:
    return {
        name: cmd for name, cmd in _command_tree().commands.items() if isinstance(cmd, click.Group)
    }


def _snippets(text: str):
    """Yield only the parts of a markdown file that claim to be commands.

    Prose is excluded on purpose: `pipx list  # rudra should be listed`
    (06-troubleshooting.md:43) and an exported memory header carrying
    `added_by=rudra filed_at=...` (15-memory.md:405) both match a bare
    `rudra <word>` regex, and neither is a command.
    """
    text = _HTML_COMMENT.sub("", text)
    for index, part in enumerate(text.split("```")):
        if index % 2 == 1:  # inside a fence: drop the info string, keep the body
            body = part.split("\n", 1)[1] if "\n" in part else ""
            for line in body.splitlines():
                yield line.split("#", 1)[0]  # a trailing shell comment is prose
        else:
            yield from _INLINE_CODE.findall(part)


def _invocations() -> list[tuple[Path, str, str]]:
    """Every `rudra <word> [<word>]` the docs show, as (file, first, second).

    The second token matters only for the staleness check: `config set` and
    `skills update` are documented absences that never appear as a first
    token, so a first-token-only view would report their own exemptions as
    stale.
    """
    found = []
    for doc in DOCS:
        if not doc.exists():
            continue
        for snippet in _snippets(doc.read_text(encoding="utf-8")):
            for match in _INVOKE.finditer(snippet):
                found.append((doc, match.group(1), match.group(2) or ""))
    return found


def test_every_documented_subcommand_exists() -> None:
    registered = set(_command_tree().commands)
    unknown = {
        f"{doc.relative_to(REPO)}: rudra {name}"
        for doc, name, _ in _invocations()
        if name not in registered and name not in DOCUMENTED_AS_ABSENT
    }
    assert not unknown, (
        "Documentation names these as commands, but the CLI does not register "
        f"them: {sorted(unknown)}. Either the command was renamed and the docs "
        "were not, or the docs describe something never built (A4.1). If the "
        "mention deliberately documents an absence, add the name to "
        "DOCUMENTED_AS_ABSENT with a file:line comment saying where."
    )


def test_every_documented_group_subcommand_exists() -> None:
    groups = _groups()
    pair = re.compile(r"(?<![\w./-])rudra\s+(" + "|".join(sorted(groups)) + r")\s+([a-z][a-z-]*)")
    unknown = set()
    for doc in DOCS:
        if not doc.exists():
            continue
        for snippet in _snippets(doc.read_text(encoding="utf-8")):
            for group, sub in pair.findall(snippet):
                if sub in DOCUMENTED_AS_ABSENT:
                    continue
                if sub not in groups[group].commands:
                    unknown.add(f"{doc.relative_to(REPO)}: rudra {group} {sub}")
    registered = {name: sorted(cmd.commands) for name, cmd in sorted(groups.items())}
    assert not unknown, (
        f"Documentation names these subcommands, which do not exist: "
        f"{sorted(unknown)}. Registered: {registered}"
    )


def test_every_relative_link_resolves() -> None:
    broken = set()
    for doc in [*DOCS, REPO / "Documentation" / "README.md", REPO / "CLAUDE.md"]:
        if not doc.exists():
            continue
        for target in _LINK.findall(doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            if not path:
                continue
            if not (doc.parent / path).exists():
                broken.add(f"{doc.relative_to(REPO)} -> {target}")
    assert not broken, (
        f"These relative links do not resolve on disk: {sorted(broken)}. A renamed "
        "or archived file leaves every index pointing at it broken."
    )


def test_documented_as_absent_is_not_stale() -> None:
    """An exemption for a name nobody mentions any more is a future blind spot.

    If a document stops mentioning `watch`, the entry stays behind and would
    silently exempt a real `rudra watch` claim added later.
    """
    groups = _groups()
    mentioned = set()
    for _, first, second in _invocations():
        mentioned.add(first)
        if first in groups and second:
            mentioned.add(second)
    stale = DOCUMENTED_AS_ABSENT - mentioned
    assert not stale, (
        f"DOCUMENTED_AS_ABSENT exempts names no document mentions: {sorted(stale)}. "
        "Remove them -- a stale exemption masks the next real defect."
    )
