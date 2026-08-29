"""Count parsing against REAL runner output.

Every fixture here was captured from an actual failing run on 2026-08-11 and
is quoted verbatim. A regex written against invented output tests the regex
against itself.
"""

from __future__ import annotations

from rudra.testing.parse import Counts, parse_counts

# pytest 9.x: one pass / one fail / one skip.
PYTEST_MIXED = """\
=========================== short test summary info ============================
FAILED test_sample.py::test_bad - assert (2 + 2) == 5
1 failed, 1 passed, 1 skipped in 0.02s
"""

# This repository's own suite, at the Step 8 baseline.
PYTEST_ALL_PASS = "516 passed, 2 skipped, 35 warnings in 2.97s\n"

# cargo 1.x: one pass / one fail / one ignored.
CARGO_MIXED = """\
failures:
    tests::bad

test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s

error: test failed, to rerun pass `--lib`
"""

# node --test: one pass / one fail / one skip.
NODE_MIXED = """\
ℹ tests 3
ℹ suites 0
ℹ pass 1
ℹ fail 1
ℹ cancelled 0
ℹ skipped 1
"""

# jest 30.x via npx: one pass / one fail / one skip.
JEST_MIXED = """\
Test Suites: 1 failed, 1 total
Tests:       1 failed, 1 skipped, 1 passed, 3 total
Snapshots:   0 total
Time:        0.118 s
"""


# unittest, captured 2026-08-29 on CPython 3.12 (OPEN-48). One pass, one
# failure, one error, one skip, one expected failure, one unexpected success.
# unittest writes this to STDERR, not stdout.
UNITTEST_MIXED = """\
======================================================================
UNEXPECTED SUCCESS: test_xpass (test_mix.T.test_xpass)
----------------------------------------------------------------------
Ran 6 tests in 0.000s

FAILED (failures=1, errors=1, skipped=1, expected failures=1, unexpected successes=1)
"""

# Two passes and a skip.
UNITTEST_OK_WITH_SKIP = """\
----------------------------------------------------------------------
Ran 3 tests in 0.000s

OK (skipped=1)
"""

# run8's own t5, quoted from
# /Users/archish/Documents/ai-ml/test-rudra-run8/run8-terminal.log:1547.
# Note "Ran 1 test" -- singular, no plural s. This is the output that was
# reported to the coder as "the test command collected nothing".
UNITTEST_RUN8_IMPORT_ERROR = """\
ImportError: cannot import name 'create_app' from 'app'

----------------------------------------------------------------------
Ran 1 test in 0.000s

FAILED (errors=1)
"""

# An empty discover run. unittest exits 5 here, exactly as pytest does.
UNITTEST_NO_TESTS = """\
----------------------------------------------------------------------
Ran 0 tests in 0.000s

NO TESTS RAN
"""


def test_pytest_mixed_counts():
    counts = parse_counts("python", PYTEST_MIXED, "")
    assert counts.failed == 1
    assert counts.skipped == 1
    assert counts.total == 3


def test_pytest_all_passing_counts():
    counts = parse_counts("python", PYTEST_ALL_PASS, "")
    assert counts.failed == 0
    assert counts.skipped == 2
    assert counts.total == 518


def test_pytest_errors_count_as_failures():
    """A collection error is not a pass, whatever pytest calls it."""
    counts = parse_counts("python", "1 passed, 2 errors in 0.10s\n", "")
    assert counts.failed == 2


def test_cargo_mixed_counts():
    counts = parse_counts("rust", CARGO_MIXED, "")
    assert counts.failed == 1
    assert counts.skipped == 1
    assert counts.total == 3


def test_cargo_sums_multiple_test_binaries():
    # cargo prints one `test result:` line per target; a crate with a lib and
    # an integration test prints two, and only the sum is meaningful.
    doubled = CARGO_MIXED + (
        "\ntest result: ok. 4 passed; 0 failed; 0 ignored; 0 measured; "
        "0 filtered out; finished in 0.01s\n"
    )
    counts = parse_counts("rust", doubled, "")
    assert counts.total == 7
    assert counts.failed == 1


def test_node_mixed_counts():
    counts = parse_counts("node", NODE_MIXED, "")
    assert counts.failed == 1
    assert counts.skipped == 1
    assert counts.total == 3


def test_jest_mixed_counts():
    counts = parse_counts("node", JEST_MIXED, "")
    assert counts.failed == 1
    assert counts.skipped == 1
    assert counts.total == 3


def test_jest_is_parsed_for_react_and_angular_too():
    """`npm test` runs whatever scripts.test declares, on any Node stack."""
    for stack in ("react", "angular"):
        assert parse_counts(stack, JEST_MIXED, "").failed == 1


def test_unrecognised_output_degrades_to_none_rather_than_guessing():
    assert parse_counts("python", "some tool printed something else", "") == Counts(
        None, None, None
    )


def test_unknown_stack_degrades_to_none():
    assert parse_counts(None, PYTEST_MIXED, "") == Counts(None, None, None)


def test_output_on_stderr_is_parsed_too():
    """jest writes its summary to stderr, which is why both streams are read."""
    counts = parse_counts("node", "", JEST_MIXED)
    assert counts.failed == 1


def test_empty_output_is_not_a_zero_count():
    """Nothing ran is not the same as everything passed."""
    assert parse_counts("python", "", "") == Counts(None, None, None)


def test_a_word_ending_in_a_label_is_not_a_count():
    """The node prefix glyph is a LETTER, so the regex leans on \\b instead."""
    assert parse_counts("node", "ℹ bypass 9\n", "") == Counts(None, None, None)


def test_unittest_mixed_counts():
    """Ran N is authoritative: it already counts every outcome (OPEN-48)."""
    counts = parse_counts("python", UNITTEST_MIXED, "")
    assert counts.total == 6
    assert counts.failed == 2, "failures and errors are both failures"
    assert counts.skipped == 2, "an expected failure is a skip, as xfailed is for pytest"


def test_unittest_ok_line_carries_its_skips():
    counts = parse_counts("python", UNITTEST_OK_WITH_SKIP, "")
    assert counts.total == 3
    assert counts.failed == 0
    assert counts.skipped == 1


def test_unittest_singular_ran_one_test_is_parsed():
    """run8's t5. "Ran 1 test" has no plural s, and a \bs\b regex misses it."""
    counts = parse_counts("python", UNITTEST_RUN8_IMPORT_ERROR, "")
    assert counts.total == 1
    assert counts.failed == 1


def test_unittest_no_tests_ran_is_a_zero_count_not_an_unparsed_one():
    """Zero and None are different claims: only zero means "collected nothing"."""
    counts = parse_counts("python", UNITTEST_NO_TESTS, "")
    assert counts.total == 0
    assert counts.failed == 0


def test_unittest_summary_is_read_from_stderr():
    """Where unittest actually writes it -- measured, not assumed."""
    counts = parse_counts("python", "", UNITTEST_MIXED)
    assert counts.total == 6


def test_pytest_output_is_still_parsed_as_pytest():
    """The python stack now has two shapes; adding one must not cost the other."""
    counts = parse_counts("python", PYTEST_MIXED, "")
    assert counts.total == 3
    assert counts.failed == 1
