"""AGENTS.md, kept alive (C7.3).

`.rudra/AGENTS.md` was created once by `_ensure_agents_md` and never
written again (A1.9), which made "persistent memory across sessions" a
promise the file itself made and nothing kept.

Everything here is a pure string transform over markdown. The operations
are all "change one section, leave the others exactly as they were" --
a regex over the whole file is how the Session Log ends up eating the
Architecture Notes.

Pure by rule: nothing here imports from Rudra.
"""

from __future__ import annotations

# S12.4: this file reaches the planner through `memory=`, so every line is
# a cost paid on every future run. Twenty is roughly two runs' worth of
# tasks -- a starting number, not a measured one, and deliberately not a
# config key: an unmeasured knob is worse than a constant somebody can
# change with evidence.
SESSION_LOG_ENTRIES = 20

_SESSION_LOG = "Session Log"


def _split(text: str) -> list[tuple[str | None, list[str]]]:
    """The document as (heading, lines) pairs. Preamble has heading None."""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in text.splitlines():
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        else:
            sections[-1][1].append(line)
    return sections


def _join(sections: list[tuple[str | None, list[str]]]) -> str:
    out: list[str] = []
    for heading, lines in sections:
        if heading is not None:
            out.append(f"## {heading}")
        out.extend(lines)
    return "\n".join(out).rstrip() + "\n"


def section_body(text: str, heading: str) -> str:
    """The body of one `##` section, or "" when it is absent."""
    for name, lines in _split(text):
        if name == heading:
            return "\n".join(lines).strip()
    return ""


def replace_section(text: str, heading: str, body: str) -> str:
    """Replace one section's body, appending the section if it is absent."""
    sections = _split(text)
    for index, (name, _) in enumerate(sections):
        if name == heading:
            sections[index] = (name, ["", body.rstrip(), ""])
            return _join(sections)
    sections.append((heading, ["", body.rstrip(), ""]))
    return _join(sections)


def _entries(body: str) -> list[str]:
    """Split a Session Log body into whole entries, discarding placeholders."""
    entries: list[str] = []
    for line in body.splitlines():
        if line.startswith("- "):
            entries.append(line)
        elif entries and line.strip():
            entries[-1] += f"\n{line}"
        # Anything before the first bullet is the "(no sessions yet)"
        # placeholder, and a real entry replaces it rather than joining it.
    return entries


def append_session_entry(text: str, entry: str, cap: int = SESSION_LOG_ENTRIES) -> str:
    """Add one entry to the Session Log, dropping the oldest past `cap`.

    An entry is a `- ` bullet plus any indented continuation lines, so a
    task that touched three files still counts as one entry.
    """
    body = section_body(text, _SESSION_LOG)
    entries = _entries(body)
    entries.append(entry.rstrip())
    return replace_section(text, _SESSION_LOG, "\n".join(entries[-cap:]))


def format_entry(date: str, task_id: str, description: str, files: tuple[str, ...]) -> str:
    """One Session Log entry. `files` comes from git, never from the model."""
    entry = f"- {date} {task_id} {description}"
    if files:
        entry += "\n  " + ", ".join(files)
    return entry
