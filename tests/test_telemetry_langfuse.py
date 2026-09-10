"""Sending a run somewhere the maintainer can reach it (OPEN-110).

Rudra's whole support channel is a user emailing `.rudra/run/logs/`
(CLAUDE.md §8a) -- a folder they must find, read for secrets, and attach by
hand. This is the other half: keys in `.rudra/config.toml` and the run
reports itself to the user's own Langfuse project as it goes.

The rules these tests hold, in order of importance:

1. **redaction is not optional.** The LangChain handler builds Langfuse
   observations, and every `input`, `output` and `metadata` on one passes
   through the client's `mask` -- so `mask=` is a real chokepoint over
   payloads Rudra never touches, and it is the only reason this is safe to
   ship. A test that lets a credential through is the one failure here that
   matters more than the feature;
2. **it is off unless keys are configured.** No keys, no client, no handler,
   no network call -- local-first is a product goal (§1 goal 7);
3. **nothing here may end a run** (C7.5), the rule `memory/degrade.py` and
   `write_usage_log` already follow;
4. **one run is one trace.** Every stream carries the same trace id, seeded
   from the run id, so the id in `meta.json`, the name of
   `debug-<id>.jsonl` and the Langfuse URL are the same run.

No test here reaches the network: `Langfuse` and `CallbackHandler` are
replaced at their import site, which works because `build_telemetry`
imports them inside the function (the `memory/store.py` rule -- an
OpenTelemetry SDK at module scope would land on `rudra --version`).
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from typing import Any

import pytest

from rudra.telemetry.langfuse_sink import (
    MAX_PAYLOAD_CHARS,
    Telemetry,
    _mask,
    build_telemetry,
)
from rudra.trace.events import TraceEvent, TraceKind


class FakeClient:
    """Stands in for `langfuse.Langfuse`. Records instead of exporting."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.events: list[dict[str, Any]] = []
        self.scores: list[dict[str, Any]] = []
        self.flushes = 0
        self.shutdowns = 0

    def create_trace_id(self, *, seed: str | None = None) -> str:
        return f"trace-for-{seed}"

    def create_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def create_score(self, **kwargs: Any) -> None:
        self.scores.append(kwargs)

    def flush(self) -> None:
        self.flushes += 1

    def shutdown(self) -> None:
        self.shutdowns += 1


class FakeHandler:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


@dataclass
class _Telemetry:
    enabled: bool = True
    host: str = "https://langfuse.example"
    public_key: str | None = "pk-lf-1"
    public_key_env: str | None = None
    secret_key: str | None = "sk-lf-2"
    secret_key_env: str | None = None
    sample_rate: float = 1.0
    timeout: int = 10
    environment: str | None = None


@dataclass
class _Cfg:
    telemetry: Any


class _Console:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: str = "") -> None:
        self.lines.append(str(text))


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> list[FakeClient]:
    """Replace the SDK at its import site. Returns every client built."""
    import langfuse
    import langfuse.langchain

    built: list[FakeClient] = []

    def make(**kwargs: Any) -> FakeClient:
        client = FakeClient(**kwargs)
        built.append(client)
        return client

    monkeypatch.setattr(langfuse, "Langfuse", make)
    monkeypatch.setattr(langfuse.langchain, "CallbackHandler", FakeHandler)
    return built


# --- off unless asked for ---------------------------------------------------


def test_no_keys_means_no_client(fake_sdk: list[FakeClient]) -> None:
    """The default state of every install. Silent, and it must stay silent:
    a user who never configured this must not be nagged once per run."""
    console = _Console()

    built = build_telemetry(
        _Cfg(_Telemetry(public_key=None, secret_key=None)), session_id="r", console=console
    )

    assert built is None
    assert fake_sdk == []
    assert console.lines == []


def test_half_a_key_pair_says_so(fake_sdk: list[FakeClient]) -> None:
    """Silence is right for "not configured" and wrong for "configured
    wrongly": that user believes their runs are being traced."""
    console = _Console()

    assert (
        build_telemetry(_Cfg(_Telemetry(secret_key=None)), session_id="r", console=console) is None
    )
    assert any("public key and a secret key" in line for line in console.lines)


def test_disabled_means_no_client_even_with_keys(fake_sdk: list[FakeClient]) -> None:
    """`enabled = false` is for a key pair in the user-global config and one
    project that must not be traced."""
    assert build_telemetry(_Cfg(_Telemetry(enabled=False)), session_id="r") is None
    assert fake_sdk == []


def test_an_absent_telemetry_section_is_not_an_error() -> None:
    """Every Config built by hand in a test predates this section."""

    class Bare:
        pass

    assert build_telemetry(Bare(), session_id="r") is None


# --- what it builds ---------------------------------------------------------


