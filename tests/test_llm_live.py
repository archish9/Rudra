"""Live model checks. Skipped unless explicitly enabled.

Two conditions, both required: RUDRA_LIVE_TESTS=1 opts in, and the configured
role must name an API key variable that is actually set. CI sets neither, so
CI never reaches a network call and needs no secrets.

Unlike every other test module, these deliberately do NOT chdir away from the
project root — they are meant to exercise the developer's real configuration,
.env included. That is the whole point: the offline suite proves the code is
correct against coded defaults, and this proves the configuration in front of
you actually reaches a model that can call tools.

Run against the configured backend with:
    RUDRA_LIVE_TESTS=1 .venv/bin/pytest -m live -q
"""

from __future__ import annotations

import os

import pytest

from rudra.config import get_config, reset_config
from rudra.llm import build_model
from rudra.llm.probe import probe_role

pytestmark = pytest.mark.live


def requires_live_backend() -> None:
    if os.getenv("RUDRA_LIVE_TESTS") != "1":
        pytest.skip("set RUDRA_LIVE_TESTS=1 to run live model checks")
    reset_config()
    settings = get_config().model_for("planner")
    if settings.api_key_env and not os.getenv(settings.api_key_env):
        pytest.skip(f"{settings.api_key_env} is not set")


def test_the_configured_planner_can_call_a_tool() -> None:
    """deepagents hard-requires tool calling. Reachability proves nothing
    about it — a model can answer a prompt and still never emit a tool call."""
    requires_live_backend()

    from langchain_core.tools import tool

    @tool
    def echo(text: str) -> str:
        """Echo text back."""
        return text

    model = build_model("planner")
    response = model.bind_tools([echo]).invoke("Call the echo tool with text hello.")

    assert response.tool_calls, f"model emitted no tool calls: {response.content!r}"
    assert response.tool_calls[0]["name"] == "echo"


def test_probe_role_reports_all_four_stages_ok() -> None:
    requires_live_backend()

    result = probe_role("planner")

    assert result.construct == "ok"
    assert result.reach == "ok"
    assert result.tools == "ok"
    assert result.ok is True
