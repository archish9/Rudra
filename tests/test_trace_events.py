"""The trace vocabulary: one dataclass every consumer speaks (Step 15a, C9.1).

Frozen and pure for the reason RunUsage entries and Ledger tasks are: three
consumers -- console, `--debug` log, 15c's transcript -- read the same
object, and a mutable one would let the first change what the second sees.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from rudra.trace import TraceEvent, TraceKind, TraceLevel


def test_levels_order_from_quiet_to_verbose():
    assert TraceLevel.QUIET < TraceLevel.NORMAL < TraceLevel.VERBOSE


def test_an_event_carries_its_namespace_as_a_tuple():
    event = TraceEvent(
        kind=TraceKind.TOOL_CALL,
        role="coder",
        namespace=("tools", "task:1"),
        index=3,
        name="write_file",
        payload="{'file_path': 'a.py'}",
        at=1.5,
    )
    assert event.namespace == ("tools", "task:1")
    assert event.name == "write_file"


def test_as_dict_is_json_writable():
    event = TraceEvent(kind=TraceKind.AI_TEXT, role="planner", namespace=(), index=1, payload="hi")
    assert json.loads(json.dumps(event.as_dict())) == {
        "kind": "ai_text",
        "role": "planner",
        "namespace": [],
        "index": 1,
        "name": "",
        "payload": "hi",
        "at": 0.0,
    }


def test_events_are_frozen():
    event = TraceEvent(kind=TraceKind.USER, role="planner", namespace=(), index=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.payload = "changed"
