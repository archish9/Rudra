"""C6.8a: the static questionnaire is gone, and stays gone.

TODO.md §0.5 counts seven surfaces. Six were live at Step 10a; the
seventh (README) was already removed by an earlier step and its citation
was stale -- see A1.69. These are the guards that stop any of them
growing back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "rudra"


def _sources() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in SRC.rglob("*.py"))


def test_project_context_is_gone():
    with pytest.raises(ImportError):
        from rudra.state import ProjectContext  # noqa: F401


def test_project_config_manager_is_gone():
    with pytest.raises(ImportError):
        from rudra.state import ProjectConfigManager  # noqa: F401


def test_the_project_config_module_is_deleted():
    assert not (SRC / "state" / "project_config.py").exists()


@pytest.mark.parametrize("field_name", ["primary_language", "additional_context"])
def test_no_source_mentions_a_questionnaire_field(field_name: str):
    assert field_name not in _sources()


def test_no_tool_named_save_project_context_exists(tmp_path: Path):
    """Assert the property, not the prose.

    The name survives in one module docstring, explaining what the new
    tools replaced. Step 6 learned this the hard way: two guards asserted
    on syntax and broke when the syntax changed while the property held.
    So this asks the factory what it produces.
    """
    from rich.console import Console

    from rudra.facts import FactStore
    from rudra.tools.interaction_tools import create_interaction_tools

    assert "def save_project_context" not in _sources()

    names = {
        tool.name
        for tool in create_interaction_tools(
            Console(quiet=True), FactStore(), tmp_path / "facts.json"
        )
    }
    assert names == {"record_fact", "ask_user"}


def test_the_tech_stack_inference_table_is_gone():
    """A1.30: an 8-row lookup table that contradicted the stack registry."""
    text = _sources()
    assert "_write_tech_stack_file" not in text
    assert "Rails" not in text
    assert "Spring Boot" not in text


def test_the_paths_module_no_longer_offers_tech_stack_md(tmp_path: Path):
    from rudra.state import rudra_paths

    assert not hasattr(rudra_paths(tmp_path), "tech_stack_md")


def test_facts_json_is_durable(tmp_path: Path):
    """D15: durable files sit at the .rudra/ root, volatile ones under run/."""
    from rudra.state import rudra_paths

    paths = rudra_paths(tmp_path)
    assert paths.facts_json == paths.root / "facts.json"


def test_the_readme_no_longer_documents_the_four_question_form():
    """A1.69: §0.5's surface #7 citation points past the end of the file."""
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    lowered = readme.lower()
    assert "primary language" not in lowered
    assert "additional context" not in lowered
