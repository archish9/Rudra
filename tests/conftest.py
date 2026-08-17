"""Suite-wide isolation from the developer's own environment (TODO.md A3.8).

`build_config()` calls `load_dotenv(root / ".env")` (`config/loader.py:249`),
and `load_dotenv` mutates `os.environ` for the whole process. So one test that
resolves `root` to the real repo — the default is `Path.cwd()` — imports the
developer's `.env` into every test that runs after it, in every file.

The observed cost: a `.env` containing `OLLAMA_NUM_PREDICT=-1` (a legitimate
Ollama value meaning "unlimited") turned 10 tests red across four files with
`max_output_tokens ... must be a positive integer, got -1 (the env layer)`.
CI never saw it, because `.env` is gitignored and a clean clone has none —
only developers who actually run Rudra were affected.

`test_agent_wiring.py` already carried this fixture locally, for the same
reason, before it was clear the leak crossed files.
"""

from __future__ import annotations

import os
import pathlib

import pytest

_LEAKY_PREFIXES = ("RUDRA_", "OLLAMA_")
_LEAKY_NAMES = ("VERBOSE",)


@pytest.fixture(autouse=True)
def _isolate_rudra_environment(monkeypatch):
    """Strip Rudra-owned variables before every test; monkeypatch restores them.

    Tests that need one set it themselves. Tests that need a `.env` read write
    their own into a tmp_path and point the loader at it.
    """
    for name in list(os.environ):
        if name.startswith(_LEAKY_PREFIXES) or name in _LEAKY_NAMES:
            monkeypatch.delenv(name, raising=False)


# Stripping the environment is not enough on its own. `get_config()` with no
# argument resolves the project root to `Path.cwd()` -- the repo -- and calls
# `load_dotenv` on it *during* the test, re-importing the developer's own .env
# after this fixture has already cleaned up. Any test that reaches
# create_planner_agent (which calls get_config() at planner_agent.py:308) then
# runs against whatever the developer happens to have configured.
#
# Measured 2026-08-17: a .env with the OpenRouter key turned 21 tests red
# across four files, and a further 12 after a partial fix. CI never sees it --
# .env is gitignored -- so this fails only for people who actually run Rudra.
#
# Tests that write their own .env into a tmp_path still work: only the repo's
# own file is skipped.
_REPO_DOTENV = (pathlib.Path(__file__).parent.parent / ".env").resolve()


@pytest.fixture(autouse=True)
def _ignore_the_developers_dotenv(monkeypatch):
    from rudra.config import loader

    real = loader.load_dotenv

    def _guarded(dotenv_path=None, *args, **kwargs):
        if dotenv_path is not None and pathlib.Path(dotenv_path).resolve() == _REPO_DOTENV:
            return False
        return real(dotenv_path, *args, **kwargs)

    monkeypatch.setattr(loader, "load_dotenv", _guarded)
