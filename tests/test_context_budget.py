"""The tool-result budget: one number, derived in one place (Step 12a, A1.47).

These tests use a fake config rather than a real Config because the
derivation is arithmetic over one field. Building a real Config here would
test config loading, which tests/test_config_loader.py already does.
"""

from __future__ import annotations

from dataclasses import dataclass

from rudra.context.budget import (
    MIN_TOOL_RESULT_TOKENS,
    TOOL_RESULT_FRACTION,
    evict_kwargs,
    evict_limit,
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
    assert evict_kwargs(_cfg(coder=131072), "coder") == {"tool_token_limit_before_evict": 13107}


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
