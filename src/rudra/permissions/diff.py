"""Rendering what a write is about to do, before it does it.

A1.16 is "files silently overwritten -- no diff, no backup, no confirm". A
prompt that asks for approval without showing the change does not close
that; it just moves the silence one step later.

Capped by default because Rudra rewrites whole files: an uncapped 400-line
rewrite scrolls the decision off screen and trains users to approve blind.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rudra.compat.virtual_paths import virtual_to_host

MAX_RENDER_BYTES = 1_000_000
NEW_FILE_PREVIEW_LINES = 10
# How far a delete preview walks a directory before reporting "N+ files".
MAX_DELETE_WALK = 500


@dataclass(frozen=True)
class DiffPreview:
    """One rendered approval body."""

    header: str
    body: str
    truncated: bool


def _resolve(project_root: Path, raw: str) -> Path:
    """The real file this tool call will touch.

    Mirrors PermissionEngine._resolve through the same shared function, and
    must: the backend is virtual_mode=True, so `/src/app.py` is
    `<project>/src/app.py`, not the host's. Reading it as a host path made
    the approval panel stat and preview a DIFFERENT file from the one the
    write would change -- the user was shown one thing and approved
    another (CR-B4).
    """
    host = virtual_to_host(raw, Path(project_root))
    return host if host is not None else Path(raw)


def _read(path: Path) -> str | None:
    """Existing text content, or None if absent, binary, or unreadable."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _delete_detail(project_root: Path, raw: str) -> str:
    """What the user is about to lose, beyond the path they can already see.

    `delete` reached a writing agent in OPEN-49, and it is not `write_file`
    with a smaller blast radius: upstream removes a directory recursively
    (filesystem.py:1258-1265). Until then this branch read the target as
    text and appended a line count, so a missing path, a binary file and a
    forty-file directory all rendered as a bare `delete  <path>` -- the one
    prompt standing between a recursive delete and the user's project said
    nothing about which of the three they were approving.

    The directory walk is capped: the number is a courtesy in a header, and
    a `node_modules`-shaped target must not make the user wait on it.
    """
    target = _resolve(project_root, raw)
    if target.is_dir():
        seen = 0
        for entry in target.rglob("*"):
            if entry.is_file():
                seen += 1
                if seen > MAX_DELETE_WALK:
                    return f" (directory, {MAX_DELETE_WALK}+ files)"
        return f" (directory, {seen} files)"
    existing = _read(target)
    if existing is not None:
        return f", {len(existing.splitlines())} lines"
    if not target.exists():
        return " (not found)"
    # Real, and readable as neither text nor a directory. The path is the
    # whole of what we can honestly say about it.
    return ""


def _counts(diff_lines: list[str]) -> tuple[int, int]:
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    return added, removed


def _cap(lines: list[str], max_lines: int, full: bool) -> tuple[str, bool]:
    if full or len(lines) <= max_lines:
        return "\n".join(lines), False
    remainder = len(lines) - max_lines
    shown = [*lines[:max_lines], f"… {remainder} more changed lines"]
    return "\n".join(shown), True


def _write_preview(
    args: dict[str, Any], project_root: Path, full: bool, max_lines: int
) -> DiffPreview:
    raw_path = str(args.get("file_path", ""))
    content = args.get("content")
    if not isinstance(content, str):
        content = ""
    path = _resolve(project_root, raw_path)
    size = len(content.encode("utf-8", errors="replace"))

    if size > MAX_RENDER_BYTES:
        return DiffPreview(
            f"write_file  {raw_path}  (too large to preview, {size} bytes)", "", False
        )
    if "\x00" in content:
        return DiffPreview(f"write_file  {raw_path}  (binary content, not rendered)", "", False)

    if not path.exists():
        lines = content.splitlines()
        body, _ = _cap(lines[:NEW_FILE_PREVIEW_LINES], NEW_FILE_PREVIEW_LINES, full=full)
        return DiffPreview(
            f"write_file  {raw_path}  (new file, {len(lines)} lines, {size} bytes)",
            body,
            len(lines) > NEW_FILE_PREVIEW_LINES,
        )

    before = _read(path)
    if before is None:
        return DiffPreview(f"write_file  {raw_path}  (existing content unreadable)", "", False)

    diff = list(
        difflib.unified_diff(
            before.splitlines(), content.splitlines(), lineterm="", n=2, fromfile="", tofile=""
        )
    )
    if not diff:
        return DiffPreview(f"write_file  {raw_path}  (overwrite, no change)", "", False)
    added, removed = _counts(diff)
    body, truncated = _cap(diff[2:], max_lines, full)
    return DiffPreview(f"write_file  {raw_path}  +{added} -{removed}  (overwrite)", body, truncated)


def _edit_preview(args: dict[str, Any], full: bool, max_lines: int) -> DiffPreview:
    raw_path = str(args.get("file_path", ""))
    old = str(args.get("old_string", ""))
    new = str(args.get("new_string", ""))
    diff = list(
        difflib.unified_diff(
            old.splitlines(), new.splitlines(), lineterm="", n=2, fromfile="", tofile=""
        )
    )
    added, removed = _counts(diff)
    body, truncated = _cap(diff[2:], max_lines, full)
    return DiffPreview(f"edit_file  {raw_path}  +{added} -{removed}", body, truncated)


def render(
    tool: str,
    args: dict[str, Any],
    project_root: Path,
    *,
    full: bool = False,
    max_lines: int = 20,
) -> DiffPreview:
    """Render one pending tool call for an approval prompt."""
    if tool == "write_file":
        return _write_preview(args, project_root, full, max_lines)
    if tool == "edit_file":
        return _edit_preview(args, full, max_lines)
    if tool == "delete":
        raw_path = str(args.get("file_path", ""))
        return DiffPreview(f"delete  {raw_path}{_delete_detail(project_root, raw_path)}", "", False)
    if tool == "execute":
        command = str(args.get("command", ""))
        return DiffPreview("execute", f"  {command}\n  cwd: {project_root}", False)
    if tool == "call_mcp_tool":
        # No diff: an MCP call has no previewable patch. What the user needs
        # is which server, which tool, and with what -- so show exactly that.
        raw_id = str(args.get("tool_id", ""))
        server, _, name = raw_id.partition("__")
        body = json.dumps(args.get("arguments") or {}, indent=2, default=str)
        return DiffPreview(f"MCP  {server} → {name}", f"  {body}", False)
    return DiffPreview(tool, "", False)


__all__ = ["MAX_RENDER_BYTES", "DiffPreview", "render"]
