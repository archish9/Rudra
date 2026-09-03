"""Whether a task list decomposes below the file level (OPEN-90).

Run `fc543fb2b82f` planned one HTML page as eight tasks, seven of which
named a region of the same file. The first coder wrote the whole file --
correctly, being told "write every file this task needs" -- and the next
six were dispatched at a finished file and touched nothing: 4,599.8 s, 63%
of the run's counted time, `files_touched: []` on every one.

`_BREAKDOWN_BODY` already forbade two of those eight in prose and the model
emitted them anyway, which is OPEN-17's lesson in a new place: a prompt
cannot outrank a prompt. So the rule lives here, in Python, beside the
duplicate check that is its precedent.

Pure functions over strings. Imports nothing from Rudra -- the rule
loop/ledger.py, bounds.py and regressions.py follow -- so its tests need no
model, no backend and no fact store.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

# Three, not two. A one-file deliverable plus its tests is an ordinary
# plan; three separate non-test tasks against one file is the defect shape.
# Deliberately conservative: this item is worth 4,599 s, and a guard that
# occasionally misses is much cheaper than one that mangles a legitimate
# plan (plan §7).
MIN_TASKS_FOR_ONE_FILE = 3

# How much of a description a refusal quotes back.
_QUOTE = 72

# "single html file", "one self contained file", "standalone file". The
# {0,3} lets an adjective or two sit between, which is how every real
# spelling of this fact has been written: `single_html_file_with_...` and
# "single HTML file /index.html with inline CSS" are the two on record.
_ONE_FILE = re.compile(r"\b(?:single|one|standalone|self contained)\s+(?:\w+\s+){0,3}?file\b")

# The other way the same claim gets written.
_NO_OTHER_FILES = re.compile(r"\bno\s+(?:separate|extra|additional|other)\s+files?\b")

# A noun immediately before "file" that scopes the claim to a PART of the
# project rather than the deliverable. "a single test file" says nothing
# about how many files the project has.
_SUB_ARTEFACT = frozenset(
    {
        "test",
        "tests",
        "config",
        "configuration",
        "requirements",
        "lock",
        "readme",
        "license",
        "licence",
        "data",
        "log",
        "logs",
        "schema",
        "migration",
        "env",
        "spec",
    }
)

# A filename named anywhere in the facts. Used only to VETO: facts that
# name two distinct non-test files describe a multi-file project whatever
# a phrase elsewhere in them says.
_FILENAME = re.compile(
    r"\b[\w][\w./\\-]*\.(?:py|pyi|js|mjs|cjs|ts|tsx|jsx|html|htm|css|scss|sass|rs|go|java"
    r"|rb|php|cs|c|cc|cpp|h|hpp|swift|kt|sql|toml|yaml|yml|json|ini|cfg|md|txt|sh)\b"
)

# Two patterns, not one, and the difference is measured. A FILE called
# `app.spec.js` is a test file; a TASK saying "Build Specifications Table
# section: full specs table" is not a test task -- and the first draft of
# this module, sharing one pattern, silently dropped that task out of the
# count it was supposed to be in.
_TEST_FILE_WORD = re.compile(r"\btests?\b|\btesting\b|\bspecs?\b|\bconftest\b")
_TEST_TASK_WORD = re.compile(r"\btests?\b|\btesting\b|\btest suite\b")

# Verbs whose whole job is to look at what already exists.
_CHECKING_VERBS = frozenset(
    {"verify", "validate", "check", "confirm", "review", "ensure", "inspect", "audit"}
)

# What the GATE already checks on its own (CLAUDE.md §3, verify/). A
# checking task aimed at one of these is the redundant task
# `_BREAKDOWN_BODY` forbids; a checking task aimed at runtime data --
# "validate user input" -- is real work and must survive, because the
# failure mode of a false positive here is work that never happens.
_GATE_TARGETS = frozenset(
    {
        "structure",
        "markup",
        "html",
        "css",
        "output",
        "build",
        "page",
        "layout",
        "behavior",
        "behaviour",
        "breakpoints",
        "breakpoint",
        "rendering",
        "styling",
        "responsive",
        "responsiveness",
        "implementation",
        "syntax",
        "lint",
        "linting",
        "types",
        "typing",
        "compiles",
        "passes",
    }
)

# Deliberately without `add` and `generate`: "Add a directory structure
# diagram to the README" and "Generate a folder structure diagram" are real
# work that names the same words. A false positive here is work that never
# happens, so the ambiguous verbs are left out rather than argued about.
_SCAFFOLD_VERBS = frozenset(
    {"create", "set", "setup", "scaffold", "initialise", "initialize", "make"}
)

# How far into the description the scaffold phrase may sit. "Create the
# project structure" puts it at word 2; a task that mentions a folder
# structure halfway through a sentence is talking about something else.
_SCAFFOLD_WINDOW = 6
_SCAFFOLD_TARGETS = (
    "project structure",
    "project skeleton",
    "project scaffold",
    "project layout",
    "directory structure",
    "folder structure",
    "project directories",
)


def _phrase_text(value: str) -> str:
    """Lowercase, underscores and hyphens as spaces, whitespace collapsed.

    `single_html_file_with_embedded_css_js` and "single HTML file" are the
    same claim written by two runs of the same stage.
    """
    return " ".join(re.sub(r"[_\-]+", " ", value).lower().split())


def _named_source_files(values: Iterable[str]) -> set[str]:
    """Distinct non-test filenames named anywhere in the facts.

    Test files are excluded: a one-file deliverable is still one file when
    the facts also name the test that covers it.
    """
    found: set[str] = set()
    for value in values:
        for match in _FILENAME.finditer(value.lower()):
            name = match.group(0)
            if _TEST_FILE_WORD.search(_phrase_text(name)):
                continue
            found.add(name)
    return found


def _claims_one_file(value: str) -> bool:
    """Does this fact's value say the project IS one file?"""
    text = _phrase_text(value)
    if _NO_OTHER_FILES.search(text):
        return True
    for match in _ONE_FILE.finditer(text):
        # "one file per module" is a convention for MANY files that matches
        # the phrase by accident.
        if re.match(r"\s+per\b", text[match.end() :]):
            continue
        words = match.group(0).split()
        if len(words) >= 2 and words[-2] in _SUB_ARTEFACT:
            continue
        return True
    return False


