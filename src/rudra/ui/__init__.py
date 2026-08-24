"""Rudra's interactive prompt component.

One selector, three call sites: `ask_user`, the plan gate, and the file
approval. Before this, each surface hand-rolled its own `Prompt.ask` and
none of them could offer the user a list to choose from -- which is why
the planner narrated its options as prose instead of asking (OPEN-11).

The split is deliberate and load-bearing: `select.py` is a pure state
machine, `render.py` turns one state into lines, and only `prompt.py`
touches a terminal.
"""

from rudra.ui.select import Cancelled, Choice, Chosen, SelectState, initial, press

__all__ = [
    "Cancelled",
    "Choice",
    "Chosen",
    "SelectState",
    "initial",
    "press",
]
