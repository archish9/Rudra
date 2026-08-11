"""Running the project's tests (Step 8, C3.6)."""

from __future__ import annotations

from rudra.testing.parse import Counts, parse_counts
from rudra.testing.runner import MAX_TAIL_CHARS, TestResult, run_tests

__all__ = ["MAX_TAIL_CHARS", "Counts", "TestResult", "parse_counts", "run_tests"]
