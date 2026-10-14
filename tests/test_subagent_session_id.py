"""The per-stream session_id reaches build_model from the subagent build site."""

from __future__ import annotations

import rudra.subagents.build as build_mod
from rudra.subagents.registry import REGISTRY


def test_model_for_forwards_session_id(monkeypatch):
    seen = {}

    def fake_build_model(role, cfg=None, *, session_id=None):
        seen["session_id"] = session_id
        return object()

    monkeypatch.setattr(build_mod, "build_model", fake_build_model)
    build_mod._model_for(REGISTRY["coder"], None, session_id="run-7-coder-ab12")
    assert seen["session_id"] == "run-7-coder-ab12"


def test_build_agent_accepts_session_id():
    import inspect

    param = inspect.signature(build_mod.build_agent).parameters["session_id"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is None


async def test_run_subagent_uses_one_id_for_thread_and_session(monkeypatch, tmp_path):
    from rudra.subagents import runner
    from tests.test_subagents_runner import make_context

    seen = {}

    def boom(spec, context, task="", *, session_id=None):
        seen["session_id"] = session_id
        raise RuntimeError("stop")

    monkeypatch.setattr(runner, "build_agent", boom)
    await runner.run_subagent("coder", "hi", context=make_context(tmp_path), thread_id="T-1")
    assert seen["session_id"] == "T-1"