def single_file_evidence(facts: Sequence[tuple[str, str]]) -> tuple[str, str] | None:
    """The first fact saying this project's deliverable is one file, or None.

    Takes (key, value) pairs and reads only the VALUES. Keying on the name
    `layout` would be a guard that silently stops working the moment
    `clarify` words the fact differently -- the fact store enumerates its
    keys nowhere in code on purpose (CLAUDE.md §3, Step 10a).

    Returns the pair so a refusal can quote the evidence it acted on.
    """
    pairs = list(facts)
    if len(_named_source_files(value for _, value in pairs)) >= 2:
        # The facts themselves name a multi-file project. Whatever phrase
        # sits elsewhere in them, this is not the case the guard is for.
        return None
    for key, value in pairs:
        if _claims_one_file(value):
            return key, value
    return None


def is_test_task(description: str) -> bool:
    """Is this task about writing or running tests?

    Test tasks do not count towards the threshold: "build the page" plus
    "write tests for the page" is a correct two-task plan for one file, and
    the prompt explicitly endorses it.
    """
    return bool(_TEST_TASK_WORD.search(description.lower()))


def _quoted(text: str) -> str:
    return text if len(text) <= _QUOTE else f"{text[: _QUOTE - 3]}..."


def over_decomposition_refusal(
    descriptions: Sequence[str],
    *,
    evidence: tuple[str, str] | None,
    existing: Sequence[str] = (),
) -> str | None:
    """Refuse a list that cuts one file into several tasks, or None.

    All-or-nothing, unlike the duplicate refusal: the defect is the SHAPE
    of the list, so there is no good half to keep.

    Args:
        descriptions: The tasks this call wants to add.
        evidence: `single_file_evidence`'s answer. None means the facts do
            not say this is a one-file project, and then nothing is
            refused -- the guard never fires on a plan it has no evidence
            about.
        existing: Descriptions already on the ledger, so a planner cannot
            slip under the threshold by splitting the same list across two
            calls.
    """
    if evidence is None:
        return None
    offenders = [text for text in descriptions if not is_test_task(text)]
    carried = [text for text in existing if not is_test_task(text)]
    if len(offenders) + len(carried) < MIN_TASKS_FOR_ONE_FILE:
        return None

    key, value = evidence
    listed = "\n".join(f'  - "{_quoted(text)}"' for text in offenders)
    return (
        f"REJECTED: {len(offenders) + len(carried)} of these tasks each build a part of "
        f"one file. The settled facts say this project is a single file "
        f'("{key}": {_quoted(value)}), and a coder is given ONE task and writes every '
        f"file that task needs -- so the first of these writes the whole file and the "
        f"rest are dispatched at a finished file and change nothing.\n"
        f"{listed}\n"
        f"Combine them into ONE task describing the whole deliverable, plus a separate "
        f"task for its tests if you want them, and call add_tasks again. This will not "
        f"be refused a second time."
    )


def _first_word(description: str) -> str:
    match = re.match(r"[^a-zA-Z]*([a-zA-Z]+)", description)
    return match.group(1).lower() if match else ""


def forbidden_shape_refusal(description: str) -> str | None:
    """Refuse a task `_BREAKDOWN_BODY` already names as BAD, or None.

    Two shapes, both quoted in that prompt and both emitted anyway by run
    `fc543fb2b82f`:

    * a task whose whole job is to check what the gate already checks
      ("Validate HTML structure and verify responsive behavior");
    * "create the project structure".

    Narrow on purpose. "Validate user input in the signup form" changes a
    file and is kept -- the failure mode of a false positive here is work
    that never happens, which is strictly worse than the turn a miss costs
    (loop/tools.py::_normalised makes the same argument for duplicates).
    """
    text = _phrase_text(description)
    words = set(text.split())

    if _first_word(description) in _CHECKING_VERBS and words & _GATE_TARGETS:
        return (
            f'REJECTED: "{_quoted(description.strip())}" only checks what the gate '
            f"already checks on its own after every task -- syntax, lint, types, tests "
            f"and stubs -- so a coder given it has no file to write and will spend the "
            f"task searching. If something is genuinely missing, add a task saying what "
            f"to WRITE instead."
        )

    opening = " ".join(text.split()[:_SCAFFOLD_WINDOW])
    if _first_word(description) in _SCAFFOLD_VERBS and any(
        target in opening for target in _SCAFFOLD_TARGETS
    ):
        return (
            f'REJECTED: "{_quoted(description.strip())}" is scaffolding, not work. '
            f"Directories are created implicitly by the files written into them, so this "
            f"task has nothing to produce. Fold it into the task that writes the first "
            f"real file."
        )

    return None


__all__ = [
    "MIN_TASKS_FOR_ONE_FILE",
    "forbidden_shape_refusal",
    "is_test_task",
    "over_decomposition_refusal",
    "single_file_evidence",
]
