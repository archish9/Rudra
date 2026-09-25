"""Failure signatures: what counts as 'the same failure twice'."""

from __future__ import annotations

from rudra.loop.bounds import (
    CONVERGING,
    NOT_CONVERGING,
    UNREADABLE,
    convergence,
    failure_signature,
    tests_produced_no_judgement,
)
from rudra.verify.result import (
    FAILED,
    NOT_APPLICABLE,
    PASSED,
    Finding,
    StageResult,
    VerifyReport,
)


def report_with(*stages):
    return VerifyReport.from_stages(stages)


def failing(name="typecheck", findings=(), detail="", tail=""):
    return StageResult(
        name=name,
        outcome=FAILED,
        blocking=True,
        findings=tuple(findings),
        detail=detail,
        output_tail=tail,
    )


def test_the_same_failure_gives_the_same_signature():
    findings = (Finding("a.py", 3, "bad type"),)
    first = report_with(failing(findings=findings))
    second = report_with(failing(findings=findings))
    assert failure_signature(first) == failure_signature(second)


def test_the_output_tail_is_excluded():
    # It carries durations, temp paths and pytest's ordering seed, so
    # including it would mean two identical failures rarely match and the
    # no-progress detector would never fire (spec S9c.3).
    findings = (Finding("a.py", 3, "bad type"),)
    quick = report_with(failing(findings=findings, tail="ran in 0.01s /tmp/aaa"))
    slow = report_with(failing(findings=findings, tail="ran in 9.40s /tmp/zzz"))
    assert failure_signature(quick) == failure_signature(slow)


def test_fixing_one_of_several_findings_changes_the_signature():
    four = [Finding("a.py", n, "bad type") for n in (1, 2, 3, 4)]
    before = report_with(failing(findings=four))
    after = report_with(failing(findings=four[:3]))
    assert failure_signature(before) != failure_signature(after)


def test_moving_a_defect_counts_as_progress():
    before = report_with(failing(findings=(Finding("a.py", 3, "bad type"),)))
    after = report_with(failing(findings=(Finding("a.py", 9, "bad type"),)))
    assert failure_signature(before) != failure_signature(after)


def test_finding_order_does_not_matter():
    one = Finding("a.py", 1, "x")
    two = Finding("b.py", 2, "y")
    assert failure_signature(report_with(failing(findings=(one, two)))) == failure_signature(
        report_with(failing(findings=(two, one)))
    )


def test_a_different_stage_is_a_different_signature():
    findings = (Finding("a.py", 3, "boom"),)
    assert failure_signature(
        report_with(failing(name="typecheck", findings=findings))
    ) != failure_signature(report_with(failing(name="stubs", findings=findings)))


def test_without_findings_the_detail_carries_the_signature():
    # e.g. syntax reports "3 file(s) do not parse" and no Finding list.
    three = report_with(failing(name="syntax", detail="3 file(s) do not parse"))
    two = report_with(failing(name="syntax", detail="2 file(s) do not parse"))
    assert failure_signature(three) != failure_signature(two)
    assert failure_signature(three) == failure_signature(
        report_with(failing(name="syntax", detail="3 file(s) do not parse"))
    )


# --- OPEN-129: a finding-less blocker is signed by what failed, too ----------
#
# Run a4196786280d's t1: attempt 1 left pytest failing at conftest import
# (exit 4), attempt 2 fixed that and exposed pytest-asyncio's INTERNALERROR
# (exit 3). Both reach the gate as "collected nothing" with no findings and
# the same `detail`, so both signed alike and the task went BLOCKED for "the
# same failure twice" after a real fix. These tails are the two real ones,
# trimmed.

_COLLECTED_NOTHING = (
    "the test command collected nothing, but 2 test file(s) are present: "
    "tests/test_api.py, tests/test_crud.py. The runner cannot reach them -- "
    "check for a missing `__init__.py`, a layout the runner does not search, "
    "or a collection error above."
)

_CONFTEST_IMPORT = """ImportError while loading conftest '/private/tmp/run-a/tests/conftest.py'.
tests/conftest.py:8: in <module>
    from main import app
E   ModuleNotFoundError: No module named 'main'
"""

