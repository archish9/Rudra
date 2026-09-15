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
        # Added by OPEN-46 (reopened): the calls that ran OUT of retries.
        # `retries` counts the attempts that were re-issued and recovered;
        # this counts the ones the provider never served, which is the
        # numerator the run's failure rate was silently missing.
        "exhaustions": 0,
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
        # Added by OPEN-39 Phase 2: guarded reads the repeat guard answered
        # instead of running. Asserted at zero rather than omitted, because
        # a key that appears only on runs that deduped is a key every
        # reader of usage.json has to guard.
        "reads_deduped": 0,
        # Added by OPEN-60: writes the repeat guard skipped because the
        # bytes were already on disk, and what they would have cost. Two
        # fields for the reason recall_chars has two: the claim is OUTPUT
        # tokens, and a bare count of 3 cannot say 300 chars or 34,000.
        "writes_skipped": 0,
        "writes_skipped_chars": 0,
        # Added by OPEN-92: edits whose `old_string` carried read_file's
        # two-space gutter as indentation and was repaired against the
        # file's own bytes. Zero here for the reason reads_deduped is --
        # a key that appears only on runs that repaired one is a key every
        # reader of usage.json has to guard.
        "edits_reindented": 0,
        "plans_refused": 0,
        # Added by OPEN-93: writes that put a project-absolute path into
        # source a real interpreter later runs, explained in the tool's own
        # result. Zero here for the same reason -- read beside plans_refused,
        # 0 means the _PATH_RULES block kept the model off the bad spelling.
        "content_paths_flagged": 0,
        # Added by OPEN-97: writes refused for carrying the model's closing
        # summary instead of the file. Always present for the same reason --
        # a key that appears only on runs that refused one is a key every
        # reader of usage.json has to guard.
        "writes_rejected_as_prose": 0,
        # Added by OPEN-99: test files refused for a name the runner cannot
        # collect. Always present for the same reason as the line above.
        "test_writes_rejected": 0,
        "planner_halts": 0,
        "planner_writes_refused": 0,
        # Added by OPEN-103: calls to a shell or completion tool the agent
        # does not have, answered with the route instead of the tool list.
        "tool_routes_answered": 0,
        # Added by OPEN-104: writes refused for being a file whose only
        # content announces the work is finished.
        "completion_files_refused": 0,
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
        "exhaustions",
        "recall_chars",
        "recall_injections",
        "tree_chars",
        "tree_injections",
        "reads_deduped",
        "writes_skipped",
        "writes_skipped_chars",
        "edits_reindented",
        "plans_refused",
        "content_paths_flagged",
        "writes_rejected_as_prose",
        "test_writes_rejected",
        "planner_halts",
        "planner_writes_refused",
        "tool_routes_answered",
        "completion_files_refused",
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


def test_deduped_reads_are_counted_per_role():
    """OPEN-39 Phase 2. Mirrors retries, and for a sharper version of the
    same reason: a re-read the guard answered is a model call that never
    happened, so it leaves no mark on calls, tokens or seconds. Nothing
    else in this file could show it."""
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    usage.record_dedupe("coder")
    usage.record_dedupe("coder")
    assert usage.as_dict()["coder"]["reads_deduped"] == 2


def test_deduped_reads_are_not_shared_between_roles():
    """The planner re-reads too -- 9 of run 83f34f50210c's 41 -- and it is
    a different problem from a coder that re-reads its own writes."""
    usage = RunUsage()
    usage.record_dedupe("planner")
    assert usage.as_dict()["planner"]["reads_deduped"] == 1
    assert usage.as_dict().get("coder") is None


def test_a_run_with_no_deduped_reads_reports_zero_not_absence():
    """usage.json is read by scripts in this project's own ledger, so a
    key that appears only sometimes is a key every reader has to guard."""
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    assert usage.as_dict()["coder"]["reads_deduped"] == 0


def test_deduped_reads_reach_the_usage_log():
    """as_log nests as_dict under `roles`, so this is inherited rather than
    plumbed -- asserted because that inheritance is what OPEN-53 changed."""
    usage = RunUsage()
    usage.record_dedupe("coder")
    log = usage.as_log(wall_now=usage.started_wall, mono_now=usage.started_mono)
    assert log["roles"]["coder"]["reads_deduped"] == 1


