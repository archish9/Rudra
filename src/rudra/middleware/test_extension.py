"""TestExtensionMiddleware — refuse a test file the runner cannot collect.

**The defect (OPEN-99).** Run `5775ba1f9855`, the verification run: the tester
wrote 7,619 bytes of Python — module docstring, `import html.parser`, a class
and eight test functions — to `tests/test_iphone15_responsive.html`, having
derived the test's name from the file under test and carried the extension
across. It then said, in its own output:

    "The tests pass when run directly with Python. The pytest collection
     doesn't pick up `.html` test files (it only collects `.py` files), but
     the test script runs successfully and all 8 tests pass."

— and shipped it. **It stated the exact defect and treated it as a caveat
rather than a blocker**, which is why Option B's prompt line is shipped
beside this and never instead of it (`TODO.md` lesson 1: a model that can
state a rule and violate it in the same invocation is not fixed by being told
the rule).

**Why nothing caught it, every component behaving as specified.** Rudra holds
two independent notions of a test and they never meet: the tester's, which is
whatever it writes into `tests/` and is constrained by prose that assumes a
neighbouring test exists to imitate — false in exactly the greenfield case
this fires in; and the gate's, `stacks/detect.py:120` → `resolve_test_command`,
which needs a `.py` file to EXIST before it will claim a Python stack at all.
So `detect()` returned `[]`, `verify/pipeline.py` reported *"this project
declares no test command"*, four of five stages were `not_applicable`, the
verdict was `passed`, and `loop/engine.py` marked the task `DONE` in one
attempt with zero runnable tests. A file satisfying the first notion and not
the second is dead on arrival and nothing in between noticed. **That gap is
what this closes.**

**Refused rather than repaired, and refused rather than annotated.** This
inverts `ContentPathMiddleware`'s preference one middleware over, for
`fix_write_params.py`'s stated reason: a note arrives after the bytes are on
disk, and here the whole value is that the file does not land under a name
nothing will ever run. Renaming the model's path silently is also out —
OPEN-81 is the record of an error naming a rewrite instead of the model's own
argument. Refuse, name the correction, let the model re-issue.

**Read `_UNCOLLECTABLE` before widening it.** It is CLOSED on purpose and
`.json`, `.yaml` and `.yml` are measured members of the must-not set, not
oversights — see the table on that constant.
"""

from __future__ import annotations

import ast
import logging
from pathlib import PurePosixPath
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

TEST_EXTENSION_NOTICE = "test-extension"
"""The `name` on the NOTICE, and what a maintainer greps `debug-<id>.jsonl`
for. One spelling, as a module constant, because a second is a second answer
to "why is my filter empty" (`CLAUDE.md` §8a)."""

# Directory names that mean "the suite lives here". Matched on any segment of
# the path, because `tests/unit/test_x.html` is as dead as `tests/test_x.html`.
_TEST_DIRS = frozenset({"tests", "test", "spec", "specs"})

# Suffixes a Python collector will never pick up, that a model plausibly
# carries across from the file under test, AND whose ordinary content cannot
# be mistaken for Python. CLOSED on purpose: an unknown suffix DECLINES,
# because the cost of a false refusal is the tester's real write.
#
# `.json`, `.yaml` and `.yml` are deliberately ABSENT and must stay absent --
# MEASURED against the installed interpreter, not assumed:
#
#     ast.parse('{"a": 1}')      -> a dict literal.       Valid Python.
#     ast.parse('[1, 2, 3]')     -> a list literal.       Valid Python.
#     ast.parse('name: rudra')   -> an AnnAssign.         Valid Python.
#
# So an ordinary `tests/data.json` fixture would be REFUSED, which is a
# refused real write. `.txt` and `.md` are out for the same class of reason,
# `.js` because a `.js` test in a JS project is correct as written.
# `tests/test_test_extension.py` pins both halves of that set.
_UNCOLLECTABLE = frozenset({".html", ".htm", ".xml", ".css"})

_REJECTED = (
    "REJECTED: `{path}` contains Python source but is named `{suffix}`, and "
    "the test runner collects only `.py` files -- this file would never run, "
    "and a project with no `.py` file in it has no test command at all. "
    "Nothing was written.\n\n"
    "Write it as `{suggested}` instead. The name of the file under test does "
    "not decide the name of the test: a test for `page.html` is "
    "`test_page.py`.\n\n"
    "If you did mean to write a fixture rather than a test, put it outside "
    "the test directory or give it a name that is not `test_*`."
)
"""Leads with `REJECTED:`, matching `fix_write_params.py`'s refusals, and sets
`status="error"` -- which, not the lead, decides how it is counted.
`trace/stream.py::message_is_error` reads `status` before any text, so
`subagents/runner.py` halts the tester on three of these in a row. Counted on
purpose (OPEN-118, the owner's OPEN-103 decision): an uncounted refusal a
model ignores is bounded only by `MAX_TOTAL_CALLS`. Corrected 2026-09-15 --
this said the lead kept the counter from firing, and it never did."""


def _pure(file_path: str) -> PurePosixPath:
    """One reading of a path, whatever the host separator.

    Backslashes are folded to `/` for `compat/virtual_paths.py`'s reason: the
    SHAPE of the path decides, never `sys.platform` (`CLAUDE.md` §1.8), so
    `tests\\test_x.html` and `tests/test_x.html` are one file everywhere.
    """
    return PurePosixPath(file_path.replace("\\", "/"))


