# Contributing

Thanks for looking. Please read this before opening anything — it is short and
it will save you time.

## Code contributions are not accepted right now

Rudra is free and open source, and it is developed by one person. **Pull
requests are not accepted.** If you open one, it will be closed unmerged, and
that is not a judgement on the code — it is that this project does not
currently have a review process, and merging work I have not planned costs
more than it saves.

You are welcome to fork it. That is what the Apache-2.0 licence is for.

This may change. "Right now" is meant literally.

## Bug reports and feature requests are welcome

These are genuinely useful and they are read. Open an issue.

Response is best-effort — solo maintainer, no service-level promise. An issue
that goes quiet has not been dismissed.

### What makes a bug report useful

Four things are almost always missing, and without them a report usually
cannot be acted on at all:

1. **Version** — the output of `rudra --version`.
2. **Model and provider** — which model, and whether it is local (Ollama) or
   hosted. A great many reports turn out to be model behaviour rather than
   Rudra behaviour, and this is what distinguishes them.
3. **The exact command**, including flags, and the permission mode. `--auto`
   and `ask` behave differently on purpose.
4. **The run record.** It already exists — every run writes one, no flag
   needed. Attach `.rudra/run/logs/`, which holds `debug-<run-id>.jsonl`,
   `meta.json` (versions, platform, permission mode, models — no keys),
   `usage.json`, `verify.log` and `permissions.jsonl`. Give the run id from
   `rudra log` if you have it.

> **Check the attachment before you post it.** Both files record tool
> arguments and command strings. Payloads are redacted where trace events are
> built, but redaction is pattern matching, not a guarantee — and paths and
> project names are not secrets Rudra knows to hide.

### Security problems do not go in issues

Report them privately — see [SECURITY.md](SECURITY.md).

## Building and running Rudra yourself

Everything about setting up, running the tests, the gate before you push, the
project layout and the house rules is in
**[Documentation/07-development.md](Documentation/07-development.md)**.

It is not repeated here on purpose. One fact, one owner — a second copy would
drift, which this project has a ledger full of examples of.
