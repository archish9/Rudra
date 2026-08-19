"""Long-term memory (Step 14).

`store.py` is the only module in Rudra that may import `mempalace`;
tests/test_no_direct_provider_imports.py enforces that by parsing every
module. Everything else here is pure and imports nothing from Rudra.

Nothing is re-exported from `store` at package scope: doing so would put a
mempalace import on `import rudra.memory`, which is the module-scope import
S14.2's lazy-import rule exists to prevent.
"""

from __future__ import annotations

from rudra.memory.entry import EntryRejected, MemoryEntry

__all__ = ["EntryRejected", "MemoryEntry"]
