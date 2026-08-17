"""A1.81: a key put where a variable *name* belongs must not be printed.

`api_key_env` names an environment variable. Confusing it with the key
itself is the obvious user error -- every other tool in this space takes
the key directly -- and until this guard existed the resulting error
message rendered the key verbatim into the terminal.
"""

from __future__ import annotations

import pytest

from rudra.llm.errors import MissingApiKeyError

SECRETS = (
    "sk-or-v1-9307625568" + "40def84a9aa247f2871f5bdf5a60d2a5dca8f16abcdef0123",
    "ghp_" + "A" * 36,
    "AKIA" + "B" * 16,
)


@pytest.mark.parametrize("secret", SECRETS)
def test_a_key_in_the_name_field_is_never_rendered(secret: str) -> None:
    message = str(MissingApiKeyError("planner", secret))

    assert secret not in message
    # Nor any long run of it -- a partial leak is still a leak.
    assert not any(secret[i : i + 20] in message for i in range(len(secret) - 20))


def test_the_message_says_what_the_user_actually_did_wrong() -> None:
    secret = "sk-or-v1-" + "c" * 50
    message = str(MissingApiKeyError("planner", secret))

    assert "api_key_env" in message
    assert "name" in message.lower()


def test_a_real_variable_name_is_still_shown() -> None:
    """The guard must not blind the common, correct case."""
    message = str(MissingApiKeyError("planner", "OPENROUTER_API_KEY"))

    assert "OPENROUTER_API_KEY" in message
