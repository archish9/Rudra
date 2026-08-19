"""What a run cost, accumulated in one place (Step 12b, C7.5).

RunUsage is pure and shared by reference, the same shape as the ledger and
the fact store: one object per run, mutated by every agent, read once at
the end.
"""

from __future__ import annotations

from rudra.context.usage import RunUsage, render_usage


def test_a_fresh_accumulator_has_no_roles():
    assert RunUsage().roles() == ()


def test_recording_accumulates_per_role():
    usage = RunUsage()
    usage.record("coder", input_tokens=100, output_tokens=10)
    usage.record("coder", input_tokens=250, output_tokens=30)
    usage.record("planner", input_tokens=7, output_tokens=1)

    data = usage.as_dict()
    assert data["coder"] == {
        "calls": 2,
        "input_tokens": 350,
        "output_tokens": 40,
        "compactions": 0,
    }
    assert data["planner"]["calls"] == 1


def test_roles_come_back_in_the_order_they_first_appeared():
    """The panel reads top to bottom; planner-then-coder is the run's order."""
    usage = RunUsage()
    usage.record("planner", input_tokens=1, output_tokens=1)
    usage.record("coder", input_tokens=1, output_tokens=1)
    usage.record("planner", input_tokens=1, output_tokens=1)
    assert usage.roles() == ("planner", "coder")


def test_a_provider_reporting_nothing_stays_none_not_zero():
    """A zero is indistinguishable from a free run and would be read as one."""
    usage = RunUsage()
    usage.record("coder", input_tokens=None, output_tokens=None)

    assert usage.as_dict()["coder"]["calls"] == 1
    assert usage.as_dict()["coder"]["input_tokens"] is None
    assert usage.as_dict()["coder"]["output_tokens"] is None


def test_a_partial_report_counts_what_it_has():
    """Some providers give input and not output. Keep the half that is real."""
    usage = RunUsage()
    usage.record("coder", input_tokens=None, output_tokens=None)
    usage.record("coder", input_tokens=100, output_tokens=None)

    assert usage.as_dict()["coder"]["input_tokens"] == 100
    assert usage.as_dict()["coder"]["output_tokens"] is None


def test_compactions_are_counted_per_role():
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    usage.record_compaction("coder")
    usage.record_compaction("coder")
    assert usage.as_dict()["coder"]["compactions"] == 2


def test_a_compaction_alone_still_registers_the_role():
    """Order of arrival must not decide whether a role is reportable."""
    usage = RunUsage()
    usage.record_compaction("tester")
    assert usage.roles() == ("tester",)


def test_render_is_empty_when_nothing_was_recorded():
    assert render_usage(RunUsage()) == ""


def test_render_is_empty_for_none():
    """Every caller must survive a run that never built an accumulator."""
    assert render_usage(None) == ""


def test_render_names_each_role_and_its_numbers():
    usage = RunUsage()
    usage.record("planner", input_tokens=41204, output_tokens=3118)
    usage.record("coder", input_tokens=88930, output_tokens=12455)
    usage.record_compaction("coder")

    text = render_usage(usage)
    assert "planner" in text
    assert "41,204" in text
    assert "coder" in text
    assert "1 compaction" in text


def test_render_says_not_reported_rather_than_zero():
    usage = RunUsage()
    usage.record("coder", input_tokens=None, output_tokens=None)

    text = render_usage(usage)
    assert "not reported" in text
    assert " 0 in" not in text


def test_module_imports_nothing_from_rudra():
    """Same rule as budget.py: pure, testable without an agent."""
    import ast
    import pathlib

    import rudra.context.usage as usage_module

    tree = ast.parse(pathlib.Path(usage_module.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not [name for name in imported if name.startswith("rudra")]
