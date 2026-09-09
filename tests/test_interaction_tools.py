"""record_fact and the batched ask_user (Step 10a, C6.8/C6.8a).

The old tool pair asked one question at a time and wrote into a 4-field
allowlist that silently discarded everything else (interaction_tools.py:67).
These tests pin the replacement's contract, including every REJECTED path:
a model reads those strings and tries again, so they are behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from rich.console import Console

from rudra.facts import FactStore
from rudra.tools.interaction_tools import AskOption, AskQuestion, create_interaction_tools


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
            "questions": [
                {"key": "language", "question": "Which language?"},
                {"key": "cli_framework", "question": "Which arg parser?"},
            ]
        }
    )

    assert store.get("language").value == "Rust"
    assert store.get("language").source == "asked"
    assert "Which language?" in store.get("language").why
    assert store.get("cli_framework").value == "clap"
    assert "Rust" in out and "clap" in out


def test_the_key_travels_with_its_question(tmp_path: Path, monkeypatch):
    """Replaces test_ask_user_rejects_a_key_question_length_mismatch, which
    asserted a REJECTED for `questions` and `keys` of different lengths.

    That check is GONE because the state it guarded is now unrepresentable:
    there is no second list to be a different length. The old test was not
    rewritten, it was deleted -- keeping it would mean asserting a runtime
    guard for a shape the type system no longer permits.
    """
    answers = iter(["Python", "pytest"])
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: next(answers))
    store, tools = _tools(tmp_path)

    tools["ask_user"].invoke(
        {
            "questions": [
                {"key": "language", "question": "Which language?"},
                {"key": "test_framework", "question": "Which test framework?"},
            ]
        }
    )

    assert store.get("language").value == "Python"
    assert store.get("test_framework").value == "pytest"


def test_ask_user_rejects_an_empty_question_list(tmp_path: Path):
    _store, tools = _tools(tmp_path)
    assert tools["ask_user"].invoke({"questions": []}).startswith("REJECTED:")


def test_an_empty_answer_is_not_recorded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "  ")
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {"questions": [{"key": "database", "question": "Which database?"}]}
    )

    assert store.get("database") is None
    assert "(no answer)" in out


def test_the_budget_is_spent_across_calls_and_then_refuses(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "yes")
    store, tools = _tools(tmp_path, max_questions=2)

    tools["ask_user"].invoke(
        {"questions": [{"key": "a", "question": "a?"}, {"key": "b", "question": "b?"}]}
    )
    out = tools["ask_user"].invoke({"questions": [{"key": "c", "question": "c?"}]})

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

    out = tools["ask_user"].invoke(
        {
            "questions": [
                {"key": "a", "question": "a?"},
                {"key": "b", "question": "b?"},
                {"key": "c", "question": "c?"},
            ]
        }
    )

    assert len(asked) == 2
    assert store.get("c") is None
    assert "not asked" in out


def test_eof_stops_the_batch_and_keeps_what_was_already_answered(tmp_path: Path, monkeypatch):
    """EOF is not the same as `esc`. Escape skips ONE question and the rest
    are still worth asking; a closed stdin means every remaining prompt
    would fail identically, so the batch stops."""
    calls = {"n": 0}

    def _ask(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "Rust"
        raise EOFError

    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", _ask)
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {
            "questions": [
                {"key": "language", "question": "Which language?"},
                {"key": "parser", "question": "Which parser?"},
            ]
        }
    )

    assert store.get("language").value == "Rust"
    assert store.get("parser") is None
    assert "(no answer)" in out


def test_an_invalid_key_rejects_only_its_own_pair(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rudra.tools.interaction_tools.Prompt.ask", lambda *a, **k: "yes")
    store, tools = _tools(tmp_path)

    out = tools["ask_user"].invoke(
        {
            "questions": [
                {"key": "BAD KEY", "question": "a?"},
                {"key": "good_key", "question": "b?"},
            ]
        }
    )

    assert store.get("good_key").value == "yes"
    assert "NOT recorded" in out


# --- OPEN-11: the capability that did not exist ---


def test_a_question_with_options_selects_by_number(tmp_path: Path):
    """The whole point. The model can offer a list, and the user picks."""
    store, tools = _tools(tmp_path, reader=lambda: "2")

    tools["ask_user"].invoke(
        {
            "questions": [
                {
                    "key": "interface",
                    "question": "What interface?",
                    "options": [{"label": "CLI"}, {"label": "TUI"}],
                }
            ]
        }
    )

    assert store.get("interface").value == "TUI"
    assert store.get("interface").source == "asked"


def test_a_question_without_options_is_still_free_text(tmp_path: Path):
    """Graceful degradation: a model that cannot manage the nested shape
    gets exactly the old behaviour, so nothing regresses at D6's 32B floor."""
    store, tools = _tools(tmp_path, reader=lambda: "Rust")

    tools["ask_user"].invoke({"questions": [{"key": "language", "question": "Which language?"}]})

    assert store.get("language").value == "Rust"


