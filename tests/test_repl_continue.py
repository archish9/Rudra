"""`continue` in the REPL resumes; a new request over pending work asks first (OPEN-162).

Run `4567ee4c5984` died inside the REPL and printed `4 tasks still pending
and the ledger is saved. Resume with: rudra --continue`. The owner typed
`continue` at the `rudra ❯` prompt; the REPL had no resume path, so the word
was planned as a request, `plan()` replaced the four pending tasks, and run
`1808f56c8329` spent 758.7 s planning it. OPEN-74 was the same trap with
`yes`, over a plan that had never been approved -- which is why the question
below fires on ANY pending task, approved or not.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest
from rich.console import Console

from rudra.cli_repl import (
    REPL_COMMANDS,
    ReplaceChoice,
    ReplIntent,
    ask_replace,
    asks_before_replacing,
    classify_input,
    unfinished_ledger,
)

REQUEST = "write a todo application APIs using FastAPI and SQLite DB"


def _ledger(root: Path, statuses: list[str], request: str = REQUEST) -> Path:
    run = root / ".rudra" / "run"
    run.mkdir(parents=True, exist_ok=True)
    path = run / "ledger.json"
    tasks = [
        {"id": f"t{n}", "description": f"task {n}", "status": status}
        for n, status in enumerate(statuses, start=1)
    ]
    path.write_text(
        json.dumps({"request": request, "saved_at": "2026-09-25T00:00:00+00:00", "tasks": tasks}),
        encoding="utf-8",
    )
    return path


# --- what an input line is ----------------------------------------------


@pytest.mark.parametrize(
    "text", ["/continue", "continue", "Continue ", "  CONTINUE", "resume", "/resume"]
)
def test_continue_is_a_command_in_every_spelling(text):
    """The word the run told the user to use is the command, never a request."""
    assert classify_input(text) is ReplIntent.CONTINUE


@pytest.mark.parametrize(
    "text", ["continue the auth work", "resume parsing from the saved offset", "continue."]
)
def test_a_request_that_merely_starts_with_continue_is_still_a_request(text):
    """A closed set of whole words, the way `exit` and `quit` are."""
    assert classify_input(text) is ReplIntent.REQUEST


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("/exit", ReplIntent.EXIT),
        ("/quit", ReplIntent.EXIT),
        ("exit", ReplIntent.EXIT),
        ("QUIT ", ReplIntent.EXIT),
        ("/help", ReplIntent.HELP),
        ("/tree", ReplIntent.TREE),
        ("/bogus", ReplIntent.UNKNOWN),
        ("", ReplIntent.EMPTY),
        ("   ", ReplIntent.EMPTY),
        ("build a JSON parser", ReplIntent.REQUEST),
    ],
)
def test_the_existing_commands_classify_as_they_always_dispatched(text, intent):
    assert classify_input(text) is intent


def test_continue_is_in_the_command_table():
    """Help and completion read one table, so a row there is both."""
    assert "/continue" in REPL_COMMANDS


# --- when there is work to protect --------------------------------------


def test_no_ledger_is_nothing_to_protect(tmp_path):
    assert unfinished_ledger(tmp_path) is None


def test_a_finished_ledger_is_nothing_to_protect(tmp_path):
    """DONE and BLOCKED resume to nothing (A1.93), so asking would be noise."""
    _ledger(tmp_path, ["done", "blocked", "dropped"])
    assert unfinished_ledger(tmp_path) is None


def test_an_unreadable_ledger_is_nothing_to_protect(tmp_path):
    run = tmp_path / ".rudra" / "run"
    run.mkdir(parents=True)
    (run / "ledger.json").write_text("{not json", encoding="utf-8")
    assert unfinished_ledger(tmp_path) is None


def test_a_never_approved_plan_is_work_to_protect(tmp_path):
    """OPEN-74's case: fifteen PENDING tasks, none attempted, replaced by `yes`."""
    _ledger(tmp_path, ["pending"] * 15)
    ledger = unfinished_ledger(tmp_path)
    assert ledger is not None
    assert len(ledger.resumable()) == 15


def test_a_half_worked_run_is_work_to_protect(tmp_path):
    """Run 4567ee4c5984's shape: done, blocked and pending together."""
    _ledger(tmp_path, ["done", "blocked", "pending", "pending"])
    ledger = unfinished_ledger(tmp_path)
    assert ledger is not None
    assert ledger.request == REQUEST


@pytest.mark.parametrize(
    ("mode", "interactive", "asks"),
    [
        ("ask", True, True),
        ("auto", True, True),
        # Plan mode leaves every plan PENDING on purpose, and a resume there
        # presents and stops too -- asking would fire on every request.
        ("plan", True, False),
        # Nobody to answer: the question would eat the next piped line.
        ("ask", False, False),
        ("auto", False, False),
    ],
)
def test_the_question_is_asked_only_where_it_means_something(mode, interactive, asks):
    assert asks_before_replacing(mode, interactive=interactive) is asks