_PLUGIN_CRASH = """collecting ... collected 0 items
INTERNALERROR> Traceback (most recent call last):
INTERNALERROR>   File "/private/tmp/run-a/.venv/lib/python3.12/site-packages/_pytest/main.py", line 285, in wrap_session
INTERNALERROR>     session.exitstatus = doit(config, session) or 0
INTERNALERROR>   File "/private/tmp/run-a/.venv/lib/python3.12/site-packages/pytest_asyncio/plugin.py", line 626, in pytest_collectstart
INTERNALERROR>     pyobject = collector.obj
INTERNALERROR> AttributeError: 'Package' object has no attribute 'obj'

============================= 2 warnings in 0.00s ==============================
"""


def _collected_nothing(tail):
    return report_with(failing(name="test", detail=_COLLECTED_NOTHING, tail=tail))


def test_two_different_collection_failures_are_not_the_same_failure():
    assert failure_signature(_collected_nothing(_CONFTEST_IMPORT)) != failure_signature(
        _collected_nothing(_PLUGIN_CRASH)
    )


def test_the_same_collection_failure_elsewhere_is_still_the_same_failure():
    """Paths, timings and addresses vary between two runs of one failure."""
    moved = _PLUGIN_CRASH.replace("/private/tmp/run-a", "/Users/someone/proj").replace(
        "0.00s", "1.37s"
    )
    assert failure_signature(_collected_nothing(_PLUGIN_CRASH)) == failure_signature(
        _collected_nothing(moved)
    )
    first = report_with(failing(name="test", tail="E   RuntimeError: <Engine at 0x10cc281a0>"))
    second = report_with(failing(name="test", tail="E   RuntimeError: <Engine at 0x7f00beef>"))
    assert failure_signature(first) == failure_signature(second)
    # By shape, not by host OS (CLAUDE.md §1.8): a drive-letter path is one too.
    on_c = report_with(failing(name="test", tail="E   OSError: C:\\Users\\a\\proj\\x.db is locked"))
    on_d = report_with(failing(name="test", tail="E   OSError: D:\\work\\proj\\x.db is locked"))
    assert failure_signature(on_c) == failure_signature(on_d)


def test_a_relative_path_in_an_exception_line_still_counts():
    """Only an ABSOLUTE path is where the project happens to live. A relative
    one names what failed: fixing `data/users.json` and meeting
    `data/orders.json` is progress, and masking it would be this item's false
    block one step later."""
    users = report_with(
        failing(
            name="test", tail="E   FileNotFoundError: [Errno 2] No such file: 'data/users.json'"
        )
    )
    orders = report_with(
        failing(
            name="test", tail="E   FileNotFoundError: [Errno 2] No such file: 'data/orders.json'"
        )
    )
    assert failure_signature(users) != failure_signature(orders)


def test_a_finding_less_blocker_without_an_exception_line_signs_as_it_did():
    """No exception line in the tail -> the pre-OPEN-129 signature, exactly."""
    from hashlib import sha256

    before = sha256(b"syntax\n3 file(s) do not parse").hexdigest()[:16]
    assert (
        failure_signature(report_with(failing(name="syntax", detail="3 file(s) do not parse")))
        == before
    )
    assert (
        failure_signature(
            report_with(failing(name="syntax", detail="3 file(s) do not parse", tail="ran in 0.3s"))
        )
        == before
    )


def test_findings_still_decide_when_there_are_findings():
    """The tail stays out of a located blocker's signature (spec S9c.3)."""
    findings = (Finding("a.py", 3, "bad type"),)
    assert failure_signature(report_with(failing(findings=findings, tail="E   KeyError: 'x'"))) == (
        failure_signature(report_with(failing(findings=findings, tail="E   ValueError: 'y'")))
    )


def test_a_passing_report_has_no_signature():
    passed = report_with(StageResult(name="syntax", outcome=PASSED, blocking=True))
    assert failure_signature(passed) is None


