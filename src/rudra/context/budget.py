"""How large a single tool result may be before it leaves the context window.

deepagents evicts an oversized tool result to `<artifacts_root>/large_tool_results/`
and replaces it with a head+tail preview plus a `read_file` hint. The
threshold defaults to 20 000 tokens
(`deepagents/middleware/filesystem.py`, `tool_token_limit_before_evict`),
which is roughly a third of a 32B window (TODO.md D6) for one failing-pytest
transcript -- and Step 7 is what made pytest reachable.

The number is derived from the same `context_tokens` that C1.4a feeds to the
model profile's `max_input_tokens`, so the eviction threshold and the
summarization trigger cannot drift apart.

Pure by rule: nothing here imports from Rudra. See TODO.md A1.47 and
Step 12a's spec, section 3.
"""

from __future__ import annotations

from typing import Any

# One tool result may not exceed a tenth of the window.
TOOL_RESULT_FRACTION = 0.10

# ...but never so little that an ordinary read evicts. Ten percent of a 4k
# window is 400 tokens, which is smaller than a routine read_file result,
# and a threshold that evicts everything has replaced the context window
# with a round trip through the filesystem.
MIN_TOOL_RESULT_TOKENS = 2000


def evict_limit(cfg: Any, role: str) -> int | None:
    """Tokens after which a tool result is offloaded, for one role.

    Args:
        cfg: Anything exposing `model_for(role)` -- Rudra's Config does
            (config/loader.py:293), and so falls back to the `default`
            role for an unknown one, which this function inherits rather
            than re-implements.
        role: A BUILTIN_ROLES member, or any string.

    Returns:
        A token count, or None when the role has declared no context
        window. None means "leave upstream's default alone": guessing low
        for a model whose size we do not know evicts results it needed
        (spec S12.9).
    """
    declared = cfg.model_for(role).context_tokens
    if declared is None:
        return None
    return max(int(declared * TOOL_RESULT_FRACTION), MIN_TOOL_RESULT_TOKENS)
