"""The vendored skill corpus and the transform that renders it.

Nothing here is wired to an agent. `bundles/<name>/src/` is a frozen,
byte-identical copy of an upstream project; `transform.render()` produces
the tree an agent would read. Step 11b decides where that tree lives and
hands it to `create_deep_agent(skills=...)`.
"""

from __future__ import annotations
