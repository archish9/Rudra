"""The schema is data. These tests pin the values other modules rely on."""

from rudra.config.schema import (
    BUILTIN_ROLES,
    DEFAULTS,
    MODEL_KEYS,
    RESERVED_SECTIONS,
    VALID_MODES,
    VALID_PROVIDERS,
)


def test_default_model_meets_the_32b_floor() -> None:
    """D6: no default anywhere may name a model below 32B."""
    assert DEFAULTS["model"]["default"]["model"] == "qwen3:32b"


def test_default_permission_mode_is_ask() -> None:
    """D5."""
    assert DEFAULTS["permissions"]["mode"] == "ask"


def test_compat_flags_default_off() -> None:
    """D4: both surviving middlewares are opt-in."""
    assert DEFAULTS["compat"] == {"task_anchor": False, "sandbox_paths": False}


def test_every_provider_the_factory_supports_is_valid_here() -> None:
    from rudra.llm.providers import PROVIDERS

    assert VALID_PROVIDERS == frozenset(PROVIDERS)


def test_valid_modes_are_exactly_the_d5_set() -> None:
    assert VALID_MODES == ("ask", "auto", "plan")


def test_reserved_sections_name_the_step_that_implements_them() -> None:
    assert set(RESERVED_SECTIONS) == {"skills", "memory", "mcp"}
    for section, note in RESERVED_SECTIONS.items():
        assert "Step" in note, f"{section} must tell the user when it arrives"


def test_tools_is_no_longer_reserved() -> None:
    """It named Step 7 as its implementing step, and Step 7 implemented it.

    A section that stays reserved past its own step reads as an oversight,
    so this asserts the transition happened rather than trusting the set
    above to have been edited.
    """
    assert "tools" not in RESERVED_SECTIONS


def test_model_keys_match_the_model_config_fields() -> None:
    from dataclasses import fields

    from rudra.config.schema import ModelConfig

    assert MODEL_KEYS == frozenset(f.name for f in fields(ModelConfig))


def test_builtin_roles_are_the_step9b_set() -> None:
    # Step 5 shipped three; Step 9b added tester and reviewer for the
    # subagents of the same names (C6.2-C6.4). Both inherit [model.default].
    assert BUILTIN_ROLES == ("default", "planner", "coder", "tester", "reviewer")


def test_defaults_only_declare_the_default_role() -> None:
    """Roles inherit; shipping per-role defaults would defeat that."""
    assert set(DEFAULTS["model"]) == {"default"}