# --- the question ---------------------------------------------------------


def _answer(tmp_path, reply):
    _ledger(tmp_path, ["done", "pending", "pending"])
    ledger = unfinished_ledger(tmp_path)
    console = Console(record=True, width=120)

    def reader():
        if isinstance(reply, BaseException):
            raise reply
        return reply

    return ask_replace(ledger, console, reader=reader), console.export_text()


@pytest.mark.parametrize(
    ("reply", "choice"),
    [
        ("c", ReplaceChoice.CONTINUE),
        ("r", ReplaceChoice.REPLACE),
        ("x", ReplaceChoice.CANCEL),
        ("3", ReplaceChoice.CANCEL),
    ],
)
def test_each_answer_is_its_choice(tmp_path, reply, choice):
    assert _answer(tmp_path, reply)[0] is choice


@pytest.mark.parametrize("gone", [EOFError(), KeyboardInterrupt()])
def test_eof_and_ctrl_c_are_cancel_never_replace(tmp_path, gone):
    """The plan gate's rule (S10c.5): giving up never destroys anything."""
    assert _answer(tmp_path, gone)[0] is ReplaceChoice.CANCEL


def test_the_question_names_what_is_at_stake(tmp_path):
    _, printed = _answer(tmp_path, "x")
    assert "2 unfinished tasks" in printed
    assert REQUEST in printed


def test_the_request_is_escaped_in_the_question(tmp_path):
    """A request is user text; Rich would eat `[bold]` and print nothing."""
    _ledger(tmp_path, ["pending"], request="fix the [bold] parser")
    console = Console(record=True, width=120)
    ask_replace(unfinished_ledger(tmp_path), console, reader=lambda: "x")
    assert "fix the [bold] parser" in console.export_text()


# --- the REPL, driven through CliRunner ------------------------------------
#
# `_repl_session` is nested inside the CLI callback, so scripted input through
# CliRunner is the only way in without a terminal (test_cli_cancel.py).


class _StubAgent:
    async def run(self):
        from rudra.agent.main_agent import AgentResult

        return AgentResult(success=True, message="done")

    async def close(self):
        return None


def _repl(monkeypatch, inputs, *, args=("--auto",), answer="", interactive=False):
    """Run the REPL over scripted lines; return (output, the factory's calls)."""
    from typer.testing import CliRunner

    from rudra import cli as cli_module
    from rudra.cli import app

    remaining = list(inputs)
    calls: list[dict] = []

    async def scripted(session, prompt_text):
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    async def factory(**kwargs):
        calls.append(kwargs)
        return _StubAgent()

    monkeypatch.setattr(cli_module, "_prompt_input", scripted)
    monkeypatch.setattr(cli_module, "create_main_agent", factory)
    monkeypatch.setattr(cli_module, "print_banner", lambda: None)
    monkeypatch.setattr("rudra.permissions.stdin_is_interactive", lambda: interactive)
    result = CliRunner().invoke(app, list(args), input=answer)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result.output, calls


@pytest.mark.parametrize("word", ["continue", "/continue", "resume"])
def test_typing_continue_resumes_the_pending_ledger(monkeypatch, tmp_path, word):
    """The run 1808f56c8329 case: the word resumes, and plans nothing."""
    monkeypatch.chdir(tmp_path)
    path = _ledger(tmp_path, ["done", "blocked", "pending", "pending"])
    before = path.read_bytes()

    output, calls = _repl(monkeypatch, [word, "/exit"])

    assert len(calls) == 1
    assert calls[0]["resume"] is True
    assert calls[0]["task"] == REQUEST, "the recorded request is the run's task"
    assert calls[0]["grants"] is not None, "a resumed turn keeps the session's grants"
    assert path.read_bytes() == before


def test_continue_with_nothing_to_resume_says_why_and_plans_nothing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    output, calls = _repl(monkeypatch, ["continue", "/exit"])

    assert calls == [], "`continue` must never be planned as a request"
    assert "Cannot continue" in output
    assert "no previous run" in output
    assert "Goodbye" in output, "a refusal returns to the prompt"


def test_continue_over_only_blocked_work_is_refused(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, ["done", "blocked"])

    output, calls = _repl(monkeypatch, ["continue", "/exit"])

    assert calls == []
    assert "nothing pending" in output


def test_a_new_request_over_pending_work_asks_and_cancel_changes_nothing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = _ledger(tmp_path, ["done", "pending"])
    before = path.read_bytes()

    output, calls = _repl(
        monkeypatch, ["add rate limiting", "/exit"], answer="x\n", interactive=True
    )

    assert calls == []
    assert "1 unfinished task" in output
    assert path.read_bytes() == before


