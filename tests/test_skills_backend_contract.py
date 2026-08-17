"""Can deepagents read skills through a CompositeBackend route?

Everything in Step 11b rests on this. D13 argued it from the 0.7.4 source
-- filesystem.py:132 says virtual_mode's primary use case is exactly a
CompositeBackend stripping a route prefix, and composite.py:774 says
execute still delegates to the default -- but nothing had run it.

These tests import deepagents internals deliberately. If an upgrade moves
them, this file failing is the signal to re-verify the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.local_shell import LocalShellBackend
from deepagents.middleware.skills import _list_skills

from rudra.skills.registry import BUNDLES, DEFAULT_ENABLED
from rudra.skills.transform import render


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> Path:
    dest = tmp_path_factory.mktemp("skills-cache")
    render(BUNDLES, DEFAULT_ENABLED, dest)
    return dest


def _composite(project: Path, skills_root: Path) -> CompositeBackend:
    """The backend Rudra builds, plus the route this step adds."""
    return CompositeBackend(
        default=LocalShellBackend(root_dir=str(project), virtual_mode=True, env={}),
        routes={
            "/artifacts/": FilesystemBackend(root_dir=str(project / "art"), virtual_mode=True),
            "/skills/": FilesystemBackend(root_dir=str(skills_root), virtual_mode=True),
        },
        artifacts_root="/artifacts",
    )


def test_skills_load_through_the_route(tmp_path: Path, rendered: Path) -> None:
    """The load-bearing assumption of the whole step."""
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)

    skills = _list_skills(_composite(project, rendered), "/skills/active/")

    names = sorted(skill["name"] for skill in skills)
    assert names == sorted(DEFAULT_ENABLED)


def test_skill_paths_are_route_relative(tmp_path: Path, rendered: Path) -> None:
    """The model is shown a path it can actually read_file."""
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)

    skills = _list_skills(_composite(project, rendered), "/skills/active/")

    for skill in skills:
        assert skill["path"].startswith("/skills/active/")
        assert skill["path"].endswith("/SKILL.md")


def test_the_library_tree_is_readable_through_the_route(tmp_path: Path, rendered: Path) -> None:
    """Cross-references point into library/, so it must be reachable too.

    library/ is deliberately NOT a skills source -- it is not indexed --
    but every rewritten cross-reference is a /skills/library/... path the
    model is expected to read.
    """
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)
    backend = _composite(project, rendered)

    response = backend.download_files(["/skills/library/superpowers/using-git-worktrees/SKILL.md"])[
        0
    ]

    assert response is not None


def test_execute_still_reaches_the_project(tmp_path: Path, rendered: Path) -> None:
    """Adding a route must not break C3.1. Regression guard."""
    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)
    (project / "marker.txt").write_text("hello", encoding="utf-8")

    result = _composite(project, rendered).execute("cat marker.txt")

    assert "hello" in str(result)


def test_route_paths_survive_the_path_normalizer(tmp_path: Path, rendered: Path) -> None:
    """A1.79: the normalizer used to eat every route path deeper than three.

    `install_path_normalizer` rescues hallucinated absolute paths by
    keeping the last two segments of anything it cannot place. That turned
    /skills/active/brainstorming/SKILL.md into /brainstorming/SKILL.md, so
    a live model could `ls` the skills and never read one -- `ls` on a
    three-segment directory survived untouched, which is what made the
    failure look like the model's fault.

    This test exists because the contract tests above pass without the
    normalizer installed: it is a monkeypatch applied only during a real
    run, so the gap between them is exactly where A1.79 hid.
    """
    from deepagents.backends import utils

    from rudra.compat.deepagents_path import install_path_normalizer

    project = tmp_path / "proj"
    (project / "art").mkdir(parents=True)
    install_path_normalizer(project, route_prefixes=("/skills/", "/artifacts/"))

    for path in (
        "/skills/active/brainstorming/SKILL.md",
        "/skills/library/superpowers/writing-plans/SKILL.md",
        "/artifacts/large_tool_results/deeply/nested/file.md",
    ):
        assert utils.validate_path(path) == path

    # Reading one through the real composite still works end to end.
    backend = _composite(project, rendered)
    assert backend.download_files(["/skills/active/brainstorming/SKILL.md"])[0] is not None


def test_the_normalizer_still_rescues_hallucinated_paths(tmp_path: Path) -> None:
    """The route exemption must not switch off what the normalizer is for."""
    from deepagents.backends import utils

    from rudra.compat.deepagents_path import install_path_normalizer

    project = tmp_path / "proj"
    project.mkdir(parents=True)
    install_path_normalizer(project, route_prefixes=("/skills/", "/artifacts/"))

    assert utils.validate_path("/testbed/app/main.py") != "/testbed/app/main.py"
