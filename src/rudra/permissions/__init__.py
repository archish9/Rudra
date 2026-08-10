"""Rudra's permission layer (Step 7 — C3.1-C3.4).

deepagents' own `permissions=` cannot be used: it raises NotImplementedError
on any backend that supports command execution, and its FilesystemOperation
is ('read', 'write') only, so it never covered `execute` regardless. See
TODO.md U.7 and A1.46.
"""

from __future__ import annotations
