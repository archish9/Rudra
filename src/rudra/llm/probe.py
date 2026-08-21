"""Live verification of a configured role — C1.6.

Four stages, each reported separately, because they fail for different
reasons and a single pass/fail hides which. In particular, Reach passing says
nothing about Tools: a model can answer a prompt perfectly and never emit a
tool call, and deepagents requires tool calls for every agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import tool

from rudra.config import get_config
from rudra.config.schema import BUILTIN_ROLES
from rudra.llm.factory import build_model
from rudra.llm.providers import effective_base_url

# Kept for callers that want the historical pair. Prefer roles_to_probe,
# which covers every role without probing one endpoint five times.
ROLES_TO_PROBE: tuple[str, ...] = ("planner", "coder")


def roles_to_probe(cfg: Any) -> list[tuple[tuple[str, ...], Any]]:
    """Distinct model endpoints, each with the roles that share it.

    Step 9b took BUILTIN_ROLES from three to five (C6.2-C6.4). Probing each
    one would fire five network calls where a single-model setup has a
    single endpoint. Deduping on the resolved identity keeps
    `rudra models test` honest for a user who configures `[model.reviewer]`
    separately, and cheap for everyone else.

    Ordered by first appearance in BUILTIN_ROLES so the table is stable
    between runs.

    Returns:
        A list of (roles sharing the endpoint, that endpoint's ModelConfig).
    """
    grouped: dict[tuple[str, str, str], list[str]] = {}
    configs: dict[tuple[str, str, str], Any] = {}

    for role in BUILTIN_ROLES:
        model = cfg.models[role]
        identity = (model.provider, effective_base_url(model) or "", model.model)
        if identity not in grouped:
            grouped[identity] = []
            configs[identity] = model
        grouped[identity].append(role)

    return [(tuple(roles), configs[identity]) for identity, roles in grouped.items()]


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


def probe_role(role: str, cfg: Any = None) -> ProbeResult:
    """Construct, reach, and tool-test one role. Never raises.

    Args:
        role: The role to probe.
        cfg: Config to read from. Defaults to the process-wide one, which is
            built from the CWD -- so `models test --project-dir X` and
            `doctor -d X` used to load X's config, then probe whatever the
            *current directory* resolved to, and print a table about a
            project they never read (CR-G2). Both callers now pass the
            Config they already loaded.
    """
    config = cfg if cfg is not None else get_config()
    settings = config.model_for(role)
    skipped = "skipped"

    try:
        model = build_model(role, config)
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