def test_eof_at_the_question_is_cancel(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = _ledger(tmp_path, ["pending"])
    before = path.read_bytes()

    output, calls = _repl(monkeypatch, ["yes", "/exit"], answer="", interactive=True)

    assert calls == [], "OPEN-74's `yes` must not reach the planner unasked"
    assert path.read_bytes() == before


def test_replace_plans_the_request(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, ["pending"])

    _, calls = _repl(monkeypatch, ["add rate limiting", "/exit"], answer="r\n", interactive=True)

    assert len(calls) == 1
    assert calls[0]["task"] == "add rate limiting"
    assert not calls[0].get("resume")


def test_continue_at_the_question_resumes_instead(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, ["pending"])

    output, calls = _repl(
        monkeypatch, ["add rate limiting", "/exit"], answer="c\n", interactive=True
    )

    assert len(calls) == 1
    assert calls[0]["resume"] is True
    assert calls[0]["task"] == REQUEST


def test_no_question_without_a_terminal(monkeypatch, tmp_path):
    """Today's behaviour stands: plan() preserves the ledger and says so."""
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, ["pending"])

    output, calls = _repl(monkeypatch, ["add rate limiting", "/exit"], interactive=False)

    assert [c["task"] for c in calls] == ["add rate limiting"]
    assert "unfinished task" not in output


def test_no_question_in_plan_mode(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, ["pending"])

    output, calls = _repl(
        monkeypatch, ["add rate limiting", "/exit"], args=("--plan",), interactive=True
    )

    assert [c["task"] for c in calls] == ["add rate limiting"]
    assert "unfinished task" not in output


def test_no_question_when_nothing_is_pending(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, ["done", "blocked"])

    output, calls = _repl(monkeypatch, ["add rate limiting", "/exit"], interactive=True)

    assert [c["task"] for c in calls] == ["add rate limiting"]
    assert "unfinished task" not in output


def test_the_question_runs_on_a_daemon_thread_off_the_loop(monkeypatch, tmp_path):
    """OPEN-2's reason, at the REPL's own prompt: prompt_toolkit's inline
    driver cannot start inside the running loop, and a non-daemon thread
    is joined at exit."""
    from rudra import cli as cli_module

    monkeypatch.chdir(tmp_path)
    _ledger(tmp_path, ["pending"])
    seen: dict[str, bool] = {}

    def recording(ledger, console, **kwargs):
        current = threading.current_thread()
        seen["daemon"] = current.daemon
        seen["off_main"] = current is not threading.main_thread()
        return ReplaceChoice.CANCEL

    monkeypatch.setattr(cli_module, "ask_replace", recording)
    _repl(monkeypatch, ["add rate limiting", "/exit"], interactive=True)

    assert seen == {"daemon": True, "off_main": True}


# --- what the REPL tells a user who stopped -------------------------------


def test_a_cancelled_repl_turn_names_the_repl_spelling(monkeypatch, tmp_path):
    """Inside the REPL, `rudra --continue` is a command it cannot run."""
    from typer.testing import CliRunner

    from rudra import cli as cli_module
    from rudra.cli import app

    monkeypatch.chdir(tmp_path)
    remaining = ["do the thing", "/exit"]

    async def scripted(session, prompt_text):
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    class _Cancelled:
        async def run(self):
            raise asyncio.CancelledError

        async def close(self):
            return None

    async def factory(**kwargs):
        return _Cancelled()

    monkeypatch.setattr(cli_module, "_prompt_input", scripted)
    monkeypatch.setattr(cli_module, "create_main_agent", factory)
    monkeypatch.setattr(cli_module, "print_banner", lambda: None)
    output = CliRunner().invoke(app, ["--auto"]).output

    assert "type continue" in output


def test_the_provider_failure_line_names_both_spellings():
    """`_provider_failure_message` cannot tell where it is running."""
    from rudra.agent.main_agent import _provider_failure_message
    from rudra.loop.ledger import Ledger, Task, TaskStatus

    ledger = Ledger()
    ledger.tasks.append(Task(id="t1", description="x", status=TaskStatus.PENDING))
    text = _provider_failure_message(RuntimeError("500"), ledger)

    assert "rudra --continue" in text
    assert "`continue` in the REPL" in text


# --- the one off-loop helper ----------------------------------------------


async def test_ask_off_loop_returns_the_answer_from_a_daemon_thread():
    from rudra.ui.prompt import ask_off_loop

    def question():
        current = threading.current_thread()
        return (current.daemon, current is threading.main_thread())

    assert await ask_off_loop(question, name="t") == (True, False)


async def test_ask_off_loop_surfaces_what_the_question_raised():
    from rudra.ui.prompt import ask_off_loop

    def question():
        raise RuntimeError("terminal exploded")

    with pytest.raises(RuntimeError, match="terminal exploded"):
        await ask_off_loop(question, name="t")