def test_multi_select_joins_the_answers_in_row_order(tmp_path: Path):
    store, tools = _tools(tmp_path, reader=lambda: "3,1")

    tools["ask_user"].invoke(
        {
            "questions": [
                {
                    "key": "features",
                    "question": "Which features?",
                    "options": [{"label": "add"}, {"label": "edit"}, {"label": "search"}],
                    "multi_select": True,
                }
            ]
        }
    )

    assert store.get("features").value == "add, search"


def test_other_is_always_offered_so_the_user_is_never_trapped(tmp_path: Path):
    """Row 3 is the appended Other, which opens a text input."""
    answers = iter(["3", "GUI with tkinter"])
    store, tools = _tools(tmp_path, reader=lambda: next(answers))

    tools["ask_user"].invoke(
        {
            "questions": [
                {
                    "key": "interface",
                    "question": "What interface?",
                    "options": [{"label": "CLI"}, {"label": "TUI"}],
                }
            ]
        }
    )

    assert store.get("interface").value == "GUI with tkinter"


def test_cancelling_one_question_skips_it_and_asks_the_next(tmp_path: Path):
    """Escape is a skip, not an abandon -- the counterpart to the EOF test
    above. Row 9 does not exist, so the driver gives up as Cancelled."""
    answers = iter(["9", "9", "9", "9", "9", "Python"])
    store, tools = _tools(tmp_path, reader=lambda: next(answers))

    tools["ask_user"].invoke(
        {
            "questions": [
                {
                    "key": "interface",
                    "question": "What interface?",
                    "options": [{"label": "CLI"}, {"label": "TUI"}],
                },
                {"key": "language", "question": "Which language?"},
            ]
        }
    )

    assert store.get("interface") is None
    assert store.get("language").value == "Python", "the second question was still asked"


def test_the_docstring_forbids_narrating_the_options(tmp_path: Path):
    """The actual fix for the reported bug. A tool that CAN carry options
    still gets bypassed if nothing tells the model to use it."""
    _store, tools = _tools(tmp_path)
    # Whitespace-normalised: the docstring is hard-wrapped, and where the
    # line happens to break is not the thing under test.
    doc = " ".join(tools["ask_user"].description.lower().split())
    assert "options" in doc
    assert "never write the choices into your message text" in doc


def test_the_docstring_no_longer_pushes_everything_into_one_call(tmp_path: Path):
    """The old wording -- "Send related questions together in ONE call
    rather than one per call" -- is what produced the three-at-once dump
    the owner reported."""
    _store, tools = _tools(tmp_path)
    assert "one at a time" in tools["ask_user"].description.lower()


# --- A1.72: an unattended run cannot have asked anybody ---


def test_an_unattended_record_fact_downgrades_asked_to_inferred(tmp_path: Path):
    """Measured in Step 10a's acceptance run: --auto, ask_user never
    registered, and all three facts came back source="asked".

    Python knows with certainty that nobody was asked, and `asked` is the
    label a later stage trusts as "the user settled this, do not revisit".
    """
    store, tools = _tools(tmp_path, interactive=False)
    out = tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "the request says Rust", "source": "asked"}
    )

    assert store.get("language").source == "inferred"
    assert "inferred" in out


def test_an_interactive_record_fact_keeps_asked(tmp_path: Path):
    store, tools = _tools(tmp_path, interactive=True)
    tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "the user said so", "source": "asked"}
    )

    assert store.get("language").source == "asked"


def test_the_other_sources_are_untouched_when_unattended(tmp_path: Path):
    store, tools = _tools(tmp_path, interactive=False)
    tools["record_fact"].invoke({"key": "a", "value": "v", "why": "w", "source": "inferred"})
    tools["record_fact"].invoke({"key": "b", "value": "v", "why": "w", "source": "detected"})

    assert store.get("a").source == "inferred"
    assert store.get("b").source == "detected"


def test_an_invalid_source_is_still_rejected_when_unattended(tmp_path: Path):
    """Coercion must not become a place bad input gets laundered."""
    store, tools = _tools(tmp_path, interactive=False)
    out = tools["record_fact"].invoke({"key": "a", "value": "v", "why": "w", "source": "invented"})

    assert out.startswith("REJECTED:")
    assert store.items() == []


def test_a_previously_asked_fact_keeps_its_provenance_when_restated(tmp_path: Path):
    """A1.73: coercion must not demote what a human actually settled.

    Found by Step 10b's brownfield run, which neither the unit tests nor
    the greenfield runs could reach: both started from an empty store.
    A repo seeded with an answered fact came back with it marked
    "inferred" while its why still said the user chose it.
    """
    store = FactStore()
    store.record("cli_framework", "argparse", "the user chose it last run", "asked")
    _store, tools = _tools(tmp_path, store=store, interactive=False)

    tools["record_fact"].invoke(
        {
            "key": "cli_framework",
            "value": "argparse",
            "why": "the user chose it last run",
            "source": "asked",
        }
    )

    assert store.get("cli_framework").source == "asked"


