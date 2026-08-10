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

if TYPE_CHECKING:  # pragma: no cover
    from rudra.config.loader import Config

# Anchored at the end so KEYBOARD_LAYOUT and TOKENIZERS_PARALLELISM survive;
# AWS_ is a prefix because its credential vars do not share a suffix.
SECRET_NAME_RE = re.compile(r"(_KEY|_KEY_ID|_TOKEN|_SECRET|_PASSWORD|_CREDENTIALS)$|^AWS_")


def scrubbed_env(cfg: Config) -> dict[str, str]:
    """`os.environ` minus every secret-shaped or configured key variable."""
    configured = {model.api_key_env for model in cfg.models.values() if model.api_key_env}
    return {
        name: value
        for name, value in os.environ.items()
        if name not in configured and not SECRET_NAME_RE.search(name)
    }


__all__ = ["SECRET_NAME_RE", "scrubbed_env"]
