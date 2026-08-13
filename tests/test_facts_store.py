"""The open fact store (Step 10a, C6.8a).

Pure data plus one file: these tests need no model, no backend and no
gate, which is the same property loop/ledger.py's tests have.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rudra.facts import Fact, FactRejected, FactStore


def test_record_returns_the_fact_and_stores_it():
    store = FactStore()
    fact = store.record("language", "Rust", "user said 'CLI in Rust'", "inferred")
    assert fact == Fact(value="Rust", why="user said 'CLI in Rust'", source="inferred")
    assert store.get("language") == fact


def test_get_returns_none_for_an_unknown_key():
    assert FactStore().get("nothing") is None


def test_keys_are_not_enumerated_anywhere():
    """The whole point of C6.8a: any key a model invents is accepted."""
    store = FactStore()
    store.record("min_rust_version", "1.75", "clap 4 needs it", "detected")
    store.record("no-async", "true", "the user asked for a sync CLI", "asked")
    assert [key for key, _ in store.items()] == ["min_rust_version", "no-async"]


def test_items_preserve_insertion_order_across_a_save_and_load(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    store.record("cli_framework", "clap", "stated", "inferred")
    path = tmp_path / "facts.json"
    store.save(path)

    loaded = FactStore.load(path)
    assert [key for key, _ in loaded.items()] == ["language", "cli_framework"]
    assert loaded.get("cli_framework").why == "stated"


def test_recording_a_key_twice_overwrites_in_place():
    store = FactStore()
    store.record("language", "Python", "guessed", "inferred")
    store.record("database", "SQLite", "stated", "asked")
    store.record("language", "Rust", "the user corrected me", "asked")

    assert [key for key, _ in store.items()] == ["language", "database"]
    assert store.get("language").value == "Rust"
    assert store.get("language").source == "asked"


@pytest.mark.parametrize(
    "key",
    ["", "Language", "has space", "a" * 65, "emoji🙂", "slash/key"],
)
def test_a_bad_key_is_rejected(key: str):
    with pytest.raises(FactRejected):
        FactStore().record(key, "v", "w", "asked")


@pytest.mark.parametrize("field_value", ["", "   ", "x" * 513])
def test_a_bad_value_or_why_is_rejected(field_value: str):
    with pytest.raises(FactRejected):
        FactStore().record("k", field_value, "why", "asked")
    with pytest.raises(FactRejected):
        FactStore().record("k", "value", field_value, "asked")


def test_a_non_string_value_is_rejected():
    with pytest.raises(FactRejected):
        FactStore().record("k", 3, "why", "asked")  # type: ignore[arg-type]


def test_an_unknown_source_is_rejected_and_names_the_valid_ones():
    with pytest.raises(FactRejected) as caught:
        FactStore().record("k", "v", "w", "guessed")
    message = str(caught.value)
    assert "asked" in message and "inferred" in message and "detected" in message


def test_a_rejected_write_leaves_the_store_untouched():
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    with pytest.raises(FactRejected):
        store.record("BAD KEY", "x", "y", "asked")
    assert [key for key, _ in store.items()] == ["language"]


def test_the_store_is_capped_and_an_overwrite_still_fits():
    store = FactStore()
    for index in range(100):
        store.record(f"k{index}", "v", "w", "detected")
    with pytest.raises(FactRejected):
        store.record("one_too_many", "v", "w", "detected")
    # An existing key is not a new fact, so it must still be writable.
    store.record("k0", "changed", "w", "detected")
    assert store.get("k0").value == "changed"


def test_save_writes_json_and_leaves_no_temp_file(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    path = tmp_path / "facts.json"
    store.save(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "facts": {"language": {"value": "Rust", "why": "stated", "source": "inferred"}}
    }
    assert list(tmp_path.iterdir()) == [path]


def test_save_creates_the_parent_directory(tmp_path: Path):
    store = FactStore()
    store.record("language", "Rust", "stated", "inferred")
    store.save(tmp_path / "nested" / "facts.json")
    assert (tmp_path / "nested" / "facts.json").is_file()


def test_load_of_a_missing_file_is_an_empty_store(tmp_path: Path):
    assert FactStore.load(tmp_path / "absent.json").items() == []


def test_load_of_a_corrupt_file_is_an_empty_store(tmp_path: Path):
    """Same contract as Ledger.load (loop/ledger.py:113-116)."""
    path = tmp_path / "facts.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert FactStore.load(path).items() == []


def test_load_drops_entries_that_fail_validation(tmp_path: Path):
    """A hand-edited file must not smuggle a 4-field questionnaire back in."""
    path = tmp_path / "facts.json"
    path.write_text(
        json.dumps(
            {
                "facts": {
                    "language": {"value": "Rust", "why": "stated", "source": "inferred"},
                    "BAD KEY": {"value": "x", "why": "y", "source": "asked"},
                    "bad_source": {"value": "x", "why": "y", "source": "invented"},
                    "not_a_dict": "Rust",
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = FactStore.load(path)
    assert [key for key, _ in loaded.items()] == ["language"]
