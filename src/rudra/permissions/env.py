"""The environment agent shell commands run with.

LocalShellBackend defaults to `inherit_env=False`, which means an EMPTY
environment -- not a minimal one. `which pytest git ruff` finds nothing, so
every venv, nvm, rustup, and pyenv toolchain is invisible and Step 8's git
and test-runner tools would fail on their first call (TODO.md A1.44).

So Rudra passes an explicit environment: inherited, minus secrets. C1.5
keeps API keys out of TOML specifically so they live in the environment;
handing that environment to a shell the model drives would undo it, since
anything the agent prints lands in the transcript and goes to the provider.

This is a real-toolchains-work default, not a sandbox. A command that reads
~/.aws/credentials off disk is unaffected -- that is the filesystem gate's
job, not the environment's.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from rudra.compat.own_interpreter import is_own_virtualenv, path_without_own

if TYPE_CHECKING:  # pragma: no cover
    from rudra.config.loader import Config

# Anchored at the end so KEYBOARD_LAYOUT and TOKENIZERS_PARALLELISM survive;
# AWS_ is a prefix because its credential vars do not share a suffix.
SECRET_NAME_RE = re.compile(r"(_KEY|_KEY_ID|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)$|^AWS_")

# pip's own switch: refuse `install`, `uninstall` and `download` outside a
# virtualenv (OPEN-120). Named once, because tests and the docs quote it.
PIP_REQUIRE_VIRTUALENV = "PIP_REQUIRE_VIRTUALENV"


def scrubbed_env(cfg: Config) -> dict[str, str]:
    """`os.environ` minus every secret-shaped key, and minus Rudra's own venv.

    The second subtraction is OPEN-80. Rudra runs from its own virtualenv, so
    that directory is first on `PATH` and `VIRTUAL_ENV` names it -- which made
    `python3`, `pip` and `pip install` inside an agent's shell mean *Rudra's*
    interpreter and *Rudra's* site-packages. Nine distributions from an August
    Flask run were still in Rudra's `.venv` on 2026-09-02, every one recording
    `pip` as its installer against a lock file that names none of them.

    **Exactly one environment is removed, and A1.44 is why it is only one.**
    LocalShellBackend's default is an EMPTY environment, which made every
    venv, nvm, rustup and pyenv toolchain invisible; this function exists to
    undo that. So every other `PATH` entry survives in its original order and
    spelling, and a `VIRTUAL_ENV` naming the *user's* project venv survives
    too -- that one is exactly what A1.44 exists to keep.

    Not a deny rule on `pip install`, deliberately. That would be
    `_system_interpreter`'s own mistake one level up: naming a spelling
    instead of removing the capability. `pip`, `python -m pip`, `uv pip`,
    `pip3`, a `setup.py`, a `Makefile` target and a test that shells out all
    reach the same place.

    **And pip may not install outside a virtualenv (OPEN-120).** Run
    `a04f89bd2ed6`'s tester, in a project with no venv, found `pip3` on PATH
    and ran `pip3 install Flask==3.0.3 SQLAlchemy==2.0.29` -- into the
    MACHINE's Python, downgrading both. That is the paragraph above's
    argument answered the other way: `PIP_REQUIRE_VIRTUALENV` is not a
    spelling but pip's own switch, read by `pip`, `pip3`, `python -m pip`, a
    `Makefile` target and a test that shells out alike, and pip inside a
    venv ignores it. `uv pip` already refuses without a venv. Not covered:
    `pip install --isolated` (ignores the environment), `setup.py install`,
    and conda, whose environments pip does not count as virtualenvs. Set
    rather than defaulted, so an exported `0` cannot switch it off -- the
    owner made it a floor. For a Python project, `testing/project_env.py`
    builds the `.venv` that pip is then allowed to install into.
    """
    configured = {model.api_key_env for model in cfg.models.values() if model.api_key_env}
    env = {
        name: value
        for name, value in os.environ.items()
        if name not in configured and not SECRET_NAME_RE.search(name)
    }

    if "PATH" in env:
        env["PATH"] = path_without_own(env["PATH"])
    if "VIRTUAL_ENV" in env and is_own_virtualenv(env["VIRTUAL_ENV"]):
        del env["VIRTUAL_ENV"]

    env[PIP_REQUIRE_VIRTUALENV] = "1"

    return env


__all__ = ["PIP_REQUIRE_VIRTUALENV", "SECRET_NAME_RE", "scrubbed_env"]
