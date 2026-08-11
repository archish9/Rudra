"""Running the project's tests (Step 8, C3.6).

`run_tests` is what Step 9's fix loop and completion gate call directly,
with no model in the loop (S8.1). The thin tool wrapping it lives in
`rudra.tools.testing_tools`, with every other tool.
"""

from __future__ import annotations

from rudra.testing.parse import Counts, parse_counts
from rudra.testing.runner import MAX_TAIL_CHARS, TestResult, run_tests

__all__ = ["MAX_TAIL_CHARS", "Counts", "TestResult", "parse_counts", "run_tests"]