def test_keys_are_resolved_from_config_first(fake_sdk: list[FakeClient]) -> None:
    built = build_telemetry(_Cfg(_Telemetry()), session_id="run1", console=_Console())

    assert built is not None
    assert fake_sdk[0].kwargs["public_key"] == "pk-lf-1"
    assert fake_sdk[0].kwargs["secret_key"] == "sk-lf-2"
    assert fake_sdk[0].kwargs["host"] == "https://langfuse.example"


def test_a_named_variable_is_read_when_no_literal_is_set(
    fake_sdk: list[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MY_LF_SECRET", "sk-from-env")

    build_telemetry(
        _Cfg(_Telemetry(secret_key=None, secret_key_env="MY_LF_SECRET")), session_id="r"
    )

    assert fake_sdk[0].kwargs["secret_key"] == "sk-from-env"


def test_the_sdks_own_variables_are_the_last_resort(
    fake_sdk: list[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user who already runs Langfuse has these set; making them work
    costs two lines and saves a config edit."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-standard")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-standard")

    build_telemetry(_Cfg(_Telemetry(public_key=None, secret_key=None)), session_id="r")

    assert fake_sdk[0].kwargs["public_key"] == "pk-standard"


def test_config_beats_the_environment(
    fake_sdk: list[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_resolve_api_key`'s precedence (OPEN-6): the user's more specific
    statement wins, and a stale variable quietly winning is the defect the
    whole loader exists to prevent."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-standard")

    build_telemetry(_Cfg(_Telemetry()), session_id="r")

    assert fake_sdk[0].kwargs["public_key"] == "pk-lf-1"


def test_the_trace_id_is_the_run_id(fake_sdk: list[FakeClient]) -> None:
    """One run, one trace, and a maintainer can say "run f845b496a2aa"."""
    built = build_telemetry(_Cfg(_Telemetry()), session_id="f845b496a2aa")

    assert built is not None
    assert built.trace_id == "trace-for-f845b496a2aa"
    assert built.handler.kwargs["trace_context"] == {"trace_id": "trace-for-f845b496a2aa"}


def test_an_sdk_that_will_not_build_is_not_a_failed_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C7.5: a run that did its work must not be reported failed because
    its own bookkeeping could not be sent."""
    import langfuse

    def explode(**kwargs: Any) -> Any:
        raise RuntimeError("host unreachable")

    monkeypatch.setattr(langfuse, "Langfuse", explode)
    console = _Console()

    assert build_telemetry(_Cfg(_Telemetry()), session_id="r", console=console) is None
    assert any("Langfuse tracing is off" in line for line in console.lines)


# --- redaction: the security rule -------------------------------------------


def test_the_mask_redacts_strings() -> None:
    assert "<redacted>" in _mask(data="api_key=sk-abcdefgh12345")


def test_the_mask_reaches_into_nested_payloads() -> None:
    """A LangChain span's input is a list of message dicts, and the secret
    is several levels down inside it."""
    masked = _mask(data={"messages": [{"content": "export API_KEY=sk-abcdefgh12345"}]})

    assert "sk-abcdefgh12345" not in str(masked)


def test_the_mask_leaves_ordinary_data_alone() -> None:
    assert _mask(data={"a": [1, 2.5, True, None]}) == {"a": [1, 2.5, True, None]}


def test_a_mask_that_fails_sends_nothing_rather_than_everything() -> None:
    """The one place in Rudra that fails CLOSED. Every other swallow here
    protects a run from its bookkeeping; this protects a user's secrets
    from a cloud host, and the safe direction is the opposite one."""

    class HostileDict(dict):
        def items(self):  # noqa: ANN201 - a mapping that raises mid-walk
            raise RuntimeError("no")

    masked = _mask(data=HostileDict(secret="sk-abcdefgh12345"))

    assert masked == "<masking failed>"
    assert "sk-abcdefgh12345" not in str(masked)


def test_the_client_is_built_with_the_mask(fake_sdk: list[FakeClient]) -> None:
    """The whole security argument. If this argument is ever dropped, the
    LangChain handler's payloads reach the host unredacted."""
    build_telemetry(_Cfg(_Telemetry()), session_id="r")

    assert fake_sdk[0].kwargs["mask"] is _mask


# --- what a stream is told --------------------------------------------------


def _built(fake_sdk: list[FakeClient]) -> Telemetry:
    telemetry = build_telemetry(_Cfg(_Telemetry()), session_id="run1", project_slug="app-1234")
    assert telemetry is not None
    return telemetry


def test_the_stream_config_carries_the_handler_and_the_session(
    fake_sdk: list[FakeClient],
) -> None:
    telemetry = _built(fake_sdk)
    config = telemetry.config("coder")

    assert config["callbacks"] == [telemetry.handler]
    assert config["metadata"]["langfuse_session_id"] == "run1"
    assert config["metadata"]["rudra_role"] == "coder"
    assert "coder" in config["metadata"]["langfuse_tags"]


def test_the_stream_config_does_not_carry_a_thread_id(fake_sdk: list[FakeClient]) -> None:
    """It is merged into a config that already has `configurable`, and the
    checkpointer's thread id must survive that merge."""
    assert "configurable" not in _built(fake_sdk).config("planner")


# --- Rudra's own events -----------------------------------------------------


def test_a_notice_reaches_the_trace(fake_sdk: list[FakeClient]) -> None:
    """A guard halt, a refused plan, a repaired edit: no LangChain callback
    fires for any of them, and they are what a maintainer reads first."""
    telemetry = _built(fake_sdk)

    telemetry.consumer()(
        TraceEvent(kind=TraceKind.NOTICE, role="rudra", name="guard", payload="80 tool calls")
    )

    sent = fake_sdk[0].events[-1]
    assert sent["name"] == "rudra.notice.guard"
    assert sent["output"] == "80 tool calls"
    assert sent["level"] == "WARNING"
    assert sent["trace_context"] == {"trace_id": telemetry.trace_id}


def test_a_tool_error_reads_as_an_error(fake_sdk: list[FakeClient]) -> None:
    telemetry = _built(fake_sdk)

    telemetry.consumer()(TraceEvent(kind=TraceKind.TOOL_ERROR, role="coder", payload="boom"))

    assert fake_sdk[0].events[-1]["level"] == "ERROR"


def test_an_oversized_payload_is_bounded(fake_sdk: list[FakeClient]) -> None:
    """This crosses a network, unlike the debug log, which is uncapped and
    bounded by retention instead."""
    telemetry = _built(fake_sdk)

    telemetry.consumer()(
        TraceEvent(kind=TraceKind.TOOL_RESULT, role="coder", payload="x" * (MAX_PAYLOAD_CHARS * 2))
    )

    assert len(fake_sdk[0].events[-1]["output"]) < MAX_PAYLOAD_CHARS * 2


def test_an_event_that_cannot_be_sent_does_not_end_a_run(fake_sdk: list[FakeClient]) -> None:
    telemetry = _built(fake_sdk)

    def explode(**kwargs: Any) -> None:
        raise RuntimeError("network")

    fake_sdk[0].create_event = explode  # type: ignore[method-assign]

    telemetry.consumer()(TraceEvent(kind=TraceKind.AI_TEXT, role="coder", payload="hello"))


# --- the run's verdict ------------------------------------------------------


class _Ledger:
    def __init__(self, **counts: int) -> None:
        self._counts = counts

    def counts(self) -> dict[str, int]:
        return self._counts


class _Usage:
    def as_dict(self) -> dict[str, Any]:
        return {"coder": {"calls": 7}}


def test_finish_reports_the_cost_and_the_verdict(fake_sdk: list[FakeClient]) -> None:
    telemetry = _built(fake_sdk)

    telemetry.finish(
        usage=_Usage(),
        ledger=_Ledger(requested=2, done=2, blocked=0, dropped=0, pending=0),
        message="2 tasks done",
    )

    finished = fake_sdk[0].events[-1]
    assert finished["name"] == "rudra.run.finished"
    assert finished["metadata"]["usage"]["coder"]["calls"] == 7
    assert finished["metadata"]["tasks"]["done"] == 2
    assert fake_sdk[0].scores[-1]["value"] == 1
    assert fake_sdk[0].flushes == 1


def test_a_blocked_run_scores_zero(fake_sdk: list[FakeClient]) -> None:
    telemetry = _built(fake_sdk)

    telemetry.finish(ledger=_Ledger(requested=2, done=1, blocked=1, dropped=0, pending=0))

    assert fake_sdk[0].scores[-1]["value"] == 0


def test_finish_never_raises_on_a_broken_ledger(fake_sdk: list[FakeClient]) -> None:
    class Broken:
        def counts(self) -> dict[str, int]:
            raise RuntimeError("no ledger")

    telemetry = _built(fake_sdk)
    telemetry.finish(ledger=Broken(), usage=None, message="")

    assert fake_sdk[0].flushes == 1


def test_flush_is_not_shutdown(fake_sdk: list[FakeClient]) -> None:
    """The SDK keeps one resource manager per public key, and the REPL
    builds a fresh run per input -- shutting it down would tear down every
    later turn's tracing (S15.4)."""
    telemetry = _built(fake_sdk)
    telemetry.flush()
    telemetry.finish(ledger=_Ledger(requested=1, done=1, blocked=0, dropped=0, pending=0))

    assert fake_sdk[0].shutdowns == 0
    assert fake_sdk[0].flushes == 2


def test_the_url_names_the_run(fake_sdk: list[FakeClient]) -> None:
    assert _built(fake_sdk).url() == "https://langfuse.example/trace/trace-for-run1"


# --- wiring -----------------------------------------------------------------


def _calls_in(module_name: str, function: str) -> str:
    import importlib

    module = importlib.import_module(module_name)
    tree = ast.parse(inspect.getsource(module))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return ast.unparse(node)
    raise AssertionError(f"{module_name} no longer defines {function}")


@pytest.mark.parametrize(
    ("module", "function"),
    [
        ("rudra.subagents.runner", "run_subagent"),
        ("rudra.agent.planner_agent", "_stream_planner_turn"),
    ],
)
def test_every_stream_site_puts_its_config_in_the_trace(module: str, function: str) -> None:
    """Both graph entry points, pinned by name (OPEN-101's lesson: what was
    missing there was anything making the SECOND call site use the fix)."""
    source = _calls_in(module, function)

    assert "telemetry.config(" in source, (
        f"{module}::{function} builds a RunnableConfig without the Langfuse "
        "callbacks, so that agent's model and tool spans are missing from the run's trace"
    )


def test_the_summariser_is_traced_too() -> None:
    """The one model call that goes through neither a subagent nor a planner
    stage. "Everything except one" is what makes a trace untrustworthy."""
    source = _calls_in("rudra.loop.engine", "summarise_architecture")

    assert "telemetry.config(" in source


def test_rudras_own_events_are_recorded_not_consumed() -> None:
    """`add_recorder`, never `add`: behind the sink's level filter,
    `--no-verbose` would cut the remote record down to errors (OPEN-7)."""
    source = _calls_in("rudra.agent.main_agent", "create_main_agent")

    assert "trace.add_recorder(telemetry.consumer())" in source


def test_langfuse_is_not_imported_at_module_scope() -> None:
    """`memory/store.py`'s rule: an OpenTelemetry-based SDK imported at
    module scope would land on `rudra --version`."""
    import rudra.telemetry.langfuse_sink as module

    tree = ast.parse(inspect.getsource(module))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {alias.name for alias in node.names} | {getattr(node, "module", "") or ""}
            assert not any(name.startswith("langfuse") for name in names)


# --- the SDK's own chatter --------------------------------------------------


def test_the_sdks_logging_is_kept_off_the_terminal(fake_sdk: list[FakeClient]) -> None:
    """Measured on an unreachable host: three lines per run reached stderr,
    past Rich, past the console tee and past every level flag -- one of them
    *"Unexpected error occurred ... contact support"*, printed after Rudra's
    own result panel.

    Both trees, because the retry loop is the EXPORTER's rather than the
    SDK's: redirecting `langfuse` alone left the two loudest lines behind.
    """
    import logging

    from rudra.telemetry.langfuse_sink import EXPORTER_LOGGER, SDK_LOGGER, _ForwardToRunLog

    for name in (SDK_LOGGER, EXPORTER_LOGGER):
        logger = logging.getLogger(name)
        logger.handlers = [h for h in logger.handlers if not isinstance(h, _ForwardToRunLog)]
        logger.propagate = True

    build_telemetry(_Cfg(_Telemetry()), session_id="r")

    for name in (SDK_LOGGER, EXPORTER_LOGGER):
        logger = logging.getLogger(name)
        assert any(isinstance(h, _ForwardToRunLog) for h in logger.handlers)
        assert logger.propagate is False


def test_the_sdks_lines_reach_the_run_log(fake_sdk: list[FakeClient]) -> None:
    """Forwarded, not silenced: those lines ARE the diagnosis when a user
    says "my Langfuse project is empty"."""
    import io
    import json
    import logging

    from rudra.trace.debug import _JsonLines

    build_telemetry(_Cfg(_Telemetry()), session_id="r")

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(_JsonLines())
    rudra_logger = logging.getLogger("rudra")
    previous = rudra_logger.handlers[:]
    rudra_logger.handlers = [handler]
    rudra_logger.setLevel(logging.DEBUG)
    try:
        logging.getLogger("langfuse").error("Unexpected error occurred")
        handler.flush()
    finally:
        rudra_logger.handlers = previous

    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    assert any("Unexpected error occurred" in line.get("payload", "") for line in lines)


def test_forwarding_is_installed_once(fake_sdk: list[FakeClient]) -> None:
    """The REPL builds a fresh run per input against one process; a handler
    per turn would multiply every line."""
    import logging

    from rudra.telemetry.langfuse_sink import SDK_LOGGER, _ForwardToRunLog

    build_telemetry(_Cfg(_Telemetry()), session_id="r1")
    build_telemetry(_Cfg(_Telemetry()), session_id="r2")

    installed = [
        h for h in logging.getLogger(SDK_LOGGER).handlers if isinstance(h, _ForwardToRunLog)
    ]
    assert len(installed) == 1
