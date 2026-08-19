"""Context management: what a run's payload costs, and what survives it.

Three unrelated pure modules land here across Step 12 -- budget.py (12a),
usage.py (12b) and agents_md.py (12c). Each is independently testable and
imports nothing from the agent layer, which is what keeps this a package
rather than a junk drawer.
"""

from rudra.context.agents_md import (
    SESSION_LOG_ENTRIES,
    append_session_entry,
    format_entry,
    replace_section,
    section_body,
)
from rudra.context.budget import evict_kwargs, evict_limit
from rudra.context.usage import RoleUsage, RunUsage, render_usage

__all__ = [
    "SESSION_LOG_ENTRIES",
    "RoleUsage",
    "RunUsage",
    "append_session_entry",
    "evict_kwargs",
    "evict_limit",
    "format_entry",
    "render_usage",
    "replace_section",
    "section_body",
]
