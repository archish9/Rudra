# 7. Development

For working on Rudra itself.

- [Setting up](#setting-up)
- [Running tests](#running-tests)
- [Linting and formatting](#linting-and-formatting)
- [Project layout](#project-layout)
- [Testing against a real model](#testing-against-a-real-model)
- [House rules](#house-rules)
- [Adding a provider](#adding-a-provider)
- [Continuous integration](#continuous-integration)

---

## Setting up

```bash
git clone https://github.com/archish9/Rudra.git
cd Rudra
uv sync
```

`uv sync` creates `.venv` and installs everything, including the dev tools. Then either activate it or prefix commands with `uv run`:

```bash
source .venv/bin/activate
```

Rudra targets **Python 3.12+**. CI runs 3.12 and 3.13.

Install is editable, so source edits take effect immediately. Reinstall only after changing dependencies or entry points in `pyproject.toml`.

---

## Running tests

```bash
.venv/bin/pytest -q
```

```
656 passed, 2 skipped
```

The two skips are live tests, which need a real model — see [below](#testing-against-a-real-model).

Useful variations:

```bash
.venv/bin/pytest tests/test_llm_factory.py -v      # one file, verbose
.venv/bin/pytest -k "provider" -v                  # by name
.venv/bin/pytest -x                                # stop at first failure
```

Coverage:

```bash
uvx coverage run -m pytest -q
uvx coverage report --include="src/*"
```

Coverage is reported but not enforced. The orchestration loop in `agent/main_agent.py` is deliberately under-tested because it's slated for replacement — tests written against it now would be written only to be deleted.

---

## Linting and formatting

[Ruff](https://docs.astral.sh/ruff/) handles both.

```bash
.venv/bin/ruff check src/ tests/           # lint
.venv/bin/ruff check --fix src/ tests/     # lint and autofix
.venv/bin/ruff format src/ tests/          # format
.venv/bin/ruff format --check src/ tests/  # check without changing
```

Both must be clean before anything merges. CI enforces it.

### Run the gates before you push

The repo ships a `pre-push` hook that runs exactly what CI runs. Enable it once per clone:

```bash
git config core.hooksPath .githooks
```

It runs `ruff check`, `ruff format --check`, and `pytest`, and aborts the push if any of them fails. `git push --no-verify` skips it for one push.

The hook prefers `uv run` over `.venv/bin` deliberately: `uv run` reconciles the environment with `uv.lock` first. A `.venv` built up incrementally over time drifts from the lockfile, and a drifted `.venv` reports failures that have nothing to do with the code you changed — which is exactly how two real defects reached `main` while the local suite was red for unrelated reasons.

If your local suite fails in ways CI doesn't, resync before debugging:

```bash
uv sync
```

---

## Project layout

```
src/rudra/
├── cli.py            Typer entry point: main callback, `models`, `config`, `init`, `doctor`
├── config/           Layered configuration
│   ├── schema.py     Dataclasses, DEFAULTS, reserved sections — imports nothing from Rudra
│   ├── layers.py     The five readers; each returns a dict, none knows precedence
│   ├── loader.py     deep_merge + provenance + validate + Config
│   └── template.py   The commented scaffold `rudra init` writes
├── llm/              Model construction — the only place providers are known
│   ├── factory.py    build_model(role) → BaseChatModel
│   ├── providers.py  Per-provider kwarg policy table
│   ├── probe.py      Live checks behind `rudra models test`
│   └── errors.py     Configuration errors, all raised before any network call
├── permissions/      The gate: what agents may do without asking
│   ├── rules.py      PermissionEngine.decide() — pure, the only policy reader
│   ├── floor.py      The three built-in deny rules
│   ├── middleware.py Short-circuits denied calls before the backend sees them
│   ├── interrupts.py Turns "ask" into a deepagents interrupt
│   ├── approval.py   The terminal prompt and the resume loop
│   ├── diff.py       What you see before approving a write
│   ├── audit.py      run/logs/permissions.jsonl
│   ├── grants.py     "always" grants, in memory for the run only
│   └── env.py        The environment commands run with, minus secrets
├── agent/
│   ├── main_agent.py Run setup: backend, gate, the three planner stages, and
│   │                 the plan-approval gate between planning and working
│   └── planner_agent.py  The three staged agents (clarify · architect ·
│                     breakdown), one tool set each via _tools_for_stage
├── facts/            What this project's agents established, and why
│   ├── store.py      Fact{value, why, source} + FactStore. Imports nothing
│   │                 from Rudra; keys are enumerated nowhere in code
│   └── render.py     facts_block() — the same facts into every agent prompt
├── loop/             The agentic loop
│   ├── ledger.py     Task · TaskStatus · Ledger. Pure data plus load/save
│   ├── tools.py      add_tasks · drop_task · read_ledger — typed, agent-facing
│   ├── bounds.py     failure_signature() — no-progress detection
│   ├── plan_view.py  render_plan() and the approve/revise/cancel prompt
│   └── engine.py     plan() · work() · run_loop() · run_task(). The only
│                     module that writes DONE, and only on a passing gate
├── verify/           The deterministic gate: syntax · lint · typecheck ·
│                     tests · stubs. Six outcomes, not two
├── subagents/        coder · tester · reviewer · general-purpose.
│                     build.py is the ONLY assembly path, so every subagent
│                     carries the permission gate
├── middleware/       Behaviour patches applied to agents
├── shell/
│   └── runner.py     run_gated() — the ONLY subprocess call site in Rudra
├── git/
│   └── core.py       Python git API: status, diff, log, branches, auto-branch
├── testing/
│   ├── runner.py     run_tests() → TestResult, the fix loop's input
│   └── parse.py      Per-runner count parsing from real summary lines
├── tools/            EVERY tool the model can call, and nothing else
│   ├── interaction_tools.py  ask_user, record_fact
│   ├── git_tools.py          git_diff — thin wrapper over git/core.py
│   └── testing_tools.py      run_tests — thin wrapper over testing/runner.py
├── skills/           Vendored methodology corpora. Nothing here is wired to
│   │                 an agent yet — that is the next step
│   ├── registry.py   BUNDLES · DEFAULT_ENABLED — the one list of what ships
│   ├── bundle.py     Bundle + load_bundle(), one BUNDLE.toml at a time
│   ├── transform.py  render(bundles, enabled, dest) — pure; library/ + active/
│   ├── manifest.py   sha256 manifests. Verifying one IS the freeze rule
│   ├── rudra_tools.py  Rudra's entry in superpowers' platform-adaptation scheme
│   ├── notice.py     Generates the repo-root NOTICE from the registry
│   └── bundles/superpowers/  6.3.0, byte-identical, MIT. Never edit — a test
│                     hashes all 52 files and fails if anyone does
├── stacks/           Language/framework detection
├── filesystem/       Project tree walking
├── compat/           deepagents version guards and monkeypatches
└── state/            paths.py (the .rudra/ layout) and the session id
```

**Four architectural rules worth knowing:**

*No module outside `rudra/llm/` may import a provider package.* `tests/test_no_direct_provider_imports.py` parses every module with `ast` and fails if one does. Ask for a model with `build_model(role)` instead.

*Configuration precedence lives in exactly one function*, `config/loader.py::deep_merge`, and every value records which layer set it. This shape exists because the two worst config bugs this project shipped were both "which source won?" questions that a per-field `x or y or default` chain structurally cannot answer. Don't reintroduce per-field resolution.

*One place starts a subprocess*, `shell/runner.py::run_gated`. Git and the test runner both go through it, and it asks the permission engine as `execute` with the real command string — which is why `deny = ["execute:git push*"]` covers the git layer without naming it, and why the audit log records `pytest -q` rather than an opaque tool name. A second call site would be a second gate check, and the half that drifts is the security-relevant one.

*Never build a `.rudra/...` path by hand.* Ask `state/paths.py`. `rudra_paths()` is pure; `ensure_layout()` is the only function that creates anything. No agent-facing prompt names a `.rudra/` path at all any more: the ledger and the fact store are reached only through tools, so a path that moves cannot leave a prompt pointing at nothing. `tests/test_rudra_dir_migration.py` asserts that absence.

---

## Testing against a real model

Live tests are skipped by default so CI needs no secrets. Two conditions must both hold: `RUDRA_LIVE_TESTS=1`, and the configured key variable actually set.

```bash
RUDRA_LIVE_TESTS=1 .venv/bin/pytest -m live -q
```

They read your real `.env`, so they test the configuration in front of you. Everything else in the suite is hermetic — config tests clear every `RUDRA_*`/`OLLAMA_*` variable and point `XDG_CONFIG_HOME` at a temp directory, so neither a developer's `.env` nor their personal `~/.config/rudra/config.toml` can change a result. Keep it that way; a test that passes in CI and fails locally is worse than no test.

For a full end-to-end check, run the real thing in a scratch directory:

```bash
cd "$(mktemp -d)"
/path/to/Rudra/.venv/bin/rudra init
$EDITOR .rudra/config.toml          # point it at a reachable model
/path/to/Rudra/.venv/bin/rudra "write hello.py that prints hello world"
python hello.py
```

To prove the config file itself is doing the work rather than an inherited environment variable, run it under `env -i`:

```bash
env -i HOME="$HOME" PATH="$PATH" /path/to/Rudra/.venv/bin/rudra models test
```

With no `RUDRA_*` or `OLLAMA_*` set at all, anything that still works came from `config.toml`. That is exactly how the layered-config work was accepted, and it catches a class of false pass that copying a `.env` into the scratch directory hides.

---

## House rules

Two conventions this project takes seriously.

### Log a bug before fixing it

Add it to `TODO.md` as `PENDING`, with `file:line` evidence, **before** writing the fix. Then fix it. Then mark it `DONE` with the output that proves it.

The point is that the reasoning survives. A fix with no recorded symptom is impossible to re-evaluate later.

### Cite evidence

Any claim about the codebase should point at `file.py:line`. Any claim that something works should show the command and its output. "Tests pass" is not evidence; the pytest summary line is.

`TODO.md` is the live ledger — what's done, what's pending, and why. Read it before starting work.

---

## Adding a provider

The provider table is data, so this is usually one entry in `PROVIDERS` in `src/rudra/llm/providers.py`:

```python
"myprovider": ProviderEntry(
    name="myprovider",
    lc_prefix="langchain_provider_name",
    max_output_kwarg="max_tokens",
    needs_api_key=True,
    supports_timeout=True,
),
```

Fields:

| Field | Meaning |
|---|---|
| `lc_prefix` | LangChain's provider name, used as the `provider:model` prefix |
| `max_output_kwarg` | What this provider calls its output-token cap |
| `context_kwarg` | Server-side context-window argument, if it has one |
| `needs_api_key` | Whether to resolve `api_key_env` |
| `supports_timeout` | `False` if the provider silently ignores `timeout` |
| `extra_kwargs` | Always-on arguments |

Then add tests to `tests/test_llm_providers.py` asserting which kwargs appear **and which don't**. Absence matters as much as presence: a provider that silently discards an argument produces a misconfiguration nothing else catches. That's exactly why `supports_timeout` exists — Ollama accepts `timeout=` and throws it away.

Add the package to `pyproject.toml` dependencies if LangChain needs one.

---

## Continuous integration

`.github/workflows/ci.yml` runs on **pull requests only**:

- `ruff check` and `ruff format --check`
- `pytest` on Python 3.12 and 3.13, run under `coverage`, which prints a report afterwards

Coverage is reported, never gated — there is no `--fail-under`. It runs inside the test job rather than in one of its own, because a separate job re-ran the entire suite a second time to produce a number that cannot fail the build.

Direct commits to `main` are not covered by CI. `.githooks/pre-push` runs the same three commands locally and is what gates them — enable it, or your commits are checked by nothing. See [Run the gates before you push](#run-the-gates-before-you-push).

What CI still adds over the hook is a *different environment*: it installs from `uv.lock` on a clean machine and has no `.env`. Local runs inherit whatever your working tree has accumulated, and that difference has already hidden real defects.

CI installs the package before running tests — `tests/test_package_version.py` compares against installed distribution metadata, and an uninstalled source tree reports `0.0.0+unknown` and fails.

Live tests never run in CI: no `RUDRA_LIVE_TESTS`, no keys, no network.

---

**Next:** [Project Status](08-project-status.md) · [How It Works](05-how-it-works.md)
