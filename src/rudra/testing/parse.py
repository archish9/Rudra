"""Turning a test runner's own summary line into counts. Pure, no I/O.

Deliberately shallow. `pytest --json-report` needs a third-party plugin
Rudra cannot install into a user's project, and `cargo test --format json`
is nightly-only, so a precise path would exist for some stacks and not
others -- and Step 9 would have to handle the degraded shape regardless
(Step 8 spec §6.3). So the degraded shape is the contract.

Counts are None when nothing matched. That is not a failure: the exit code
is always authoritative, and a guessed count is worse than no count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# pytest: "1 failed, 1 passed, 1 skipped in 0.02s"
_PYTEST_PAIR = re.compile(r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed)")

# cargo: "test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; ..."
_CARGO_LINE = re.compile(
    r"test result:\s+\S+\s+(\d+)\s+passed;\s+(\d+)\s+failed;\s+(\d+)\s+ignored"
)

# jest: "Tests:       1 failed, 1 skipped, 1 passed, 3 total"
_JEST_LINE = re.compile(r"^Tests:\s+(.+)$", re.MULTILINE)
_JEST_PAIR = re.compile(r"(\d+)\s+(passed|failed|skipped|todo|total)")

# node --test: "ℹ pass 1" / "ℹ fail 1" / "ℹ skipped 1" / "ℹ tests 3"
#
# `.*?` rather than `\W*` for the leading glyph: U+2139 INFORMATION SOURCE
# is Unicode category Ll -- a lowercase LETTER -- so \W never matches it and
# a \W* prefix consumes nothing. Caught only because the fixture is real.
# The \b still stops `bypass 9` matching as `pass 9`.
_NODE_LINE = re.compile(r"^.*?\b(tests|pass|fail|skipped)\s+(\d+)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Counts:
    """How many tests ran, failed, and were skipped. None means unparsed."""

    total: int | None
    failed: int | None
    skipped: int | None


_UNKNOWN = Counts(None, None, None)


def _parse_pytest(text: str) -> Counts:
    """pytest's last summary line wins; earlier lines may be per-file noise."""
    for line in reversed(text.strip().splitlines()):
        if " in " not in line:
            continue
        pairs = _PYTEST_PAIR.findall(line)
        if not pairs:
            continue
        tally: dict[str, int] = {}
        for number, label in pairs:
            tally[label.rstrip("s") if label.startswith("error") else label] = int(number)
        passed = tally.get("passed", 0)
        failed = tally.get("failed", 0) + tally.get("error", 0)
        skipped = tally.get("skipped", 0)
        return Counts(total=passed + failed + skipped, failed=failed, skipped=skipped)
    return _UNKNOWN


def _parse_cargo(text: str) -> Counts:
    """Sum every binary's line: a crate prints one per test target."""
    matches = _CARGO_LINE.findall(text)
    if not matches:
        return _UNKNOWN
    passed = sum(int(m[0]) for m in matches)
    failed = sum(int(m[1]) for m in matches)
    ignored = sum(int(m[2]) for m in matches)
    return Counts(total=passed + failed + ignored, failed=failed, skipped=ignored)


def _parse_jest(text: str) -> Counts:
    match = _JEST_LINE.search(text)
    if match is None:
        return _UNKNOWN
    tally = {label: int(number) for number, label in _JEST_PAIR.findall(match.group(1))}
    if not tally:
        return _UNKNOWN
    failed = tally.get("failed", 0)
    skipped = tally.get("skipped", 0) + tally.get("todo", 0)
    total = tally.get("total", tally.get("passed", 0) + failed + skipped)
    return Counts(total=total, failed=failed, skipped=skipped)


def _parse_node_native(text: str) -> Counts:
    found = {label: int(number) for label, number in _NODE_LINE.findall(text)}
    if "pass" not in found and "fail" not in found:
        return _UNKNOWN
    failed = found.get("fail", 0)
    skipped = found.get("skipped", 0)
    total = found.get("tests", found.get("pass", 0) + failed + skipped)
    return Counts(total=total, failed=failed, skipped=skipped)


def _parse_node(text: str) -> Counts:
    """`npm test` runs whatever the project declares, so try each shape.

    jest first because it is by far the most common; node's own runner
    second. Anything else -- vitest, mocha, karma -- falls through to
    _UNKNOWN, which is a correct answer rather than a wrong count.
    """
    for parser in (_parse_jest, _parse_node_native):
        counts = parser(text)
        if counts != _UNKNOWN:
            return counts
    return _UNKNOWN


_PARSERS = {
    "python": _parse_pytest,
    "rust": _parse_cargo,
    "node": _parse_node,
    "react": _parse_node,
    "angular": _parse_node,
}


def parse_counts(stack: str | None, stdout: str, stderr: str) -> Counts:
    """Counts for `stack`, or all-None when nothing recognisable was found.

    Both streams are searched: jest writes its summary to stderr, and a
    runner invoked through a wrapper script may have had either redirected.
    """
    parser = _PARSERS.get(stack or "")
    if parser is None:
        return _UNKNOWN
    for text in (stdout, stderr):
        if not text:
            continue
        counts = parser(text)
        if counts != _UNKNOWN:
            return counts
    return _UNKNOWN


__all__ = ["Counts", "parse_counts"]
