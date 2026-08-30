"""What a run cost, accumulated in one place (Step 12b, C7.5).

RunUsage is pure and shared by reference, the same shape as the ledger and
the fact store: one object per run, mutated by every agent, read once at
the end.
"""

from __future__ import annotations

import pytest

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
        # Added by OPEN-45: how often the model call this row counts had to
        # be re-issued. It rides invisibly inside `calls` otherwise --
        # ModelRetryMiddleware sits OUTSIDE UsageMiddleware by design, so
        # each attempt is one recorded call and a reader finding calls=4
        # against three model turns cannot tell a retry from a miscount.
        "retries": 0,
        # Added in Step 15a: wall clock, the one number a local backend
        # always has -- token counts are frequently absent (C9.6).
        "seconds": 0.0,
        # Added in Step 14b: the recall block's cost, isolated because it
        # otherwise rides invisibly inside input_tokens (spec 4.6).
        "recall_chars": 0,
        "recall_injections": 0,
        # Added by OPEN-39: the project listing's cost, isolated for the
        # reason the recall block's is -- it is a FIXED per-call prompt
        # cost, and the trade it makes is model calls bought with tokens.
        "tree_chars": 0,
        "tree_injections": 0,
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


def test_retries_are_counted_per_role():
    """OPEN-45. Mirrors compactions, for the reason it mirrors it: the
    retry is outside the usage middleware, so `calls` already absorbs it
    and nothing labels which of those calls were re-issued."""
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    usage.record_retry("coder")
    usage.record_retry("coder")
    assert usage.as_dict()["coder"]["retries"] == 2


def test_retries_are_not_shared_between_roles():
    """A failing coder endpoint and a healthy planner are a different
    problem from a provider that is down -- which is the whole reason
    ModelRetryMiddleware carries a role at all."""
    usage = RunUsage()
    usage.record_retry("coder")
    assert usage.as_dict()["coder"]["retries"] == 1
    assert usage.as_dict().get("planner") is None


def test_a_retry_alone_still_registers_the_role():
    """A role whose FIRST model call failed has retried before it has
    recorded, so arrival order must not decide whether it is reportable."""
    usage = RunUsage()
    usage.record_retry("tester")
    assert usage.roles() == ("tester",)


