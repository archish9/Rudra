"""Live verification of a configured role — C1.6.

Four stages, each reported separately, because they fail for different
reasons and a single pass/fail hides which. In particular, Reach passing says
nothing about Tools: a model can answer a prompt perfectly and never emit a
tool call, and deepagents requires tool calls for every agent.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import tool

from rudra.config import get_config
from rudra.llm.factory import build_model

ROLES_TO_PROBE: tuple[str, ...] = ("planner", "coder")


@dataclass(frozen=True)
class ProbeResult:
    """One row of `rudra models test` output."""

    role: str
    provider: str
    model: str
    construct: str
    reach: str
    tools: str
    context_tokens: int | None
    ok: bool


@tool
def echo(text: str) -> str:
    """Echo text back."""
    return text


def _short(error: Exception) -> str:
    """One-line error text, so a table row stays a row."""
    text = str(error).replace("\n", " ")
    return text if len(text) <= 120 else f"{text[:117]}..."


def probe_role(role: str) -> ProbeResult:
    """Construct, reach, and tool-test one role. Never raises."""
    settings = get_config().model_for(role)
    skipped = "skipped"

    try:
        model = build_model(role)
    except Exception as error:  # noqa: BLE001 — every failure is a reportable row
        return ProbeResult(
            role=role,
            provider=settings.provider,
            model=settings.model,
            construct=_short(error),
            reach=skipped,
            tools=skipped,
            context_tokens=None,
            ok=False,
        )

    context_tokens = (model.profile or {}).get("max_input_tokens")

    try:
        model.invoke("Reply with the single word: ok")
    except Exception as error:  # noqa: BLE001
        return ProbeResult(
            role=role,
            provider=settings.provider,
            model=settings.model,
            construct="ok",
            reach=_short(error),
            tools=skipped,
            context_tokens=context_tokens,
            ok=False,
        )

    try:
        response = model.bind_tools([echo]).invoke("Call the echo tool with text hello.")
        tools = "ok" if response.tool_calls else "no tool_calls emitted"
    except Exception as error:  # noqa: BLE001
        tools = _short(error)

    return ProbeResult(
        role=role,
        provider=settings.provider,
        model=settings.model,
        construct="ok",
        reach="ok",
        tools=tools,
        context_tokens=context_tokens,
        ok=tools == "ok",
    )
