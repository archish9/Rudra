"""One Rudra run, one Langfuse trace (OPEN-110).

**Three layers, and "every trace" needs all three.** LangChain sees only
what LangChain does, which is most of a run and not the part that explains
a bad one:

* **graph and model spans** -- the `CallbackHandler` in the `RunnableConfig`
  that `subagents/runner.py` and `agent/planner_agent.py` hand to
  `astream`. Every model call, tool call, subagent subtree, token count and
  latency, for free, because langgraph propagates callbacks into subgraphs.
* **what RUDRA did** -- `consumer()` is a `TraceSink` recorder, registered
  at the same seam as `trace/debug.py::debug_consumer`. A guard halt, a
  refused plan, a reindented edit and a planner-write refusal are things
  Rudra did on its own account; no LangChain callback fires for any of
  them, and they are the lines a maintainer reads first.
* **the run's verdict and cost** -- `finish()` at `RudraAgent.close()`,
  where `usage.json`'s numbers and the ledger's counts are known.

They land in ONE trace because all three carry the same `trace_id`, seeded
from the run's own session id (`create_trace_id(seed=...)` is deterministic).
So the id in `meta.json`, the id in `debug-<id>.jsonl`'s filename and the
Langfuse URL are the same run, and a user can say "run f845b496a2aa" and
mean one thing.

**Redaction is not optional and has one chokepoint.** The LangChain handler
builds `LangfuseSpan`/`LangfuseGeneration` objects, and every one of
`input`, `output` and `metadata` passes through the client's `mask` before
export (`langfuse/_client/span.py::_process_media_and_apply_mask`). So the
`mask=` below covers payloads Rudra never touches, which is the only reason
this integration is safe to ship: `trace/redact.py` protects the artifacts a
human keeps, and without this hook a cloud host would be the one artifact it
did not protect (A1.95).

**It is off unless keys are configured**, and that is a design constraint
rather than a preference: this is the first outbound network call Rudra
makes for a non-inference purpose, and local-first is a product goal
(CLAUDE.md §1 goal 7). No keys, no client, no handler, no cost.

**Nothing here raises.** A host that is down, a rejected key, a langfuse
version that moved an import -- each degrades to "no tracing" with one
console line, which is `memory/degrade.py`'s bargain and `write_usage_log`'s
rule (C7.5): a run that did its work must not be reported failed because
its own bookkeeping could not be sent.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

from rudra.trace.redact import redact

LOGGER = logging.getLogger("rudra.telemetry")

EVENT_PREFIX = "rudra."
"""Every observation this module creates itself is named `rudra.<what>`,
so a Langfuse trace separates Rudra's own record from the LangChain spans
beside it at a glance. Named here because it is what somebody will filter
on, and a second spelling is a second answer to "why is my filter empty"
(`trace/debug.py::MODEL_CALL_KIND`'s reasoning)."""

MAX_PAYLOAD_CHARS = 4000
"""What of one event's payload is sent. The debug log is uncapped and
bounded by retention; this is bounded because it crosses a network and
because a 200 KB tool result is not read in a trace viewer -- the transcript
already caps at 2000 for the same reason, and this is looser because a
maintainer reading a trace has nothing else to open."""

_STANDARD_PUBLIC = "LANGFUSE_PUBLIC_KEY"
_STANDARD_SECRET = "LANGFUSE_SECRET_KEY"
"""The SDK's own environment variables, consulted last. A user who already
runs Langfuse has these set; making them work costs two lines and saves a
config edit. Rudra's own config wins, which is `_resolve_api_key`'s rule
(llm/factory.py:50): the more specific statement is the one the user made
here, about this project."""


SDK_LOGGER = "langfuse"
"""The SDK's own logger tree, which is not under `rudra.*` and so is not
captured by `trace/debug.py`."""

EXPORTER_LOGGER = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
"""The other half of the noise, and the louder one. Langfuse exports over
OTLP/HTTP, and the retry loop lives in the exporter rather than in the SDK
-- so redirecting `langfuse` alone left *"Transient error
HTTPConnectionPool(host='127.0.0.1', port=9) ... retrying in 1.18s"* and
*"Failed to export span batch due to timeout"* on the terminal, measured.

Named exactly, never the `opentelemetry` tree: mempalace pulls chromadb,
which pulls its own OpenTelemetry, and taking the parent tree would mean
Rudra deciding where somebody else's records go."""

SDK_FORWARD_LOGGER = "rudra.telemetry.sdk"
"""Where its records are re-emitted, so they land in `debug-<id>.jsonl`
with everything else about the run."""


class _ForwardToRunLog(logging.Handler):
    """Re-emit one `langfuse.*` record under `rudra.telemetry.sdk`.

    The SDK logs its export failures at WARNING and ERROR, and with no
    handler of its own they reach `logging.lastResort`, which writes to
    **stderr** -- past Rich, past the console tee, past every level flag.
    Measured against an unreachable host: three lines per run, one of them
    *"Unexpected error occurred. Please check your request and contact
    support"*, printed after Rudra's own result panel.

    Silencing the tree outright would be the easy fix and the wrong one:
    those lines are the diagnosis when a user says "my Langfuse project is
    empty". So they are forwarded rather than dropped -- into the file that
    is meant to be the complete record, which is where a message about
    Rudra's own bookkeeping belongs.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            logging.getLogger(SDK_FORWARD_LOGGER).debug(
                "%s: %s", record.levelname, record.getMessage()
            )
        except Exception:  # noqa: BLE001 -- logging about logging never raises
            return


def _quiet_sdk_logging() -> None:
    """Keep the SDK's chatter off the terminal and in the run log.

    Idempotent by handler type, the way `install_console_recorder` is
    idempotent by file type: the REPL builds a fresh run per input against
    one process, and a handler added per turn would multiply every line.
    """
    for name in (SDK_LOGGER, EXPORTER_LOGGER):
        logger = logging.getLogger(name)
        if any(isinstance(handler, _ForwardToRunLog) for handler in logger.handlers):
            continue
        logger.addHandler(_ForwardToRunLog())
        # Stop here rather than climbing to the root logger, whose handlers
        # belong to whoever embedded Rudra -- `trace/debug.py`'s reasoning
        # for the `rudra` tree, applied to the two beside it.
        logger.propagate = False


def _mask(*, data: Any) -> Any:
    """Redact every string in `data`, however it is nested.

    The keyword-only `data` is the SDK's calling convention
    (`_mask_attribute` calls `self._langfuse_client._mask(data=data)`).

    Failure returns the string `<masking failed>` rather than the original:
    if this function cannot do its job, sending the payload anyway is the
    one outcome that must not happen. That is the opposite of every other
    swallow in Rudra, and deliberately so -- the others protect a run from
    its bookkeeping, and this protects a user's secrets from a cloud host.
    """
    try:
        if isinstance(data, str):
            return redact(data)
        if isinstance(data, dict):
            return {key: _mask(data=value) for key, value in data.items()}
        if isinstance(data, (list, tuple)):
            return [_mask(data=item) for item in data]
        return data
    except Exception:  # noqa: BLE001 -- see docstring: fail closed, not open
        return "<masking failed>"


def _resolve(literal: str | None, named: str | None, standard: str) -> str | None:
    """A key: the literal, else the named variable, else the SDK's own.

    `_resolve_api_key`'s precedence (llm/factory.py:50), for its reason: a
    user who wrote the key into config meant that key, and preferring a
    stale environment variable over it is the "which source won?" defect
    the whole loader exists to prevent (OPEN-6).
    """
    if literal:
        return literal
    if named:
        value = os.getenv(named)
        if value:
            return value
    return os.getenv(standard) or None


def _truncate(text: str) -> str:
    if len(text) <= MAX_PAYLOAD_CHARS:
        return text
    return f"{text[:MAX_PAYLOAD_CHARS]}… [{len(text) - MAX_PAYLOAD_CHARS} more chars]"


_LEVELS = {
    "tool_error": "ERROR",
    "notice": "WARNING",
}
"""How a Rudra event reads in a trace viewer. A NOTICE is Rudra saying it
DID something on its own account -- halted an invocation, refused a plan,
repaired an edit -- and every one of those is a thing somebody is looking
for when they open the trace, so it must not read as ordinary chatter."""


@dataclass
class Telemetry:
    """One run's Langfuse client, handler and trace id.

    Built once per run and shared by reference, the rule the gate, the
    FactStore, the Ledger and the TraceSink all follow
    (`subagents/runner.py:312`): two clients would be two traces of one run.
    """

    client: Any
    handler: Any
    trace_id: str
    session_id: str
    host: str
    project_slug: str = ""
    callbacks: list[Any] = field(default_factory=list)

    def config(self, role: str) -> dict[str, Any]:
        """The `RunnableConfig` fragment that puts one stream in this trace.

        `langfuse_session_id`, `langfuse_trace_name` and `langfuse_tags` are
        the metadata keys the langchain integration reads
        (`langfuse/langchain`), so the run groups in the UI without this
        module reaching into the handler.
        """
        return {
            "callbacks": list(self.callbacks),
            "metadata": {
                "langfuse_session_id": self.session_id,
                "langfuse_trace_name": f"rudra run {self.session_id}",
                "langfuse_tags": ["rudra", role],
                "rudra_role": role,
                "rudra_project": self.project_slug,
            },
        }

    def event(
        self,
        name: str,
        *,
        payload: str = "",
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
    ) -> None:
        """One observation in this run's trace. Never raises."""
        try:
            self.client.create_event(
                trace_context={"trace_id": self.trace_id},
                name=f"{EVENT_PREFIX}{name}",
                output=_truncate(payload) if payload else None,
                metadata=metadata,
                level=level,
            )
        except Exception:  # noqa: BLE001 -- telemetry never ends a run (C7.5)
            LOGGER.debug("langfuse event not sent", exc_info=True)

    def consumer(self) -> Any:
        """A `TraceSink` recorder: every Rudra event into this trace.

        Registered with `add_recorder`, never `add`, for `debug_consumer`'s
        reason (OPEN-7): as an ordinary consumer it would sit behind the
        sink's level filter, and `--no-verbose` would quietly cut the
        remote record down to errors.
        """

        def consume_event(event: Any) -> None:
            kind = getattr(getattr(event, "kind", None), "value", "") or "event"
            self.event(
                f"{kind}.{event.name}" if getattr(event, "name", "") else kind,
                payload=getattr(event, "payload", "") or "",
                metadata={
                    "role": getattr(event, "role", ""),
                    "namespace": list(getattr(event, "namespace", ()) or ()),
                    "at": getattr(event, "at", 0.0),
                },
                level=_LEVELS.get(kind, "DEFAULT"),
            )

        return consume_event

    def finish(self, *, usage: Any = None, ledger: Any = None, message: str = "") -> None:
        """The run's cost and verdict, then flush. Never raises.

        Called from `RudraAgent.close()`, where `usage.json` has just been
        written and the ledger is final -- the same moment `archive.py`
        copies them out, and for the same reason: it is the last point at
        which every instrument still exists.
        """
        counts: dict[str, Any] = {}
        try:
            if ledger is not None:
                counts = dict(ledger.counts())
        except Exception:  # noqa: BLE001 -- telemetry never ends a run
            counts = {}

        tally: dict[str, Any] = {}
        try:
            if usage is not None:
                tally = usage.as_dict()
        except Exception:  # noqa: BLE001 -- telemetry never ends a run
            tally = {}

        self.event(
            "run.finished",
            payload=message,
            metadata={"tasks": counts, "usage": tally, "run_id": self.session_id},
        )
        try:
            # A BOOLEAN score, so a Langfuse project can chart "how many of
            # my runs finish" without anybody writing a parser -- which is
            # §8a failure shape 3, one system out.
            self.client.create_score(
                name="rudra.run.completed",
                value=1 if counts.get("blocked", 0) == 0 and counts.get("pending", 0) == 0 else 0,
                trace_id=self.trace_id,
                data_type="BOOLEAN",
                comment=message[:500] if message else None,
            )
        except Exception:  # noqa: BLE001 -- telemetry never ends a run
            LOGGER.debug("langfuse score not sent", exc_info=True)
        self.flush()

    def flush(self) -> None:
        """Send what is queued. Never raises, and never `shutdown()`.

        The SDK keeps one resource manager per public key, so shutting the
        client down would tear it down for every later run in the same
        process -- and the REPL builds a fresh agent, and a fresh run, per
        input (S15.4).
        """
        try:
            self.client.flush()
        except Exception:  # noqa: BLE001 -- telemetry never ends a run (C7.5)
            LOGGER.debug("langfuse flush failed", exc_info=True)

    def url(self) -> str:
        """Where to read this run. Printed once, so a user can hand it over."""
        return f"{self.host.rstrip('/')}/trace/{self.trace_id}"


def build_telemetry(
    cfg: Any,
    *,
    session_id: str,
    project_slug: str = "",
    console: Any = None,
) -> Telemetry | None:
    """Build this run's Langfuse client, or return None.

    `None` is the normal answer and means "not configured": no keys, or
    `[telemetry] enabled = false`. It is also the answer to every failure
    -- an SDK that will not import, a client that will not construct --
    with one console line, because a user who configured this and is not
    getting it must be told, and a user who did not must not be nagged.

    The import is function-local, following `memory/store.py`'s rule: this
    module is reached from `create_main_agent`, and a module-scope import
    of an OpenTelemetry-based SDK would land on `rudra --version`.
    """
    telemetry = getattr(cfg, "telemetry", None)
    if telemetry is None or not getattr(telemetry, "enabled", False):
        return None

    public_key = _resolve(telemetry.public_key, telemetry.public_key_env, _STANDARD_PUBLIC)
    secret_key = _resolve(telemetry.secret_key, telemetry.secret_key_env, _STANDARD_SECRET)
    if not public_key or not secret_key:
        # Deliberately silent. Half a key pair is worth saying something
        # about; no key pair at all is the default state of every install.
        if public_key or secret_key:
            _warn(console, "Langfuse needs both a public key and a secret key; tracing is off.")
        return None

    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
    except Exception as exc:  # noqa: BLE001 -- a missing SDK is not a failed run
        _warn(console, f"Langfuse tracing is off: {exc}")
        return None

    try:
        client = Langfuse(
            public_key=public_key,
            secret_key=secret_key,
            host=telemetry.host,
            timeout=telemetry.timeout,
            sample_rate=telemetry.sample_rate,
            environment=telemetry.environment,
            release=_version(),
            # The whole security argument for this feature. See the module
            # docstring: it covers the LangChain handler's payloads too,
            # which nothing else in Rudra can reach.
            mask=_mask,
        )
        # Deterministic from the run id, so the Langfuse trace, the debug
        # log's filename and meta.json's `run_id` all name one run.
        trace_id = client.create_trace_id(seed=session_id)
        handler = CallbackHandler(public_key=public_key, trace_context={"trace_id": trace_id})
        _quiet_sdk_logging()
    except Exception as exc:  # noqa: BLE001 -- telemetry never ends a run
        _warn(console, f"Langfuse tracing is off: {exc}")
        return None

    built = Telemetry(
        client=client,
        handler=handler,
        trace_id=trace_id,
        session_id=session_id,
        host=telemetry.host,
        project_slug=project_slug,
        callbacks=[handler],
    )
    if console is not None:
        console.print(f"[dim]Langfuse tracing → {built.url()}[/dim]")
    return built


def _warn(console: Any, message: str) -> None:
    """Say it once, on the console and in the run log.

    Both, because the console scrolls away and the run log is what gets
    attached -- and a user asking "why is my Langfuse project empty" is
    asking about a line one of the two still holds.
    """
    LOGGER.warning(message)
    if console is not None:
        try:
            console.print(f"[yellow]{message}[/yellow]")
        except Exception:  # noqa: BLE001 -- a console that refuses is not a failure
            return


def _version() -> str:
    try:
        import rudra

        return str(rudra.__version__)
    except Exception:  # noqa: BLE001 -- the trace is worth more than the field
        return "unknown"


__all__ = [
    "EVENT_PREFIX",
    "MAX_PAYLOAD_CHARS",
    "EXPORTER_LOGGER",
    "SDK_FORWARD_LOGGER",
    "SDK_LOGGER",
    "Telemetry",
    "build_telemetry",
]
