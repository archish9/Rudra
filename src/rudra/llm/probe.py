"""Live verification of a configured role — C1.6.

Four stages, each reported separately, because they fail for different
reasons and a single pass/fail hides which. In particular, Reach passing says
nothing about Tools: a model can answer a prompt perfectly and never emit a
tool call, and deepagents requires tool calls for every agent.
"""

from __future__ import annotations

import time
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
    # How long each of the two live calls took, in seconds (OPEN-40). Both
    # calls were already being made and neither was timed, so `models test`
    # answered "is this model usable?" without the half of the answer that
    # decides a run's wall clock: a run is `calls x latency`, and run6 spent
    # 91% of its clock inside the model.
    #
    # `None` on every failure path rather than an elapsed time. How long a
    # connection took to refuse is the error's number, not the model's, and
    # a duration printed beside an error invites reading it as one. It also
    # keeps the two states distinguishable: "not measured" and "measured at
    # zero" are different facts.
    reach_seconds: float | None = None
    tools_seconds: float | None = None


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

    # The client SDK's own retries are off (OPEN-115), and a probe runs
    # outside any agent graph, so both calls go through the same retry an
    # agent's calls do. A flap that clears on the second attempt is an
    # endpoint that works, and a red row for it sends a user to debug a
    # config that is fine. A retried call's seconds include its backoff.
    from rudra.middleware.model_retry import ModelRetryMiddleware

    retry = ModelRetryMiddleware(role)

    started = time.perf_counter()
    try:
        retry.wrap_model_call("Reply with the single word: ok", model.invoke)
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

    reach_seconds = time.perf_counter() - started

    # A round trip that returned no tool calls is still a round trip: the
    # model answered, it just answered wrong, so the duration is the
    # model's and is kept. Only the exception path loses its number.
    tools_seconds: float | None = None
    started = time.perf_counter()
    try:
        response = retry.wrap_model_call(
            "Call the echo tool with text hello.", model.bind_tools([echo]).invoke
        )
        tools = "ok" if response.tool_calls else "no tool_calls emitted"
        tools_seconds = time.perf_counter() - started
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
        reach_seconds=reach_seconds,
        tools_seconds=tools_seconds,
    )