def test_no_test_judgement_is_detected():
    report = report_with(
        StageResult(name="syntax", outcome=PASSED, blocking=True),
        StageResult(
            name="test",
            outcome=NOT_APPLICABLE,
            blocking=True,
            detail="no tests were collected",
        ),
    )
    assert tests_produced_no_judgement(report) is True


def test_a_passing_test_stage_is_a_judgement():
    report = report_with(StageResult(name="test", outcome=PASSED, blocking=True))
    assert tests_produced_no_judgement(report) is False


def test_an_absent_test_stage_is_not_a_missing_judgement():
    # The pipeline short-circuited before tests. That is a blocked run, not
    # a project without tests -- dispatching the tester would be wrong.
    report = report_with(failing(name="syntax"))
    assert tests_produced_no_judgement(report) is False


def test_max_fix_attempts_must_be_a_positive_whole_number(tmp_path):
    # Zero would make every task block without the coder running once,
    # which reads as a broken model rather than a config mistake.
    from rudra.config import ConfigError, build_config

    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    base = (
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'
    )

    for bad in ("0", "-1", "true", '"three"'):
        (rudra / "config.toml").write_text(
            f"{base}\n[agent]\nmax_fix_attempts = {bad}\n", encoding="utf-8"
        )
        try:
            build_config(tmp_path)
        except ConfigError as exc:
            assert "max_fix_attempts" in str(exc)
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError(f"max_fix_attempts = {bad} was accepted")

    (rudra / "config.toml").write_text(f"{base}\n[agent]\nmax_fix_attempts = 5\n", encoding="utf-8")
    assert build_config(tmp_path).agent.max_fix_attempts == 5


def test_max_invocation_seconds_must_be_a_whole_number_of_zero_or_more(tmp_path):
    """OPEN-91's bound is configurable because the number that separates a
    runaway from real work is a property of the PROVIDER's latency -- 80
    calls is 4.7 minutes at 3.5 s/call and 61 minutes at 46 s/call.

    0 is legal here and illegal for max_fix_attempts, deliberately: it is
    how a user on a very slow provider says "no time bound" without losing
    the call ceiling, which is a separate guard.
    """
    from rudra.config import ConfigError, build_config

    rudra = tmp_path / ".rudra"
    rudra.mkdir(parents=True)
    base = (
        '[model.default]\nprovider = "ollama"\n'
        'base_url = "http://localhost:11434"\nmodel = "qwen3:32b"\n'
    )

    for bad in ("-1", "true", '"1200"', "12.5"):
        (rudra / "config.toml").write_text(
            f"{base}\n[agent]\nmax_invocation_seconds = {bad}\n", encoding="utf-8"
        )
        try:
            build_config(tmp_path)
        except ConfigError as exc:
            assert "max_invocation_seconds" in str(exc)
        else:  # pragma: no cover - the assertion below reports it
            raise AssertionError(f"max_invocation_seconds = {bad} was accepted")

    for good in (0, 3600):
        (rudra / "config.toml").write_text(
            f"{base}\n[agent]\nmax_invocation_seconds = {good}\n", encoding="utf-8"
        )
        assert build_config(tmp_path).agent.max_invocation_seconds == good


def test_the_shipped_default_is_the_one_the_runner_uses(tmp_path):
    """Two spellings of one bound is two answers to "why did it stop".
    `subagents/runner.py` reads the config and falls back to its constant
    when there is none, so the two must be the same number."""
    from rudra.config.schema import DEFAULTS
    from rudra.subagents.runner import MAX_INVOCATION_SECONDS

    assert DEFAULTS["agent"]["max_invocation_seconds"] == MAX_INVOCATION_SECONDS


# --- convergence (OPEN-160, the owner's option B') ---------------------------
#
# Read at exhaustion only, and extends nothing: it says whether the attempt
# that ran the budget out was making progress. The cases are the archive's.


def _pytest(*exceptions, counts="1 run, 1 failed"):
    tail = "\n".join(f"E   {line}" for line in exceptions)
    return report_with(failing("test", detail=counts, tail=f"{tail}\n{counts}"))


