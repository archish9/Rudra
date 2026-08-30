"""Two budgets derived from one number: tool-result eviction, and recall.

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

# A human message may cost more than a tool result, in the same proportion
# upstream's own defaults use (50 000 / 20 000), so the two thresholds keep
# their relative shape while both scale with the declared window.
HUMAN_MESSAGE_MULTIPLIER = 2.5


def execute_kwargs(cfg: Any) -> dict[str, int]:
    """`FilesystemMiddleware`'s command-timeout keyword, from Rudra's own.

    Upstream defaults `max_execute_timeout` to 3600, and nothing in the
    package set it -- so an agent `execute` call could hang for an hour
    while Rudra's own `[tools] test_timeout` was 600. An unattended
    `--auto --allow-shell` run that tripped an interactive or wedged
    command burned that hour per call, with no ceiling the user had
    configured. Derived from the one timeout the user does set, for the
    reason the eviction thresholds are derived from one window: two
    independently configured numbers drift (CR-F2).
    """
    tools = getattr(cfg, "tools", None)
    timeout = getattr(tools, "test_timeout", None)
    # Duck-typed like the rest of this module (see evict_limit's `cfg`
    # argument): a caller with no [tools] section leaves upstream's default
    # alone rather than being handed a guess.
    return {} if timeout is None else {"max_execute_timeout": int(timeout)}


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


def evict_kwargs(cfg: Any, role: str) -> dict[str, int]:
    """`FilesystemMiddleware` keyword arguments for this role's budget.

    A dict rather than a plain value because **omitting the argument and
    passing None are different things**, and the difference is dangerous:
    the constructor defaults to 20 000, but every consumer guards with
    `if not self._tool_token_limit_before_evict`
    (`deepagents/middleware/filesystem.py:2738`, `:3147`, `:3464`), so an
    explicit None **switches eviction off entirely**. That is worse than
    the default it was meant to preserve.

    Measured 2026-08-19 while executing Step 12a's plan, which had the
    call sites passing None directly.
    Covers BOTH eviction thresholds. The human-message one was never set
    anywhere in the package, so every agent took upstream's fixed 50 000
    while its sibling was derived from `context_tokens` -- and for the
    32B/32k model D6 makes the design center, 50 000 exceeds the whole
    window, so that eviction path could never fire. That is exactly the
    drift "one number, two consumers" exists to prevent, on the knob it
    forgot (CR-F1).
    """
    limit = evict_limit(cfg, role)
    if limit is None:
        return {}
    return {
        "tool_token_limit_before_evict": limit,
        # A user message may take more than a tool result -- it is the
        # user's actual words, not a transcript -- but still a bounded
        # share of the window rather than a fixed number larger than it.
        "human_message_token_limit_before_evict": max(
            int(limit * HUMAN_MESSAGE_MULTIPLIER), MIN_TOOL_RESULT_TOKENS
        ),
    }


# The whole recall block may take a fiftieth of the window. Set against
# TOOL_RESULT_FRACTION deliberately: one oversized tool result may cost a
# tenth, because it is transient and evictable, while this block is paid on
# every model call for the whole run and nothing sheds it.
#
# 0.02 is a starting number, not a measured one, and deliberately not a
# config key -- an unmeasured knob is worse than a constant somebody can
# change with evidence (S12.4, and MemoryConfig's docstring says the same).
# It becomes [memory] recall_fraction when a measurement says what it
# should be. At 32k this is 640 tokens, which lands independently on
# MemPalace's own wake_up() design point of 600-900 (layers.py:407).
RECALL_FRACTION = 0.02

# ...but never so little that no whole entry fits. Two percent of a 4k
# window is 80 tokens; a block that can only ever be truncated to nothing
# costs prompt space and delivers no memory.
MIN_RECALL_TOKENS = 300


def recall_limit(cfg: Any, role: str) -> int:
    """Tokens the injected recall block may occupy, for one role.

    Args:
        cfg: Anything exposing `model_for(role)` -- Rudra's Config does
            (config/loader.py:293), and so falls back to the `default`
            role for an unknown one, which this function inherits rather
            than re-implements.
        role: A BUILTIN_ROLES member, or any string.

    Returns:
        A token count, always. An undeclared window takes
        MIN_RECALL_TOKENS.

    This returned None on an undeclared window until OPEN-54, on the
    reading that S12.9's rule for evict_limit applied here unchanged.
    It does not, and the difference is which way each one fails:

      - evict_limit's None is *open*. evict_kwargs omits the argument
        (:79) so upstream's 20000-token default stands, and the agent
        keeps working with a documented number.
      - recall_limit's None was *closed*. recall_block returns "" on it
        (memory/render.py:41), `if block:` at subagents/build.py:181 is
        false, and the feature is off with nothing in any log saying so.

    Since `rudra init` ships context_tokens commented out
    (config/template.py:43), that was the shipped default: three runs
    reported recall_chars: 0 for all four roles against a palace that
    answers a search in 0.42s, and the feature had been presented as
    working since Step 14.

    S12.9's reasoning is about the cost of guessing wrong, and the two
    costs are not comparable. Guessing low for eviction throws away a
    tool result the agent needed; guessing low here spends 300 tokens of
    prompt. And 300 is not a guess: 0.02 * 15000 is exactly
    MIN_RECALL_TOKENS, so it is already what every declared window below
    15k receives. An undeclared model is handed the smallest supported
    model's budget, which is the conservative direction.
    """
    declared = cfg.model_for(role).context_tokens
    if declared is None:
        return MIN_RECALL_TOKENS
    return max(int(declared * RECALL_FRACTION), MIN_RECALL_TOKENS)
