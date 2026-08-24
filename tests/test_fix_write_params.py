"""Unit tests for markdown-fence stripping and path-argument repair.

The fence half is the behavior compat/overwrite_backend.py used to provide
at the backend layer, with the regex `^```[^\n]*\n(.*)\n```$`. That file no
longer exists, so no line number is cited (A4.9).
U.3 deletes that backend, so the middleware must cover at least as much.

The path half is OPEN-8: a model that emits `/. rudra/AGENTS.md` for
`/.rudra/AGENTS.md`.
"""

from __future__ import annotations

from types import SimpleNamespace

from rudra.middleware.fix_write_params import (
    FixWriteParamsMiddleware,
    _repair_split_dot_segment,
    _strip_fences,
)


def _fixed(name: str, args: dict, *, sandbox: bool = False) -> dict:
    """Run one tool call through the middleware and return its args."""
    request = SimpleNamespace(tool_call={"name": name, "args": args})
    middleware = FixWriteParamsMiddleware(strip_sandbox_prefixes=sandbox)
    return middleware._fix_args(request).tool_call["args"]


def test_strips_language_tagged_fence():
    assert _strip_fences('```python\nprint("hi")\n```') == 'print("hi")\n'


def test_strips_bare_fence():
    assert _strip_fences('```\nprint("hi")\n```') == 'print("hi")\n'


def test_leaves_unfenced_content_alone():
    source = 'print("hi")\n'
    assert _strip_fences(source) == source


def test_strips_fence_with_trailing_newline():
    assert _strip_fences('```python\nprint("hi")\n```\n') == 'print("hi")\n'


def test_strips_fence_with_info_string_attributes():
    r"""The backend's `[^\n]*` matched this; the middleware's
    `[a-zA-Z0-9_\-]*` did not. Deleting the backend must not shrink
    coverage. See TODO.md U.15."""
    assert _strip_fences('```py title="x"\nprint("hi")\n```') == 'print("hi")\n'


def test_keeps_inner_fences_when_stripping_outer():
    content = "```markdown\nSee:\n```\ninner\n```\n```"
    assert _strip_fences(content) == "See:\n```\ninner\n```\n"


def test_leaves_unterminated_fence_alone():
    source = '```python\nprint("hi")\n'
    assert _strip_fences(source) == source


def test_keeps_a_readme_that_legitimately_opens_and_closes_with_a_fence():
    """The mirror image of test_keeps_inner_fences_when_stripping_outer.

    Both inputs start with ``` and end with ```, and both contain further
    fences inside. What separates them is the info string: a model that
    wraps a whole markdown file announces that with ```markdown, so the
    outer fence is a wrapper and comes off. A README's own first line is
    a ```bash block -- the fences are the content, and stripping them
    deletes the file's first opening fence and last closing fence.
    """
    readme = "```bash\nnpm i\n```\n\nSome text\n\n```js\nconst a = 1;\n```"
    assert _strip_fences(readme) == readme


# --- OPEN-8: a dot-segment the model split with whitespace -----------------


def test_repairs_the_observed_malformation():
    """Measured, not invented. nvidia/nemotron emitted this four times in a
    row against a project whose `ls` had just returned `/.rudra/`."""
    assert _repair_split_dot_segment("/. rudra/AGENTS.md") == "/.rudra/AGENTS.md"


def test_repairs_a_dot_segment_anywhere_in_the_path():
    assert _repair_split_dot_segment("/src/. github/workflows") == "/src/.github/workflows"


def test_repairs_a_relative_path_at_the_start_of_the_string():
    assert _repair_split_dot_segment(". rudra/config.toml") == ".rudra/config.toml"


def test_leaves_a_legitimate_space_in_a_filename_alone():
    """The reason the rule anchors to a dot-segment rather than stripping
    whitespace: `My Documents` is a real directory on every desktop OS."""
    for path in ("/My Documents/notes.txt", "/a/b c/d e.py", "/Program Files/x"):
        assert _repair_split_dot_segment(path) == path, path


def test_leaves_ordinary_dot_segments_alone():
    """`.` and `..` are path syntax, and a dot with no whitespace after it
    is not the malformation."""
    for path in ("./x", "../x", "/a/./b", "/a/../b", "/.rudra/AGENTS.md", "."):
        assert _repair_split_dot_segment(path) == path, path


def test_leaves_a_dot_inside_a_segment_alone():
    """Anchored to `^` or `/`, so a dot mid-segment is never touched -- a
    version directory like `v1. 2` keeps whatever the user named it."""
    assert _repair_split_dot_segment("/pkg/v1. 2/mod.py") == "/pkg/v1. 2/mod.py"


def test_leaves_a_trailing_dot_space_alone():
    """Nothing follows the whitespace, so there is no segment to rejoin and
    no evidence the model meant a dotfile."""
    assert _repair_split_dot_segment("/a/. ") == "/a/. "


# --- the repair reaches the tools, with sandbox stripping OFF -------------


def test_read_file_is_repaired_by_default():
    """The failing call from the report. `read_file` was previously
    untouched with `[compat] sandbox_paths` off, because the only path loop
    sat inside that opt-in branch."""
    args = _fixed("read_file", {"file_path": "/. rudra/AGENTS.md"})
    assert args["file_path"] == "/.rudra/AGENTS.md"


def test_write_file_is_repaired_by_default():
    """The severity case. A read of the mangled path errors loudly; a WRITE
    creates a directory literally named `. rudra` and reports success --
    and the shipped AGENTS.md tells the model to `edit_file` that path."""
    args = _fixed("write_file", {"file_path": "/. rudra/AGENTS.md", "content": "x"})
    assert args["file_path"] == "/.rudra/AGENTS.md"


def test_edit_file_and_ls_and_glob_are_repaired_too():
    assert _fixed("edit_file", {"file_path": "/. rudra/x"})["file_path"] == "/.rudra/x"
    assert _fixed("ls", {"path": "/. rudra"})["path"] == "/.rudra"
    assert _fixed("glob", {"pattern": "/. rudra/**"})["pattern"] == "/.rudra/**"


def test_the_repair_survives_the_filename_alias():
    """`filename` is renamed to `file_path` first, so the repair has to run
    after the alias or it would clean a key that no longer exists."""
    args = _fixed("write_file", {"filename": "/. rudra/x", "content": "y"})
    assert args["file_path"] == "/.rudra/x"


def test_content_is_never_path_repaired():
    """File content is data. A line reading `. rudra` inside a document is
    not a path and must survive verbatim."""
    args = _fixed("write_file", {"file_path": "/a.md", "content": "see /. rudra/x\n"})
    assert args["content"] == "see /. rudra/x\n"
