"""server__tool ids, and who may see which (C4.5)."""

import pytest

from rudra.mcp.registry import filter_ids, matches, split_id, tool_id


def test_id_joins_with_a_double_underscore():
    assert tool_id("kala", "verify") == "kala__verify"


def test_split_returns_server_and_tool():
    assert split_id("kala__verify") == ("kala", "verify")


def test_split_keeps_underscores_inside_a_tool_name():
    assert split_id("kala__system_bootstrap") == ("kala", "system_bootstrap")


def test_split_rejects_a_bare_name():
    with pytest.raises(ValueError, match="server__tool"):
        split_id("verify")


def test_two_servers_of_the_same_kind_stay_distinct():
    # The C4.5 case: kala collides with no Rudra tool name, so the collision
    # that matters is one server mounted twice.
    assert tool_id("kala", "verify") != tool_id("kala2", "verify")


def test_matches_supports_globs():
    assert matches("kala__*", "kala__verify")
    assert not matches("kala__*", "kala2__verify")
    assert matches("*__verify", "kala2__verify")
    assert matches("kala__verify", "kala__verify")


def test_empty_allow_means_everything_is_visible():
    ids = ["kala__verify", "kala__system_bootstrap"]
    assert filter_ids(ids, allow=[], deny=[]) == ("kala__system_bootstrap", "kala__verify")


def test_allow_restricts():
    ids = ["kala__verify", "kala__system_bootstrap"]
    assert filter_ids(ids, allow=["kala__verify"], deny=[]) == ("kala__verify",)


def test_deny_beats_allow():
    ids = ["kala__verify", "kala__system_bootstrap"]
    assert filter_ids(ids, allow=["kala__*"], deny=["*__system_bootstrap"]) == ("kala__verify",)
