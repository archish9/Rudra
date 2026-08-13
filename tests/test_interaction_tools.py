"""record_fact and the batched ask_user (Step 10a, C6.8/C6.8a).

The old tool pair asked one question at a time and wrote into a 4-field
allowlist that silently discarded everything else (interaction_tools.py:67).
These tests pin the replacement's contract, including every REJECTED path:
a model reads those strings and tries again, so they are behaviour.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from rudra.facts import FactStore
from rudra.tools.interaction_tools import create_interaction_tools


def _tools(tmp_path: Path, store=None, **kwargs):
    store = store if store is not None else FactStore()
    tools = create_interaction_tools(Console(quiet=True), store, tmp_path / "facts.json", **kwargs)
    return store, {tool.name: tool for tool in tools}


def test_record_fact_stores_and_persists(tmp_path: Path):
    store, tools = _tools(tmp_path)
    out = tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "stated", "source": "inferred"}
    )
    assert "language" in out
    assert store.get("language").value == "Rust"
    assert (tmp_path / "facts.json").is_file()


def test_record_fact_returns_rejected_rather_than_raising(tmp_path: Path):
    store, tools = _tools(tmp_path)
    out = tools["record_fact"].invoke(
        {"key": "BAD KEY", "value": "x", "why": "y", "source": "asked"}
    )
    assert out.startswith("REJECTED:")
    assert store.items() == []
    assert not (tmp_path / "facts.json").exists()


def test_ask_user_is_absent_when_the_run_is_unattended(tmp_path: Path):
    """S10a.5: absence, not a refusal message. The S9b.3 precedent."""
    _store, tools = _tools(tmp_path, interactive=False)
    assert "ask_user" not in tools
    assert "record_fact" in tools


def test_ask_user_is_absent_when_the_budget_is_zero(tmp_path: Path):
    _store, tools = _tools(tmp_path, max_questions=0)
    assert "ask_user" not in tools


def test_ask_user_records_each_answer_as_a_fact(tmp_path: Path, monkeypatch):
    answers = iter(["Rust", "clap"])
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: next(answers))
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {
            "questions": ["Which language?", "Which arg parser?"],
            "keys": ["language", "cli_framework"],
        }
    )

    assert store.get("language").value == "Rust"
    assert store.get("language").source == "asked"
    assert "Which language?" in store.get("language").why
    assert store.get("cli_framework").value == "clap"
    assert "Rust" in out and "clap" in out


def test_ask_user_rejects_a_key_question_length_mismatch(tmp_path: Path, monkeypatch):
    called = []
    monkeypatch.setattr(
        "rudra.tools.interaction_tools.Prompt.ask",
        lambda *a, **k: called.append(1) or "x",
    )
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke({"questions": ["a?", "b?"], "keys": ["a"]})

    assert out.startswith("REJECTED:")
    assert called == []  # nothing prompted
    assert store.items() == []  # nothing recorded


def test_ask_user_rejects_an_empty_question_list(tmp_path: Path):
    _store, tools = _tools(tmp_path)
    assert tools["ask_user"].invoke({"questions": [], "keys": []}).startswith("REJECTED:")


def test_an_empty_answer_is_not_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "  ")
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke({"questions": ["Which database?"], "keys": ["database"]})

    assert store.get("database") is None
    assert "(no answer)" in out


def test_the_budget_is_spent_across_calls_and_then_refuses(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "yes")
    store, tools = _tools(tmp_path, max_questions=2)

    tools["ask_user"].invoke({"questions": ["a?", "b?"], "keys": ["a", "b"]})
    out = tools["ask_user"].invoke({"questions": ["c?"], "keys": ["c"]})

    assert out.startswith("REJECTED:")
    assert "2" in out
    assert store.get("c") is None


def test_a_batch_larger_than_the_budget_asks_what_fits_and_says_so(tmp_path: Path, monkeypatch):
    """Refusing the whole batch would punish the batching C6.8 asks for."""
    asked: list[int] = []

    monkeypatch.setattr(
        "rudra.tools.interaction_tools.Prompt.ask",
        lambda *a, **k: asked.append(1) or "yes",
    )
    store, tools = _tools(tmp_path, max_questions=2)

    out = tools["ask_user"].invoke({"questions": ["a?", "b?", "c?"], "keys": ["a", "b", "c"]})

    assert len(asked) == 2
    assert store.get("c") is None
    assert "not asked" in out


def test_eof_stops_the_batch_and_keeps_what_was_already_answered(tmp_path: Path, monkeypatch):
    calls = {"n": 0}

    def _ask(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Rust"
        raise EOFError

    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", _ask)
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {"questions": ["Which language?", "Which parser?"], "keys": ["language", "parser"]}
    )

    assert store.get("language").value == "Rust"
    assert store.get("parser") is None
    assert "(no answer)" in out


def test_an_invalid_key_rejects_only_its_own_pair(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "yes")
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke({"questions": ["a?", "b?"], "keys": ["BAD KEY", "good_key"]})

    assert store.get("good_key").value == "yes"
    assert "NOT recorded" in out
