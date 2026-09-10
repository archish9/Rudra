# Security Policy

## Reporting a vulnerability

Report privately through GitHub: go to the
[Security tab](https://github.com/archish9/Rudra/security) on this repository
and choose **Report a vulnerability**. That opens a private advisory visible
only to the maintainer.

**Please do not open a public issue for a security problem.** Ordinary bugs
belong in issues — see [CONTRIBUTING.md](CONTRIBUTING.md).

Rudra is maintained by one person. Reports are read and taken seriously, but
there is no response-time guarantee.

## Supported versions

Rudra is pre-1.0. The only supported version is the current `main`. There is
no backporting and no security-fix branch.

## What counts as a vulnerability

A defect that lets something happen which Rudra's permission model says
cannot happen. For example:

- A tool call that should have been denied by the permission engine, the deny
  floor, or the `--auto` shell rule, and was allowed.
- A credential reaching the console, `.rudra/run/transcripts/`, or
  `.rudra/run/logs/debug-<run-id>.jsonl` — redaction happens where a trace
  event is built, and a path around it is a real defect. That includes a
  traceback: an exception's own message reaches the log too (OPEN-109).
- A write escaping the project root through a filesystem tool, which
  `virtual_mode=True` is supposed to confine.
- Config or `.env` handling that discloses an API key.

## What is not a vulnerability

Rudra runs commands and writes files because that is what it is for. The
following are documented behaviour, not defects:

- **The agent running a command you approved.** `ask` mode shows you the
  command string first; approving it is consent.
- **`--auto --allow-shell` running commands unattended.** That flag exists to
  turn this on, and turning it on restores the full exposure knowingly.
- **An approved command doing anything your user account can do.** There is no
  sandbox. See the threat model below.
- **A model writing bad, insecure, or wrong code.** Review what it produces.

## The threat model

The shell-execution model, what the permission layer does and does not
contain, and a measured example of an agent routing around a denial, are set
out in full in
**[Permissions — the security model](Documentation/09-permissions.md#the-security-model-stated-plainly)**.

Read that before running Rudra unattended on anything you care about.
