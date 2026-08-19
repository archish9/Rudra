"""Context management: what a run's payload costs, and what survives it.

Three unrelated pure modules land here across Step 12 -- budget.py (12a),
usage.py (12b) and agents_md.py (12c). Each is independently testable and
imports nothing from the agent layer, which is what keeps this a package
rather than a junk drawer.
"""

from rudra.context.budget import evict_limit

__all__ = ["evict_limit"]
