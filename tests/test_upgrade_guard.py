from types import SimpleNamespace

from rudra.loop.upgrade_guard import (
    AdverseUpgrade,
    UpgradeIntent,
    detect_upgrade_intent,
    evaluate_guard,
)


def test_detects_versioned_upgrade():
    i = detect_upgrade_intent("Upgrade flask to 3.0", {})
    assert i == UpgradeIntent(package="flask", version="3.0")


def test_detects_unversioned_upgrade():
    assert detect_upgrade_intent("bump requests", {}) == UpgradeIntent(
        package="requests", version=None
    )


def test_edge_punctuation_is_stripped():
    assert detect_upgrade_intent("Update django to 5.1.", {}) == UpgradeIntent("django", "5.1")


def test_non_upgrade_task_is_not_intent():
    assert detect_upgrade_intent("add a flask route for /health", {}) is None


def test_vague_upgrade_is_not_intent():
    assert detect_upgrade_intent("upgrade everything", {}) is None


def test_two_candidate_packages_is_not_intent():
    assert detect_upgrade_intent("upgrade flask and requests", {}) is None


def test_task_merely_mentioning_package_after_other_verb_is_not_intent():
    assert detect_upgrade_intent("write tests so flask routes work", {}) is None


def test_fact_value_supplies_intent():
    facts = {"k": SimpleNamespace(value="upgrade flask to 3.0")}
    assert detect_upgrade_intent("do the thing", facts) == UpgradeIntent("flask", "3.0")


def test_block_when_requested_version_matches_adverse():
    intent = UpgradeIntent("flask", "3.0")
    adverse = [AdverseUpgrade("flask", "3.0", "abc123", "broke 4 tests", "2026-10-02")]
    d = evaluate_guard(intent, adverse)
    assert d.blocked and d.evidence == tuple(adverse)
    assert "3.0" in d.explanation() and "abc123" in d.explanation()


def test_unversioned_request_blocks_on_any_adverse():
    d = evaluate_guard(
        UpgradeIntent("flask", None), [AdverseUpgrade("flask", "3.0", "r", "x", "d")]
    )
    assert d.blocked


def test_version_mismatch_does_not_block():
    d = evaluate_guard(
        UpgradeIntent("flask", "2.9"), [AdverseUpgrade("flask", "3.0", "r", "x", "d")]
    )
    assert not d.blocked


def test_other_package_does_not_block():
    d = evaluate_guard(
        UpgradeIntent("flask", None), [AdverseUpgrade("django", "3.0", "r", "x", "d")]
    )
    assert not d.blocked


def test_no_intent_never_blocks():
    assert not evaluate_guard(None, []).blocked


def test_update_pkg_followed_by_noun_is_not_intent():
    assert detect_upgrade_intent("update flask routes to return JSON", {}) is None


def test_update_readme_or_docs_is_not_intent():
    assert detect_upgrade_intent("update README", {}) is None
    assert detect_upgrade_intent("update docs", {}) is None


def test_later_real_upgrade_is_found_after_a_non_intent_verb():
    got = detect_upgrade_intent("update the docs, then upgrade flask to 3.0", {})
    assert got == UpgradeIntent("flask", "3.0")


def test_update_to_version_still_detected():
    assert detect_upgrade_intent("update flask to 3.0", {}) == UpgradeIntent("flask", "3.0")
