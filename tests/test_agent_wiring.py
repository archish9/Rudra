"""Locks the two real behavioral changes of the deepagents 0.7.4 upgrade
(U.3, U.14) that no test previously imported rudra.agent to check.

  - planner_agent.py wires FixWriteParamsMiddleware, first in the list, so it
    cleans tool args before anything else sees them (U.14).
  - main_agent.py constructs plain FilesystemBackend(virtual_mode=True), not
    the deleted OverwriteFilesystemBackend (U.3).

IMPORTANT: this module must never call
rudra.compat.deepagents_path.install_path_normalizer, directly or
transitively, or it will break the ordering-sensitive guard in
tests/test_deepagents_contract.py::test_validate_path_is_shared_object when
the whole suite runs together (see that test and this module's own docstring
there for why).

Importing rudra.agent.planner_agent is safe: reading its source shows it
never imports rudra.compat.deepagents_path and never calls
install_path_normalizer at module level — confirmed here by grepping the
imported module's own source, not merely asserted.

rudra.agent.main_agent DOES call install_path_normalizer, but only inside the
body of the async create_main_agent() factory. Importing the module (which
this file does, to read its source) does not execute that factory, so it
stays safe. Actually proving the FilesystemBackend(virtual_mode=True) wiring
would require calling create_main_agent() — which patches — so that
assertion is made via source inspection (ast) instead, per the task's
explicit fallback for exactly this situation.
"""

from __future__ import annotations

import ast
import importlib
import inspect


def test_planner_agent_module_does_not_call_install_path_normalizer():
    """Verifies the safety claim this module's docstring relies on, rather
    than merely asserting it."""
    module = importlib.import_module("rudra.agent.planner_agent")
    source = inspect.getsource(module)
    assert "install_path_normalizer" not in source
    assert "deepagents_path" not in source


def test_planner_wires_fix_write_params_middleware_first():
    module = importlib.import_module("rudra.agent.planner_agent")
    source = inspect.getsource(module)
    tree = ast.parse(source)

    middleware_kw = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "create_deep_agent":
            for kw in node.keywords:
                if kw.arg == "middleware":
                    middleware_kw = kw.value
    assert middleware_kw is not None, (
        "create_deep_agent(..., middleware=...) not found in planner_agent.py"
    )
    assert isinstance(middleware_kw, ast.List) and middleware_kw.elts, (
        "planner's middleware= is not a nonempty list literal"
    )

    names = [
        getattr(elt.func, "id", None) for elt in middleware_kw.elts if isinstance(elt, ast.Call)
    ]
    assert "FixWriteParamsMiddleware" in names, (
        "FixWriteParamsMiddleware is not wired into the planner (U.14)"
    )
    assert names[0] == "FixWriteParamsMiddleware", (
        f"planner's first middleware is {names[0]!r}, expected "
        "FixWriteParamsMiddleware — it must clean tool args before anything "
        "else in the chain sees them (U.14)"
    )


def test_main_agent_constructs_filesystem_backend_with_virtual_mode():
    module = importlib.import_module("rudra.agent.main_agent")
    source = inspect.getsource(module)
    tree = ast.parse(source)

    # A historical prose mention of the deleted class in a comment is fine
    # (TODO.md A4.9) — what must be gone is any import of it or any attempt
    # to construct one. Check the AST, not a bare substring match.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = {alias.name for alias in node.names}
            assert "OverwriteFilesystemBackend" not in names, (
                "main_agent.py still imports the deleted OverwriteFilesystemBackend (U.3)"
            )
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "OverwriteFilesystemBackend"
        ):
            raise AssertionError(
                "main_agent.py still constructs the deleted OverwriteFilesystemBackend (U.3)"
            )

    fs_backend_call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FilesystemBackend":
            fs_backend_call = node
            break
    assert fs_backend_call is not None, (
        "no FilesystemBackend(...) construction found in main_agent.py"
    )

    virtual_mode_kw = next(
        (kw for kw in fs_backend_call.keywords if kw.arg == "virtual_mode"), None
    )
    assert virtual_mode_kw is not None, (
        "FilesystemBackend(...) in main_agent.py has no virtual_mode= kwarg"
    )
    assert (
        isinstance(virtual_mode_kw.value, ast.Constant) and virtual_mode_kw.value.value is True
    ), "main_agent.py's FilesystemBackend is not constructed with virtual_mode=True"