def test_a_new_failure_where_the_old_one_was_is_converging():
    """Run 4989aefefacb's t12: 12/39 on pydantic's from_attributes, then 6/39
    on the tests' own assertions -- the failure it was sent is gone."""
    previous = _pytest("pydantic.errors.PydanticUserError: You must set from_attributes=True")
    current = _pytest("AssertionError: assert 'x' == 'y'", "ValidationError: 1 validation error")

    verdict, reason = convergence(previous, current)

    assert verdict == CONVERGING
    assert "none of the previous gate's" in reason


def test_a_failure_that_survives_is_not_converging():
    """Run 689f0ea263be's t3 shape: 51 then 50 failing, on the same error."""
    previous = _pytest("NameError: name 'app' is not defined", "KeyError: 'id'")
    current = _pytest("NameError: name 'app' is not defined")

    verdict, reason = convergence(previous, current)

    assert verdict == NOT_CONVERGING
    assert "1 of the previous gate's 2" in reason


def test_paths_and_addresses_do_not_make_a_failure_new():
    """The signature's own masking: one failure, two temp dirs, two ids."""
    previous = _pytest("FileNotFoundError: /tmp/a1/x.json at 0x10ab")
    current = _pytest("FileNotFoundError: /tmp/b2/x.json at 0x99ff")

    assert convergence(previous, current)[0] == NOT_CONVERGING


def test_a_gate_that_got_further_is_converging():
    """Run 4989aefefacb's t8: syntax, syntax, then a test failure."""
    previous = report_with(failing("syntax", findings=[Finding("a.py", 49, "invalid syntax")]))
    current = _pytest("AttributeError: module 'app.models' has no attribute 'User'")

    verdict, reason = convergence(previous, current)

    assert verdict == CONVERGING
    assert "from syntax to test" in reason


def test_a_gate_that_stopped_earlier_is_not_converging():
    previous = _pytest("AssertionError: assert 1 == 2")
    current = report_with(failing("syntax", findings=[Finding("a.py", 3, "invalid syntax")]))

    verdict, reason = convergence(previous, current)

    assert verdict == NOT_CONVERGING
    assert "stopped earlier" in reason


def test_a_moved_finding_is_the_same_failure():
    """A typecheck error that moved line is `failure_signature`'s progress, and
    rightly -- it is not the same signature -- but it is not convergence: the
    same message in the same file is still there."""
    previous = report_with(failing(findings=[Finding("a.py", 1, "bad type")]))
    current = report_with(failing(findings=[Finding("a.py", 2, "bad type")]))

    assert convergence(previous, current)[0] == NOT_CONVERGING


def test_a_gate_with_no_exception_line_is_unreadable_not_converging():
    """Run 566076f2af28's t1: the previous tail held no `E` line, so "the old
    failure is gone" would be true of an empty set. The replay counted that as
    converging; said here, it is only unreadable."""
    previous = report_with(failing("test", detail="13 run, 4 failed", tail="4 failed"))
    current = _pytest("AssertionError: assert 2 == 1")

    verdict, reason = convergence(previous, current)

    assert verdict == UNREADABLE
    assert "previous" in reason


def test_nothing_to_compare_is_no_verdict():
    current = _pytest("AssertionError: assert 2 == 1")

    passed = report_with(StageResult(name="test", outcome=PASSED, blocking=True))

    assert convergence(None, current) is None
    assert convergence(passed, current) is None
    assert convergence(current, passed) is None


def test_a_rewritten_assertion_is_a_failure_line():
    """Run 566076f2af28's t1: pytest prints a failed bare `assert` as
    `E       assert 422 == 200`, with no `AssertionError:` in front, so the
    signature's reading saw nothing and the task read as unreadable. Four
    422s, then two new assertions, is converging."""
    previous = report_with(
        failing(
            "test",
            detail="13 run, 4 failed",
            tail="E       assert 422 == 200\nE        +  where 422 = <Response [422]>.status_code",
        )
    )
    current = _pytest("AssertionError: assert 2 == 1")

    assert convergence(previous, current)[0] == CONVERGING
    assert convergence(previous, previous)[0] == NOT_CONVERGING
