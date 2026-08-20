"""Rendering is pure, and it escapes (Step 15a, C9.1 / A1.67).

Model output and tool results are DATA. Printed through Rich unescaped,
`[pending]` is a style tag and vanishes -- which is exactly what A1.67
measured on read_ledger's output: the user saw `t1    write the parser`
with no status, the one thing the tool exists to report.
"""

from __future__ import annotations

from rudra.trace import TraceEvent, TraceKind, TraceLevel
from rudra.trace.render import AI_TEXT_CHARS, render


def _event(kind, **kwargs):
    base = {"role": "coder", "namespace": (), "index": 1}
    return TraceEvent(kind=kind, **{**base, **kwargs})


def test_a_tool_result_containing_markup_survives_rendering():
    event = _event(TraceKind.TOOL_RESULT, name="read_ledger", payload="t1  [pending]  parse")
    line = "\n".join(render(event, level=TraceLevel.NORMAL))
    assert r"\[pending]" in line


def test_a_tool_call_names_the_tool_and_the_role():
    event = _event(TraceKind.TOOL_CALL, name="write_file", payload="{'file_path': 'a.py'}")
    line = "\n".join(render(event, level=TraceLevel.NORMAL))
    assert "write_file" in line
    assert "coder" in line


def test_ai_text_is_hidden_at_normal_and_shown_at_verbose():
    event = _event(TraceKind.AI_TEXT, payload="thinking about the parser")
    assert render(event, level=TraceLevel.NORMAL) == []
    assert "thinking about the parser" in "\n".join(render(event, level=TraceLevel.VERBOSE))


def test_quiet_keeps_errors_and_drops_everything_else():
    error = _event(TraceKind.TOOL_ERROR, name="execute", payload="Error: boom")
    call = _event(TraceKind.TOOL_CALL, name="execute", payload="{}")
    assert render(error, level=TraceLevel.QUIET) != []
    assert render(call, level=TraceLevel.QUIET) == []


def test_long_payloads_truncate_at_normal_and_survive_at_verbose():
    long = "x" * (AI_TEXT_CHARS * 3)
    event = _event(TraceKind.TOOL_RESULT, name="execute", payload=long)
    normal = "\n".join(render(event, level=TraceLevel.NORMAL))
    verbose = "\n".join(render(event, level=TraceLevel.VERBOSE))
    assert len(normal) < len(long)
    assert "+" in normal  # the "… +N chars" marker
    assert long in verbose


def test_a_subagent_line_names_its_namespace():
    event = _event(TraceKind.TOOL_CALL, namespace=("task:1",), name="write_file", payload="{}")
    assert "task:1" in "\n".join(render(event, level=TraceLevel.NORMAL))


def test_a_tool_name_containing_markup_is_escaped_too():
    """The tool name is model-supplied on a hallucinated call, so it is
    data on exactly the same footing as the payload."""
    event = _event(TraceKind.TOOL_CALL, name="[bold]write", payload="{}")
    assert r"\[bold]write" in "\n".join(render(event, level=TraceLevel.NORMAL))