def has_test_name(file_path: str) -> bool:
    """Is this basename in pytest's own discovery shape, `test_x` or `x_test`?

    THE definition, not a definition. `is_test_path` is this reading OR'd
    with the directory one, and `verify/pipeline.py` asks it alone: a
    `tests/fixture.html` is a legitimate fixture and naming it in the gate's
    message would be noise, while a `tests/test_page.html` is the shape worth
    reporting. One spelling with two consumers, because `is_build_output` is
    this project's record of what happens when a rule gets a second copy
    (`CLAUDE.md` §3).

    **Not the same question as `stacks/detect.py::_is_test_filename`, and
    they must not be collapsed.** That one asks whether a file is a test the
    gate should COUNT, so it is gated on `_TEST_FILE_SUFFIXES` and answers
    False for every suffix this function exists to catch. It also accepts
    `api.test.ts` and `api.spec.tsx`, which pytest does not collect under any
    name. This is `source_files` versus `project_files` again (OPEN-63): two
    neighbouring predicates, two different questions, and conflating them is
    what that item cost.
    """
    if not file_path:
        return False
    stem = _pure(file_path).stem.lower()
    return stem.startswith("test_") or stem.endswith("_test")


def is_test_path(file_path: str) -> bool:
    """Is this path inside the suite, or named like a test?

    Two independent readings, either sufficient: a segment that names a test
    directory, or a basename in pytest's own discovery shape. A project that
    keeps tests beside their source (`app/user_test.py`) is covered by the
    second. A segment merely CONTAINING "test" is not one -- `docs/testing.md`
    declines.
    """
    if not file_path:
        return False
    if any(part.lower() in _TEST_DIRS for part in _pure(file_path).parts):
        return True
    return has_test_name(file_path)


def is_uncollectable_test(file_path: str, content: object) -> bool:
    """Is this Python source under a name the runner will never collect?

    THREE conditions, all required, and the last is the load-bearing one:
    `ast.parse` succeeding is not a guess about intent, it is the same
    question `verify/pipeline.py`'s syntax stage asks. An HTML document is not
    valid Python, so a real `tests/fixture.html` cannot reach the refusal.

    Declines on anything uncertain: content that is not a non-empty string, a
    path that is not a test, a suffix outside the closed set (`.py` included),
    and content that will not parse.
    """
    if not isinstance(content, str) or not content.strip():
        return False
    if not isinstance(file_path, str) or not is_test_path(file_path):
        return False
    if _pure(file_path).suffix.lower() not in _UNCOLLECTABLE:
        return False
    try:
        ast.parse(content)
    except (SyntaxError, ValueError):
        return False
    return True


def suggested_name(file_path: str) -> str:
    """The same file, named so the runner can collect it."""
    return str(_pure(file_path).with_suffix(".py"))


class TestExtensionMiddleware(AgentMiddleware):
    """Refuse a `write_file` putting Python under a name nothing collects.

    The TESTER's stack only (`subagents/build.py::_middleware_for`, gated on
    `"run_tests" in spec.rudra_tools`). The coder writes `.html` deliverables
    on purpose -- that is what the user asked for -- and must never see this.
    """

    # Not a test class. The name begins with `Test`, so pytest tries to
    # collect it wherever it is imported and warns that it cannot; this is
    # the documented opt-out and it costs nothing at runtime.
    __test__ = False

    def __init__(
        self,
        role: str | None = None,
        *,
        usage: Any = None,
        trace: Any = None,
    ) -> None:
        super().__init__()
        self.role = role
        self.usage = usage
        self.trace = trace

    def _announce(self, path: str, suggested: str) -> None:
        """Count and say that a write was refused (`TODO.md` lesson 5).

        Both halves swallow their own failure: a run that did its work must
        not be reported failed because a log line could not be written
        (`CLAUDE.md` §8a). The notice names the PATH because a false positive
        here is a refused real write, and the count alone cannot be checked.
        """
        role = self.role or "agent"
        try:
            if self.usage is not None:
                self.usage.record_test_write_rejected(role)
        except Exception:  # noqa: BLE001 - bookkeeping may never end a run
            logger.debug("test-extension refusal not counted", exc_info=True)
        try:
            if self.trace is not None:
                self.trace.notice(
                    f"refused a write to {path}: it is Python source under a "
                    f"name the test runner will never collect, and nothing "
                    f"reached disk -- {suggested} is the collectable name",
                    role=role,
                    name=TEST_EXTENSION_NOTICE,
                )
        except Exception:  # noqa: BLE001 - same rule
            logger.debug("test-extension refusal not announced", exc_info=True)

    def _refusal(self, request):
        """A ToolMessage refusing this write, or None to let it through.

        `write_file` only. `edit_file` cannot create a file, so the extension
        was decided by an earlier `write_file` this already saw; judging it
        again would refuse a repair of a file already on disk.
        """
        call = request.tool_call
        if call.get("name") != "write_file":
            return None
        args = call.get("args") or {}
        path = args.get("file_path")
        if not isinstance(path, str) or not is_uncollectable_test(path, args.get("content")):
            return None

        suggested = suggested_name(path)
        self._announce(path, suggested)
        return ToolMessage(
            content=_REJECTED.format(
                path=path,
                suffix=_pure(path).suffix.lower(),
                suggested=suggested,
            ),
            tool_call_id=call.get("id", ""),
            name="write_file",
            status="error",
        )

    def wrap_tool_call(self, request, handler):
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request, handler):
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)


__all__ = [
    "TEST_EXTENSION_NOTICE",
    "TestExtensionMiddleware",
    "has_test_name",
    "is_test_path",
    "is_uncollectable_test",
    "suggested_name",
]
