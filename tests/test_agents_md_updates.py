"""The two writers behind C7.3: Python per task, one model call at the end."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from rudra.context.agents_md import section_body
from rudra.loop.engine import record_task_in_memory, summarise_architecture
from rudra.loop.ledger import Ledger, TaskStatus

STARTER = """# Project Memory

## Project Facts
language = Python

## Project Structure
(not yet built)

## Architecture Notes
(none yet)

## Session Log
(no sessions yet)
"""


@dataclass
class FakePaths:
    root: Path

    @property
    def agents_md(self) -> Path:
        return self.root / "AGENTS.md"


def _paths(tmp_path: Path) -> FakePaths:
    (tmp_path / "AGENTS.md").write_text(STARTER, encoding="utf-8")
    return FakePaths(root=tmp_path)


def _context(tmp_path: Path, paths: Any, model: Any):
    @dataclass
    class Ctx:
        project_path: Path
        console: Console
        cfg: Any
        paths: Any
        subagents: Any = None
        usage: Any = None
        _model: Any = None

    ctx = Ctx(
        project_path=tmp_path,
        console=Console(quiet=True),
        cfg=object(),
        paths=paths,
    )
    ctx._model = model
    return ctx


def test_a_done_task_lands_in_the_session_log(tmp_path):
    paths = _paths(tmp_path)
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.files_touched = ("src/parser.py",)

    record_task_in_memory(paths, task)

    body = section_body(paths.agents_md.read_text(encoding="utf-8"), "Session Log")
    assert "write the parser" in body
    assert "src/parser.py" in body


def test_recording_leaves_the_other_sections_alone(tmp_path):
    paths = _paths(tmp_path)
    ledger = Ledger()
    record_task_in_memory(paths, ledger.add("t"))

    text = paths.agents_md.read_text(encoding="utf-8")
    assert section_body(text, "Project Facts").strip() == "language = Python"


def test_recording_without_an_agents_md_does_nothing(tmp_path):
    """No AGENTS.md means no run created one. Never invent it here."""
    paths = FakePaths(root=tmp_path)
    ledger = Ledger()

    record_task_in_memory(paths, ledger.add("t"))  # must not raise

    assert not paths.agents_md.exists()


def test_recording_never_ends_a_run(tmp_path):
    """A finished task must not be undone by a memory write."""
    paths = FakePaths(root=tmp_path)
    paths.agents_md.mkdir()  # a directory where the file should be
    ledger = Ledger()

    record_task_in_memory(paths, ledger.add("t"))  # must not raise


def test_the_summariser_writes_architecture_notes(tmp_path):
    paths = _paths(tmp_path)
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.status = TaskStatus.DONE

    class FakeModel:
        async def ainvoke(self, messages):
            @dataclass
            class Reply:
                content: str

            return Reply(content="The parser is hand-rolled and recursive-descent.")

    context = _context(tmp_path, paths, FakeModel())
    asyncio.run(summarise_architecture(context, ledger))

    body = section_body(paths.agents_md.read_text(encoding="utf-8"), "Architecture Notes")
    assert "recursive-descent" in body


def test_the_summariser_is_skipped_when_nothing_finished(tmp_path):
    paths = _paths(tmp_path)

    class Exploding:
        async def ainvoke(self, messages):
            raise AssertionError("no model call when no task reached DONE")

    context = _context(tmp_path, paths, Exploding())
    asyncio.run(summarise_architecture(context, Ledger()))

    text = paths.agents_md.read_text(encoding="utf-8")
    assert section_body(text, "Architecture Notes").strip() == "(none yet)"


def test_a_failing_summariser_costs_polish_not_the_run(tmp_path):
    """The deterministic entries are already on disk. That is the point."""
    paths = _paths(tmp_path)
    ledger = Ledger()
    task = ledger.add("write the parser")
    task.status = TaskStatus.DONE

    class Exploding:
        async def ainvoke(self, messages):
            raise RuntimeError("provider down")

    context = _context(tmp_path, paths, Exploding())
    asyncio.run(summarise_architecture(context, ledger))  # must not raise


# --- OPEN-115: the summariser's only retry layer is Rudra's now -------------
#
# `summarise_architecture` calls the model directly, outside any agent graph,
# so it never passed through ModelRetryMiddleware -- the client SDK's retries
# were its only ones. Those are switched off for every provider, so without
# this a single 500 at run end costs the Architecture Notes.


class _Status(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"Error code: {code}")
        self.status_code = code


class _Sink:
    def __init__(self) -> None:
        self.notices: list[dict] = []

    def notice(self, payload, *, role, name="", namespace=(), index=0, at=0.0):
        self.notices.append({"payload": payload, "role": role, "name": name})


def _flaky_model(failures: int):
    @dataclass
    class Reply:
        content: str

    class Flaky:
        calls = 0

        async def ainvoke(self, messages):
            Flaky.calls += 1
            if Flaky.calls <= failures:
                raise _Status(503)
            return Reply(content="The parser is hand-rolled and recursive-descent.")

    return Flaky()


def _no_sleep(monkeypatch) -> None:
    async def _instant(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


def test_a_transient_summariser_failure_is_retried(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    paths = _paths(tmp_path)
    ledger = Ledger()
    ledger.add("write the parser").status = TaskStatus.DONE
    model = _flaky_model(failures=2)

    asyncio.run(summarise_architecture(_context(tmp_path, paths, model), ledger))

    body = section_body(paths.agents_md.read_text(encoding="utf-8"), "Architecture Notes")
    assert "recursive-descent" in body
    assert type(model).calls == 3


def test_a_summariser_retry_reaches_the_trace(tmp_path, monkeypatch):
    """Noticed, under the name telemetry already gives this call."""
    _no_sleep(monkeypatch)
    paths = _paths(tmp_path)
    ledger = Ledger()
    ledger.add("write the parser").status = TaskStatus.DONE
    context = _context(tmp_path, paths, _flaky_model(failures=1))
    sink = _Sink()

    @dataclass
    class Subagents:
        trace: Any
        telemetry: Any = None

    context.subagents = Subagents(trace=sink)

    asyncio.run(summarise_architecture(context, ledger))

    assert [(n["name"], n["role"]) for n in sink.notices] == [("retry", "summariser")]
    assert "503" in sink.notices[0]["payload"]


def test_a_non_transient_summariser_failure_is_not_retried(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    paths = _paths(tmp_path)
    ledger = Ledger()
    ledger.add("write the parser").status = TaskStatus.DONE

    class Unauthorised:
        calls = 0

        async def ainvoke(self, messages):
            Unauthorised.calls += 1
            raise _Status(401)

    asyncio.run(summarise_architecture(_context(tmp_path, paths, Unauthorised()), ledger))

    assert Unauthorised.calls == 1
