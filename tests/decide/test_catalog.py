"""The S2 decision catalog: the four typed question sets + a budget helper.

Shapes are pinned against the real laya schema recorded in
docs/.../NOTES-output-schema.md: choice carries a `criteria` dict
{label: description|None}; score carries an ordered `criteria` list; noul
carries NO criteria.
"""

from rudra.decide import catalog


def _is_question(q: object) -> bool:
    if not isinstance(q, dict):
        return False
    if q.get("type") not in ("choice", "score", "noul"):
        return False
    if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
        return False
    kind = q["type"]
    if kind == "noul":
        return "criteria" not in q
    if kind == "choice":
        crit = q.get("criteria")
        return isinstance(crit, dict) and all(
            isinstance(k, str) and (v is None or isinstance(v, str)) for k, v in crit.items()
        )
    # score
    crit = q.get("criteria")
    return isinstance(crit, list) and len(crit) >= 2 and all(isinstance(x, str) for x in crit)


def _all_valid(questions: dict) -> bool:
    return bool(questions) and all(_is_question(q) for q in questions.values())


# --- #2 CLARIFY_NEEDED (noul) ------------------------------------------------


def test_clarify_needed_is_a_single_valid_noul():
    assert set(catalog.CLARIFY_NEEDED) == {"needs_clarification"}
    q = catalog.CLARIFY_NEEDED["needs_clarification"]
    assert q["type"] == "noul"
    assert "criteria" not in q
    assert _all_valid(catalog.CLARIFY_NEEDED)


# --- #1 FAILURE_CLASS (choice) ----------------------------------------------


def test_failure_class_is_a_valid_choice_with_bounded_classes():
    assert set(catalog.FAILURE_CLASS) == {"failure_class"}
    q = catalog.FAILURE_CLASS["failure_class"]
    assert q["type"] == "choice"
    assert _all_valid(catalog.FAILURE_CLASS)
    # Token budget: keep the class count small (plan Task 1, Step 3: <=4-5).
    assert 2 <= len(q["criteria"]) <= 5


def test_failure_class_labels_are_outcome_recovery_classes():
    labels = set(catalog.FAILURE_CLASS["failure_class"]["criteria"])
    # These are the recovery routes S5 switches on; they must be stable names.
    assert labels == {
        "missing_dependency",
        "wrong_approach",
        "flaky_or_environment",
        "needs_decomposition",
    }


# --- #3 TASK_TRIAGE (noul + score + choice) ---------------------------------


def test_task_triage_has_three_questions_of_the_right_kinds():
    assert set(catalog.TASK_TRIAGE) == {"needs_tests", "complexity", "domain"}
    assert catalog.TASK_TRIAGE["needs_tests"]["type"] == "noul"
    assert catalog.TASK_TRIAGE["complexity"]["type"] == "score"
    assert catalog.TASK_TRIAGE["domain"]["type"] == "choice"
    assert _all_valid(catalog.TASK_TRIAGE)


def test_task_triage_complexity_is_ordered_levels():
    crit = catalog.TASK_TRIAGE["complexity"]["criteria"]
    assert isinstance(crit, list)
    # ordered level strings, lowest first (NOTES score schema: "N label: desc")
    assert crit[0].startswith("0")
    assert len(crit) >= 2


def test_task_triage_domain_covers_the_known_stacks_plus_other():
    from rudra.stacks.registry import PROFILES

    crit = catalog.TASK_TRIAGE["domain"]["criteria"]
    for p in PROFILES:
        assert p.name in crit
    assert "other" in crit


# --- #4 TOOL_SELECT / SKILL_SELECT (dynamic choice builders) -----------------


def test_tool_select_builds_a_choice_over_the_given_tool_ids():
    q = catalog.tool_select(["search_memory", "ask_user", "git_diff"])
    assert set(q) == {"mcp_tool"}
    assert q["mcp_tool"]["type"] == "choice"
    assert set(q["mcp_tool"]["criteria"]) == {"search_memory", "ask_user", "git_diff"}
    assert _all_valid(q)


def test_skill_select_builds_a_choice_over_the_given_skill_names():
    q = catalog.skill_select(["test-driven-development", "systematic-debugging"])
    assert set(q) == {"skills_topk"}
    assert q["skills_topk"]["type"] == "choice"
    assert set(q["skills_topk"]["criteria"]) == {
        "test-driven-development",
        "systematic-debugging",
    }
    assert _all_valid(q)


def test_dynamic_builders_tolerate_empty_options_without_crashing():
    # At call time there are always options; an empty list must still return a
    # well-formed (if degenerate) question rather than raise.
    assert catalog.tool_select([])["mcp_tool"]["type"] == "choice"
    assert catalog.skill_select([])["skills_topk"]["criteria"] == {}


# --- fits_budget -------------------------------------------------------------


def test_fits_budget_true_for_a_small_set_and_small_state():
    assert catalog.fits_budget(catalog.CLARIFY_NEEDED, "build a flask app") is True
    assert catalog.fits_budget(catalog.FAILURE_CLASS, {"gate": "ImportError: no module x"}) is True


def test_fits_budget_false_when_instructions_blow_the_budget():
    huge = {"q": {"type": "noul", "instructions": "word " * 2000}}
    assert catalog.fits_budget(huge, "") is False


def test_fits_budget_accounts_for_the_state_too():
    # A set that fits with a tiny state can be pushed over by a large state.
    big_state = {"gate_output": "x" * 4000}
    assert catalog.fits_budget(catalog.FAILURE_CLASS, big_state) is False
