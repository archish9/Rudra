"""The three ledger tools -- and the one they deliberately are not."""

from __future__ import annotations

import json

from rudra.loop.ledger import Ledger, TaskStatus
from rudra.loop.tools import create_ledger_tools


def tools_for(tmp_path, ledger=None):
    ledger = ledger if ledger is not None else Ledger()
    path = tmp_path / "ledger.json"
    return {tool.name: tool for tool in create_ledger_tools(ledger, path)}, ledger, path


def test_exactly_three_tools(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    assert set(tools) == {"add_tasks", "drop_task", "read_ledger"}


def test_no_tool_takes_a_status_argument(tmp_path):
    # The invariant that enforces D9: only engine.py may write DONE or
    # BLOCKED. Checked against the tools' *parameters* -- prose in a
    # docstring is not a capability, and asserting on it would fail the
    # moment a description said "done" in passing.
    tools, _, _ = tools_for(tmp_path)
    for tool in tools.values():
        properties = tool.args_schema.model_json_schema().get("properties", {})
        assert "status" not in properties, f"{tool.name} lets the model set a status"
        for name, spec in properties.items():
            allowed = json.dumps(spec.get("enum", [])).lower()
            assert "done" not in allowed, f"{tool.name}.{name} can be set to done"
            assert "blocked" not in allowed, f"{tool.name}.{name} can be set to blocked"


def test_the_only_status_a_tool_can_write_is_dropped(tmp_path):
    # drop_task is the single exception, and it is deliberate: without it a
    # mistaken task burns the whole attempt budget. Bounded by visibility --
    # a reason is required and every drop is reported (spec §3).
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a", "b"]})
    tools["drop_task"].invoke({"task_id": "t1", "reason": "not needed"})
    written = {task.status for task in ledger.tasks}
    assert written == {TaskStatus.DROPPED, TaskStatus.PENDING}


def test_add_tasks_appends_and_persists(tmp_path):
    tools, ledger, path = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["write the parser", "write its tests"]})
    assert [task.description for task in ledger.tasks] == [
        "write the parser",
        "write its tests",
    ]
    assert Ledger.load(path).tasks == ledger.tasks


def test_add_tasks_reports_the_ids_it_assigned(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    result = tools["add_tasks"].invoke({"descriptions": ["a", "b"]})
    assert "t1" in result
    assert "t2" in result


def test_add_tasks_rejects_an_empty_list(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    result = tools["add_tasks"].invoke({"descriptions": []})
    assert "REJECTED" in result
    assert ledger.tasks == []


def test_add_tasks_ignores_blank_descriptions(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["real work", "   ", ""]})
    assert [task.description for task in ledger.tasks] == ["real work"]


def test_drop_task_marks_and_records_the_reason(tmp_path):
    tools, ledger, path = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a"]})
    tools["drop_task"].invoke({"task_id": "t1", "reason": "already handled by t0"})
    assert ledger.get("t1").status is TaskStatus.DROPPED
    assert "already handled" in ledger.get("t1").note
    assert Ledger.load(path).get("t1").status is TaskStatus.DROPPED


def test_drop_task_refuses_an_unknown_id(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    assert "REJECTED" in tools["drop_task"].invoke({"task_id": "t9", "reason": "x"})


def test_drop_task_refuses_a_finished_task(tmp_path):
    # Dropping something the gate already passed would rewrite history.
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a"]})
    ledger.get("t1").status = TaskStatus.DONE
    assert "REJECTED" in tools["drop_task"].invoke({"task_id": "t1", "reason": "x"})
    assert ledger.get("t1").status is TaskStatus.DONE


def test_the_settled_refusal_names_the_move_that_works(tmp_path):
    """OPEN-24. `REJECTED: t1 is already blocked.` was a dead end.

    A settled status is terminal -- nothing in engine.py moves a task out of
    one -- so the identical call fails identically forever. The refusal has
    to say so and name `add_tasks`, or the model has nothing to steer toward
    and re-issues it, which is exactly what a 550B planner did three times.
    """
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a"]})
    ledger.get("t1").status = TaskStatus.BLOCKED

    refusal = tools["drop_task"].invoke({"task_id": "t1", "reason": "x"})

    assert "REJECTED" in refusal
    assert "add_tasks" in refusal, "the refusal must name the move that works"
    assert "identical" in refusal, "and say the same call will fail the same way"


def test_the_settled_refusal_covers_every_settled_status(tmp_path):
    # DONE, BLOCKED and DROPPED are one rule, and a refusal that only reads
    # right for one of them is a refusal a model meets in the wrong wording.
    for status in (TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.DROPPED):
        tools, ledger, _ = tools_for(tmp_path)
        tools["add_tasks"].invoke({"descriptions": ["a"]})
        ledger.get("t1").status = status

        refusal = tools["drop_task"].invoke({"task_id": "t1", "reason": "x"})

        assert status.value in refusal, "name the status it is actually in"
        assert "add_tasks" in refusal


def test_drop_task_requires_a_reason(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a"]})
    assert "REJECTED" in tools["drop_task"].invoke({"task_id": "t1", "reason": "  "})
    assert ledger.get("t1").status is TaskStatus.PENDING


def test_read_ledger_shows_every_task_and_its_status(tmp_path):
    tools, ledger, _ = tools_for(tmp_path)
    tools["add_tasks"].invoke({"descriptions": ["a", "b"]})
    ledger.get("t1").status = TaskStatus.DONE
    rendered = tools["read_ledger"].invoke({})
    assert "t1" in rendered and "done" in rendered
    assert "t2" in rendered and "pending" in rendered


def test_read_ledger_says_so_when_empty(tmp_path):
    tools, _, _ = tools_for(tmp_path)
    assert "no tasks" in tools["read_ledger"].invoke({}).lower()
