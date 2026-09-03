"""OPEN-90: the task list that decomposes below the file level.

Run `fc543fb2b82f` planned one HTML page as eight tasks, seven of which
named a region of the same file. Six coder invocations then ran against a
finished file and touched nothing -- 4,599.8 s, 63% of the run's counted
time. Nothing in Python looked at the list; `_BREAKDOWN_BODY` had already
forbidden two of the eight in prose and lost.

The fixtures below are that run's `ledger.json` and `facts.json`, verbatim,
from `~/.local/state/rudra/runs/test-rudra-d80a39ab/fc543fb2b82f/`.
"""

from __future__ import annotations

from rudra.loop.decomposition import (
    forbidden_shape_refusal,
    over_decomposition_refusal,
    single_file_evidence,
)

# ledger.json, the eight descriptions add_tasks was called with at ts 470.
RUN_TASKS = [
    "Create the single HTML file structure with proper doctype, html, head, and body tags",
    "Write embedded CSS for Apple-style minimal design: San Francisco font stack, generous "
    "whitespace, subtle animations, responsive breakpoints (375px, 768px, 1440px)",
    "Build Hero section: iPhone 15 product name, tagline, primary CTA button, product image "
    "placeholder with aspect-ratio",
    "Build Features Grid section: 4 feature cards for Dynamic Island, Camera system, A16 "
    "Bionic chip, USB-C — each with icon placeholder, title, description",
    "Build Specifications Table section: full specs table with categories (Display, Chip, "
    "Camera, Battery, Connectivity, Dimensions, Weight, Colors, Storage)",
    "Build Footer section: Apple-style navigation links (Shop, Support, Legal, Privacy), "
    "copyright notice",
    "Add embedded JavaScript for subtle scroll animations (fade-in on scroll) and smooth "
    "scroll for anchor links",
    "Validate HTML structure and verify responsive behavior at all breakpoints",
]

# facts.json, every value verbatim. The guard is given (key, value) pairs and
# must not key on the name `layout` -- the store is deliberately open.
RUN_FACTS = [
    ("project_type", "new_single_html_page"),
    ("page_purpose", "product_showcase_landing_page"),
    ("design_style", "apple_style_minimal"),
    ("content_scope", "hero_features_specs"),
    ("layout", "single_html_file_with_embedded_css_js"),
    ("boundaries", "hero_section, features_grid, specs_table, footer"),
    (
        "error_handling",
        "static_page_no_runtime_errors; font_fallback_stack; image_placeholder_fallback",
    ),
    ("tests", "manual_visual_verification; html_validation; responsive_breakpoints_check"),
]

# The regression case that matters: a plan the guard must never touch.
FASTAPI_TASKS = [
    "write the SQLAlchemy models for a todo item with id, title, done and created_at",
    "write the CRUD routes for creating, listing, updating and deleting a todo",
    "write the settings loader that reads the database URL from the environment",
    "write tests covering every CRUD route against an in-memory database",
]
FASTAPI_FACTS = [
    ("language", "python 3.12"),
    ("framework", "fastapi with sqlalchemy"),
    ("layout", "src/todo/models.py, src/todo/routes.py, src/todo/config.py, tests/"),
    ("tests", "pytest, one test file per module"),
]


# --- single_file_evidence: does anything in the fact store say ONE file? ---


def test_the_reported_run_s_facts_say_one_file():
    found = single_file_evidence(RUN_FACTS)
    assert found is not None
    key, value = found
    assert key == "layout"
    assert value == "single_html_file_with_embedded_css_js"


def test_prose_spelling_of_the_same_fact_is_found_too():
    # The same project on a later run wrote the layout as English rather
    # than a snake_case token. Both mean one file.
    found = single_file_evidence(
        [("layout", "single HTML file /index.html with inline CSS, no separate files")]
    )
    assert found is not None


def test_a_multi_file_project_says_nothing_of_the_kind():
    assert single_file_evidence(FASTAPI_FACTS) is None


def test_one_file_per_module_is_not_a_single_file_project():
    # "one file per X" is a convention for MANY files and matches the
    # phrase by accident. The veto is what keeps the guard off a real plan.
    assert single_file_evidence([("tests", "pytest, one file per module")]) is None


def test_a_single_file_claim_about_a_sub_artefact_does_not_count():
    # "single test file" is a claim about the tests, not the deliverable.
    assert single_file_evidence([("tests", "a single test file, tests/test_app.py")]) is None


