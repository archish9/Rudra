"""Tests for the deepagents path-normalizer monkeypatch (TODO.md U.4, A4.11).

This module was at 0% coverage -- never imported by any test -- while being
the most fragile thing in the repo: it rewrites a deepagents internal in two
places and will break on any version bump.

All expected values here were measured against the pinned deepagents 0.7.4.
Note what that measurement showed: the upstream validate_path does NOT reject
absolute paths (it returns them unchanged and prepends "/" to relative ones,
virtual-root semantics). The module's real job is stripping real-machine and
sandbox prefixes so the resulting virtual path is correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rudra.compat.deepagents_path import install_path_normalizer

_ORIGINAL_KEY = "_rudra_original_validate_path"


@pytest.fixture
def normalizer(tmp_path: Path):
    """Install the patch, then fully restore deepagents afterwards.

    Without the restore, the patch leaks into every later test in the session
    -- it rewrites module-level names in two deepagents modules.
    """
    import deepagents.backends.utils as utils
    import deepagents.middleware.filesystem as fs_mw

    saved_utils = utils.validate_path
    saved_fs_mw = fs_mw.validate_path

    def _install(plan_lines: str | None = None, root: Path | None = None):
        plan_path = None
        if plan_lines is not None:
            plan_path = tmp_path / ".rudra" / "run" / "PLAN.md"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(plan_lines, encoding="utf-8")
        install_path_normalizer((root or tmp_path).resolve(), plan_path=plan_path)
        return utils.validate_path

    yield _install

    utils.validate_path = saved_utils
    fs_mw.validate_path = saved_fs_mw
    if hasattr(utils, _ORIGINAL_KEY):
        delattr(utils, _ORIGINAL_KEY)


def test_relative_paths_pass_through(normalizer):
    validate = normalizer()
    assert validate("main.py") == "/main.py"


def test_both_modules_are_patched(normalizer):
    """FilesystemMiddleware does `from ... import validate_path`, so patching
    only backends.utils leaves the middleware's copy untouched (CLAUDE.md §7)."""
    import deepagents.backends.utils as utils
    import deepagents.middleware.filesystem as fs_mw

    normalizer()
    assert utils.validate_path is fs_mw.validate_path


def test_project_root_prefix_is_stripped(normalizer, tmp_path: Path):
    validate = normalizer()
    absolute = str(tmp_path.resolve() / "src" / "models.py")
    assert validate(absolute) == "/src/models.py"


@pytest.mark.parametrize("root", ["/app", "/tmp/work", "/code/proj", "/home/user/proj"])
def test_real_project_root_beats_a_sandbox_prefix(normalizer, root: str):
    """A project genuinely rooted under a sandbox prefix must strip the ROOT.

    SANDBOX_PREFIXES lists "/tmp/", "/app/", "/code/", "/src/", "/root/" and
    "/home/user/" -- all of which are ordinary directories someone may really
    work in. Stripping the prefix first truncates at the wrong place and the
    write lands somewhere nobody reads (TODO.md A1.51).
    """
    validate = normalizer(root=Path(root))
    assert validate(f"{root}/src/models.py") == "/src/models.py"


def test_windows_absolute_path_is_stripped(normalizer):
    validate = normalizer()
    assert validate("C:\\project\\models.py") == "/project/models.py"


@pytest.mark.parametrize(
    ("hallucinated", "expected"),
    [
        ("/testbed/app/main.py", "/app/main.py"),
        ("/workspace/src/main.rs", "/src/main.rs"),
    ],
)
def test_sandbox_prefixes_are_stripped(normalizer, hallucinated, expected):
    """LLMs trained on SWE-bench and Codespaces emit these constantly."""
    validate = normalizer()
    assert validate(hallucinated) == expected


def test_plan_aware_suffix_match_resolves_an_unknown_prefix(normalizer):
    validate = normalizer("- [ ] app/database.py\n")
    assert validate("/unknown/prefix/app/database.py") == "/app/database.py"


def test_plan_matching_is_language_agnostic(normalizer):
    """D18: the planner's checklist is filenames, whatever the stack."""
    validate = normalizer("- [ ] src/main.rs\n- [x] Cargo.toml\n")
    assert validate("/home/user/repos/myproj/Cargo.toml") == "/Cargo.toml"


def test_unknown_deep_path_keeps_only_the_last_two_segments(normalizer):
    """Last resort: avoid materialising deep hallucinated directory trees."""
    validate = normalizer()
    assert validate("/a/b/c/d/e.py") == "/d/e.py"


def test_install_is_idempotent(normalizer, tmp_path: Path):
    """create_main_agent may be called more than once per process; re-wrapping
    would stack normalizers."""
    import deepagents.backends.utils as utils

    normalizer()
    first_original = getattr(utils, _ORIGINAL_KEY)
    normalizer()
    assert getattr(utils, _ORIGINAL_KEY) is first_original
    assert utils.validate_path("main.py") == "/main.py"