def test_render_is_silent_about_deduped_reads():
    """It is a saving, not something to act on, so it belongs in the file
    and not in the panel -- and a clean run's panel stays byte-identical to
    the one before this landed, the same rule OPEN-45 kept."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)
    usage.record_dedupe("coder")

    assert "dedup" not in render_usage(usage)


def test_skipped_writes_are_counted_per_role_with_their_size():
    """OPEN-60 Half A. A rewrite the guard refused is OUTPUT tokens that
    were never emitted, so like a deduped read it leaves no mark on calls,
    tokens or seconds -- and unlike one, its size is the whole claim."""
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    usage.record_write_skipped("coder", 303)
    usage.record_write_skipped("coder", 1200)
    assert usage.as_dict()["coder"]["writes_skipped"] == 2
    assert usage.as_dict()["coder"]["writes_skipped_chars"] == 1503


def test_skipped_writes_do_not_touch_the_deduped_read_count():
    """Two claims, two numbers. OPEN-39 Phase 2 banked re-reads avoided;
    this banks rewrites avoided. One field carrying both would be neither."""
    usage = RunUsage()
    usage.record_write_skipped("coder", 10)
    assert usage.as_dict()["coder"]["reads_deduped"] == 0
    usage.record_dedupe("coder")
    assert usage.as_dict()["coder"]["writes_skipped"] == 1


def test_a_run_with_no_skipped_writes_reports_zero_not_absence():
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    assert usage.as_dict()["coder"]["writes_skipped"] == 0
    assert usage.as_dict()["coder"]["writes_skipped_chars"] == 0


def test_skipped_writes_reach_the_usage_log():
    """The RUN #7 checklist reads usage.json, not as_dict."""
    usage = RunUsage()
    usage.record_write_skipped("coder", 303)
    log = usage.as_log(wall_now=usage.started_wall, mono_now=usage.started_mono)
    assert log["roles"]["coder"]["writes_skipped"] == 1
    assert log["roles"]["coder"]["writes_skipped_chars"] == 303


def test_the_write_belief_map_is_shared_by_role_and_never_reported():
    """OPEN-62 6a. It is run state a guard reads across agent rebuilds, not
    an accounting number anything reports -- and `usage.json`'s schema is
    read by this repo's own ledger scripts, which iterate its roles."""
    from rudra.context.usage import RunUsage

    usage = RunUsage()
    usage.beliefs_for("coder")["app.py"] = "sha"
    usage.record("coder", input_tokens=1, output_tokens=1)

    assert usage.beliefs_for("coder") == {"app.py": "sha"}, "the map is returned live"
    assert usage.beliefs_for("tester") == {}, "one role's writes are not another's"
    assert "write_beliefs" not in usage.as_dict()
    assert "write_beliefs" not in usage.as_dict()["coder"]


# --- OPEN-46 (reopened): the retry that ran OUT ------------------------------
#
# `retries` counts a failed attempt that was RE-ISSUED. The attempt that
# exhausted the budget is a failure too, and it was counted nowhere -- so
# `p = retries / calls` was a lower bound and nothing said by how much.
# run14 lost a whole task to an exhaustion whose only surviving trace was a
# ledger note that every later branch overwrites.


def test_exhaustions_are_counted_per_role():
    """The number the item exists for: a failed attempt that was NOT
    retried, which `retries` structurally cannot hold."""
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    usage.record_exhaustion("coder")
    assert usage.as_dict()["coder"]["exhaustions"] == 1


def test_exhaustions_are_not_shared_between_roles():
    """A coder whose endpoint gave up and a healthy planner are the split
    ModelRetryMiddleware carries a role for."""
    usage = RunUsage()
    usage.record_exhaustion("coder")
    assert usage.as_dict()["coder"]["exhaustions"] == 1
    assert usage.as_dict().get("planner") is None


def test_an_exhaustion_alone_still_registers_the_role():
    """A role whose FIRST call exhausted has never recorded one."""
    usage = RunUsage()
    usage.record_exhaustion("tester")
    assert usage.roles() == ("tester",)


def test_a_run_with_no_exhaustion_reports_zero_not_absence():
    """§8.3: the key exists even at 0. A healthy endpoint reporting an
    honest zero is a result, not a missing instrument -- which is the
    OPEN-45 lesson this row inherits."""
    usage = RunUsage()
    usage.record("coder", input_tokens=1, output_tokens=1)
    assert usage.as_dict()["coder"]["exhaustions"] == 0


def test_exhaustions_reach_the_usage_log():
    """RUN #9's checklist reads usage.json, not as_dict."""
    usage = RunUsage()
    usage.record_exhaustion("coder")
    log = usage.as_log(wall_now=usage.started_wall, mono_now=usage.started_mono)
    assert log["roles"]["coder"]["exhaustions"] == 1


def test_render_is_silent_about_exhaustions_when_there_were_none():
    """Guarded exactly as retries and compactions are, so a clean run's
    panel is byte-identical to the one before this landed."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)
    assert "exhaust" not in render_usage(usage)


def test_render_names_exhaustions_when_they_happened():
    """It is worse than a retry and must not read as one: a retry cost
    seconds, an exhaustion cost the call."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)
    usage.record_exhaustion("coder")
    assert "1 exhaustion" in render_usage(usage)

    usage.record_exhaustion("coder")
    assert "2 exhaustions" in render_usage(usage)


def test_an_exhaustion_does_not_read_as_a_retry_in_the_panel():
    """`retr` is what the OPEN-45 silence test greps for, and an
    exhaustion must not trip it."""
    usage = RunUsage()
    usage.record("coder", input_tokens=10, output_tokens=1)
    usage.record_exhaustion("coder")
    assert "retr" not in render_usage(usage)


def test_venv_sync_cost_is_written_in_the_run_block():
    """OPEN-120: Rudra's own installs belong to the run, not to any role, and
    a slow first task whose time went to `pip install` must be answerable
    from usage.json alone."""
    usage = RunUsage()
    usage.record_env_sync(1.25)
    usage.record_env_sync(2.5)

    run = usage.as_log(wall_now=usage.started_wall, mono_now=usage.started_mono)["run"]

    assert run["env_syncs"] == 2
    assert run["env_sync_seconds"] == pytest.approx(3.75)