def test_naming_two_source_files_vetoes_the_claim():
    # If the facts themselves name two non-test files, the project is not
    # one file whatever a phrase elsewhere says. Missing is cheap here;
    # over-firing mangles a legitimate plan (plan §7).
    facts = [
        ("layout", "a single file per concern: src/app.py and src/db.py"),
    ]
    assert single_file_evidence(facts) is None


def test_the_project_s_own_test_file_does_not_veto():
    # index.html plus its test is still a one-file deliverable.
    facts = [("layout", "single HTML file index.html, tested by tests/test_index_html.py")]
    assert single_file_evidence(facts) is not None


# --- over_decomposition_refusal: the list-level guard ---


def test_the_reported_run_s_plan_is_refused():
    refusal = over_decomposition_refusal(RUN_TASKS, evidence=single_file_evidence(RUN_FACTS))
    assert refusal is not None
    assert refusal.startswith("REJECTED:")


def test_the_refusal_names_the_offending_tasks_not_only_a_count():
    refusal = over_decomposition_refusal(RUN_TASKS, evidence=single_file_evidence(RUN_FACTS))
    assert refusal is not None
    assert "Build Hero section" in refusal
    assert "Build Footer section" in refusal


def test_the_refusal_quotes_the_fact_it_relied_on():
    refusal = over_decomposition_refusal(RUN_TASKS, evidence=single_file_evidence(RUN_FACTS))
    assert refusal is not None
    assert "layout" in refusal
    assert "single_html_file_with_embedded_css_js" in refusal


def test_the_refusal_says_what_to_do_next():
    # A model told only "no" re-issues the identical call (OPEN-24).
    refusal = over_decomposition_refusal(RUN_TASKS, evidence=single_file_evidence(RUN_FACTS))
    assert refusal is not None
    assert "add_tasks" in refusal


def test_a_multi_file_plan_is_untouched():
    refusal = over_decomposition_refusal(
        FASTAPI_TASKS, evidence=single_file_evidence(FASTAPI_FACTS)
    )
    assert refusal is None


def test_no_facts_means_no_refusal():
    assert over_decomposition_refusal(RUN_TASKS, evidence=None) is None


def test_two_tasks_on_one_file_are_allowed():
    # The threshold is three. A page plus its tests is a normal plan.
    tasks = [
        "build the iPhone 15 product page as one self-contained HTML file",
        "write tests for the page's structure and required content",
    ]
    assert over_decomposition_refusal(tasks, evidence=("layout", "single html file")) is None


def test_test_tasks_do_not_count_towards_the_threshold():
    tasks = [
        "build the page",
        "build the styles into the same page",
        "write tests for the page",
        "write integration tests for the page",
    ]
    # Two non-test tasks, so under the threshold whatever the test tasks say.
    assert over_decomposition_refusal(tasks, evidence=("layout", "single html file")) is None


def test_tasks_already_on_the_ledger_count_too():
    # A planner that splits the same bad plan across two calls must not
    # slip under the threshold.
    assert (
        over_decomposition_refusal(
            ["Build Footer section: navigation links"],
            evidence=("layout", "single html file"),
            existing=["Build Hero section", "Build Features Grid section"],
        )
        is not None
    )


# --- forbidden_shape_refusal: the prompt's own BAD list, in Python ---


def test_the_run_s_validate_task_is_refused():
    refusal = forbidden_shape_refusal(
        "Validate HTML structure and verify responsive behavior at all breakpoints"
    )
    assert refusal is not None
    assert refusal.startswith("REJECTED:")
    assert "gate" in refusal


def test_validating_runtime_input_is_real_work_and_is_kept():
    # The failure mode of a false positive here is work that never happens
    # (loop/tools.py::_normalised). A task that validates the user's input
    # changes a file; a task that validates the project's own output does
    # not.
    assert forbidden_shape_refusal("Validate user input in the signup form") is None
    assert forbidden_shape_refusal("Check the email address before sending the invite") is None


def test_create_the_project_structure_is_refused():
    # Quoted in _BREAKDOWN_BODY as BAD, and emitted anyway.
    assert forbidden_shape_refusal("Create the project structure") is not None
    assert forbidden_shape_refusal("Set up the project skeleton and directories") is not None


def test_ordinary_work_is_never_refused():
    for description in FASTAPI_TASKS:
        assert forbidden_shape_refusal(description) is None
    assert forbidden_shape_refusal("add a --format flag to the CLI") is None
    assert forbidden_shape_refusal("write a CSV parser that handles quoted commas") is None