def test_render_is_silent_about_retries_when_there_were_none():
    """The regression that matters most: a clean run's panel must be
    byte-identical to the one before OPEN-45, or every existing assertion
    about it becomes noise."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)

    assert "retr" not in render_usage(usage)


def test_render_names_retries_when_they_happened():
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)
    usage.record_retry("coder")
    assert "1 retry" in render_usage(usage)

    usage.record_retry("coder")
    assert "2 retries" in render_usage(usage)


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


def test_a_silent_provider_says_not_reported_once_not_twice():
    """ "not reported in / not reported out" reads as two absences, not one."""
    usage = RunUsage()
    usage.record("coder", input_tokens=None, output_tokens=None)
    assert render_usage(usage).count("not reported") == 1


def test_a_half_silent_provider_still_shows_both_sides():
    usage = RunUsage()
    usage.record("coder", input_tokens=100, output_tokens=None)
    text = render_usage(usage)
    assert "100 in" in text
    assert "not reported out" in text


def test_recall_cost_is_tracked_separately_from_the_calls_it_rides_in() -> None:
    """Spec 4.6: the recall block's budget must be defensible with numbers.

    It is part of input_tokens on every call, so nothing else can isolate
    it -- and a constant nobody can measure is a constant nobody can
    revise with evidence.
    """
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.record_recall("coder", 1263)
    assert usage.as_dict()["coder"]["recall_chars"] == 1263


def test_recall_cost_accumulates_across_builds() -> None:
    """The block is rebuilt on every agent construction, not once."""
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.record_recall("coder", 100)
    usage.record_recall("coder", 200)
    assert usage.as_dict()["coder"]["recall_chars"] == 300
    assert usage.as_dict()["coder"]["recall_injections"] == 2


def test_a_role_that_never_recalled_reports_zero_not_none() -> None:
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.record("coder", input_tokens=5, output_tokens=5)
    assert usage.as_dict()["coder"]["recall_chars"] == 0


def test_seconds_accumulate_per_role():
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1, seconds=1.5)
    usage.record("coder", input_tokens=1, output_tokens=1, seconds=2.25)
    assert usage.as_dict()["coder"]["seconds"] == 3.75


def test_a_provider_that_reports_no_tokens_still_reports_seconds():
    """The local-first case: tokens are often absent, wall clock never is."""
    usage = RunUsage()
    usage.record("coder", input_tokens=None, output_tokens=None, seconds=4.0)
    rendered = render_usage(usage)
    assert "not reported" in rendered
    assert "4.0s" in rendered


def test_a_role_with_no_measured_time_says_nothing_about_it():
    """0.0s is noise in a panel read at a glance -- the facts_block rule."""
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    assert "0.0s" not in render_usage(usage)


def test_the_usage_block_carries_no_currency():
    """S15.2: cost is out of scope, permanently. Rudra is free and the
    provider bill is the user's; a bundled price table would go stale
    silently and be wrong for every proxy."""
    usage = RunUsage()
    usage.record("coder", input_tokens=100, output_tokens=10, seconds=1.0)
    rendered = render_usage(usage)
    assert "$" not in rendered
    assert "cost" not in rendered.lower()


def test_tree_cost_is_tracked_separately_from_the_calls_it_rides_in() -> None:
    """OPEN-39. The project listing is a FIXED per-call cost -- it is
    re-sent on every coder call whether or not the coder reads it -- and it
    rides inside input_tokens like the recall block does. The trade it
    makes is calls bought with tokens, and this is the number that says
    which side won."""
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.record_tree("coder", 1820)
    assert usage.as_dict()["coder"]["tree_chars"] == 1820


def test_tree_cost_accumulates_across_builds() -> None:
    """The listing is rebuilt on every agent construction, never cached:
    the coder writes the files it is shown."""
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.record_tree("coder", 100)
    usage.record_tree("coder", 200)
    assert usage.as_dict()["coder"]["tree_chars"] == 300
    assert usage.as_dict()["coder"]["tree_injections"] == 2


def test_a_role_that_never_got_a_listing_reports_zero_not_none() -> None:
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.record("reviewer", input_tokens=5, output_tokens=5)
    assert usage.as_dict()["reviewer"]["tree_chars"] == 0


# --- OPEN-40: seconds per call, derived in the renderer ---------------------


def test_render_divides_seconds_by_calls() -> None:
    """OPEN-40. `usage.json` has `calls` and `seconds`; the panel printed
    both and never divided them, so the number that says WHICH ROLE TO
    CHANGE was left as arithmetic for the reader. Run6's coder: 114 calls
    over 1108.502s."""
    usage = RunUsage()
    for _ in range(4):
        usage.record("coder", input_tokens=10, output_tokens=1, seconds=0.5)

    assert "0.50s/call" in render_usage(usage)


def test_seconds_per_call_is_never_stored() -> None:
    """Derived in the renderer, never in the schema: two representations of
    one fact drift, and this project has a ledger full of examples. A
    division in render_usage cannot disagree with the numbers it divides."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1, seconds=2.0)

    assert "s/call" not in str(usage.as_dict()["coder"].keys())
    assert set(usage.as_dict()["coder"]) == {
        "calls",
        "input_tokens",
        "output_tokens",
        "compactions",
        "retries",
        "recall_chars",
        "recall_injections",
        "tree_chars",
        "tree_injections",
        "seconds",
    }


def test_render_omits_seconds_per_call_for_a_role_that_made_no_calls() -> None:
    """The only new failure mode OPEN-40 introduces, and its document says
    to write this test first. A slot can exist with `calls == 0`:
    `record_recall`, `record_tree` and `record_retry` all reach `_slot`
    without recording a call, so the denominator is reachable at zero."""
    from rudra.context.usage import RoleUsage

    usage = RunUsage()
    usage.per_role["coder"] = RoleUsage(calls=0, seconds=5.0)

    rendered = render_usage(usage)

    assert "s/call" not in rendered
    assert "coder" in rendered


