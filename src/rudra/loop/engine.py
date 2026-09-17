"""The loop: dispatch work, verify it, fix it, and decide when to stop.

The split this module exists to enforce (spec S9c.1): the model decides
what work exists and what to do next; Python decides when a task is done
and when to stop. C6.1 asked for an agent that owns the todo list; D9
forbids an LLM deciding termination. Both hold here because the ledger
tools cannot write DONE and this module is the only thing that can.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from rudra.context.middleware import log_model_call
from rudra.context.usage import SUSPENDED_NOTICE_SECONDS, render_usage
from rudra.git.core import is_repo, status
from rudra.loop.bounds import failure_signature, tests_produced_no_judgement
from rudra.loop.ledger import Ledger, Task, TaskStatus
from rudra.loop.regressions import INHERITED, PASSED, failure_keys, verdict_for
from rudra.memory.degrade import last_failure
from rudra.memory.entry import MemoryEntry
from rudra.middleware.content_paths import (
    find_project_absolute_mentions,
    project_top_level,
)
from rudra.subagents import SubagentContext, run_subagent
from rudra.testing.project_env import ProjectEnvState, ensure_project_env
from rudra.verify import verify_project
from rudra.verify.stubs import is_build_output, project_files

_LOG = logging.getLogger("rudra.loop.engine")


class Outcome(StrEnum):
    """What running one task told the caller to do next."""

    DONE = "done"
    BLOCKED = "blocked"
    STOP_RUN = "stop_run"
    # The user pressed Ctrl-C (C9.3). Distinct from STOP_RUN because
    # nothing is wrong: the ledger is intact, the in-flight task is back
    # at PENDING, and `rudra --continue` picks up exactly here.
    CANCELLED = "cancelled"


@dataclass
class LoopContext:
    """Everything one run needs, assembled once.

    Bundled for the reason Gate and SubagentContext are: these must be the
    *same* objects across calls, the gate and its session grants
    especially.
    """

    subagents: SubagentContext
    project_path: Path
    console: Console
    cfg: Any
    paths: Any
    # The run's token tally. Optional: every 9c-era test builds a
    # LoopContext without one.
    usage: Any = None
    # The run's MemoryStore, or None when it could not be built. Shared by
    # reference with SubagentContext -- one run, one palace handle. Optional
    # for the reason `usage` is.
    memory: Any = None
    # What the gate was failing on when it last ran, as loop/regressions.py
    # keys (OPEN-23). Mutated by run_task, read by the next task, and
    # deliberately NOT persisted: a fresh process starts empty, every failure
    # then reads as new, and the loop behaves exactly as it did before this
    # field existed. The degraded mode is the old blocking one.
    failure_baseline: frozenset[str] = frozenset()
    # How many subagent runs in a row never produced a turn (OPEN-33).
    # Mutated by run_task, reset by any run that does produce one. Not
    # persisted, for the reason `failure_baseline` is not: a fresh process
    # starts at zero and gets its full budget, which is the safe direction.
    run_errors: int = 0
    # What this run has learned about the project's .venv (OPEN-120):
    # installs that failed, and whether building it was denied. Created on
    # first use by `_sync_project_env`, and not persisted, for
    # `failure_baseline`'s reason.
    project_env: Any = None


def _digest(path: Path) -> str:
    """A cheap content fingerprint, or "" when the file cannot be read.

    Unreadable and absent both yield "", which is what a deletion should
    look like to the comparison below.
    """
    try:
        with path.open("rb") as handle:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: handle.read(65536), b""):
                hasher.update(chunk)
    except OSError:
        return ""
    return hasher.hexdigest()


def git_snapshot(context: LoopContext) -> dict[str, str] | None:
    """What git reports as changed, each path with a content fingerprint.

    None is a real answer the caller acts on: without git there is no way
    to tell what an attempt touched, so the gate falls back to scanning
    everything -- the same choice `rudra verify` makes.

    Two things here are A1.66, and they compound. **`all_untracked=True`**:
    plain porcelain collapses an untracked directory into one entry, so
    once a task has created `src/`, a later task writing `src/main.rs`
    adds no new entry. **The fingerprint**: a file that is untracked
    before and after an attempt appears identically in both listings even
    when its contents were rewritten -- which is exactly what a fix-loop
    retry does to code written in a greenfield repo. Under either one the
    diff comes back empty, the empty-diff guard reads that as "the coder
    wrote nothing", and real work is blocked. Measured on a greenfield
    Rust run: every task after the first blocked at 3 attempts, with its
    files on disk the whole time.

    Hashing is bounded by what git already reports and by the build-output
    pruning below, so this reads the changed files, not the project.

    That pruning is `verify.stubs.is_build_output`, which `project_files`
    calls too. It used to be a private copy here that matched `out`,
    `build`, `dist`, `target` and `coverage` at EVERY depth while both
    other copies applied A1.29's root-anchored split, so in a project that
    HAS a `.git` -- which no test run ever has -- `src/out/handler.py`
    never reached `files_touched` (OPEN-64).
    """
    gate = context.subagents.gate
    if not is_repo(context.project_path, gate=gate, console=context.console, cfg=context.cfg):
        return None
    entries = status(
        context.project_path,
        gate=gate,
        console=context.console,
        cfg=context.cfg,
        all_untracked=True,
    )
    if entries is None:
        # git failed. None means "no snapshot", which run_task already
        # handles -- returning {} would make the empty-diff guard read every
        # attempt as "the coder wrote nothing" (CR-E12).
        return None
    return {
        entry.path: _digest(context.project_path / entry.path)
        for entry in entries
        if entry.path and not is_build_output(entry.path)
    }


def tree_snapshot(context: LoopContext) -> dict[str, str]:
    """Every file in the project, each with a fingerprint.

    What `git_snapshot` is for a project that has no git, and it must mean
    the same thing (OPEN-63). This used to walk `source_files`, which keeps
    nine source suffixes, while `git_snapshot` prunes build output and
    nothing else -- so whether a file the coder wrote was recorded at all
    depended on whether the project had a `.git`, and no project Rudra has
    been run against has ever had one. run12's t1 wrote `requirements.txt`;
    its `files_touched` names `src/app.py` and `tests/test_app.py`, and t2
    -- whose brief WAS that file -- reported "the coder wrote nothing".

    The docstring here used to claim the two bounds were already the same
    ("plus `_is_build_output`"), which is how it survived: the tree path
    carried a second filter git has no equivalent of. `project_files` is
    now that same walk without it.

    A marker file therefore counts as a touch on both paths now. That does
    not weaken the empty-diff guard, because the branch it skips is not
    where the protection lives: `changed_files` scopes the syntax stage and
    the stub scan (verify/pipeline.py), so lint, typecheck and test are
    whole-project either way, and a gate that judged nothing routes to the
    tester rather than to DONE. Corrected 2026-09-17 (OPEN-123): this said
    the stub scan ONLY, and the syntax stage reads the same list.

    It is a whole-tree walk where the git path is a `git status` call, and
    that is the price of the answer: without it there is no answer at all,
    which is OPEN-13.
    """
    root = Path(context.project_path)
    return {path: _digest(root / path) for path in project_files(root)}


def attempt_snapshot(context: LoopContext) -> dict[str, str]:
    """The before/after fingerprint for one attempt. Never None.

    OPEN-13: `git_snapshot` returns None outside a repo, and the empty-diff
    guard in `run_task` was written as `before is not None and not
    files_touched` -- so in a project with no `.git` the guard never ran,
    an attempt that wrote nothing reached a gate with zero changed files,
    the gate passed it vacuously ("0 changed file(s) scanned"), and the
    task was marked DONE. Measured on the owner's 2026-08-24 run: two tasks
    `done`, `files_touched: []`, no file created.

    None was never a third state the caller wanted -- it was "ask the
    filesystem instead", which is what this does, once, in one place, so
    the before and the after cannot come from different sources.
    """
    snapshot = git_snapshot(context)
    return tree_snapshot(context) if snapshot is None else snapshot


def changed_since(context: LoopContext, before: dict[str, str] | None) -> tuple[str, ...]:
    """What this attempt touched.

    Read from git -- or from the tree, outside a repo -- rather than from
    the model. Asking the coder what it wrote invites a wrong answer at
    exactly the moment the answer matters, because it feeds 9a's stub scan.

    A path counts when it is new, gone, or its fingerprint moved. The last
    case is what makes a retry that rewrites an untracked file visible
    (A1.66).

    `before is None` still means "no snapshot was taken", and still answers
    with the whole project. Nothing in the loop passes it any more --
    `run_task` uses `attempt_snapshot`, which always has one -- but the
    contract is kept for callers outside it. It walks `project_files` for
    the reason `tree_snapshot` does (OPEN-63): "the whole project" and "the
    project's source files" were the same answer here, and they are not the
    same question.
    """
    if before is None:
        return project_files(context.project_path)
    after = attempt_snapshot(context)
    touched = {path for path, digest in after.items() if before.get(path) != digest}
    touched |= {path for path in before if path not in after}
    return tuple(sorted(touched))


def _record_touched(task: Task, touched: tuple[str, ...]) -> tuple[str, ...]:
    """Add one attempt's diff to what the task has touched; return the diff.

    `task.files_touched` is EVERY attempt's files, never the last one's
    (OPEN-123). Both call sites used to assign, so each attempt replaced the
    record: run `a04f89bd2ed6`'s t5 wrote two files on attempt 1, nothing on
    attempts 2 and 3, and went BLOCKED with `files_touched: []` -- the line
    CLAUDE.md 8a calls the highest-signal one in the folder, saying the
    opposite of what happened. That is 8a failure shape 4, one field over.

    The overwrite was also a gate defect, not only a record one: `_verify`
    hands this field to the gate, whose syntax stage and stub scan read
    nothing else, so a file attempt 1 broke and attempt 2 did not touch was
    judged by no stage that could see it. Reproduced: DONE, `def f(:` on disk.

    The diff is returned because the empty-diff guard must keep reading it.
    "Did THIS attempt write anything" is that guard's question, and the union
    answers "has any attempt", which after attempt 1 is always yes.
    """
    task.files_touched = tuple(sorted({*task.files_touched, *touched}))
    return touched


def _coder_prompt(task: Task, blocker_text: str = "") -> str:
    prompt = (
        f"Task {task.id}: {task.description}\n\n"
        "Write every file this task needs. Stop when the task is complete."
    )
    if blocker_text:
        prompt += (
            "\n\nYour previous attempt did not pass verification. "
            "Fix exactly this, then stop:\n\n" + blocker_text
        )
    return prompt


def _tester_prompt(task: Task) -> str:
    files = ", ".join(task.files_touched) or "the code this project contains"
    return (
        f"The project has no tests covering recent work on: {files}\n\n"
        f"That work was: {task.description}\n\n"
        "Write tests for it, run them, and report what happened."
    )


# OPEN-93 Option D. Appended to the gate's own words when the test suite
# failed on a path that is the VIRTUAL spelling of a file this project really
# has. `_blocker_text` is otherwise verbatim on purpose -- a paraphrase is a
# worse input -- so this is an addition and never a replacement.
_TEST_PATH_NOTE = (
    '\n\nNote: this failure quotes "{spelling}", and this project really does '
    'hold that file at "{relative}". A leading "/" is correct as an argument '
    "to Rudra's file tools, which are rooted at the project -- but the test was "
    'run by a real interpreter, to which "{spelling}" is the MACHINE\'s root. '
    "{where}The code under test may be entirely correct; the path written into "
    "the test is not."
)


def _test_path_note(report: Any, project_path: Any) -> str:
    """The sentence that says a failing TEST, not the code, holds a bad path.

    Run `2cde3406f7d6` spent 1,348 s and three attempts here. The coder read
    the blocker, saw `tests/test_iphone15.py:14: AssertionError`, and rewrote
    480 lines of already-correct HTML -- twice -- because nothing in the
    blocker said the test's own string literal was the defect, and its task
    scoped it to the HTML file anyway (OPEN-93 4.3 is the ownership half,
    which this does not fix).

    Narrow deliberately, and every clause is a decline:

    * the TEST stage only -- a lint or typecheck failure quoting a path is a
      different problem;
    * a mention whose first segment names a real top-level entry of this
      project, so `/usr/bin/env` and `/api/v1/users` are left alone
      (`find_project_absolute_mentions` -- the prose scanner, not the source
      one: the run's own pytest tail quotes the path on one line and not on
      the next, and only one of those two is a string literal);
    * and the file must ACTUALLY be there under the relative spelling. That
      last one is what makes the claim true rather than plausible: without
      it this would tell a coder its test was wrong about a file that really
      was missing.
    """
    blocker = report.blocker
    if project_path is None or blocker is None or blocker.name != "test":
        return ""
    root = Path(project_path)
    top_level = project_top_level(root)
    if not top_level:
        return ""
    haystack = "\n".join(
        [*(finding.message or "" for finding in blocker.findings), blocker.output_tail or ""]
    )
    for spelling in find_project_absolute_mentions(haystack, top_level):
        relative = spelling.replace("\\", "/").lstrip("/")
        try:
            if not (root / relative).exists():
                continue
        except OSError:  # pragma: no cover - unreadable path is a decline
            continue
        located = [finding.file for finding in blocker.findings if finding.file]
        where = f'The failing test is in "{located[0]}". ' if located else ""
        return _TEST_PATH_NOTE.format(spelling=spelling, relative=relative, where=where)
    return ""


def _blocker_text(report: Any, project_path: Any = None) -> str:
    """The gate's complaint, verbatim -- a paraphrase is a worse input.

    `project_path` is optional so every existing caller and test keeps its
    meaning: without it this is exactly the function it has always been, and
    with it the gate can also say when the TEST's path is what is wrong
    (OPEN-93).

    **A failing test stage quotes the runner's output, never its findings
    (OPEN-119).** Those findings are pytest's frame lines -- `path:line: in
    <func>`, `path:line: AttributeError` -- parsed out of this same tail
    (verify/pipeline.py's test stage), and not one of them carries the
    exception, which pytest prints on an `E` line the parser does not match.
    This used to list the findings and append the tail only when there were
    none, so a single frame suppressed the whole tail: run `a04f89bd2ed6`'s
    t5 was sent three frames twice as "fix exactly this", never read
    `NameError: name 'Column' is not defined`, and went BLOCKED having
    written nothing. Quoting the tail loses nothing, since every finding is
    a line of it, and needs no per-runner parser, since every runner's own
    words are in it. Other stages are unchanged: a typecheck finding line IS
    its message. `findings` themselves are untouched, and both the no-progress
    signature (loop/bounds.py) and the regression keys (loop/regressions.py)
    still read only them.
    """
    blocker = report.blocker
    if blocker is None:  # pragma: no cover - only called on a failure
        return ""
    lines = [f"{blocker.name} failed: {blocker.detail}".rstrip(": ")]
    if blocker.output_tail and (blocker.name == "test" or not blocker.findings):
        lines.append(blocker.output_tail)
    else:
        lines.extend(
            f"  {finding.file}:{finding.line}: {finding.message}" for finding in blocker.findings
        )
    return "\n".join(lines) + _test_path_note(report, project_path)


def _confirms_nothing_to_do(report: Any, verdict: str) -> bool:
    """Does this gate run prove the task did not need doing?

    Both halves are load-bearing.

    Green -- or green but for failures that predate the task (OPEN-23) --
    says the project is in the state the task wanted.
    `tests_produced_no_judgement` says whether "green" means anything at all:
    a project with no files parses no syntax and collects no tests, and that
    is exactly the shape OPEN-12/13 wrongly passed, marking two tasks DONE
    having created no file. A vacuous pass is not evidence, so it blocks.
    """
    return (verdict is PASSED or verdict is INHERITED) and not tests_produced_no_judgement(report)


def _already_satisfied_note(report: Any, *, retry: bool = False) -> str:
    """Why a task that wrote nothing is DONE (OPEN-27).

    `retry` is whether a gate already declined this task in this run. The
    OPEN-27 sentence -- "nothing needed writing: this task's work was already
    in place" -- is true of a FIRST attempt, where an earlier task built the
    work, and false on a retry, where this task's own previous gate did not
    accept the same files (OPEN-123). It says what was observed and names no
    cause: `_sync_project_env` runs before every gate and a test can be
    flaky, and a note that guessed would send the reader after one of them.
    """
    tested = next((stage for stage in report.stages if stage.name == "test"), None)
    detail = f" ({tested.detail})" if tested is not None and tested.detail else ""
    if retry:
        return (
            "the coder wrote nothing on a retry, and the gate now passes over the "
            f"whole project with its tests run{detail}. It did not accept an earlier "
            "attempt of this task, and the coder has written nothing since: the "
            "same files drew two verdicts."
        )
    return (
        "the coder wrote nothing, and nothing needed writing: this task's "
        "work was already in place, and the gate passes over the whole "
        f"project with its tests run{detail}."
    )


def _wrote_nothing_note(blocker_text: str, report: Any = None, project_path: Any = None) -> str:
    """Why an attempt changed no file, and WHICH of the cases it is.

    **This exact string has been the visible symptom of three unrelated
    defects in three consecutive runs**: a stale test the coder was not
    allowed to fix (OPEN-23), a delegation to an agent that could not do the
    work (OPEN-26), and work an earlier task had already completed
    (OPEN-27). Every time it sent the reader after a lazy coder, and every
    time the coder was behaving correctly. So it now says which.

    The substring "wrote nothing" is load-bearing -- the BLOCKED path below
    greps for it to avoid overwriting this note with an attempt count.
    """
    if report is not None and tests_produced_no_judgement(report):
        return (
            "the coder wrote nothing, and the project cannot confirm whether "
            "it needed to: no tests were collected, so a green gate proves "
            "nothing here. Write the work, or add a test that covers it."
        )
    if blocker_text:
        return f"the coder wrote nothing on a retry. It had been asked to fix:\n\n{blocker_text}"
    if report is not None and report.blocker is not None:
        gate = _blocker_text(report, project_path)
        return f"the coder wrote nothing, and the gate is failing:\n\n{gate}"
    return "the coder wrote nothing"


def _inherited_note(report: Any, inherited: frozenset[str]) -> str:
    """Why a task passed while the suite is red (OPEN-23).

    Names the failures rather than counting them, for the reason
    `_blocker_text` quotes the gate verbatim: the next reader of this ledger
    wants to know WHICH file is red and whether anything owns it, and a
    number answers neither.
    """
    still = sorted(failure_keys(report) & inherited)
    listed = "\n".join(f"  {key}" for key in still)
    return (
        "passed: this task introduced no failure.\n\n"
        f"{len(still)} failure(s) were already failing before it ran and still are:\n"
        f"{listed}\n\n"
        "They belong to whatever task owns those files, not to this one."
    )


async def _sync_project_env(context: LoopContext) -> None:
    """Build or sync a Python project's .venv, off the event loop (OPEN-120).

    Before every gate run, because the gate is what reads it: once
    `.venv/bin/python` exists `stacks/detect.py` runs the tests under it,
    and the tester -- the one agent holding `execute` -- runs only after a
    gate. Off the loop for `_verify`'s reason: the event loop must not block
    on a subprocess with `test_timeout` seconds to run.

    That does not make Ctrl-C free. `asyncio.to_thread` cancellation cancels
    only the awaiting coroutine -- the worker thread keeps running the
    blocking `subprocess.communicate()` underneath it to completion, and
    `asyncio.run`'s shutdown joins that thread (up to `THREAD_JOIN_TIMEOUT`,
    300s in 3.12). So the await returns promptly, but a Ctrl-C during a long
    `venv`/pip install can appear to hang until that command finishes or its
    `test_timeout` expires -- and since this now also runs at the top of
    `work()`, that exposure is wider than `_verify`'s. The double-Ctrl-C hard
    exit (`cli.py::_cancel_on_sigint`) is the escape hatch for that wait.

    Never ends a run. An internal error degrades to the gate as it ran
    before OPEN-120, and pip still refuses to install outside a venv
    whatever happens here: that is the shell's environment, not this.
    """
    if context.project_env is None:
        context.project_env = ProjectEnvState()
    try:
        await asyncio.to_thread(
            ensure_project_env,
            context.project_path,
            gate=getattr(context.subagents, "gate", None),
            console=context.console,
            cfg=context.cfg,
            state=context.project_env,
            usage=context.usage,
            trace=getattr(context.subagents, "trace", None),
        )
    except Exception:  # noqa: BLE001 - a failed sync must not end the run
        _LOG.debug("syncing the project's .venv raised", exc_info=True)


def _env_sync_note(context: Any) -> str:
    """pip's output from a failed dependency install, for the blocker (OPEN-120).

    Beside the gate's text, never instead of it. A failure the install
    explains shows up as a missing module, and the coder -- which holds no
    shell -- can fix a bad pin in requirements.txt only if it is shown one.
    """
    failure = getattr(getattr(context, "project_env", None), "failure", "")
    if not failure:
        return ""
    return (
        "\n\nBefore this gate ran, installing the project's declared dependencies into "
        ".venv failed, so a missing module above may be that rather than the code. "
        f"pip said:\n{failure}"
    )


async def _verify(task: Task, context: LoopContext) -> Any:
    """Run the gate over what this task touched.

    Off the event loop, because verify_project reaches subprocess.Popen
    (shell/runner.py) with `[tools] test_timeout` -- 600 seconds by
    default. `_cancel_on_sigint` installs its handler with
    `loop.add_signal_handler`, whose callback only runs when the loop
    regains control, so a synchronous call here meant that during a ten
    minute pytest the first Ctrl-C did nothing AND the second press's
    `os._exit` -- the escape hatch whose whole purpose is that a hung
    cancel must not need a kill from another terminal -- was deferred
    just as long (CR-C5).
    """
    await _sync_project_env(context)
    return await asyncio.to_thread(
        verify_project,
        context.project_path,
        changed_files=task.files_touched,
        gate=context.subagents.gate,
        console=context.console,
        cfg=context.cfg,
    )


# Consecutive subagent runs that never happened before the run gives up
# (OPEN-33). A budget rather than a classifier: `subagents/runner.py`
# catches every exception and hands the loop a string, so "is this
# transient?" can only be answered by string-matching a provider's prose --
# which is a guess that breaks on the next provider. Whether the failure
# recurs is not a guess, and it is the thing the old code was asserting
# when it said an error "would recur on every remaining task".
#
# Three, because it must be small enough that a genuinely broken
# environment still stops in seconds. A build failure raises before any
# network call (llm/factory.py), so three of those cost nothing.
MAX_CONSECUTIVE_RUN_ERRORS = 3


def _record_run_error(task: Task, role: str, error: str) -> str:
    """Keep a subagent invocation that never happened (OPEN-46).

    Returns the sentence, so the coder path can also put it in `note`
    without the two spellings drifting.

    `run_errors` is a list for the reason `halts` is one, and the reason
    is the same failure: `note` is rewritten by every branch that finishes
    a task and cleared outright by the passing one, so an invocation the
    provider never served vanished exactly when the task later succeeded.
    run14 lost task t8 to an exhausted retry budget and its debug log,
    ledger and terminal held nothing about it afterwards.

    It is separate from `halts` because the two are different events: a
    halt is an invocation Rudra STOPPED, and this is one that never
    started. And it is recorded WITHOUT classifying the string -- asking
    "was this a retry exhaustion?" of a provider's prose is the guess
    MAX_CONSECUTIVE_RUN_ERRORS exists to avoid making.
    """
    sentence = f"the {role} could not run: {error}"
    task.run_errors = (*task.run_errors, sentence)
    return sentence


# The tool whose absence means a tester did not do its job. Named here
# rather than inferred from the spec's `rudra_tools` tuple, because that
# tuple's ORDER is not a contract and "the first one" would silently follow
# an edit to it.
TESTER_TOOL = "run_tests"


def _called_nothing(result: Any, tool: str) -> bool:
    """Did this invocation demonstrably not call `tool`?

    False whenever the answer is unknown. `SubagentResult.tools` is None
    for a result nothing measured -- one built by hand, or a stand-in --
    and reading that as "called nothing" would fire this record on the test
    harness rather than on a run. `{}` is the other answer: it ran and
    called nothing (OPEN-98, and TODO.md's second watched habit).
    """
    tools = getattr(result, "tools", None)
    return tools is not None and tool not in tools


def _record_incomplete(task: Task, role: str, what: str) -> str:
    """Keep an invocation that ran, ended cleanly, and did not do its job.

    The THIRD member of `run_errors`' family, and the field is right for it
    for `_record_run_error`'s reason: `note` is rewritten by every branch
    that finishes a task, so anything landing there vanishes exactly when
    the task later succeeds.

    A separate sentence rather than `_record_run_error`'s, because that one
    asserts the invocation never started and here it did -- run
    `2cde3406f7d6`'s tester spent 180.4 s and 22 tool calls before it
    stopped, and a record claiming it "could not run" would send a reader
    hunting a provider outage that never happened (OPEN-98).

    It states what was OBSERVED and never what follows from it. The measured
    tester shelled out to `python3 -m pytest --co` and so did touch pytest;
    what it never did was call the tool that reports a verdict. "Ended
    without running the suite" would be a claim about the world, and this is
    a claim about the log.
    """
    sentence = f"the {role} ended without {what}"
    task.run_errors = (*task.run_errors, sentence)
    return sentence


def _record_halt(task: Task, result: Any, context: LoopContext) -> None:
    """Keep a subagent guard halt, and say it happened (OPEN-44).

    `task.note` used to be the only place this landed, and every branch
    that finishes a task rewrites that field -- the PASSING one clears it
    outright. So a halt survived exactly when the task ALSO failed, and
    vanished in the case a reader most needs it: run bf6be7525991 ended 6
    of 6 coder invocations on MAX_REPEATED_CALLS and its ledger, its
    terminal and its debug log all said nothing about any of them.

    `halts` is a separate field rather than a wider `note` for a reason
    the fix must not undo: `note` is interpolated verbatim into
    consult_planner's prompt and filed in the palace by
    record_block_memory (CR-C4), so it is read by a MODEL later. A halt is
    a fact about Rudra's machinery, not about the work.

    The console line is the same report review_once has made for the
    reviewer since OPEN-35 -- printed where it happens, because the run
    trace carries it at VERBOSE only (trace/render.py).
    """
    if not result.halted_reason:
        return
    task.halts = (*task.halts, result.halted_reason)
    context.console.print(
        f"[dim]{escape(result.name)} stopped early: {escape(result.halted_reason)}[/dim]"
    )


async def run_task(task: Task, ledger: Ledger, *, context: LoopContext) -> Outcome:
    """Write, verify, fix, reverify -- until the gate passes or we stop.

    Only this function writes DONE, and only on VerifyReport.passed.
    """
    tested = False
    blocker_text = ""
    # Whether a gate has already declined this task in this call, which is
    # what makes an empty diff that then passes a RETRY rather than work an
    # earlier task did (OPEN-123). In memory, like `inherited`: a fresh
    # process has no evidence and says what it said before.
    rejected = False
    # What was already failing before this task ran (OPEN-23). Captured ONCE,
    # here, and deliberately not refreshed per attempt: a regression attempt 1
    # introduced must still be this task's own on attempt 2, and re-reading
    # the baseline inside the loop would launder it into an inheritance.
    inherited = context.failure_baseline
    # Across every attempt, not per attempt: "how long did this task take"
    # is the question, and a task that failed twice before passing cost
    # the user all three tries (C9.6).
    started = time.monotonic()

    def _stop(outcome: Outcome) -> Outcome:
        """Record the elapsed time, save, and return. Assignment BEFORE the
        save, or the ledger on disk reports 0.0 for a task that took a
        minute."""
        # Stored at full precision and rounded only where it is shown.
        # Rounding here made a task that finished in under 10 ms read as
        # 0.0 -- never true of real work, but it also meant the stored
        # number was a display decision rather than a measurement.
        task.seconds = time.monotonic() - started
        # A run that stops mid-task must leave that task PENDING, never
        # IN_PROGRESS: Ledger.resumable() excludes IN_PROGRESS on purpose
        # (ledger.py:91-100), so leaving it there makes `--continue` skip
        # the one task that never finished. This is A1.93 on the STOP_RUN
        # path -- the cancel handler below already does the same thing for
        # the same reason, and the two paths must not disagree.
        if outcome is Outcome.STOP_RUN and task.status is TaskStatus.IN_PROGRESS:
            task.status = TaskStatus.PENDING
        ledger.save(context.paths.ledger_json)
        return outcome

    # Every coder invocation this task makes, served or not. `task.attempts`
    # counts only the ones that RAN (OPEN-46 §6 option C), so it can stay the
    # same across two dispatches -- and a thread id keyed on it alone would
    # then repeat, handing the second invocation whatever partial state the
    # first one left in the checkpointer. This counter never goes backwards.
    dispatch = 0

    while task.attempts < context.cfg.agent.max_fix_attempts:
        dispatch += 1
        task.attempts += 1
        task.status = TaskStatus.IN_PROGRESS
        ledger.save(context.paths.ledger_json)
        # Beside the ledger save, not instead of it (OPEN-88). These are the
        # two facts about a run in progress -- what is being worked, and what
        # it has cost so far -- and they are worth equally little if one of
        # them is two hours stale. Per ATTEMPT and not per task, because run
        # `fc543fb2b82f`'s worst task was a single 2,704 s attempt.
        flush_usage(context)

        before = attempt_snapshot(context)
        result = await run_subagent(
            "coder",
            _coder_prompt(task, blocker_text),
            context=context.subagents,
            thread_id=f"{context.subagents.session_id}-{task.id}-a{task.attempts}-{dispatch}",
        )

        if result.error:
            # The coder never produced a turn: a build failure, or an
            # exception mid-stream. `runner.py` cannot tell those apart --
            # it catches every Exception and reports `str(exc)` -- so a
            # provider's HTTP 500 arrives here indistinguishable from a
            # missing package.
            #
            # This used to end the run outright, on the reasoning that both
            # are "environment-class and would recur on every remaining
            # task". A 500 is the counterexample, and it cost run
            # `eb2e1e2e2b2c` all eleven of its tasks 693 seconds in
            # (OPEN-33). So: spend the attempt, and stop only once the
            # errors have actually shown they recur.
            context.run_errors += 1
            # Durably first, then in `note` -- which does not survive this
            # task finishing (OPEN-46). One sentence, written once.
            task.note = _record_run_error(task, "coder", result.error)
            # Give the attempt back (OPEN-46 §6, option C, chosen by the owner
            # 2026-09-01). The retry budget is for a coder that produced a turn
            # and got it wrong; this one never ran, so the task has not had a
            # try and must not be charged for one. run14's t8 was lost exactly
            # here -- "after 4 attempt(s): NotFoundError (404). Nothing was
            # written." spent all three attempts on an endpoint that served no
            # call, and the task that would have done the work was never
            # attempted.
            #
            # This does NOT make the loop unbounded, and the bound is the one
            # already above: MAX_CONSECUTIVE_RUN_ERRORS ends the RUN after
            # three in a row, and any served invocation resets it. Uncharging
            # the attempt is why option C is small -- §6 sized it as "the
            # largest change" before §5 built the counter and the record it
            # needs.
            #
            # The decrement happens BEFORE the ceiling returns, so a task the
            # run stops on reports the tries it actually had. `_stop` leaves it
            # PENDING (A1.93), so `--continue` resumes it with its budget
            # intact rather than with three attempts already spent on a dead
            # provider.
            task.attempts -= 1
            if context.run_errors >= MAX_CONSECUTIVE_RUN_ERRORS:
                return _stop(Outcome.STOP_RUN)
            ledger.save(context.paths.ledger_json)
            continue

        # A run that happened. Whatever else went wrong with it, the
        # provider is answering -- which is the only thing the counter above
        # is measuring.
        context.run_errors = 0

        if result.halted_reason:
            # A guard fired. Recorded for the summary; the gate below is what
            # says how badly the attempt actually went.
            task.note = result.halted_reason
            _record_halt(task, result, context)

        touched = _record_touched(task, changed_since(context, before))
        # No `before is not None` qualifier any more: `attempt_snapshot`
        # always has one, and the qualifier was what disabled this guard
        # outside a git repository (OPEN-13). The ATTEMPT's diff, never
        # `task.files_touched`, which since OPEN-123 holds every attempt's.
        if not touched:
            # An empty diff is not proof of failure -- it is an absence of
            # evidence. OPEN-27 measured five tasks where the honest reading
            # was "there was nothing to write": an earlier task had already
            # built this one's work, and the coder read the file, said so,
            # and stopped. Ask the gate rather than assuming.
            #
            # `changed_files` scopes the syntax stage and the stub scan, and
            # is every file an EARLIER attempt of this task wrote (OPEN-123),
            # so a retry that wrote nothing still has that work parsed and
            # scanned. On a first attempt it is empty and those two stages
            # judge nothing, which is correct, because nothing was written;
            # every other stage is whole-project either way. Corrected
            # 2026-09-17: this said the stub scan ONLY, and reasoned from it
            # that the verdict was already whole-project.
            report = await _verify(task, context)
            verdict = verdict_for(report, inherited=inherited)
            context.failure_baseline = failure_keys(report)
            if _confirms_nothing_to_do(report, verdict):
                task.status = TaskStatus.DONE
                task.note = _already_satisfied_note(report, retry=rejected)
                outcome = _stop(Outcome.DONE)
                record_task_in_memory(context.paths, task)
                record_task_memory(context, task)
                return outcome
            task.note = _wrote_nothing_note(blocker_text, report, context.project_path)
            rejected = True
            ledger.save(context.paths.ledger_json)
            continue

        report = await _verify(task, context)

        # The tester writes tests; it does not re-do the task. Re-verifying
        # in place rather than looping is deliberate: `continue` here would
        # send the *coder* round again and spend an attempt on work that
        # already passed.
        if report.passed and not tested and tests_produced_no_judgement(report):
            tested = True
            tester_result = await run_subagent(
                "tester",
                _tester_prompt(task),
                context=context.subagents,
                thread_id=f"{context.subagents.session_id}-{task.id}-tester",
            )
            _record_halt(task, tester_result, context)
            if tester_result.error:
                # The tester's silence was worse than the coder's, and
                # OPEN-46 is where it surfaced: the coder's error branch
                # at least writes `note`, while this result went only to
                # `_record_halt` -- which returns early unless a guard
                # fired. So a tester whose model call exhausted the retry
                # budget was recorded in no field at all, and the task
                # went on to pass with the gate reporting no test
                # judgement.
                #
                # NOT into `note`, and not into `context.run_errors`: a
                # dead tester does not fail the task (the gate already
                # passed), `note` is read by a MODEL later (CR-C4), and
                # counting it toward MAX_CONSECUTIVE_RUN_ERRORS would end
                # runs over optional work.
                _record_run_error(task, "tester", tester_result.error)
            elif tester_result.ok and _called_nothing(tester_result, TESTER_TOOL):
                # OPEN-98. The same silence one CONDITION over, and this is
                # the case the run was actually lost to: the tester wrote
                # its test file, said "Wait, I made an error. The file path
                # should be relative to the project root, not absolute. Let
                # me check the project structure." -- and the turn ended,
                # because that message carried no tool call. langgraph's
                # ReAct loop stops on an AIMessage with no tool calls, which
                # `_FINISH_RULES` deliberately teaches as the completion
                # signal (OPEN-42), and nothing distinguishes "I am done"
                # from "I have noticed a mistake and am about to fix it".
                #
                # `subagent_done` recorded `ok: true`, no halt, no error, so
                # `_record_halt` returned early and the branch above did not
                # apply: steps 3 and 4 of the tester's own workflow -- run
                # the suite, report what failed -- never happened, and the
                # invocation appeared in NO field of the ledger.
                #
                # A record and not a repair: it does not fix the stop
                # condition, it makes the next occurrence one grep away
                # instead of a reconstruction from 678 lines of JSONL
                # (CLAUDE.md 8a failure shape 4).
                _record_incomplete(task, "tester", f"calling {TESTER_TOOL}")
            _record_touched(task, changed_since(context, before))
            report = await _verify(task, context)

        if report.escalate:
            task.note = _blocker_text(report, context.project_path)
            return _stop(Outcome.STOP_RUN)

        # Every gate run updates what the NEXT task inherits, including a
        # failing one -- that is the whole point. Set before the branches
        # below so no early return can skip it.
        verdict = verdict_for(report, inherited=inherited)
        context.failure_baseline = failure_keys(report)

        if verdict is PASSED or verdict is INHERITED:
            task.status = TaskStatus.DONE
            # An inherited failure is not this task's blocker, but it is not
            # nothing either: the note is the only place a reader learns the
            # suite was already red when this task started, and why it passed
            # anyway (OPEN-23).
            task.note = "" if verdict is PASSED else _inherited_note(report, inherited)
            outcome = _stop(Outcome.DONE)
            record_task_in_memory(context.paths, task)
            record_task_memory(context, task)
            return outcome

        signature = failure_signature(report)
        blocker_text = _blocker_text(report, context.project_path) + _env_sync_note(context)
        if signature is not None and signature == task.last_signature:
            task.status = TaskStatus.BLOCKED
            # The blocker, not just the shape of the failure. `task.note` is
            # what consult_planner interpolates verbatim when it asks for a
            # different approach (planner_agent.py), and what
            # record_block_memory files in the palace -- so overwriting it
            # with a content-free string meant the planner was asked to
            # re-plan around a failure it was told nothing about, and the
            # palace stored a bug with no bug in it (CR-C4).
            task.note = f"no progress: the same failure twice\n\n{blocker_text}"
            outcome = _stop(Outcome.BLOCKED)
            record_block_memory(context, task)
            return outcome
        task.last_signature = signature
        rejected = True

    task.status = TaskStatus.BLOCKED
    if "wrote nothing" not in task.note and "could not run" not in task.note:
        # Same reason as above (CR-C4): the count is not a blocker. "could
        # not run" is excluded for the same reason "wrote nothing" is -- it
        # names what actually happened, and "3 attempts exhausted" with an
        # empty blocker names nothing (OPEN-33).
        task.note = f"{task.attempts} attempts exhausted\n\n{blocker_text}".rstrip()
    outcome = _stop(Outcome.BLOCKED)
    record_block_memory(context, task)
    return outcome


_STATUS_MARK = {
    TaskStatus.DONE: "[green]✓[/green]",
    TaskStatus.BLOCKED: "[red]✗[/red]",
    TaskStatus.DROPPED: "[yellow]–[/yellow]",
    TaskStatus.PENDING: "[dim]·[/dim]",
    TaskStatus.IN_PROGRESS: "[dim]·[/dim]",
}

_NOT_ATTEMPTED = "never attempted — the run stopped"


def _review_prompt(ledger: Ledger) -> str:
    """Tell the reviewer what this run touched, by name (A1.68).

    `git_diff` shows changes to *tracked* files, and on a greenfield run
    every file is new and untracked, so the reviewer was handed nothing
    and reported nothing -- silently, because an empty review prints
    nothing. It worked exactly when a file was already committed, which is
    the case Rudra is least often pointed at.

    The names come from the ledger rather than from a second git call: the
    loop already recorded them per task, and since A1.66 that record is
    file-level and correct. Blocked tasks are included deliberately --
    half-finished work is what most deserves a second opinion.
    """
    seen: dict[str, None] = {}
    for task in ledger.tasks:
        for path in task.files_touched:
            seen.setdefault(path, None)

    prompt = "Review the changes this run made and report any problems."
    if seen:
        listed = "\n".join(f"- {path}" for path in seen)
        prompt += (
            f"\n\nThese files were written or changed:\n{listed}\n\n"
            "Read them with read_file. git_diff shows changes to files git "
            "already tracks, so on a new project it will show nothing even "
            "though the files above exist."
        )
    return prompt


async def review_once(context: LoopContext, ledger: Ledger) -> None:
    """One advisory pass over everything that changed. Printed, never acted on.

    D9's split: the deterministic gate decides done-or-not; the reviewer
    comments on quality and gates nothing.
    """
    result = await run_subagent(
        "reviewer",
        _review_prompt(ledger),
        context=context.subagents,
        thread_id=f"{context.subagents.session_id}-review",
    )
    if result.text.strip():
        context.console.print("\n[bold]Review[/bold] [dim](advisory)[/dim]")
        context.console.print(result.text)

    # A guard fired, or the reviewer never produced a turn. Say so (OPEN-35).
    # `run_subagent` returns text="" when a guard fires before any prose, and
    # printing only `.text` meant run 36023bb8bdd1 said NOTHING about a
    # reviewer killed mid-read -- which read as a crash. The coder path has
    # recorded this since 9c (`result.halted_reason` -> task.note); this is
    # the same report, on the one path that had none. Still advisory: it
    # gates nothing and changes no exit code.
    reason = result.halted_reason or result.error
    if reason:
        context.console.print(f"\n[dim]Review incomplete: {escape(reason)}[/dim]")


_ARCHITECTURE_PROMPT = """You are updating a project's engineering memory.

Below is the running log of work completed on this project, and the current
Architecture Notes. Rewrite the Architecture Notes so they describe how this
project is built: its layout, its boundaries, and any decision a future
contributor would otherwise have to rediscover.

Write prose, not a changelog. Do not list the tasks back. Do not invent
anything the log does not support. Reply with the notes and nothing else.

## Current Architecture Notes
{notes}

## Session Log
{log}
"""


def record_task_in_memory(paths: Any, task: Task) -> None:
    """Append one completed task to AGENTS.md's Session Log (C7.3).

    Deterministic: the description is the task's own, and `files_touched`
    came from git rather than from the model (S9c). Nothing here may raise
    -- a task that genuinely finished must not be undone by a memory write.

    Absent AGENTS.md is a no-op, not a create: `_ensure_agents_md` owns
    creating it, and inventing one here would produce a memory file for a
    project that never ran a plan.
    """
    from rudra.context.agents_md import append_session_entry, format_entry, write_agents_md

    try:
        path = paths.agents_md
        if not path.is_file():
            return
        entry = format_entry(
            datetime.now(timezone.utc).date().isoformat(),
            task.id,
            task.description,
            tuple(task.files_touched),
        )
        # Atomic: AGENTS.md is durable and is the project's whole
        # cross-session memory, and the `except OSError` below cannot
        # restore a half-written one (CR-A5).
        write_agents_md(path, append_session_entry(path.read_text(encoding="utf-8"), entry))
    except OSError:
        return


def record_task_memory(context: Any, task: Task) -> None:
    """File one completed task in the palace (C8.3).

    The palace twin of record_task_in_memory, and deliberately adjacent to
    it: AGENTS.md is the recent window, capped at 20 (S12.4), and the
    palace is the unbounded history. Writing both from the same place is
    what makes C8.9's "must not diverge" structural rather than a promise.

    No try/except here on purpose -- MemoryStore.write is already
    @degrades-wrapped, and a second guard would swallow a Rudra bug as if
    it were a ChromaDB one.
    """
    store = getattr(context, "memory", None)
    if store is None:
        return
    files = ", ".join(task.files_touched) if task.files_touched else "no files changed"
    store.write(
        MemoryEntry(
            content=f"Completed: {task.description}. Files: {files}.",
            room="tasks",
            added_by="rudra",
        )
    )


def record_block_memory(context: Any, task: Task) -> None:
    """File one blocked task and its blocker (C8.3).

    Bug-to-fix pairs are what make this worth storing: the next run's
    planner sees what stopped the last one before it plans the same thing
    again.
    """
    store = getattr(context, "memory", None)
    if store is None:
        return
    store.write(
        MemoryEntry(
            content=f"Blocked: {task.description}. Reason: {task.note or 'unknown'}.",
            room="blockers",
            added_by="rudra",
        )
    )


def record_plan_memory(store: Any, facts: Any, tasks: Any) -> None:
    """File the approved plan's facts, each with its source (C8.3).

    Facts, not the task list: the tasks are this run's shape and mean
    nothing to the next request, which is why the ledger is volatile
    (D15). A fact carries why it is believed, and that survives.
    """
    if store is None or facts is None:
        return
    for key, fact in facts.items():
        store.write(
            MemoryEntry(
                content=f"{key} = {fact.value} ({fact.source}: {fact.why})",
                room="decisions",
                added_by="rudra",
            )
        )


SUMMARISER_ROLE = "summariser"
"""The summariser's name in every record it leaves: its `retry` notice, its
telemetry span and its `model_call` records (OPEN-127). Not a `usage.json`
role, and must not become one -- that file keeps four (OPEN-58)."""


async def summarise_architecture(context: LoopContext, ledger: Ledger) -> None:
    """Fold the run's Session Log into Architecture Notes. One model call.

    Runs once, at run end, and only when something reached DONE. Wrapped
    so any failure costs polish rather than the run: the deterministic
    entries are already on disk, which is why the Python half is done
    first.

    `context._model` is a test seam -- when absent the model comes from
    build_model("default"). Documented rather than hidden, because an
    undocumented seam is a trap for the next reader.
    """
    from rudra.context.agents_md import replace_section, section_body, write_agents_md

    if not any(task.status is TaskStatus.DONE for task in ledger.tasks):
        return

    try:
        path = context.paths.agents_md
        if not path.is_file():
            return

        text = path.read_text(encoding="utf-8")
        model = getattr(context, "_model", None)
        if model is None:
            from rudra.llm import build_model

            model = build_model("default", context.cfg)

        # The one model call in this file that does not go through a
        # subagent or a planner stage, so it is the one that needs the
        # callbacks handed to it (OPEN-110). Without this the AGENTS.md
        # summariser is the single model call missing from a run's trace,
        # which is exactly the kind of "everything except" that makes a
        # trace untrustworthy.
        telemetry = getattr(getattr(context, "subagents", None), "telemetry", None)
        prompt = [
            {
                "role": "user",
                "content": _ARCHITECTURE_PROMPT.format(
                    notes=section_body(text, "Architecture Notes"),
                    log=section_body(text, "Session Log"),
                ),
            }
        ]

        # `config=` only when there is something to put in it: an argument
        # that is always passed is an argument every caller's model must
        # accept, and the models here are sometimes doubles.
        def call(request: Any) -> Any:
            if telemetry is not None:
                return model.ainvoke(request, config=telemetry.config(SUMMARISER_ROLE))
            return model.ainvoke(request)

        # Outside any agent graph, so no ModelRetryMiddleware wraps this
        # call -- and since OPEN-115 switched every client SDK's own retries
        # off, it would otherwise have none. The middleware's method is
        # called directly so the policy, the backoff and the `retry` notice
        # are the ones every other model call gets. `usage` is deliberately
        # not passed: this call is not in `usage.json`'s `calls`, and a
        # retry count with no call count is a rate with no denominator.
        from rudra.middleware.model_retry import ModelRetryMiddleware

        # One `model_call` record per attempt (OPEN-127). `usage` stays
        # unpassed for the reason above, and that had cost the record too:
        # only UsageMiddleware wrote one, and this call is in no graph. Step
        # 15's run made 131 chat/completions requests against 130 records, and
        # the missing one was this call -- the last a run makes, so the one a
        # user reporting "it hung at the end" most needs timed. A failure is
        # recorded and re-raised unchanged: the retry middleware decides.
        async def timed(request: Any) -> Any:
            started = time.perf_counter()
            try:
                answer = await call(request)
            except BaseException as exc:
                log_model_call(
                    SUMMARISER_ROLE,
                    time.perf_counter() - started,
                    ok=False,
                    error=type(exc).__name__,
                    exc=exc,
                )
                raise
            metadata = getattr(answer, "usage_metadata", None) or {}
            log_model_call(
                SUMMARISER_ROLE,
                time.perf_counter() - started,
                input_tokens=metadata.get("input_tokens"),
                output_tokens=metadata.get("output_tokens"),
            )
            return answer

        retry = ModelRetryMiddleware(
            SUMMARISER_ROLE, trace=getattr(getattr(context, "subagents", None), "trace", None)
        )
        reply = await retry.awrap_model_call(prompt, timed)
        notes = str(getattr(reply, "content", "")).strip()
        if notes:
            write_agents_md(path, replace_section(text, "Architecture Notes", notes))
    except Exception:  # noqa: BLE001 - memory is polish; a run must survive it
        context.console.print("[dim]Could not update AGENTS.md architecture notes.[/dim]")


def write_usage_log(path: Path, usage: Any) -> None:
    """Write the run's tally beside permissions.jsonl and verify.log.

    Volatile subtree (D15): regenerated every run, never worth committing.
    Nothing here may raise. A run that finished its work must not be
    reported as failed because its own bookkeeping could not be written --
    the same rule _maybe_auto_branch follows for a branch it cannot make.

    Written even when no role made a call (OPEN-79). The `not usage.roles()`
    guard this used to carry predates OPEN-53's `run` block, and since that
    block exists an empty-roles run still has a true wall clock and a
    `suspended_seconds` -- which is exactly the number that settles "why did
    this take so long" for a run that died before it called anything.
    """
    if usage is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(usage.as_log(), indent=2), encoding="utf-8")
    except OSError:
        return


def flush_usage(context: Any) -> None:
    """Write the tally out mid-run, wherever the ledger is being saved.

    OPEN-88: `usage.json` reached disk in exactly two places, `work()`'s
    last statement and `RudraAgent.close()`, and both are ENDS. So the file
    that carries `seconds`, `calls`, `retries` and the true wall clock
    existed for every run except the ones anybody complains about -- because
    a run somebody is complaining about is one that has not finished. Run
    `fc543fb2b82f` was two hours into a single-page task with no
    `usage.json` in its logs directory at all.

    Cheap enough to do often: `write_usage_log` overwrites with one
    `write_text` of a few hundred bytes and swallows `OSError` itself. Read
    defensively for the reason `_write_usage_log` is -- several tests build
    a LoopContext with no usage and no paths.
    """
    usage = getattr(context, "usage", None)
    paths = getattr(context, "paths", None)
    if usage is None or paths is None:
        return
    logs = getattr(paths, "logs", None)
    if logs is None:
        return
    write_usage_log(Path(logs) / "usage.json", usage)


def notice_if_suspended(trace: Any, usage: Any) -> None:
    """Say once, at run end, that the machine slept through part of it.

    Every clock Rudra keeps counts only time the process was running, so a
    suspended machine leaves them all honest and all disagreeing with the
    user's stopwatch. run10 was 4888 seconds of wall clock over 1243
    seconds of counted time, and three sessions read the difference as a
    missing instrument before `pmset -g log` put macOS Deep Idle sleep in
    exactly the three gaps (OPEN-53).

    A NOTICE rather than a print, on OPEN-44's and OPEN-45's precedent: it
    is something that happened TO the run rather than something a model
    did, so it renders at VERBOSE and reaches the debug log at every
    level. `role="rudra"` for the reason the palace uses that name -- it
    marks what Rudra observed itself, as against what an agent said.

    Nothing here may raise, the rule `write_usage_log` follows: a run that
    finished its work must not be reported failed over its own
    bookkeeping (C7.5).
    """
    if trace is None or usage is None:
        return
    try:
        suspended = usage.suspended_seconds(wall_now=time.time(), mono_now=time.monotonic())
        if suspended < SUSPENDED_NOTICE_SECONDS:
            return
        wall = time.time() - usage.started_wall
        trace.notice(
            f"the machine was suspended for {suspended:.1f}s of this run's "
            f"{wall:.1f}s wall clock; every reported duration excludes it",
            role="rudra",
            name="suspended",
        )
    except Exception:  # noqa: BLE001 -- observability never ends a run
        return


def summarise(ledger: Ledger, console: Console, usage: Any = None, cancelled: bool = False) -> Any:
    """Print every task and return the run's result.

    A1.25 died here: there is no filter between what was declared and what
    is reported, and a task that was never attempted says so rather than
    vanishing.
    """
    # Local import: main_agent imports run_loop from this module, so a
    # module-level import would be a cycle. Same pattern build_backend
    # already uses for its backends.
    from rudra.agent.main_agent import AgentResult

    counts = ledger.counts()
    headline = (
        f"Tasks: {counts['requested']} requested · {counts['done']} done · "
        f"{counts['blocked']} blocked · {counts['dropped']} dropped"
    )
    if counts["pending"]:
        headline += f" · {counts['pending']} never attempted"
    if cancelled:
        headline = f"Cancelled — {headline}"
    console.print(f"\n[bold]{headline}[/bold]\n")
    if cancelled:
        # Said here rather than at the signal, because this is where the
        # user learns how much was left: the ledger a cancel leaves is the
        # ledger --continue already knows how to work.
        console.print("[yellow]Stopped by you. Resume with:[/yellow] rudra --continue\n")

    block = render_usage(usage)
    if block:
        console.print(block)
        console.print()

    # A1.89: `degrades` keeps a run alive through a broken palace, and this
    # is the other half of C8.6 -- a mandatory subsystem that quietly did
    # nothing is worse than one that failed. Printed after the tasks are
    # counted and before they are listed, because it explains an empty
    # `rudra memory list` the user is about to be surprised by.
    failure = last_failure()
    if failure:
        console.print("[yellow]Long-term memory failed this run — nothing was recorded.[/yellow]")
        console.print(f"[dim]{failure}[/dim]\n")

    for task in ledger.tasks:
        note = task.note
        if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
            note = _NOT_ATTEMPTED
        # escape(): a task description is model-written and a bracketed
        # word in it would be parsed as a style tag (A1.91's class).
        took = f"  [dim]({task.seconds:.1f}s)[/dim]" if task.seconds >= 0.05 else ""
        console.print(f"  {_STATUS_MARK[task.status]} {task.id}  {escape(task.description)}{took}")
        if note:
            console.print(f"      [dim]{escape(str(note))}[/dim]")

    files: list[str] = []
    for task in ledger.tasks:
        files.extend(task.files_touched)

    success = (
        counts["requested"] > 0
        and counts["done"] > 0
        and not (counts["blocked"] or counts["pending"])
    )
    return AgentResult(
        success=success,
        message=headline,
        files_created=sorted(set(files)),
        files_modified=[],
        iterations=sum(1 for task in ledger.tasks if task.attempts > 0),
        usage=usage,
    )


PREVIOUS_LEDGER = "ledger.previous.json"
"""Where a replaced ledger's unfinished work is kept (OPEN-74).

Beside `ledger.json`, in the same volatile `run/` subtree: it is a copy of
volatile state and has exactly its lifetime. ONE file, replaced each time
-- the recovery this supports is "the run I just lost", never an archive.
The archive is `state/archive.py`'s job and already keeps twenty.
"""


def preserve_previous_ledger(path: Path) -> int:
    """Copy a ledger with unfinished work aside. Returns what was at risk.

    `plan()` saves an empty ledger before its first model call, so the next
    request typed into the REPL replaces the previous run's tasks BEFORE
    the new plan exists. In the run this was filed on, fifteen pending
    tasks became `{"request": "yes", "tasks": []}` five minutes later --
    and both `cli.py`'s cancel message and `_provider_failure_message` had
    already promised `rudra --continue` would pick them up. Both were true
    when printed and false one line of input later.

    Gated on `Ledger.resumable()`, which is PENDING-only (A1.93), for the
    same reason `_provider_failure_message` is: a ledger of DONE and
    BLOCKED tasks resumes to nothing, so copying it aside would be noise
    rather than recovery.

    The bytes are copied verbatim rather than re-serialised from a parsed
    Ledger: what a user restores must be what the previous run wrote, not
    this version's idea of it. Nothing here raises -- bookkeeping must not
    end a run (write_usage_log's rule), and a ledger this cannot read is
    one the new run is about to replace anyway.
    """
    path = Path(path)
    try:
        raw = path.read_bytes()
    except OSError:
        return 0
    try:
        pending = len(Ledger.load(path).resumable())
    except Exception:  # noqa: BLE001 - any unreadable ledger is not recovery
        return 0
    if not pending:
        return 0
    try:
        path.with_name(PREVIOUS_LEDGER).write_bytes(raw)
    except OSError:
        return 0
    return pending


async def plan(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger | None = None,
) -> Ledger:
    """Run the three planning stages and return the ledger they filled.

    Touches nothing in the project: no coder runs here, which is what
    makes `--plan` honest and what lets RudraAgent put an approval gate
    between this and work() (C6.9).

    `ledger` must be the SAME object the planner's tools were bound to --
    otherwise the tasks it adds are invisible. Defaults to a fresh one
    only so tests can drive planning without wiring an agent.
    """
    ledger = ledger if ledger is not None else Ledger()

    # BEFORE the save below, which is what destroys the previous run's
    # tasks (OPEN-74). Deleting resumable work is allowed -- one run, one
    # ledger -- but doing it silently, and before a new plan exists to
    # replace it, is not.
    replaced = preserve_previous_ledger(context.paths.ledger_json)
    if replaced:
        plural = "" if replaced == 1 else "s"
        context.console.print(
            f"[yellow]{replaced} unfinished task{plural} from the previous run "
            f"were replaced.[/yellow] "
            f"[dim]A copy is at .rudra/run/{PREVIOUS_LEDGER} — "
            f"restore it over ledger.json and use `rudra --continue`.[/dim]"
        )
        trace = getattr(getattr(context, "subagents", None), "trace", None)
        if trace is not None:
            trace.notice(
                f"replaced a ledger holding {replaced} unfinished task{plural}",
                role="planner",
                name="ledger",
            )

    # The one save that knows the request, so record it here (C7.2).
    # Every later save passes nothing and keeps it. Without this the field
    # existed and was always "", which a live run found and no unit test
    # could -- they called save(request=...) directly.
    ledger.save(context.paths.ledger_json, request=request)
    # The planner is not free -- run `fc543fb2b82f` spent 450 s across 18
    # calls here before the first task was dispatched, and a run examined
    # during planning had no tally at all (OPEN-88).
    flush_usage(context)

    # Three stages, in order (C6.7, S10b.1): settle the facts, decide the
    # shape, then declare the work. Each is a separate agent with its own
    # tools, so a stage cannot do another stage's job. Only `breakdown` is
    # ever re-entered, and only by work() below (S10b.3).
    #
    # Bare `await`, and that is a DECIDED disposition rather than a missing
    # handler (OPEN-83, owner's call 2026-09-02). A provider error to the
    # planner ends the run, where the identical error to the coder is
    # absorbed and the attempt refunded (run_task above). The asymmetry was
    # measured -- run `689f0ea263be` lost 26 queued tasks to one 500 -- and
    # kept: the planner is not optional the way a tester is, a run whose
    # breakdown cannot answer has no work to do, and `--continue` resumes
    # from the ledger, which is saved before this line and after every task.
    # So the cost is time, never output. Option B (count it into
    # `context.run_errors` at the re-consult sites and carry on) is written
    # up in full at docs/superpowers/plans/2026-09-02-open-83-planner-
    # provider-error-is-fatal.md §5(b); do not implement it without asking
    # again, and do not re-file the asymmetry as a defect.
    for stage in ("clarify", "architect", "breakdown"):
        await planner(ledger, request, stage=stage, reason="initial")

    return ledger


# How many times a run may block a task and ask the planner to try
# something else before giving up. Small on purpose: each cycle costs
# max_fix_attempts coder invocations plus a planner call, and a planner that
# has not found a working approach in this many tries is not going to
# (CR-C7).
MAX_BLOCKED_CONSULTS = 5


def _stale_failure_list(keys: frozenset[str]) -> str:
    """The unowned failures, one per line, sorted (OPEN-23).

    Sorted rather than in whatever order a set iterates: this text reaches a
    model AND a ledger a human reads, and two runs of the same red suite
    should produce the same lines in the same order.
    """
    return "\n".join(f"  {key}" for key in sorted(keys))


async def work(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger,
) -> Any:
    """Run every pending task to a verdict, review once, and report.

    Consults only the `breakdown` stage, and only on a stall: the facts
    and the architecture were settled by plan(), and a user may since
    have approved them.

    Every `await planner(...)` below is unguarded on purpose, including the
    three re-consults that have a ledger to fall back on: a provider error
    here ends the run and `--continue` picks the pending tasks up. See
    plan() for why, and OPEN-83 for what the alternative would cost.
    """
    # Before any agent that holds a shell runs (OPEN-120) -- and not in
    # plan(), whose promise is that `--plan` writes nothing into the project.
    # No planner stage holds `execute`, so nothing is lost by waiting.
    await _sync_project_env(context)
    consulted_on_empty = False
    consulted_on_stale = False
    blocked_consults = 0
    cancelled = False

    while True:
        task = ledger.next_pending()
        if task is None:
            # Asked BEFORE "is anything missing?", and that order is the
            # point (OPEN-23). A red suite with an empty ledger is not a
            # question about the plan -- the plan may be complete and the
            # suite still failing on tests no task ever owned. A planner
            # reading a finished plan answers "nothing missing" and the run
            # ends, which is how a red suite used to leave no trace.
            #
            # Here rather than after the loop so a task it adds is actually
            # worked: `continue` re-enters with the new task pending.
            if context.failure_baseline and not consulted_on_stale:
                consulted_on_stale = True
                await planner(
                    ledger,
                    request,
                    stage="breakdown",
                    reason="stale_failures",
                    feedback=_stale_failure_list(context.failure_baseline),
                )
                continue
            if consulted_on_empty:
                break
            consulted_on_empty = True
            await planner(ledger, request, stage="breakdown", reason="ledger_empty")
            continue

        try:
            outcome = await run_task(task, ledger, context=context)
        except asyncio.CancelledError:
            # A cooperative stop, not a teardown. The task goes back to
            # PENDING because Ledger.resumable() excludes IN_PROGRESS on
            # purpose (ledger.py:91-100) -- leaving it there is A1.93,
            # where `--continue` silently skips the one task the user
            # actually interrupted.
            if task.status is TaskStatus.IN_PROGRESS:
                task.status = TaskStatus.PENDING
            task.note = "cancelled by the user"
            ledger.save(context.paths.ledger_json)
            cancelled = True
            break
        ledger.save(context.paths.ledger_json)

        if outcome is Outcome.STOP_RUN:
            context.console.print(
                f"\n[bold red]Run stopped early.[/bold red] [dim]{task.note}[/dim]"
            )
            break
        if outcome is Outcome.BLOCKED:
            # Only a stall consults the planner -- never an ordinary success.
            blocked_consults += 1
            if blocked_consults > MAX_BLOCKED_CONSULTS:
                # Bounded. `consulted_on_empty` was the only loop bound and
                # every block reset it, so a planner that answered each
                # "take a DIFFERENT approach" with add_tasks produced a task
                # that blocked, which consulted it again, indefinitely --
                # max_fix_attempts coder calls plus a planner call per
                # cycle, with Ctrl-C as the user's only exit (CR-C7).
                context.console.print(
                    f"\n[bold red]Stopping.[/bold red] [dim]{blocked_consults - 1} tasks "
                    f"blocked and re-planning is not making progress.[/dim]"
                )
                break
            consulted_on_empty = False
            await planner(ledger, request, stage="breakdown", reason="blocked", task=task)

    run_usage = getattr(context, "usage", None)

    # Both of these are model calls, and somebody who just pressed Ctrl-C
    # is not waiting through two more inferences to be told they succeeded
    # in stopping. The usage log is still written: a cancelled run still
    # cost tokens and wall clock, which is exactly when that is worth
    # knowing.
    if not cancelled:
        if any(task.status is TaskStatus.DONE for task in ledger.tasks):
            await review_once(context, ledger)
        await summarise_architecture(context, ledger)

    notice_if_suspended(getattr(context.subagents, "trace", None), run_usage)
    write_usage_log(context.paths.logs / "usage.json", run_usage)
    return summarise(ledger, context.console, run_usage, cancelled=cancelled)


async def run_loop(
    request: str,
    *,
    context: LoopContext,
    planner: Any,
    ledger: Ledger | None = None,
) -> Any:
    """Plan, work, verify, and stop. The whole run.

    Kept as the composition of plan() and work() rather than replaced by
    them: it is what every 9c and 10b test drives, and a caller with no
    interest in the seam should not have to know one exists. The seam is
    used by RudraAgent, which puts the approval gate between the two
    (C6.9).

    `planner` is an awaitable called as
    `planner(ledger, request, stage=..., reason=..., task=...)`; it adds
    or drops tasks and records facts through its tools and returns
    nothing. Injected rather than constructed here so the loop is
    testable without a model.

    `ledger` must be the SAME object the planner's tools were bound to --
    otherwise the tasks it adds are invisible here. Defaults to a fresh
    one only so tests can drive the loop without wiring an agent.
    """
    filled = await plan(request, context=context, planner=planner, ledger=ledger)
    return await work(request, context=context, planner=planner, ledger=filled)


__all__ = [
    "LoopContext",
    "Outcome",
    "PREVIOUS_LEDGER",
    "SUMMARISER_ROLE",
    "attempt_snapshot",
    "changed_since",
    "flush_usage",
    "git_snapshot",
    "tree_snapshot",
    "plan",
    "preserve_previous_ledger",
    "review_once",
    "run_loop",
    "run_task",
    "record_task_in_memory",
    "record_task_memory",
    "record_block_memory",
    "record_plan_memory",
    "summarise",
    "summarise_architecture",
    "notice_if_suspended",
    "write_usage_log",
    "work",
]
