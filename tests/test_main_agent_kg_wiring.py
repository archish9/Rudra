"""The knowledge graph reaches the run context: without it the guard is inert."""

from __future__ import annotations

import ast
import inspect

from rudra.agent import main_agent
from rudra.memory.kg import ProjectKG


class _Store:
    palace_path = "/proj/.rudra/memory/palace"


def test_kg_is_built_beside_the_stores_palace():
    kg = main_agent.build_project_kg(_Store())
    assert isinstance(kg, ProjectKG)
    assert kg.graph_path.startswith(_Store.palace_path)


def test_no_store_means_no_kg():
    assert main_agent.build_project_kg(None) is None


def test_every_loop_context_in_main_agent_is_given_a_kg():
    tree = ast.parse(inspect.getsource(main_agent))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "LoopContext"
    ]
    assert calls
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert isinstance(kw.get("kg"), ast.Name) and kw["kg"].id == "project_kg"