# --- the two clocks (OPEN-53) ----------------------------------------------


def test_a_fresh_run_reports_no_suspension() -> None:
    """Both clocks start together, so their difference starts at zero.

    This is the guard for the whole feature: `wall - mono` is not an
    estimate of anything, it is exactly the time the process was not
    running, and on a machine that never slept it must be 0.
    """
    usage = RunUsage()

    assert usage.suspended_seconds(wall_now=usage.started_wall, mono_now=usage.started_mono) == 0.0


def test_suspension_is_the_difference_between_the_two_clocks() -> None:
    """run10's numbers, which is what this exists for: 4888s of wall clock
    against 1243s the monotonic clock counted. macOS `time.monotonic()` is
    `mach_absolute_time()`, which does not tick while the machine is
    suspended, so the gap IS the sleep -- no threshold, no inference."""
    usage = RunUsage(started_wall=1000.0, started_mono=50.0)

    suspended = usage.suspended_seconds(wall_now=1000.0 + 4888.0, mono_now=50.0 + 1243.0)

    assert suspended == pytest.approx(3645.0)


def test_a_clock_that_ran_backwards_reports_no_suspension() -> None:
    """`time.time()` is not monotonic: an NTP step backwards would make the
    subtraction negative, and a negative "suspended" is a nonsense the
    panel must never print. Clamped at the source, not at the renderer."""
    usage = RunUsage(started_wall=1000.0, started_mono=50.0)

    assert usage.suspended_seconds(wall_now=1000.0 + 10.0, mono_now=50.0 + 30.0) == 0.0


def test_render_is_silent_about_suspension_on_a_normal_run() -> None:
    """The regression that matters most, on the OPEN-45 precedent: a run
    that never slept must produce the panel it produced before OPEN-53."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)

    assert "suspend" not in render_usage(usage)


def test_render_names_suspension_when_the_machine_slept() -> None:
    import time

    usage = RunUsage(started_wall=time.time() - 4888.0, started_mono=time.monotonic() - 1243.0)
    usage.record("coder", input_tokens=10, output_tokens=1)

    rendered = render_usage(usage)

    assert "suspended" in rendered
    assert "3645" in rendered


def test_a_short_clock_step_is_below_the_notice_threshold() -> None:
    """SUSPENDED_NOTICE_SECONDS exists for NTP, not for sleep: a few
    seconds of clock adjustment is not a suspended machine and must not
    put a line in every panel."""
    import time

    from rudra.context.usage import SUSPENDED_NOTICE_SECONDS

    usage = RunUsage(
        started_wall=time.time() - 100.0,
        started_mono=time.monotonic() - (100.0 - SUSPENDED_NOTICE_SECONDS / 2),
    )
    usage.record("coder", input_tokens=10, output_tokens=1)

    assert "suspend" not in render_usage(usage)


def test_the_log_shape_separates_the_roles_from_the_run() -> None:
    """usage.json gains a run block, and the roles move under `roles`
    rather than gaining a fifth sibling that reads as a role. It breaks
    every reader once and loudly, which is the point -- a "run" key beside
    "coder" would be counted as a role by anything that iterates."""
    usage = RunUsage(started_wall=1000.0, started_mono=50.0)
    usage.record("coder", input_tokens=10, output_tokens=1)

    log = usage.as_log(wall_now=1000.0 + 4888.0, mono_now=50.0 + 1243.0)

    assert set(log) == {"roles", "run"}
    assert log["roles"]["coder"]["input_tokens"] == 10
    assert log["run"]["wall_seconds"] == pytest.approx(4888.0)
    assert log["run"]["suspended_seconds"] == pytest.approx(3645.0)


def test_as_dict_still_returns_the_roles_alone() -> None:
    """`as_dict` is the per-role snapshot the panel reads; `as_log` is the
    file. Two shapes, two callers, so neither has to carry the other's
    concern."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)

    assert set(usage.as_dict()) == {"coder"}