# --- add_tasks itself: the guard where the model actually meets it ---


class _Fact:
    def __init__(self, value: str) -> None:
        self.value = value


class _Facts:
    """The shape add_tasks duck-types: .items() -> [(key, fact)]."""

    def __init__(self, pairs):
        self._pairs = [(key, _Fact(value)) for key, value in pairs]

    def items(self):
        return list(self._pairs)


def _tools(tmp_path, facts=None, ledger=None):
    from rudra.loop.ledger import Ledger
    from rudra.loop.tools import create_ledger_tools

    ledger = ledger if ledger is not None else Ledger()
    path = tmp_path / "ledger.json"
    tools = create_ledger_tools(ledger, path, facts=facts)
    return {tool.name: tool for tool in tools}, ledger, path


def test_add_tasks_refuses_the_reported_run_s_plan(tmp_path):
    tools, ledger, _ = _tools(tmp_path, facts=_Facts(RUN_FACTS))
    answer = tools["add_tasks"].invoke({"descriptions": RUN_TASKS})
    assert answer.startswith("REJECTED:")
    assert ledger.tasks == [], "nothing may be added when the whole shape is wrong"


def test_add_tasks_accepts_a_multi_file_plan_unchanged(tmp_path):
    # The regression that matters: the guard's failure mode is over-firing.
    tools, ledger, _ = _tools(tmp_path, facts=_Facts(FASTAPI_FACTS))
    answer = tools["add_tasks"].invoke({"descriptions": FASTAPI_TASKS})
    assert not answer.startswith("REJECTED:")
    assert [task.description for task in ledger.tasks] == FASTAPI_TASKS


def test_the_refusal_fires_once_and_the_next_list_is_accepted(tmp_path):
    # A guard that refuses for ever turns a bad plan into no plan at all.
    tools, ledger, _ = _tools(tmp_path, facts=_Facts(RUN_FACTS))
    first = tools["add_tasks"].invoke({"descriptions": RUN_TASKS})
    assert first.startswith("REJECTED:")
    stubborn = [text for text in RUN_TASKS if "Build" in text or "Write embedded" in text]
    second = tools["add_tasks"].invoke({"descriptions": stubborn})
    assert not second.startswith("REJECTED:")
    assert len(ledger.tasks) == len(stubborn)


def test_the_once_flag_is_not_written_to_the_ledger_file(tmp_path):
    import json

    tools, ledger, path = _tools(tmp_path, facts=_Facts(RUN_FACTS))
    tools["add_tasks"].invoke({"descriptions": RUN_TASKS})
    tools["add_tasks"].invoke({"descriptions": ["build the page as one file"]})
    written = json.loads(path.read_text())
    assert "decomposition_refused" not in written
    assert ledger.decomposition_refused is True


def test_without_facts_the_guard_never_fires(tmp_path):
    # Every existing caller passed no facts; none of them may change.
    tools, ledger, _ = _tools(tmp_path)
    answer = tools["add_tasks"].invoke({"descriptions": RUN_TASKS[:7]})
    assert not answer.startswith("REJECTED:")
    assert len(ledger.tasks) == 7


def test_an_unreadable_fact_store_declines_rather_than_raising(tmp_path):
    # Bookkeeping may never end a run (CLAUDE.md §8a).
    class _Broken:
        def items(self):
            raise RuntimeError("no")

    tools, ledger, _ = _tools(tmp_path, facts=_Broken())
    answer = tools["add_tasks"].invoke({"descriptions": RUN_TASKS[:7]})
    assert not answer.startswith("REJECTED:")
    assert len(ledger.tasks) == 7


def test_the_forbidden_shape_refusal_keeps_the_rest_of_the_batch(tmp_path):
    tools, ledger, _ = _tools(tmp_path)
    answer = tools["add_tasks"].invoke(
        {
            "descriptions": [
                "write a CSV parser that handles quoted commas",
                "Validate HTML structure and verify responsive behavior at all breakpoints",
            ]
        }
    )
    assert "REJECTED:" in answer
    assert [task.description for task in ledger.tasks] == [
        "write a CSV parser that handles quoted commas"
    ]


def test_a_split_plan_cannot_slip_under_the_threshold(tmp_path):
    tools, ledger, _ = _tools(tmp_path, facts=_Facts(RUN_FACTS))
    first = tools["add_tasks"].invoke({"descriptions": RUN_TASKS[1:3]})
    assert not first.startswith("REJECTED:")
    second = tools["add_tasks"].invoke({"descriptions": [RUN_TASKS[5]]})
    assert second.startswith("REJECTED:")


