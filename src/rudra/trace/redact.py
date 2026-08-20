"""Keep credentials out of the artifacts a human keeps (A1.95).

Found on a real key: the planner read `.env` with `read_file` -- a
legitimate, deliberately ungated read, since CLAUDE.md's rule is that
reads are never gated -- and the trace rendered the result verbatim. The
key reached the console, `.rudra/run/logs/debug.jsonl`, and would have
reached 15c's transcript, which is written by default rather than behind a
flag. `Documentation/06-troubleshooting.md` asks users to attach that log
to a GitHub issue.

**Applied where the event is BUILT, not where it is rendered.** The debug
consumer writes `event.as_dict()` and never calls `render()`, so a fix in
the renderer would have cleaned the screen and left the file intact --
which is the more dangerous of the two.

**What this does not do, stated rather than implied:**

* The model still sees whatever it read. This protects the artifacts a
  human keeps and shares; it cannot unread a file.
* It is pattern matching, so it is not a guarantee. A credential with no
  recognisable shape, in a variable with an innocuous name, passes
  through. Treat it as a large reduction in exposure, not as a promise --
  and keep reading a debug log before posting it.
"""

from __future__ import annotations

import re

REDACTED = "<redacted>"
"""What replaces the value. Short, obvious, and searchable in a log."""

_SECRET_NAME = r"[A-Za-z0-9_.-]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Za-z0-9_.-]*"

_ASSIGNMENT = re.compile(
    rf"(?P<name>\b{_SECRET_NAME}\b)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^\s'\"]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)
"""`NAME=value`, `NAME: value`, quoted or not. The NAME is kept: redacting
the whole line would defeat the point of having a trace at all -- seeing
*that* the agent read a credential file is useful, seeing the credential
is not."""

_BEARER = re.compile(r"(?P<prefix>\bBearer\s+)(?P<value>[A-Za-z0-9._\-]{8,})", re.IGNORECASE)

_VENDOR_KEY = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}"
    r"|nvapi-[A-Za-z0-9_-]{8,}"
    r"|ghp_[A-Za-z0-9]{8,}"
    r"|gho_[A-Za-z0-9]{8,}"
    r"|github_pat_[A-Za-z0-9_]{8,}"
    r"|xoxb-[A-Za-z0-9-]{8,}"
    r"|xoxp-[A-Za-z0-9-]{8,}"
    r"|AKIA[0-9A-Z]{12,}"
    r"|AIza[A-Za-z0-9_-]{8,})"
)
"""Bare keys, for when there is no `NAME=` around them -- a key echoed by
a tool, or pasted into prose. The vendor prefix is the only signal there
is, so this list is the one place to add a provider."""


def _is_numeric(value: str) -> bool:
    """Is this a plain number?

    `MAX_TOKENS=131072` and `context_tokens = 512288` match the name
    pattern -- "TOKEN" is a substring of "TOKENS" -- and are ordinary
    configuration a trace should show. Credentials are not bare integers,
    so the value's shape settles it. Measured against Rudra's own config
    keys, which include max_output_tokens and context_tokens.

    The accepted cost: a numeric-only secret (a PIN) survives. That is
    preferred over redacting every token budget in every trace, which is
    how a redactor becomes something people switch off.
    """
    return value.replace("_", "").replace(",", "").isdigit()


def redact(text: str) -> str:
    """Replace credential-shaped values in `text`.

    Idempotent: `<redacted>` contains nothing any pattern matches, so
    redacting twice is redacting once. That matters because an event can
    be rendered for the console and serialised for the debug log from the
    same object.
    """
    if not text:
        return text

    def _assignment(match: re.Match[str]) -> str:
        if _is_numeric(match.group("value")):
            return match.group(0)
        return f"{match.group('name')}{match.group('sep')}{REDACTED}"

    text = _ASSIGNMENT.sub(_assignment, text)
    text = _BEARER.sub(lambda m: f"{m.group('prefix')}{REDACTED}", text)
    return _VENDOR_KEY.sub(REDACTED, text)


__all__ = ["REDACTED", "redact"]
