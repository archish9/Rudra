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
187 passed, 2 skipped
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

---

## Project layout

```
src/rudra/
├── cli.py            Typer entry point: the main callback and `models test`
├── config.py         ModelConfig, Config.model_for(role), env resolution
├── llm/              Model construction — the only place providers are known
│   ├── factory.py    build_model(role) → BaseChatModel
│   ├── providers.py  Per-provider kwarg policy table
│   ├── probe.py      Live checks behind `rudra models test`
│   └── errors.py     Configuration errors, all raised before any network call
├── agent/
│   ├── main_agent.py Orchestrates planner → coder
│   ├── planner_agent.py
│   └── coder_agent.py
├── middleware/       Behaviour patches applied to agents
├── tools/            Custom tools (planning, interaction)
├── stacks/           Language/framework detection
├── filesystem/       Project tree walking
├── compat/           deepagents version guards and monkeypatches
└── state/            Project config persistence
```

**The one architectural rule worth knowing:** no module outside `rudra/llm/` may import a provider package. `tests/test_no_direct_provider_imports.py` parses every module with `ast` and fails if one does. Ask for a model with `build_model(role)` instead.

---

## Testing against a real model

Live tests are skipped by default so CI needs no secrets. Two conditions must both hold: `RUDRA_LIVE_TESTS=1`, and the configured key variable actually set.

```bash
RUDRA_LIVE_TESTS=1 .venv/bin/pytest -m live -q
```

They read your real `.env`, so they test the configuration in front of you. Everything else in the suite is hermetic — those tests `chdir` to an empty directory so a developer's `.env` can't change the result. Keep it that way; a test that passes in CI and fails locally is worse than no test.

For a full end-to-end check, run the real thing in a scratch directory:

```bash
cd "$(mktemp -d)"
cp /path/to/Rudra/.env .
/path/to/Rudra/.venv/bin/rudra "write hello.py that prints hello world"
python hello.py
```

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

`.github/workflows/ci.yml` runs on every push and pull request:

- `ruff check` and `ruff format --check`
- `pytest` on Python 3.12 and 3.13
- a coverage report (not gated)

CI installs the package before running tests — `tests/test_package_version.py` compares against installed distribution metadata, and an uninstalled source tree reports `0.0.0+unknown` and fails.

Live tests never run in CI: no `RUDRA_LIVE_TESTS`, no keys, no network.

---

**Next:** [Project Status](08-project-status.md) · [How It Works](05-how-it-works.md)
