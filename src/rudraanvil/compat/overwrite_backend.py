"""FilesystemBackend subclass that allows write_file to overwrite existing files.

WHY THIS EXISTS
--------------
deepagents' FilesystemBackend.write() refuses to overwrite an existing file:

    if resolved_path.exists():
        return WriteResult(error="Cannot write to ... because it already exists.")

This forces the LLM to use the read → edit workflow for any file it already
created.  In practice, LLMs using smaller/local models often lose track of
this constraint and retry write_file in a loop:

  write_file → "already exists" error
  edit_file  → no-op success (same content replacing same content)
  write_file → "already exists" error   ← infinite loop

The real intent of the constraint is to prevent *accidental* overwrites.  For
an autonomous coding agent that is *explicitly* providing complete file
content, silently overwriting is the correct behaviour.

This subclass removes that restriction: write_file always succeeds whether the
file exists or not.
"""

from __future__ import annotations

import re
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import WriteResult


def _strip_markdown_fences(content: str) -> str:
    """Strip leading/trailing markdown code fences if the whole content is fenced.

    Handles:
      ```python\\n...code...\\n```
      ```\\n...code...\\n```
    """
    stripped = content.strip()
    match = re.match(r"^```[^\n]*\n(.*)\n```$", stripped, re.DOTALL)
    return match.group(1) if match else content


class OverwriteFilesystemBackend(FilesystemBackend):
    """FilesystemBackend that allows write_file to overwrite existing files."""

    def write(self, file_path: str, content: str) -> WriteResult:
        """Create or overwrite a file with the given content.

        Identical to the parent implementation except the existence check is
        removed — existing files are silently overwritten.
        """
        try:
            resolved_path = self._resolve_path(file_path)
            resolved_path.parent.mkdir(parents=True, exist_ok=True)

            content = _strip_markdown_fences(content)
            Path(resolved_path).write_text(content, encoding="utf-8")

            return WriteResult(path=file_path, files_update=None)
        except (OSError, UnicodeEncodeError) as e:
            return WriteResult(error=f"Error writing file '{file_path}': {e}")
