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

    def _install(
        plan_lines: str | None = None,
        root: Path | None = None,
        strip_sandbox: bool = False,
    ):
        plan_path = None
        if plan_lines is not None:
            plan_path = tmp_path / ".rudra" / "run" / "PLAN.md"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(plan_lines, encoding="utf-8")
        install_path_normalizer(
            (root or tmp_path).resolve(),
            plan_path=plan_path,
            strip_sandbox_prefixes=strip_sandbox,
        )
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
def test_sandbox_prefixes_are_stripped_when_opted_in(normalizer, hallucinated, expected):
    """LLMs trained on SWE-bench and Codespaces emit these constantly.

    Opt-in since OPEN-31: `[compat] sandbox_paths` gates this the same way
    it gates the identical stripping in `middleware/fix_write_params.py`.
    """
    validate = normalizer(strip_sandbox=True)
    assert validate(hallucinated) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/src/models.py",
        "/src/storage.py",
        "/app/main.py",
        "/code/lib.py",
        "/tmp/scratch.py",
        "/testbed/app/main.py",
        "/workspace/src/main.rs",
    ],
)
def test_sandbox_prefixes_are_left_alone_by_default(normalizer, path: str):
    """OPEN-31: with `[compat] sandbox_paths` off, nothing is stripped.

    `SANDBOX_PREFIXES` lists `/src/`, `/app/`, `/code/`, `/tmp/` and
    `/root/`, which are ordinary project directories -- `/src/` most of all.
    Stripping them unconditionally rewrote `/src/models.py` to `/models.py`,
    and `read_file` then reported `File '/models.py' not found` for a file
    `ls` had just listed as `/src/models.py`. Every agent in run
    `d104fd13d9cc` looped on that contradiction.

    Under `virtual_mode=True` there is nothing to rescue by default: a real
    hallucination like `/testbed/app/main.py` simply means
    `<project>/testbed/app/main.py`, which does not exist, so the model gets
    an error naming the path it actually asked for.
    """
    validate = normalizer()
    assert validate(path) == path


def test_ls_and_read_file_agree_on_a_src_path(normalizer):
    """OPEN-31's unrecoverable pair, stated as the invariant it broke.

    The prefixes carry a trailing slash, so `/src` (what `ls` is called
    with) never matched and `/src/models.py` (what `ls` returns, and what
    `read_file` is then called with) always did. The listing tool was the
    one tool the truncation could not reach, which is precisely what made
    the loop unbreakable.
    """
    validate = normalizer()
    assert validate("/src") == "/src"
    assert validate("/src/models.py") == "/src/models.py"


def test_plan_aware_suffix_match_resolves_an_unknown_prefix(normalizer):
    validate = normalizer("- [ ] app/database.py\n")
    assert validate("/unknown/prefix/app/database.py") == "/app/database.py"


def test_plan_matching_is_language_agnostic(normalizer):
    """D18: the planner's checklist is filenames, whatever the stack."""
    validate = normalizer("- [ ] src/main.rs\n- [x] Cargo.toml\n")
    assert validate("/home/user/repos/myproj/Cargo.toml") == "/Cargo.toml"


def test_a_container_shaped_deep_path_keeps_only_the_last_two_segments(normalizer):
    """Last resort: avoid materialising deep hallucinated directory trees.

    Narrowed by OPEN-12 to paths whose LEADING component is a directory a
    container owns. `/home` qualifies; a project's own top-level name does
    not, which is the case below.
    """
    validate = normalizer()
    assert validate("/home/someone/proj/e.py") == "/proj/e.py"


@pytest.mark.parametrize(
    "path",
    [
        "/todoapp/src/models/todo.py",
        "/.rudra/run/transcripts/a5aa4552c9ab.jsonl",
        "/a/b/c/d/e.py",
    ],
)
def test_a_deep_project_path_is_not_truncated(normalizer, path: str):
    """OPEN-12: depth alone is not a hallucination signal.

    Before this, any absolute path more than three components deep came back
    as its last two, so `write_file("/todoapp/src/models/todo.py")` created
    `<project>/models/todo.py` and reported success. The gate had already
    previewed the untruncated path (`compat/virtual_paths.py` does not
    mirror this trimming), so the approval and the write named two different
    files.
    """
    validate = normalizer()
    assert validate(path) == path


def test_install_is_idempotent(normalizer, tmp_path: Path):
    """create_main_agent may be called more than once per process; re-wrapping
    would stack normalizers."""
    import deepagents.backends.utils as utils

    normalizer()
    first_original = getattr(utils, _ORIGINAL_KEY)
    normalizer()
    assert getattr(utils, _ORIGINAL_KEY) is first_original
    assert utils.validate_path("main.py") == "/main.py"


def test_a_symlinked_root_matches_the_path_the_user_typed(normalizer, tmp_path: Path):
    """A1.58: the root is recorded resolved; the model writes what it was shown.

    Platform-independent restatement of the macOS case. There `/tmp` is a
    symlink to `/private/tmp` and `/home` is autofs, so a project at
    `/tmp/work` is recorded as `/private/tmp/work`, every absolute path the
    model writes misses the root strip, and it falls through to the sandbox
    list -- the exact truncation A1.51 exists to prevent.

    Built with a real symlink so it fails on Linux too, rather than leaning
    on a platform quirk to expose it.

    The path is deliberately three levels deep. Step 2d's last-resort
    heuristic keeps the final two components, so a shallower path would
    yield the right answer by accident and the test would pass without the
    root strip ever running.
    """
    real = tmp_path / "real_root"
    real.mkdir()
    link = tmp_path / "linked_root"
    link.symlink_to(real, target_is_directory=True)

    # Installed resolved, exactly as create_main_agent does.
    validate = normalizer(root=link)

    # Written through the link, as the user typed it.
    assert validate(f"{link}/src/deep/models.py") == "/src/deep/models.py"
    # And the resolved spelling keeps working.
    assert validate(f"{real}/src/deep/models.py") == "/src/deep/models.py"


def test_the_normalizer_is_wired_to_the_same_flag_as_the_middleware():
    """OPEN-31: the flag existed, the normalizer was never given it.

    `middleware/fix_write_params.py` gates the identical stripping behind
    `[compat] sandbox_paths` and `subagents/build.py` passes it; the
    normalizer's copy ran unconditionally, so `sandbox_paths = false` in a
    user's config stripped `/src/` anyway. Both call sites must read the
    same field, and this fails if either stops.

    Asserted against the source rather than a run because
    `create_main_agent` builds a graph, a backend and a checkpointer before
    it gets here -- the same reason `tests/test_agent_wiring.py` reads
    source.
    """
    from pathlib import Path as _Path

    main_agent = _Path("src/rudra/agent/main_agent.py").read_text(encoding="utf-8")
    build = _Path("src/rudra/subagents/build.py").read_text(encoding="utf-8")

    assert "strip_sandbox_prefixes=cfg.compat.sandbox_paths" in main_agent
    assert "strip_sandbox_prefixes=context.cfg.compat.sandbox_paths" in build
