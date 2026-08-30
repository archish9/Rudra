"""The tool-result budget: one number, derived in one place (Step 12a, A1.47).

These tests use a fake config rather than a real Config because the
derivation is arithmetic over one field. Building a real Config here would
test config loading, which tests/test_config_loader.py already does.
"""

from __future__ import annotations

from dataclasses import dataclass

from rudra.context.budget import (
    MIN_RECALL_TOKENS,
    MIN_TOOL_RESULT_TOKENS,
    RECALL_FRACTION,
    TOOL_RESULT_FRACTION,
    evict_kwargs,
    evict_limit,
    recall_limit,
)


@dataclass
class FakeModelConfig:
    context_tokens: int | None


@dataclass
class FakeCfg:
    """Mirrors the one method evict_limit uses (config/loader.py:293)."""

    per_role: dict

    def model_for(self, role: str):
        return self.per_role.get(role, self.per_role["default"])


def _cfg(**roles):
    per_role = {name: FakeModelConfig(value) for name, value in roles.items()}
    per_role.setdefault("default", FakeModelConfig(None))
    return FakeCfg(per_role=per_role)


def test_declared_window_yields_a_fraction_of_it():
    cfg = _cfg(coder=131072)
    assert evict_limit(cfg, "coder") == int(131072 * TOOL_RESULT_FRACTION)


def test_undeclared_window_returns_none_so_upstream_default_stands():
    # S12.9: guessing low for an unknown model evicts results it needed.
    cfg = _cfg(coder=None)
    assert evict_limit(cfg, "coder") is None


def test_small_window_is_floored_not_scaled():
    # 10% of 4k is 400 tokens -- smaller than a routine read_file, so a
    # threshold that low replaces the context window with a filesystem
    # round trip.
    cfg = _cfg(coder=4096)
    assert evict_limit(cfg, "coder") == MIN_TOOL_RESULT_TOKENS


def test_the_floor_binds_exactly_where_arithmetic_says():
    # The crossover is coarser than "one more token", because int()
    # truncates: 20_009 * 0.10 is 2_000.9, which floors back onto the
    # floor. 20_010 is the first window the fraction actually wins.
    assert evict_limit(_cfg(coder=20_000), "coder") == MIN_TOOL_RESULT_TOKENS
    assert evict_limit(_cfg(coder=20_009), "coder") == MIN_TOOL_RESULT_TOKENS
    assert evict_limit(_cfg(coder=20_010), "coder") == 2001


def test_unknown_role_falls_back_to_default_like_the_rest_of_rudra():
    # model_for falls back to `default` for an unknown role
    # (config/loader.py:294-300); evict_limit inherits that, it does not
    # re-implement it.
    cfg = _cfg(default=65536)
    assert evict_limit(cfg, "no-such-role") == int(65536 * TOOL_RESULT_FRACTION)


def test_module_imports_nothing_from_rudra():
    # Same rule as loop/ledger.py and facts/store.py: this module is pure
    # arithmetic and must stay testable without a Config, a backend, or an
    # agent.
    import ast
    import pathlib

    import rudra.context.budget as budget

    tree = ast.parse(pathlib.Path(budget.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not [name for name in imported if name.startswith("rudra")]


def test_evict_kwargs_omits_the_argument_when_no_window_is_declared():
    """Omitting and passing None are NOT the same thing.

    deepagents' constructor defaults to 20 000, but every consumer guards
    with `if not self._tool_token_limit_before_evict`
    (filesystem.py:2738, :3147, :3464), so an explicit None switches
    eviction off entirely -- worse than the default it meant to preserve.
    Measured while executing Step 12a's plan, which passed None directly.
    """
    assert evict_kwargs(_cfg(coder=None), "coder") == {}


def test_evict_kwargs_passes_the_derived_limit_when_there_is_one():
    # Both thresholds, since CR-F1: the human-message one was never set
    # anywhere, so every agent took upstream's fixed 50 000 -- larger than
    # the whole window of the 32B model D6 makes the design center, so that
    # eviction path could never fire.
    assert evict_kwargs(_cfg(coder=131072), "coder") == {
        "tool_token_limit_before_evict": 13107,
        "human_message_token_limit_before_evict": 32767,
    }


def test_none_really_does_disable_eviction_upstream():
    """The measurement the two tests above rest on, pinned upstream.

    If deepagents ever makes None mean "use the default", evict_kwargs
    becomes unnecessary rather than load-bearing -- and this test says so
    instead of leaving a helper nobody can justify.
    """
    from deepagents.backends.filesystem import FilesystemBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    backend = FilesystemBackend(root_dir="/tmp", virtual_mode=True)
    omitted = FilesystemMiddleware(backend=backend)
    explicit_none = FilesystemMiddleware(backend=backend, tool_token_limit_before_evict=None)

    assert omitted._tool_token_limit_before_evict == 20000
    assert explicit_none._tool_token_limit_before_evict is None


def test_recall_limit_is_two_percent_of_a_declared_window() -> None:
    assert recall_limit(_cfg(planner=32_000), "planner") == int(32_000 * RECALL_FRACTION)


def test_recall_limit_never_drops_below_the_floor() -> None:
    """Two percent of a 4k window is 80 tokens, which cannot hold one
    decision. A block that can only ever be truncated is worse than none."""
    assert recall_limit(_cfg(planner=4_000), "planner") == MIN_RECALL_TOKENS


def test_recall_limit_falls_to_the_floor_when_no_window_is_declared() -> None:
    """OPEN-54: this returned None, and None reached recall_block, which
    returns "" on it -- so the shipped config, which comments
    context_tokens out (config/template.py:43), switched recall off in
    silence. Three runs reported recall_chars: 0 for all four roles before
    anyone read the zero.

    S12.9 is why it returned None, and S12.9 stops at eviction. Guessing
    low there discards a tool result the agent needed; guessing low here
    costs 300 tokens of prompt. And 300 is not a guess: it is the number
    every declared window under 15k already gets, since 0.02 * 15000 is
    exactly MIN_RECALL_TOKENS.
    """
    assert recall_limit(_cfg(planner=None), "planner") == MIN_RECALL_TOKENS


def test_recall_limit_is_never_none() -> None:
    """The property OPEN-54 bought, stated on its own: recall degrades
    open, the way eviction and summarization always did. evict_limit's
    None is untouched and deliberate -- evict_kwargs omits the argument
    rather than passing it (budget.py:79), and the two failure modes are
    not the same one (OPEN-54 doc section 7)."""
    for declared in (None, 4_000, 32_000, 131_072):
        assert recall_limit(_cfg(planner=declared), "planner") is not None


def test_recall_limit_is_much_smaller_than_the_eviction_threshold() -> None:
    """Both come off the same context_tokens, and the relationship is the
    point: one tool result may take a tenth of the window, the whole recall
    block may take a fiftieth."""
    cfg = _cfg(planner=32_000)
    assert recall_limit(cfg, "planner") < evict_limit(cfg, "planner")


def test_recall_limit_falls_back_to_default_for_an_unknown_role() -> None:
    """Inherited from cfg.model_for, not re-implemented here -- the same
    property evict_limit relies on (config/loader.py:293)."""
    assert recall_limit(_cfg(default=32_000), "nonesuch") == int(32_000 * RECALL_FRACTION)