def test_a_changed_value_cannot_inherit_asked(tmp_path: Path):
    """Restating what the user said is honest; changing it is not."""
    store = FactStore()
    store.record("cli_framework", "argparse", "the user chose it", "asked")
    _store, tools = _tools(tmp_path, store=store, interactive=False)

    tools["record_fact"].invoke(
        {"key": "cli_framework", "value": "clap", "why": "rust needs clap", "source": "asked"}
    )

    fact = store.get("cli_framework")
    assert fact.value == "clap"
    assert fact.source == "inferred", "nobody was asked about clap"


def test_an_inferred_fact_is_not_promoted_by_restating_it(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "the request says Rust", "inferred")
    _store, tools = _tools(tmp_path, store=store, interactive=False)

    tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "the request says Rust", "source": "asked"}
    )

    assert store.get("language").source == "inferred"


# --- OPEN-102: a complete answer rejected on its shape ---


def test_ask_option_accepts_a_bare_string():
    """Run d8f742805b9b's planner sent `options: ['Product landing page', ...]`
    and got 24 lines of pydantic error across four questions. `description`
    already defaults to "", so a bare string is exactly a valid AskOption:
    the information was complete and only the spelling was wrong.
    """
    option = AskOption.model_validate("SQLite")
    assert option.label == "SQLite"
    assert option.description == ""

    question = AskQuestion(key="storage", question="Which store?", options=["SQLite", "Postgres"])
    assert [choice.label for choice in question.options] == ["SQLite", "Postgres"]
    assert all(choice.description == "" for choice in question.options)


def test_ask_option_still_rejects_a_non_string_scalar():
    """The pin against over-widening: ONE unambiguous case, not `Any`."""
    with pytest.raises(ValidationError):
        AskOption.model_validate(3)
    with pytest.raises(ValidationError):
        AskOption.model_validate({"description": "no label"})
    with pytest.raises(ValidationError):
        AskQuestion(key="k", question="q?", options=[["SQLite"]])


def test_the_reported_call_now_validates(tmp_path: Path):
    """Run d8f742805b9b, debug line 8, verbatim -- and through the tool,
    because validating the model is not the path the run took."""
    question = AskQuestion(
        key="purpose",
        question="What is the purpose of this HTML file for iPhone 15?",
        options=[
            "Product landing page",
            "Device specification sheet",
            "Marketing/ads page",
            "Developer documentation",
            "Personal project",
            "Other",
        ],
        multi_select=False,
    )
    assert len(question.options) == 6
    assert question.options[0].label == "Product landing page"

    store, tools = _tools(tmp_path, reader=lambda: "1")
    out = tools["ask_user"].invoke(
        {
            "questions": [
                {
                    "key": "purpose",
                    "question": "What is the purpose?",
                    "options": ["Product landing page", "Device specification sheet"],
                }
            ]
        }
    )
    assert "REJECTED" not in out
    assert store.get("purpose").value == "Product landing page"


def test_record_fact_defaults_source_to_inferred(tmp_path: Path):
    """The reported run omitted `source` once and `why` once, and paid a
    call for each. `inferred` claims the least of the three labels."""
    store, tools = _tools(tmp_path)
    out = tools["record_fact"].invoke(
        {"key": "layout_approach", "value": "single_html_file", "why": "the user asked for one"}
    )
    assert "REJECTED" not in out
    assert store.get("layout_approach").source == "inferred"


def test_record_fact_still_requires_why(tmp_path: Path):
    """The pin that stops a future session relaxing the wrong field: a fact
    without a reason is exactly what `why` exists to prevent."""
    store, tools = _tools(tmp_path)
    with pytest.raises(ValidationError):
        tools["record_fact"].invoke({"key": "ready", "value": "yes", "source": "inferred"})
    assert store.items() == []


def test_record_fact_still_rejects_an_unknown_source(tmp_path: Path):
    """Shape coercion must not become value coercion (facts/store.py:88)."""
    store, tools = _tools(tmp_path)
    out = tools["record_fact"].invoke(
        {"key": "language", "value": "Rust", "why": "stated", "source": "guessed"}
    )
    assert out.startswith("REJECTED:")
    assert store.items() == []


def test_an_unattended_run_still_rejects_source_asked(tmp_path: Path):
    """A1.72 re-pinned, because the signature changed underneath it. A
    default of `inferred` sits correctly under that rule; `asked` would
    violate it, and an explicit `asked` is still downgraded."""
    store, tools = _tools(tmp_path, interactive=False)
    tools["record_fact"].invoke({"key": "language", "value": "Rust", "why": "the request says so"})
    assert store.get("language").source == "inferred"

    tools["record_fact"].invoke(
        {"key": "cli", "value": "clap", "why": "rust needs one", "source": "asked"}
    )
    assert store.get("cli").source == "inferred", "nobody was asked"
