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
        route_prefixes: tuple[str, ...] = (),
    ):
        plan_path = None
        if plan_lines is not None:
            plan_path = tmp_path / ".rudra" / "run" / "PLAN.md"
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(plan_lines, encoding="utf-8")
        install_path_normalizer(
            (root or tmp_path).resolve(),
            plan_path=plan_path,
            route_prefixes=route_prefixes,
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


# The six paths run 689f0ea263be's coder actually asked for, and the six
# distinct error strings it got back. Before OPEN-82 the right-hand column
# was what `validate_path` returned, so deepagents' "not found" error named
# a path appearing in no argument the model ever wrote:
#
#   /home/user/Rudra/pytest.ini              -> /Rudra/pytest.ini
#   /home/user/Rudra/requirements.txt        -> /Rudra/requirements.txt
#   /home/user/Rudra/src/database/session.py -> /database/session.py
#   /home/user/Rudra/src/config/settings.py  -> /config/settings.py
#   /home/user/Rudra/src/models/models.py    -> /models/models.py
#   /home/user/Rudra/src/config/__init__.py  -> /config/__init__.py
#
# All six are byte-identical to the errors in
# `debug-689f0ea263be.jsonl`; 13 calls hit them, ~1,330 s at that run's
# 51 s/call, and not one was recoverable because the model was never told
# what it got wrong.
_RUN_689F_HALLUCINATIONS = [
    "/home/user/Rudra/pytest.ini",
    "/home/user/Rudra/requirements.txt",
    "/home/user/Rudra/src/database/session.py",
    "/home/user/Rudra/src/config/settings.py",
    "/home/user/Rudra/src/models/models.py",
    "/home/user/Rudra/src/config/__init__.py",
]


@pytest.mark.parametrize("path", _RUN_689F_HALLUCINATIONS)
def test_a_hallucinated_path_is_reported_as_the_model_wrote_it(normalizer, path: str):
    """OPEN-82: the error must name the path the caller passed.

    This is the module docstring's own promise -- "the model gets an error
    naming the path it asked for and can correct itself, instead of a
    silent rewrite naming one it did not" -- applied to the one step that
    did not keep it. Under `virtual_mode=True` an unrescued absolute path
    is already correct-by-construction: it means `<project>/home/user/...`,
    which does not exist, so the miss is honest and self-describing.
    """
    validate = normalizer()
    assert validate(path) == path


def test_a_hallucinated_write_does_not_land_at_a_path_nobody_named(normalizer, tmp_path: Path):
    """OPEN-82 / OPEN-12: the test that would have caught both, and did not exist.

    Reads through the old trim were merely wasteful. A WRITE through it
    succeeded, at a path the model never wrote and the user never saw --
    the approval panel previews the resolved path, so there was no
    discrepancy left to show by then.

    Composed exactly as `deepagents.middleware.filesystem` composes it:
    `validate_path(file_path)` then `backend.write(validated, ...)`
    (`sync_write_file`, 0.7.4). Not a mock of the seam -- the seam.
    """
    from deepagents.backends.filesystem import FilesystemBackend

    root = tmp_path / "project"
    # The real layout the run had, so the decoy below is a plausible
    # sibling rather than an empty directory.
    (root / "src" / "models").mkdir(parents=True)
    (root / "src" / "models" / "models.py").write_text("# real", encoding="utf-8")

    validate = normalizer(root=root)
    backend = FilesystemBackend(root_dir=str(root.resolve()), virtual_mode=True)

    asked = "/home/user/Rudra/src/models/todo.py"
    backend.write(validate(asked), "# written by the coder")

    # The old branch put this here, beside the real src/models/, and said
    # it had succeeded.
    assert not (root / "models" / "todo.py").exists()
    # It lands where the model named it: visible, greppable, and the same
    # path the gate previewed and the error would report.
    assert (root / "home" / "user" / "Rudra" / "src" / "models" / "todo.py").exists()


def test_a_container_shaped_deep_path_is_no_longer_trimmed(normalizer):
    """OPEN-82: depth plus a container-shaped leading component is not consent.

    OPEN-12 narrowed this branch to paths whose leading component is a
    directory a container owns, on the theory that the shape identified a
    hallucination. It does -- and that is the objection: a hallucinating
    model produces container-shaped paths, so the guard narrowed the
    silent rewrite to exactly the population that reaches it.
    """
    validate = normalizer()
    assert validate("/home/someone/proj/e.py") == "/home/someone/proj/e.py"


@pytest.mark.parametrize(
    "route_path",
    [
        "/artifacts/large_tool_results/abc123.txt",
        "/skills/active/brainstorming/SKILL.md",
    ],
)
def test_backend_routes_are_returned_untouched(normalizer, route_path: str):
    """Step 0, A1.79: a CompositeBackend route is a real mount the model was
    TOLD about, not a hallucination. It is returned before any rewriting can
    reach it, and stays so."""
    validate = normalizer(route_prefixes=("/artifacts/", "/skills/"))
    assert validate(route_path) == route_path


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

    The path is three levels deep for a reason that OPEN-82 retired: Step
    2d used to keep the final two components, so a shallower path yielded
    the right answer by accident and this test passed without the root
    strip ever running. Step 2d no longer rewrites anything, so the depth
    is now belt-and-braces -- a missed root strip returns the full host
    path and fails here at any depth. Kept because it costs nothing and
    the failure it guards against is a silent one.
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
