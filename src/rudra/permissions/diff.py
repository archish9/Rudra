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
        existing = _read(_resolve(project_root, raw_path))
        size = f", {len(existing.splitlines())} lines" if existing is not None else ""
        return DiffPreview(f"delete  {raw_path}{size}", "", False)
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
