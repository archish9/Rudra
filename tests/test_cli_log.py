"""`rudra log` replays a transcript through the renderer the run used.

Through the SAME render(), not a second formatter -- so the replay and the
live view cannot drift apart, and escaping (A1.67) comes free rather than
being re-implemented and re-forgotten.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from rudra.cli import app

runner = CliRunner()


def _event(**kwargs):
    base = {
        "kind": "tool_call",
        "role": "coder",
        "namespace": [],
        "index": 1,
        "name": "write_file",
        "payload": "{}",
        "at": 0.0,
    }
    return {**base, **kwargs}


def _transcript(project: Path, session: str, events: list[dict]) -> Path:
    directory = project / ".rudra" / "run" / "transcripts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session}.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    return path


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    return project


def test_log_last_renders_the_newest_transcript(tmp_path: Path):
    project = _project(tmp_path)
    old = _transcript(project, "aaa", [_event(name="old_tool")])
    new = _transcript(project, "bbb", [_event(name="new_tool")])
    import os

    os.utime(old, (1, 1))
    os.utime(new, (2, 2))

    result = runner.invoke(app, ["log", "--last", "--project-dir", str(project)])

    assert result.exit_code == 0
    assert "new_tool" in result.output
    assert "old_tool" not in result.output


def test_log_can_name_a_run(tmp_path: Path):
    project = _project(tmp_path)
    _transcript(project, "aaa", [_event(name="old_tool")])
    _transcript(project, "bbb", [_event(name="new_tool")])

    result = runner.invoke(app, ["log", "--run", "aaa", "--project-dir", str(project)])

    assert "old_tool" in result.output
    assert "new_tool" not in result.output


def test_log_can_filter_by_role(tmp_path: Path):
    project = _project(tmp_path)
    _transcript(
        project,
        "aaa",
        [_event(role="coder", name="coder_tool"), _event(role="planner", name="planner_tool")],
    )

    result = runner.invoke(
        app, ["log", "--last", "--role", "planner", "--project-dir", str(project)]
    )

    assert "planner_tool" in result.output
    assert "coder_tool" not in result.output


def test_log_with_no_transcripts_says_so_and_does_not_crash(tmp_path: Path):
    project = _project(tmp_path)

    result = runner.invoke(app, ["log", "--last", "--project-dir", str(project)])

    assert result.exit_code != 0
    assert "no transcript" in result.output.lower()


def test_log_lists_the_runs_when_given_neither_flag(tmp_path: Path):
    """Bare `rudra log` is a question -- which runs are there? -- not a
    command to dump the newest one."""
    project = _project(tmp_path)
    _transcript(project, "aaa", [_event()])
    _transcript(project, "bbb", [_event()])

    result = runner.invoke(app, ["log", "--project-dir", str(project)])

    assert result.exit_code == 0
    assert "aaa" in result.output
    assert "bbb" in result.output


def test_an_unknown_run_id_is_an_error_not_an_empty_replay(tmp_path: Path):
    """Silence would read as "that run did nothing" rather than "no such
    run"."""
    project = _project(tmp_path)
    _transcript(project, "aaa", [_event()])

    result = runner.invoke(app, ["log", "--run", "zzz", "--project-dir", str(project)])

    assert result.exit_code != 0


def test_markup_in_a_recorded_payload_survives_the_replay(tmp_path: Path):
    """A1.67's rule, inherited for free: the replay goes through the same
    render() the live trace does."""
    project = _project(tmp_path)
    _transcript(
        project,
        "aaa",
        [_event(kind="tool_result", name="read_ledger", payload="t1  [pending]  parse")],
    )

    result = runner.invoke(app, ["log", "--last", "--project-dir", str(project)])

    assert "[pending]" in result.output


def test_a_subagents_namespace_is_visible_in_the_replay(tmp_path: Path):
    project = _project(tmp_path)
    _transcript(project, "aaa", [_event(namespace=["tools", "task:1"], name="write_file")])

    result = runner.invoke(app, ["log", "--last", "--project-dir", str(project)])

    assert "task:1" in result.output


def test_a_truncated_transcript_still_replays_what_it_has(tmp_path: Path):
    """The file a killed run leaves is exactly the one worth reading."""
    project = _project(tmp_path)
    directory = project / ".rudra" / "run" / "transcripts"
    directory.mkdir(parents=True)
    (directory / "aaa.jsonl").write_text(
        json.dumps(_event(name="survived")) + "\n" + '{"kind": "tool_ca'
    )

    result = runner.invoke(app, ["log", "--last", "--project-dir", str(project)])

    assert result.exit_code == 0
    assert "survived" in result.output