def test_the_breakdown_stage_s_add_tasks_can_see_the_facts(tmp_path):
    # The wiring OPEN-90 depends on, asserted through behaviour rather than
    # source text: without it the guard reads an empty store and never
    # fires, which is a silent no-op rather than a failure.
    from rich.console import Console

    from rudra.agent.planner_agent import _tools_for_stage
    from rudra.config import get_config
    from rudra.facts import FactStore
    from rudra.loop.ledger import Ledger
    from rudra.state.paths import rudra_paths

    facts = FactStore()
    for key, value in RUN_FACTS:
        facts.record(key, value, why="from run fc543fb2b82f", source="inferred")

    tools = _tools_for_stage(
        "breakdown",
        ledger=Ledger(),
        facts=facts,
        paths=rudra_paths(tmp_path),
        console=Console(),
        cfg=get_config(),
        interactive=False,
    )
    add_tasks = next(tool for tool in tools if tool.name == "add_tasks")
    assert add_tasks.invoke({"descriptions": RUN_TASKS}).startswith("REJECTED:")


def test_the_refusal_is_announced_to_the_trace_and_the_usage_log(tmp_path):
    # A new guard is a new silence unless you wire it to something
    # (TODO.md, lesson 5). The model is told by the tool result; the user
    # and the maintainer are told by these two.
    from rudra.loop.ledger import Ledger
    from rudra.loop.tools import create_ledger_tools

    said = []

    class _Trace:
        def notice(self, message, *, role, name):
            said.append((message, role, name))

    class _Usage:
        def __init__(self):
            self.refused = 0

        def record_plan_refused(self, role):
            self.refused += 1

    usage = _Usage()
    tools = {
        tool.name: tool
        for tool in create_ledger_tools(
            Ledger(),
            tmp_path / "ledger.json",
            facts=_Facts(RUN_FACTS),
            trace=_Trace(),
            usage=usage,
        )
    }
    tools["add_tasks"].invoke({"descriptions": RUN_TASKS})
    assert usage.refused == 1
    assert len(said) == 1
    message, role, name = said[0]
    assert name == "plan-shape"
    assert role == "planner"
    assert "refused a plan" in message


def test_a_broken_trace_or_usage_never_fails_the_call(tmp_path):
    # Bookkeeping may never end a run (CLAUDE.md §8a).
    from rudra.loop.ledger import Ledger
    from rudra.loop.tools import create_ledger_tools

    class _Boom:
        def notice(self, *args, **kwargs):
            raise RuntimeError("no")

        def record_plan_refused(self, *args, **kwargs):
            raise RuntimeError("no")

    tools = {
        tool.name: tool
        for tool in create_ledger_tools(
            Ledger(),
            tmp_path / "ledger.json",
            facts=_Facts(RUN_FACTS),
            trace=_Boom(),
            usage=_Boom(),
        )
    }
    assert tools["add_tasks"].invoke({"descriptions": RUN_TASKS}).startswith("REJECTED:")


def test_a_specifications_task_is_not_a_test_task():
    # "Build Specifications Table section: full specs table" is the run's
    # t5. Sharing one pattern with the test-FILE check dropped it out of
    # the count it was supposed to be in, and out of the refusal that
    # names the offenders.
    from rudra.loop.decomposition import is_test_task

    assert is_test_task(RUN_TASKS[4]) is False
    assert is_test_task("write tests for the parser") is True
    assert is_test_task("write the integration test suite") is True


def test_the_refusal_names_every_one_of_the_run_s_offending_tasks():
    refusal = over_decomposition_refusal(RUN_TASKS, evidence=single_file_evidence(RUN_FACTS))
    assert refusal is not None
    assert "Build Specifications Table section" in refusal
    assert refusal.startswith("REJECTED: 8 of these tasks")


def test_a_task_that_merely_mentions_a_folder_structure_is_kept():
    # The scaffold rule's own false positives, named so they stay fixed:
    # both of these produce a file.
    assert forbidden_shape_refusal("Add a directory structure diagram to the README") is None
    assert forbidden_shape_refusal("Generate a folder structure diagram for the docs") is None
    assert forbidden_shape_refusal("write the installer that mirrors the project structure") is None
    # ...and the shape it is actually for still goes.
    assert forbidden_shape_refusal("Create the folder structure for the app") is not None
