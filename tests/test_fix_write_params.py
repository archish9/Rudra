"""Unit tests for markdown-fence stripping.

This is the behavior compat/overwrite_backend.py used to provide at the
backend layer, with the regex `^```[^\n]*\n(.*)\n```$`. That file no
longer exists, so no line number is cited (A4.9).
U.3 deletes that backend, so the middleware must cover at least as much.
"""

from __future__ import annotations

from rudra.middleware.fix_write_params import _strip_fences


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
