# Step 5 — Provider-Agnostic Model Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Rudra's hardcoded `ChatOllama` construction with a role-based factory that builds any LangChain chat model from configuration, so Rudra works against Ollama, vLLM, OpenRouter, LM Studio, Anthropic, OpenAI, and Google.

**Architecture:** A frozen per-provider policy table (`llm/providers.py`) declares which kwargs each provider actually accepts. A resolver in `config.py` turns environment variables into a `ModelConfig` per role. `llm/factory.py` composes the two into a single `init_chat_model` call, layered on deepagents' provider profiles. Every existing `ChatOllama` call site becomes `build_model(role)`.

**Tech Stack:** Python 3.12+ (project `.venv` is 3.13) · `deepagents==0.7.4` · `langchain>=1.3.14` · `langchain-core>=1.5.0` · `langchain-openai>=1.4.1` (new) · Typer · Rich · pytest · ruff

**Spec:** `docs/superpowers/specs/2026-08-10-step5-model-factory-design.md` (commits `87b0a69`, `a68475c`)

## Global Constraints

- **Session rule 2 (non-negotiable):** never fix a bug on discovery. Add it to `TODO.md` as `PENDING` with `file:line` evidence, then fix it, then mark `DONE`. Task 0 exists solely to satisfy this for `N1`–`N5`.
- **Session rule 3:** every claim about the codebase cites `file.py:line`. No assumptions.
- **Session rule 4:** verify before claiming done. Run the command, show the output.
- **Session rule 6:** update `TODO.md` in the same commit as the code it describes.
- **Ruff standard:** `.venv/bin/ruff check src/ tests/` must print `All checks passed!` and `.venv/bin/ruff format --check src/ tests/` must report all files formatted. Step 4 left both clean; do not regress them.
- **Test standard:** `.venv/bin/pytest -q` currently reports `114 passed`. It must never go down.
- **D6 — minimum local model is 32B.** No default anywhere may name a smaller model.
- **C1.5 — API keys never live in configuration.** Config names an environment *variable*; the value is read from the environment. No error message, log line, or repr may contain a key value.
- **Python version:** use `.venv/bin/python`, `.venv/bin/pytest`, `.venv/bin/ruff`. The venv is Python 3.13; the package supports 3.12+.
- **Live tests never run by default.** They require `RUDRA_LIVE_TESTS=1` plus a reachable backend, so CI stays green with no secrets.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/rudra/llm/errors.py` | Create — three exception types, all raised before any network call | 1 |
| `src/rudra/llm/providers.py` | Create — frozen per-provider kwarg policy table; pure data plus one method | 1 |
| `src/rudra/llm/__init__.py` | Create — public surface: `build_model` | 3 |
| `src/rudra/llm/factory.py` | Create — `build_model(role, cfg)`; the only module that calls `init_chat_model` | 3 |
| `src/rudra/llm/probe.py` | Create — live reachability + tool-calling probe | 6 |
| `src/rudra/config.py` | Modify — `ModelConfig` replaces `OllamaConfig`; `Config.model_for`; `Config.load(project_root)` | 2 |
| `src/rudra/agent/planner_agent.py` | Modify `:78-84` — `ChatOllama(...)` → `build_model("planner")` | 4 |
| `src/rudra/agent/coder_agent.py` | Modify `:58-64` — `ChatOllama(...)` → `build_model("coder")` | 4 |
| `src/rudra/agent/main_agent.py` | Modify `:543-544` — role-based model names | 4 |
| `src/rudra/cli.py` | Modify `:211-215`, `:224-225`, `:308-309` — A5.2 reorder, model display, `models` sub-app | 5, 6 |
| `tests/test_llm_providers.py` | Create — registry and kwarg-gating tests | 1 |
| `tests/test_config.py` | Modify — role resolution, deprecation shim, A5.2 | 2 |
| `tests/test_llm_factory.py` | Create — construction, profile merge, capability check, errors, N4, N5 | 3 |
| `tests/test_no_direct_provider_imports.py` | Create — the greppable C1.1 acceptance criterion | 4 |
| `tests/test_llm_live.py` | Create — `@pytest.mark.live`, skipped without a backend | 6 |
| `pyproject.toml` | Modify — `+langchain-openai>=1.4.1`, pytest `live` marker | 3, 6 |
| `.env.example` | Modify — `RUDRA_*` block, `OLLAMA_*` marked deprecated | 7 |
| `TODO.md` | Modify — `N1`–`N5` added in Task 0; rows closed as tasks land | 0, all |

**Boundaries:** `llm/providers.py` imports nothing from Rudra. `llm/factory.py` imports `providers`, `config`, `errors`, and langchain. `llm/probe.py` imports `factory` only. Nothing under `llm/` imports `agent/` or `cli.py`, so the dependency arrow points one way and `llm/` is testable with no agent machinery.

---

## Task 0: Log all five findings in TODO.md before touching code

Session rule 2 is explicit and the owner asked for it directly: nothing gets fixed before it is listed as `PENDING` with evidence. This task writes no source code.

**Files:**
- Modify: `TODO.md` (Section A ledger)

**Interfaces:**
- Consumes: nothing
- Produces: ledger IDs `A1.34`–`A1.38`, referenced by every later task's commit message

- [ ] **Step 1: Find the highest allocated A1.x ID**

Run: `grep -oE '^\| A1\.[0-9]+' TODO.md | sort -t. -k2 -n | tail -3`

Expected: the highest is `A1.33`. If it is not, use the next five IDs after whatever the real highest is, and substitute them everywhere below.

- [ ] **Step 2: Append five PENDING rows to the Section A table**

Insert after the `A1.27` row, matching the existing table's column layout (`| ID | Status | Description | Evidence |`):

```markdown
| A1.34 | PENDING | **`config.py:19` defaults `OLLAMA_MODEL` to `qwen3:14b`, below the D6 32B floor.** `.env.example:9` was corrected to `qwen3:32b` by A1.23, but the coded default it shadows was not — so any user without a `.env` silently runs a 14B model against a codebase whose small-model workarounds were deleted in Step 2 under D6. Same class as A1.23, different file. Fixed in Step 5 (`docs/superpowers/plans/2026-08-10-step5-model-factory.md`, Task 2): `OllamaConfig` is deleted and the replacement `ModelConfig` default is `qwen3:32b` | `src/rudra/config.py:19` (`os.getenv("OLLAMA_MODEL", "qwen3:14b")`) vs `.env.example:9` (`OLLAMA_MODEL=qwen3:32b`) |
| A1.35 | PENDING | **Built-in deepagents harness profiles can never match a Rudra model.** `graph.py:584` sets `_model_spec = model if isinstance(model, str) else None`, and Rudra passes `BaseChatModel` instances, so spec is `None`. The instance fallback in `_harness_profile_for_model` then fails because `_get_harness_profile` rejects any spec with `count(":") > 1`, which every Ollama tag (`qwen3:32b`) and the OpenRouter `:free` suffix produce. Measured against the dev model: `ls_provider=openai`, identifier `nvidia/nemotron-3-ultra-550b-a55b:free` → `HarnessProfile()` default, so the shipped Nemotron 3 Ultra profile never loads. **Deferred to U.10** — detecting the miss requires guessing a canonical spec from user config, which is the judgment U.10 exists to make | `.venv/.../deepagents/graph.py:584,605`; `profiles/harness/harness_profiles.py:1078,1086-1087,1283,1300`; `profiles/harness/_nvidia_nemotron_3_ultra.py:52` registers `openrouter:nvidia/nemotron-3-ultra-550b-a55b` |
| A1.36 | PENDING | **`OllamaConfig.timeout` is dead config.** No call site passes it — `planner_agent.py:78-84` and `coder_agent.py:58-64` pass only `model`, `base_url`, `temperature`, `num_predict`, `reasoning`. Worse, `ChatOllama` has no `timeout` field and no alias, so passing it is accepted and silently discarded: `ChatOllama(model='q', base_url='http://x', timeout=42)` constructs, and `getattr(m, 'timeout', 'ABSENT')` returns `ABSENT`. Honest support means `client_kwargs={"timeout": n}`, a behavior change. **Deferred** — Step 5 omits `timeout` for the `ollama` provider rather than faking it | `src/rudra/config.py:33`; `src/rudra/agent/planner_agent.py:78-84`; `src/rudra/agent/coder_agent.py:58-64` |
| A1.37 | PENDING | **`apply_provider_profile` is a beta-flagged deepagents API and becomes load-bearing in Step 5's model factory.** Its module docstring states `deepagents.profiles` "exposes beta APIs that may receive minor changes in future releases". It joins the U.4 `validate_path` monkeypatch and the U.13 private-module import on the upgrade-hazard list guarded by `src/rudra/compat/version_guard.py`. Fixed in Step 5 Task 3 via `require_deepagents_attr` | `.venv/.../deepagents/profiles/provider/provider_profiles.py` module docstring; `pyproject.toml:30` pins `deepagents==0.7.4` |
| A1.38 | PENDING | **`openai_compatible` silently inherits OpenAI's Responses API.** The `count(":") > 1` rejection above applies to the *provider* registry too, not just harness profiles. `openai_compatible` maps to the `openai` LangChain prefix, so a colon-free model id picks up deepagents' built-in `ProviderProfile(init_kwargs={"use_responses_api": True})` and injects `/responses` into endpoints that serve only `/chat/completions` — vLLM, LM Studio, Groq, Together, and OpenRouter. The dev model escapes only because its identifier contains a colon. Fixed in Step 5 Task 1: `openai_compatible` sets `use_responses_api=False` | measured: `apply_provider_profile('openai:gpt-5.4')` → `{'use_responses_api': True}`; `apply_provider_profile('openai:nvidia/nemotron-3-ultra-550b-a55b:free')` → `{}`; `.venv/.../deepagents/profiles/provider/_openai.py:21-23` |
```

- [ ] **Step 3: Mark Step 5 IN PROGRESS in Section E**

In the Section E table row for step **5**, append to the "Why here" cell:

```markdown
 — **STEP 5 IN PROGRESS** (started 2026-08-10). Spec: `docs/superpowers/specs/2026-08-10-step5-model-factory-design.md`. Plan: `docs/superpowers/plans/2026-08-10-step5-model-factory.md`. Findings logged before work began per session rule 2: `A1.34`–`A1.38`.
```

- [ ] **Step 4: Verify the table still renders and IDs are unique**

Run: `grep -c '^| A1\.\(28\|29\|30\|31\|32\) |' TODO.md`
Expected: `5`

Run: `grep -oE '^\| A1\.[0-9]+' TODO.md | sort | uniq -d`
Expected: no output (no duplicate IDs)

- [ ] **Step 5: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): log A1.34-A1.38 before Step 5 touches code

Session rule 2: nothing gets fixed before it is listed as PENDING with
file:line evidence. Five findings from the Step 5 design session.

A1.34 qwen3:14b default below the D6 32B floor  -> fixed in Task 2
A1.35 harness profiles unreachable for any model -> deferred to U.10
A1.36 OllamaConfig.timeout is dead config        -> deferred
A1.37 apply_provider_profile is a beta API       -> guarded in Task 3
A1.38 openai_compatible inherits Responses API   -> fixed in Task 1"
```

---

## Task 1: Provider registry and errors

The foundation: pure data with no Rudra imports, so it tests without any agent or config machinery.

**Files:**
- Create: `src/rudra/llm/__init__.py` (empty for now; Task 3 fills it)
- Create: `src/rudra/llm/errors.py`
- Create: `src/rudra/llm/providers.py`
- Test: `tests/test_llm_providers.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class ModelConfigError(Exception)`, `class UnknownProviderError(ModelConfigError)`, `class MissingApiKeyError(ModelConfigError)`, `class ModelCapabilityError(ModelConfigError)`
  - `UnknownProviderError(provider: str, valid: tuple[str, ...])`
  - `MissingApiKeyError(role: str, env_var: str)`
  - `ModelCapabilityError(role: str, model: str)`
  - `@dataclass(frozen=True) class ProviderEntry` with fields `name: str`, `lc_prefix: str`, `max_output_kwarg: str | None`, `needs_api_key: bool`, `supports_timeout: bool`, `context_kwarg: str | None`, `extra_kwargs: Mapping[str, Any]`
  - `ProviderEntry.build_kwargs(settings) -> dict[str, Any]` where `settings` is Task 2's `ModelConfig`
  - `PROVIDERS: Mapping[str, ProviderEntry]` keyed by `ollama`, `openai_compatible`, `openai`, `anthropic`, `google`

`build_kwargs` is typed against `ModelConfig` from Task 2, which does not exist yet. Use a `TYPE_CHECKING` import so Task 1 stands alone, and duck-type the attribute reads. The attributes it reads are exactly: `base_url`, `temperature`, `max_output_tokens`, `context_tokens`, `timeout`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm_providers.py`:

```python
"""Per-provider kwarg policy — guards A1.36 and A1.38.

Kwarg passthrough is not safe. ChatOllama accepts `timeout=` and silently
discards it (no field, no alias), while ChatOpenAI, ChatAnthropic, and
ChatGoogleGenerativeAI accept it through pydantic aliases. A blanket
passthrough therefore works on three providers and loses a setting on the
fourth with no error — which is why each provider declares its own kwargs.

These tests assert absence as loudly as presence. A kwarg that should not
be emitted is a silent misconfiguration, not a crash, so nothing else will
catch it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rudra.llm.errors import (
    MissingApiKeyError,
    ModelCapabilityError,
    ModelConfigError,
    UnknownProviderError,
)
from rudra.llm.providers import PROVIDERS


@dataclass(frozen=True)
class FakeSettings:
    """Stand-in for Task 2's ModelConfig — build_kwargs only reads attributes."""

    base_url: str | None = "http://localhost:11434"
    temperature: float | None = 0.3
    max_output_tokens: int | None = 4096
    context_tokens: int | None = 32768
    timeout: int | None = 300


def test_every_documented_provider_is_registered() -> None:
    assert set(PROVIDERS) == {
        "ollama",
        "openai_compatible",
        "openai",
        "anthropic",
        "google",
    }


def test_ollama_emits_its_own_kwarg_names() -> None:
    kwargs = PROVIDERS["ollama"].build_kwargs(FakeSettings())

    assert kwargs["num_predict"] == 4096
    assert kwargs["num_ctx"] == 32768
    assert kwargs["reasoning"] is True
    assert kwargs["temperature"] == 0.3
    assert kwargs["base_url"] == "http://localhost:11434"


def test_ollama_omits_timeout() -> None:
    """A1.36: ChatOllama accepts timeout= and silently discards it."""
    assert "timeout" not in PROVIDERS["ollama"].build_kwargs(FakeSettings())


def test_ollama_sets_the_server_side_context_window() -> None:
    """num_ctx is the Ollama server's allocation; it defaults to 4096 and
    truncates silently above that. profile["max_input_tokens"] only tells
    deepagents when to summarize, so setting one without the other leaves
    either the agent or the server working against the wrong window."""
    assert PROVIDERS["ollama"].build_kwargs(FakeSettings()).get("num_ctx") == 32768


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai_compatible", "max_tokens"),
        ("openai", "max_tokens"),
        ("anthropic", "max_tokens"),
        ("google", "max_output_tokens"),
    ],
)
def test_hosted_providers_use_their_own_max_output_kwarg(provider: str, expected: str) -> None:
    kwargs = PROVIDERS[provider].build_kwargs(FakeSettings())
    assert kwargs[expected] == 4096


@pytest.mark.parametrize("provider", ["openai_compatible", "openai", "anthropic", "google"])
def test_ollama_only_kwargs_never_leak_to_other_providers(provider: str) -> None:
    """C1.4: num_predict and reasoning are Ollama-only and raise elsewhere."""
    kwargs = PROVIDERS[provider].build_kwargs(FakeSettings())

    assert "num_predict" not in kwargs
    assert "num_ctx" not in kwargs
    assert "reasoning" not in kwargs


@pytest.mark.parametrize("provider", ["openai_compatible", "openai", "anthropic", "google"])
def test_hosted_providers_accept_timeout(provider: str) -> None:
    assert PROVIDERS[provider].build_kwargs(FakeSettings())["timeout"] == 300


def test_openai_compatible_disables_the_responses_api() -> None:
    """A1.38: openai_compatible maps to the `openai` prefix, so it inherits
    deepagents' ProviderProfile(init_kwargs={"use_responses_api": True}).
    vLLM, LM Studio, Groq, Together, and OpenRouter serve /chat/completions
    and not /responses."""
    assert PROVIDERS["openai_compatible"].build_kwargs(FakeSettings())[
        "use_responses_api"
    ] is False


def test_real_openai_does_not_disable_the_responses_api() -> None:
    """The suppression is scoped to the compatible path, not to OpenAI itself."""
    assert "use_responses_api" not in PROVIDERS["openai"].build_kwargs(FakeSettings())


def test_unset_optional_settings_emit_no_kwarg() -> None:
    """A None must mean "let the provider default", not "send None"."""
    empty = FakeSettings(
        base_url=None, temperature=None, max_output_tokens=None, context_tokens=None, timeout=None
    )
    kwargs = PROVIDERS["anthropic"].build_kwargs(empty)

    assert "base_url" not in kwargs
    assert "temperature" not in kwargs
    assert "max_tokens" not in kwargs
    assert "timeout" not in kwargs


@pytest.mark.parametrize("provider", ["openai_compatible", "openai", "anthropic", "google"])
def test_hosted_providers_need_an_api_key(provider: str) -> None:
    assert PROVIDERS[provider].needs_api_key is True


def test_ollama_needs_no_api_key() -> None:
    assert PROVIDERS["ollama"].needs_api_key is False


def test_lc_prefixes_are_the_langchain_names() -> None:
    """These strings are passed to init_chat_model; a typo is a runtime error."""
    assert PROVIDERS["ollama"].lc_prefix == "ollama"
    assert PROVIDERS["openai_compatible"].lc_prefix == "openai"
    assert PROVIDERS["openai"].lc_prefix == "openai"
    assert PROVIDERS["anthropic"].lc_prefix == "anthropic"
    assert PROVIDERS["google"].lc_prefix == "google_genai"


def test_unknown_provider_error_lists_the_valid_names() -> None:
    error = UnknownProviderError("gpt4all", tuple(PROVIDERS))

    assert "gpt4all" in str(error)
    for name in PROVIDERS:
        assert name in str(error)


def test_missing_api_key_error_names_the_variable_not_the_value() -> None:
    """C1.5: no error may ever carry a key value."""
    error = MissingApiKeyError("planner", "OPENROUTER_API_KEY")

    assert "OPENROUTER_API_KEY" in str(error)
    assert "planner" in str(error)


def test_capability_error_names_role_and_model() -> None:
    error = ModelCapabilityError("coder", "some-model")

    assert "coder" in str(error)
    assert "some-model" in str(error)


def test_all_errors_share_one_base() -> None:
    """Lets a caller catch every configuration failure with one except."""
    assert issubclass(UnknownProviderError, ModelConfigError)
    assert issubclass(MissingApiKeyError, ModelConfigError)
    assert issubclass(ModelCapabilityError, ModelConfigError)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_providers.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'rudra.llm'`

- [ ] **Step 3: Create the package and errors module**

Create `src/rudra/llm/__init__.py`:

```python
"""Provider-agnostic model construction. See TODO.md C1.1-C1.8."""
```

Create `src/rudra/llm/errors.py`:

```python
"""Model-construction failures.

Every error here is raised before any network call, so a misconfigured run
dies at startup instead of part-way through inference. That is the point of
C1.7.

No error message may contain an API key value (C1.5). Messages name the
environment variable; the value is never read into a message.
"""

from __future__ import annotations


class ModelConfigError(Exception):
    """Base for every model-construction failure."""


class UnknownProviderError(ModelConfigError):
    """Configuration named a provider Rudra does not implement."""

    def __init__(self, provider: str, valid: tuple[str, ...]) -> None:
        super().__init__(
            f"Unknown provider {provider!r}. Valid providers: {', '.join(sorted(valid))}."
        )
        self.provider = provider


class MissingApiKeyError(ModelConfigError):
    """A provider needs a key and the named environment variable is unset."""

    def __init__(self, role: str, env_var: str) -> None:
        super().__init__(
            f"Role {role!r} needs an API key, but environment variable {env_var!r} is not set. "
            f"Export it, or point the role's api_key_env at a different variable. "
            f"Rudra never reads key values from configuration files."
        )
        self.role = role
        self.env_var = env_var


class ModelCapabilityError(ModelConfigError):
    """The model's profile explicitly reports that it cannot call tools."""

    def __init__(self, role: str, model: str) -> None:
        super().__init__(
            f"Model {model!r} (role {role!r}) reports tool_calling=False. "
            f"deepagents requires tool calling for every agent, so this model cannot be used. "
            f"Run `rudra models test` to check a candidate model."
        )
        self.role = role
        self.model = model
```

- [ ] **Step 4: Create the provider registry**

Create `src/rudra/llm/providers.py`:

```python
"""Per-provider construction policy.

Why a policy table and not kwarg passthrough (A1.36): ChatOllama has no
`timeout` field and no alias, so `ChatOllama(model='q', timeout=42)`
constructs without error and drops the value — `getattr(m, 'timeout',
'ABSENT')` returns `ABSENT`. The other three providers accept `base_url`,
`api_key`, and `timeout` through pydantic aliases despite differing field
names (`anthropic_api_url`, `default_request_timeout`, `google_api_key`,
`openai_api_base`, `request_timeout`). Blanket passthrough would therefore
work on three providers and silently lose a setting on the fourth.

Adding a provider is one row in PROVIDERS, not a new code path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rudra.config import ModelConfig


@dataclass(frozen=True)
class ProviderEntry:
    """How to turn a role's settings into constructor kwargs.

    This module deliberately avoids naming LangChain's model-construction
    entry point even in prose: tests/test_no_direct_provider_imports.py
    asserts that exactly one file in the package mentions it.
    """

    name: str
    lc_prefix: str
    """LangChain provider name used as the `provider:model` spec prefix."""

    max_output_kwarg: str | None
    """Provider's name for the output-token cap, or None if it has none."""

    needs_api_key: bool
    supports_timeout: bool
    """False only for ollama, which silently discards `timeout` (A1.36)."""

    context_kwarg: str | None = None
    """Server-side context-window kwarg. Ollama only: `num_ctx` tells the
    server how much context to allocate (it defaults to 4096 and truncates
    silently above that), which is a separate concern from
    profile["max_input_tokens"] telling deepagents when to summarize."""

    extra_kwargs: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    """Always-on kwargs for this provider."""

    def build_kwargs(self, settings: ModelConfig) -> dict[str, Any]:
        """Emit exactly the kwargs this provider accepts.

        A `None` setting emits no kwarg at all, so the provider's own default
        applies rather than an explicit `None` being sent over the wire.
        """
        kwargs: dict[str, Any] = {}
        if settings.base_url is not None:
            kwargs["base_url"] = settings.base_url
        if settings.temperature is not None:
            kwargs["temperature"] = settings.temperature
        if self.max_output_kwarg is not None and settings.max_output_tokens is not None:
            kwargs[self.max_output_kwarg] = settings.max_output_tokens
        if self.context_kwarg is not None and settings.context_tokens is not None:
            kwargs[self.context_kwarg] = settings.context_tokens
        if self.supports_timeout and settings.timeout is not None:
            kwargs["timeout"] = settings.timeout
        kwargs.update(self.extra_kwargs)
        return kwargs


PROVIDERS: Mapping[str, ProviderEntry] = MappingProxyType(
    {
        "ollama": ProviderEntry(
            name="ollama",
            lc_prefix="ollama",
            max_output_kwarg="num_predict",
            needs_api_key=False,
            supports_timeout=False,
            context_kwarg="num_ctx",
            extra_kwargs=MappingProxyType({"reasoning": True}),
        ),
        "openai_compatible": ProviderEntry(
            name="openai_compatible",
            lc_prefix="openai",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
            # A1.38: this path maps to the `openai` prefix and would otherwise
            # inherit deepagents' ProviderProfile(use_responses_api=True).
            # vLLM, LM Studio, Groq, Together, and OpenRouter serve
            # /chat/completions, not OpenAI's /responses.
            extra_kwargs=MappingProxyType({"use_responses_api": False}),
        ),
        "openai": ProviderEntry(
            name="openai",
            lc_prefix="openai",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
        ),
        "anthropic": ProviderEntry(
            name="anthropic",
            lc_prefix="anthropic",
            max_output_kwarg="max_tokens",
            needs_api_key=True,
            supports_timeout=True,
        ),
        "google": ProviderEntry(
            name="google",
            lc_prefix="google_genai",
            max_output_kwarg="max_output_tokens",
            needs_api_key=True,
            supports_timeout=True,
        ),
    }
)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_llm_providers.py -q`
Expected: `29 passed` (four of the test functions are parametrized over the four hosted providers)

- [ ] **Step 6: Lint and format**

Run: `.venv/bin/ruff check src/rudra/llm/ tests/test_llm_providers.py && .venv/bin/ruff format src/rudra/llm/ tests/test_llm_providers.py`
Expected: `All checks passed!` then a reformat summary

- [ ] **Step 7: Commit**

```bash
git add src/rudra/llm/ tests/test_llm_providers.py
git commit -m "feat(llm): add per-provider kwarg policy table

Closes part of C1.4. Kwarg passthrough is unsafe: ChatOllama accepts
timeout= and silently discards it, having no such field and no alias,
while the other three providers accept it through pydantic aliases. Each
provider now declares exactly which kwargs it takes, and the tests assert
absence as loudly as presence.

Also fixes A1.38: openai_compatible maps to the openai LangChain prefix
and would otherwise inherit deepagents' use_responses_api=True profile,
injecting OpenAI's /responses endpoint into vLLM, LM Studio, Groq,
Together, and OpenRouter servers that serve only /chat/completions."
```

---

## Task 2: Role-based configuration

Replaces `OllamaConfig` with per-role `ModelConfig`, adds the deprecation shim, fixes `A1.34`, and lands the config half of `A5.2`.

**Files:**
- Modify: `src/rudra/config.py` (whole-file rewrite of the model section; `AgentConfig` and `get_checkpoint_path` unchanged)
- Test: `tests/test_config.py` (extend; the existing A1.15/A5.1 tests must keep passing with `OLLAMA_*` now flowing through the shim)

**Interfaces:**
- Consumes: nothing from Task 1 (deliberately — `providers.py` duck-types these attributes)
- Produces:
  - `@dataclass(frozen=True) class ModelConfig` with fields `provider: str`, `model: str`, `base_url: str | None`, `api_key_env: str | None`, `temperature: float | None`, `context_tokens: int | None`, `max_output_tokens: int | None`, `timeout: int | None`
  - `Config.models: dict[str, ModelConfig]` — keys `default`, `planner`, `coder`
  - `Config.model_for(role: str) -> ModelConfig` — unknown role returns the `default` entry
  - `Config.load(project_root: Path | None = None) -> Config`
  - `get_config(project_root: Path | None = None) -> Config`
  - `reset_config() -> None` — also clears the deprecation-warning dedupe set

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`. Also change the module docstring's first line to mention A1.34, A5.2, and C1.3, and extend `OLLAMA_ENV_VARS` handling by adding a second tuple:

```python
RUDRA_ENV_VARS = tuple(
    f"RUDRA_{prefix}{suffix}"
    for prefix in ("", "PLANNER_", "CODER_")
    for suffix in (
        "PROVIDER",
        "MODEL",
        "BASE_URL",
        "API_KEY_ENV",
        "TEMPERATURE",
        "CONTEXT_TOKENS",
        "MAX_OUTPUT_TOKENS",
        "TIMEOUT",
    )
)
```

Then widen the existing `clean_config_state` fixture to cover both tuples by replacing its two references to `OLLAMA_ENV_VARS` with `(*OLLAMA_ENV_VARS, *RUDRA_ENV_VARS)`. Leave the rest of that fixture, including its docstring and the manual `os.environ` restore, exactly as it is — its reasoning about `load_dotenv` writing straight to `os.environ` is still correct.

Add these tests:

```python
def test_defaults_are_ollama_at_32b(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A1.34: the coded default was qwen3:14b, below the D6 32B floor, while
    .env.example:9 said qwen3:32b. A user with no .env silently ran a 14B
    model against a codebase whose 14B workarounds were deleted in Step 2.

    chdir to an empty directory first: run from the repo root, Config.load
    reads the developer's own gitignored .env and this stops testing the
    coded defaults at all. CI has no .env and would pass either way, which
    is exactly the kind of only-fails-on-someone-else's-machine gap A5.1
    was about.
    """
    monkeypatch.chdir(tmp_path)
    reset_config()

    settings = get_config().model_for("default")

    assert settings.provider == "ollama"
    assert settings.model == "qwen3:32b"
    assert settings.base_url == "http://localhost:11434"


def test_role_specific_keys_beat_the_bare_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_MODEL", "shared-model")
    monkeypatch.setenv("RUDRA_PLANNER_MODEL", "planner-model")
    reset_config()

    assert get_config().model_for("planner").model == "planner-model"
    assert get_config().model_for("coder").model == "shared-model"
    assert get_config().model_for("default").model == "shared-model"


def test_unknown_roles_fall_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 9 calls build_model("reviewer") before any reviewer config exists.
    Falling back beats raising, and beats shipping dead config now."""
    monkeypatch.setenv("RUDRA_MODEL", "shared-model")
    reset_config()

    assert get_config().model_for("reviewer").model == "shared-model"
    assert get_config().model_for("summarizer").model == "shared-model"


def test_every_setting_is_role_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUDRA_CODER_PROVIDER", "anthropic")
    monkeypatch.setenv("RUDRA_CODER_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("RUDRA_CODER_BASE_URL", "https://example.test")
    monkeypatch.setenv("RUDRA_CODER_API_KEY_ENV", "MY_KEY_VAR")
    monkeypatch.setenv("RUDRA_CODER_TEMPERATURE", "0.7")
    monkeypatch.setenv("RUDRA_CODER_CONTEXT_TOKENS", "200000")
    monkeypatch.setenv("RUDRA_CODER_MAX_OUTPUT_TOKENS", "8192")
    monkeypatch.setenv("RUDRA_CODER_TIMEOUT", "120")
    reset_config()

    coder = get_config().model_for("coder")

    assert coder.provider == "anthropic"
    assert coder.model == "claude-sonnet-4-5"
    assert coder.base_url == "https://example.test"
    assert coder.api_key_env == "MY_KEY_VAR"
    assert coder.temperature == 0.7
    assert coder.context_tokens == 200000
    assert coder.max_output_tokens == 8192
    assert coder.timeout == 120


def test_legacy_ollama_vars_still_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """C1.3: one release of backwards compatibility."""
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://legacy:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-model")
    monkeypatch.setenv("OLLAMA_MODEL_PLANNER", "legacy-planner")
    monkeypatch.setenv("OLLAMA_MODEL_CODER", "legacy-coder")
    monkeypatch.setenv("OLLAMA_TEMPERATURE", "0.9")
    monkeypatch.setenv("OLLAMA_TIMEOUT", "42")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "2048")
    reset_config()

    cfg = get_config()

    assert cfg.model_for("default").base_url == "http://legacy:11434"
    assert cfg.model_for("default").model == "legacy-model"
    assert cfg.model_for("planner").model == "legacy-planner"
    assert cfg.model_for("coder").model == "legacy-coder"
    assert cfg.model_for("default").temperature == 0.9
    assert cfg.model_for("default").timeout == 42
    assert cfg.model_for("default").max_output_tokens == 2048


def test_legacy_vars_warn_once_each(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """chdir first, or the repo's own gitignored .env re-populates the other
    six OLLAMA_* variables during Config.load and each one warns too, making
    the count assertion below fail on a developer's machine and pass in CI."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-model")
    reset_config()

    with pytest.warns(DeprecationWarning) as record:
        get_config().model_for("planner")
        get_config().model_for("coder")

    messages = [str(w.message) for w in record]
    assert len(messages) == 1, f"expected one warning per variable, got {messages}"
    assert "OLLAMA_MODEL" in messages[0]
    assert "RUDRA_MODEL" in messages[0]


def test_rudra_vars_beat_legacy_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "legacy-model")
    monkeypatch.setenv("RUDRA_MODEL", "new-model")
    reset_config()

    assert get_config().model_for("default").model == "new-model"


def test_dotenv_follows_the_project_root_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A5.2: `rudra -d /path/to/proj` run from elsewhere must read the
    project's .env, not the invocation directory's. A5.1 fixed the upward
    walk but keyed the lookup to Path.cwd(), which is the project root only
    when -d is absent."""
    project = tmp_path / "project"
    elsewhere = tmp_path / "elsewhere"
    project.mkdir()
    elsewhere.mkdir()
    (project / ".env").write_text("RUDRA_MODEL=from-project\n", encoding="utf-8")
    (elsewhere / ".env").write_text("RUDRA_MODEL=from-cwd\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    reset_config()
    assert get_config(project).model_for("default").model == "from-project"


def test_dotenv_still_defaults_to_the_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-argument path must keep working for tests and subcommands."""
    (tmp_path / ".env").write_text("RUDRA_MODEL=from-cwd\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    reset_config()
    assert get_config().model_for("default").model == "from-cwd"


def test_ollama_config_is_gone() -> None:
    """C1.3: the class is deleted, not deprecated in place."""
    assert not hasattr(rudra.config, "OllamaConfig")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: the new tests fail with `AttributeError: 'Config' object has no attribute 'model_for'`; the pre-existing tests still pass.

- [ ] **Step 3: Rewrite the model section of `src/rudra/config.py`**

Replace everything from the `OllamaConfig` class through `reset_config`, keeping `AgentConfig` and `get_checkpoint_path` as they are:

```python
"""Configuration settings for Rudra."""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_DEFAULT_PROVIDER = "ollama"
# A1.34: was qwen3:14b, below the D6 32B floor that .env.example:9 already used.
_DEFAULT_MODEL = "qwen3:32b"
_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_TEMPERATURE = 0.3
_DEFAULT_MAX_OUTPUT_TOKENS = 131072
_DEFAULT_TIMEOUT = 300

_ROLES = ("default", "planner", "coder")

_DEPRECATED_VARS = {
    "OLLAMA_BASE_URL": "RUDRA_BASE_URL",
    "OLLAMA_MODEL": "RUDRA_MODEL",
    "OLLAMA_MODEL_PLANNER": "RUDRA_PLANNER_MODEL",
    "OLLAMA_MODEL_CODER": "RUDRA_CODER_MODEL",
    "OLLAMA_TEMPERATURE": "RUDRA_TEMPERATURE",
    "OLLAMA_TIMEOUT": "RUDRA_TIMEOUT",
    "OLLAMA_NUM_PREDICT": "RUDRA_MAX_OUTPUT_TOKENS",
}

_warned_vars: set[str] = set()
"""Deprecation warnings are deduped explicitly rather than relying on the
warnings module's default per-location filter, so `warn once per variable`
is a property the tests can assert deterministically."""


@dataclass(frozen=True)
class ModelConfig:
    """Everything needed to construct one role's chat model.

    `api_key_env` names an environment variable. The key value itself is
    never stored here, never logged, and never written to a config file
    (C1.5).
    """

    provider: str
    model: str
    base_url: str | None
    api_key_env: str | None
    temperature: float | None
    context_tokens: int | None
    max_output_tokens: int | None
    timeout: int | None


def _legacy(name: str) -> str | None:
    """Read a deprecated OLLAMA_* variable, warning once per variable."""
    value = os.getenv(name)
    if value is None:
        return None
    if name not in _warned_vars:
        _warned_vars.add(name)
        warnings.warn(
            f"{name} is deprecated; use {_DEPRECATED_VARS[name]} instead. "
            f"OLLAMA_* variables will be removed in the next release.",
            DeprecationWarning,
            stacklevel=4,
        )
    return value


def _env(role: str, suffix: str) -> str | None:
    """Role-specific key first, then the bare key."""
    if role != "default":
        scoped = os.getenv(f"RUDRA_{role.upper()}_{suffix}")
        if scoped is not None:
            return scoped
    return os.getenv(f"RUDRA_{suffix}")


def _legacy_model(role: str) -> str | None:
    if role == "planner":
        return _legacy("OLLAMA_MODEL_PLANNER") or _legacy("OLLAMA_MODEL")
    if role == "coder":
        return _legacy("OLLAMA_MODEL_CODER") or _legacy("OLLAMA_MODEL")
    return _legacy("OLLAMA_MODEL")


def _optional_int(raw: str | None, fallback: int | None) -> int | None:
    return int(raw) if raw is not None else fallback


def _optional_float(raw: str | None, fallback: float | None) -> float | None:
    return float(raw) if raw is not None else fallback


def _resolve_model_config(role: str) -> ModelConfig:
    return ModelConfig(
        provider=_env(role, "PROVIDER") or _DEFAULT_PROVIDER,
        model=_env(role, "MODEL") or _legacy_model(role) or _DEFAULT_MODEL,
        base_url=_env(role, "BASE_URL") or _legacy("OLLAMA_BASE_URL") or _DEFAULT_BASE_URL,
        api_key_env=_env(role, "API_KEY_ENV"),
        temperature=_optional_float(
            _env(role, "TEMPERATURE") or _legacy("OLLAMA_TEMPERATURE"), _DEFAULT_TEMPERATURE
        ),
        context_tokens=_optional_int(_env(role, "CONTEXT_TOKENS"), None),
        max_output_tokens=_optional_int(
            _env(role, "MAX_OUTPUT_TOKENS") or _legacy("OLLAMA_NUM_PREDICT"),
            _DEFAULT_MAX_OUTPUT_TOKENS,
        ),
        timeout=_optional_int(_env(role, "TIMEOUT") or _legacy("OLLAMA_TIMEOUT"), _DEFAULT_TIMEOUT),
    )


def _resolve_all_models() -> dict[str, ModelConfig]:
    """Resolve every role once, at Config construction.

    Resolving here rather than inside model_for preserves the A1.15
    semantics the existing tests assert: a Config freezes the environment it
    was built from, and only reset_config() picks up later changes.
    """
    return {role: _resolve_model_config(role) for role in _ROLES}


@dataclass
class AgentConfig:
    """Agent execution configuration."""

    # Verbose logging — ON by default, set VERBOSE=false in .env to disable
    verbose: bool = field(default_factory=lambda: os.getenv("VERBOSE", "true").lower() != "false")


@dataclass
class Config:
    """Main configuration container."""

    agent: AgentConfig = field(default_factory=AgentConfig)
    models: dict[str, ModelConfig] = field(default_factory=_resolve_all_models)

    # Paths
    checkpoint_dir: str = ".rudra"

    def model_for(self, role: str) -> ModelConfig:
        """Settings for a role, falling back to `default` for unknown roles.

        Step 9 will call build_model("reviewer") before any reviewer config
        exists. Falling back beats raising, and beats shipping config keys
        for roles that have no consumer yet.
        """
        return self.models.get(role, self.models["default"])

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Config":
        """Load configuration from the environment, reading the project's .env first.

        The .env path is explicit rather than bare `load_dotenv()`, which
        resolves by walking upward from *this module's* directory — so a .env
        anywhere above the installed package leaked into every project the
        user ran Rudra in (TODO.md A5.1).

        `project_root` is passed explicitly because the CLI accepts
        --project-dir, and the cwd is the project root only when that flag is
        absent (TODO.md A5.2). It defaults to the cwd so tests and
        subcommands that have no project path keep working.

        `override=False` is python-dotenv's default and is deliberately kept:
        a real environment variable still beats .env.
        """
        load_dotenv((project_root or Path.cwd()) / ".env")
        return cls()

    def get_checkpoint_path(self, project_dir: Path) -> Path:
        """Get the checkpoint directory path for a project."""
        checkpoint_path = project_dir / self.checkpoint_dir
        checkpoint_path.mkdir(parents=True, exist_ok=True)
        return checkpoint_path


_config: Config | None = None


def get_config(project_root: Path | None = None) -> Config:
    """Return the process-wide Config, loading it on first use.

    Replaces the module-level `config = Config.load()`, which froze the entire
    environment at first import of this module. See TODO.md A1.15.

    `project_root` is honored only on the call that actually constructs the
    Config. Every later call returns the cache — which is exactly why
    cli.py must resolve the project path before its first get_config() call
    (TODO.md A5.2).
    """
    global _config
    if _config is None:
        _config = Config.load(project_root)
    return _config


def reset_config() -> None:
    """Drop the cached Config so the next get_config() re-reads the environment.

    Test-support only. Also clears the deprecation-warning dedupe set, so a
    test asserting "warns once" is not silenced by an earlier test.
    """
    global _config
    _config = None
    _warned_vars.clear()
```

- [ ] **Step 4: Run the config tests**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: all pass, including the three pre-existing A5.1 tests, which now read `OLLAMA_NUM_PREDICT` through the shim.

Note: `test_dotenv_is_looked_up_at_the_cwd_only` asserts `recorded == [tmp_path / ".env"]`. That still holds — `get_config()` with no argument passes `None`, and `load` falls back to `Path.cwd()`.

- [ ] **Step 5: Run the whole suite to catch the call sites that now break**

Run: `.venv/bin/pytest -q`
Expected: FAIL. `tests/test_agent_wiring.py` and anything importing the agents break with `AttributeError: 'Config' object has no attribute 'ollama'`. That is Task 4's job — do not fix it here.

Record the exact failing test names; Task 4's Step 1 needs them.

- [ ] **Step 6: Lint and format**

Run: `.venv/bin/ruff check src/rudra/config.py tests/test_config.py && .venv/bin/ruff format src/rudra/config.py tests/test_config.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/rudra/config.py tests/test_config.py
git commit -m "feat(config): role-based ModelConfig replaces OllamaConfig

Closes C1.3 and the config half of A5.2. Fixes A1.34.

Roles are default, planner, and coder; an unknown role falls back to
default, so Step 9's build_model(\"reviewer\") works the day it lands
without shipping config keys that have no consumer today.

Config.load now takes the project root explicitly. A5.1 fixed the upward
walk but keyed the lookup to Path.cwd(), which is the project root only
when --project-dir is absent. The CLI half of that fix is a separate
commit because get_config() caches, so cli.py must also reorder.

The seven OLLAMA_* variables keep working for one release behind a
DeprecationWarning, deduped explicitly per variable so the once-only
property is deterministically testable.

A1.34: the coded default was qwen3:14b while .env.example:9 said
qwen3:32b, so a user with no .env silently ran a model below the D6 floor.

Agent call sites are knowingly broken by this commit and repaired in the
next one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: The model factory

**Files:**
- Create: `src/rudra/llm/factory.py`
- Modify: `src/rudra/llm/__init__.py`
- Modify: `pyproject.toml` — add `langchain-openai>=1.4.1`
- Test: `tests/test_llm_factory.py`

**Interfaces:**
- Consumes: `PROVIDERS`, `ProviderEntry.build_kwargs` (Task 1); `Config`, `ModelConfig`, `get_config` (Task 2); `require_deepagents_attr` from `rudra.compat.version_guard` (exists, `tests/test_version_guard.py:49` shows the signature `(module, attr, ledger_id)`)
- Produces: `build_model(role: str, cfg: Config | None = None) -> BaseChatModel`, re-exported from `rudra.llm`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, in `[project] dependencies`, after the `langchain-google-genai` line:

```toml
    # Every OpenAI-compatible endpoint — vLLM, OpenRouter, LM Studio, Groq,
    # Together — goes through ChatOpenAI with a base_url. See TODO.md C1.2.
    "langchain-openai>=1.4.1",
```

Run: `uv sync`
Then: `.venv/bin/python -c "import langchain_openai; print(langchain_openai.__version__)"`
Expected: `1.4.2` or later

- [ ] **Step 2: Write the failing test**

Create `tests/test_llm_factory.py`:

```python
"""build_model — the single place Rudra constructs a chat model.

No test here makes a network call. Constructing a LangChain chat model does
no I/O; only .invoke()/.stream() do. Live coverage is in test_llm_live.py
behind the `live` marker.
"""

from __future__ import annotations

import pytest

from rudra.config import Config, ModelConfig
from rudra.llm import build_model
from rudra.llm.errors import (
    MissingApiKeyError,
    ModelCapabilityError,
    UnknownProviderError,
)


def make_config(**overrides) -> Config:
    """A Config with one explicit role, bypassing environment resolution."""
    base = {
        "provider": "ollama",
        "model": "qwen3:32b",
        "base_url": "http://localhost:11434",
        "api_key_env": None,
        "temperature": 0.3,
        "context_tokens": None,
        "max_output_tokens": 4096,
        "timeout": 300,
    }
    base.update(overrides)
    settings = ModelConfig(**base)
    return Config(models={"default": settings, "planner": settings, "coder": settings})


def test_ollama_builds_a_chat_ollama() -> None:
    model = build_model("planner", make_config())

    assert type(model).__name__ == "ChatOllama"
    assert model.model == "qwen3:32b"
    assert model.num_predict == 4096


def test_a_colon_in_the_model_name_is_not_a_provider_separator() -> None:
    """`ollama:qwen3:32b` must split on the FIRST colon. Ollama tags contain
    colons, so getting this wrong turns every tagged model into a bad spec."""
    model = build_model("planner", make_config(model="qwen3:32b"))

    assert model.model == "qwen3:32b"


def test_openai_compatible_builds_a_chat_openai_with_the_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "coder",
        make_config(
            provider="openai_compatible",
            model="nvidia/nemotron-3-ultra-550b-a55b:free",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert type(model).__name__ == "ChatOpenAI"
    assert model.model_name == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert model.openai_api_base == "https://openrouter.ai/api/v1"


def test_openai_compatible_never_enables_the_responses_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A1.38. The colon-free model id matters: with a colon, deepagents'
    provider-profile lookup rejects the spec and the OpenAI profile never
    applies, so the bug hides. A colon-free id is where it bites."""
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "coder",
        make_config(
            provider="openai_compatible",
            model="qwen3-coder-30b",
            base_url="http://localhost:8000/v1",
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert model.use_responses_api is False


def test_context_tokens_land_in_the_model_profile() -> None:
    """C1.4a / A1.17. compute_summarization_defaults only switches from the
    fixed 170k trigger to ("fraction", 0.85) when profile["max_input_tokens"]
    is an int, so without this every local model never compacts."""
    model = build_model("planner", make_config(context_tokens=32768))

    assert model.profile["max_input_tokens"] == 32768


def test_context_tokens_also_size_the_ollama_server_window() -> None:
    """num_ctx is a separate concern from the profile: it is what the Ollama
    server allocates, defaulting to 4096 and truncating silently above it."""
    model = build_model("planner", make_config(context_tokens=32768))

    assert model.num_ctx == 32768


def test_no_context_tokens_leaves_the_profile_alone() -> None:
    model = build_model("planner", make_config(context_tokens=None))

    assert model.profile is None


def test_the_profile_merge_preserves_provider_capability_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blind assignment would delete tool_calling, which the C1.7 check reads."""
    monkeypatch.setenv("TEST_KEY_VAR", "sk-test-not-a-real-key")
    model = build_model(
        "planner",
        make_config(
            provider="anthropic",
            model="claude-sonnet-4-5-20250929",
            base_url=None,
            api_key_env="TEST_KEY_VAR",
            context_tokens=200000,
        ),
    )

    assert model.profile["max_input_tokens"] == 200000
    assert model.profile["tool_calling"] is True


def test_unknown_provider_raises_before_construction() -> None:
    with pytest.raises(UnknownProviderError) as excinfo:
        build_model("planner", make_config(provider="gpt4all"))

    assert "gpt4all" in str(excinfo.value)
    assert "openai_compatible" in str(excinfo.value)


def test_missing_key_variable_raises_and_never_prints_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1.5."""
    monkeypatch.delenv("ABSENT_KEY_VAR", raising=False)

    with pytest.raises(MissingApiKeyError) as excinfo:
        build_model(
            "coder",
            make_config(provider="anthropic", model="claude-sonnet-4-5", api_key_env="ABSENT_KEY_VAR"),
        )

    assert "ABSENT_KEY_VAR" in str(excinfo.value)


def test_a_provider_needing_a_key_with_none_configured_raises() -> None:
    with pytest.raises(MissingApiKeyError):
        build_model(
            "coder",
            make_config(provider="anthropic", model="claude-sonnet-4-5", api_key_env=None),
        )


def test_a_resolved_key_never_appears_in_the_model_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1.5, as a property rather than a promise.

    Asserting only that error messages are clean would be trivially true —
    they are raised before the key is ever read. The real exposure is a
    successfully built model getting logged, repr'd into a traceback, or
    printed by a debugger. LangChain wraps keys in SecretStr for exactly
    this reason; the test pins that behavior rather than trusting it.
    """
    secret = "sk-do-not-leak-this-value"
    monkeypatch.setenv("TEST_KEY_VAR", secret)

    model = build_model(
        "coder",
        make_config(
            provider="openai_compatible",
            model="qwen3-coder-30b",
            base_url="http://localhost:8000/v1",
            api_key_env="TEST_KEY_VAR",
        ),
    )

    assert secret not in repr(model)
    assert secret not in str(model)


def test_a_model_reporting_no_tool_calling_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """C1.7. Patched at the profile level because no shipped model reports
    tool_calling=False, and the branch must still be covered."""
    import rudra.llm.factory as factory

    real_init = factory.init_chat_model

    def fake_init(spec, **kwargs):
        model = real_init(spec, **kwargs)
        return model.model_copy(update={"profile": {"tool_calling": False}})

    monkeypatch.setattr(factory, "init_chat_model", fake_init)

    with pytest.raises(ModelCapabilityError) as excinfo:
        build_model("planner", make_config())

    assert "planner" in str(excinfo.value)


def test_an_absent_profile_is_not_treated_as_no_tool_calling() -> None:
    """Every local model reports profile=None. Refusing there would ban
    Ollama outright, so absent must mean "unknown", never "unsupported"."""
    model = build_model("planner", make_config())

    assert model.profile is None  # precondition: this is the absent case


def test_unknown_roles_resolve_through_default() -> None:
    """Step 9 calls build_model("reviewer") before reviewer config exists."""
    model = build_model("reviewer", make_config(model="qwen3:32b"))

    assert model.model == "qwen3:32b"


def test_apply_provider_profile_is_still_available() -> None:
    """A1.37: beta deepagents API, same hazard class as the U.4 monkeypatch."""
    from rudra.llm.factory import _apply_provider_profile

    assert callable(_apply_provider_profile())
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_llm_factory.py -q`
Expected: collection error — `ImportError: cannot import name 'build_model' from 'rudra.llm'`

- [ ] **Step 4: Write the factory**

Create `src/rudra/llm/factory.py`:

```python
"""Build a chat model for a role, for any provider.

This is the only module in Rudra that calls init_chat_model or imports a
provider package's concept of a model. Everything else asks for a role.

Why not deepagents' resolve_model: it accepts only the spec
(deepagents/_models.py:35), so per-role base_url, temperature, and profile
cannot ride it. Why not register_provider_profile: its registry is keyed
globally by provider or provider:model, so two roles pointing at the same
model would overwrite each other's kwargs. Composing at the call site gets
the built-in profiles with no global writes and no role collisions.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from langchain.chat_models import init_chat_model

from rudra.compat.version_guard import require_deepagents_attr
from rudra.config import get_config
from rudra.llm.errors import MissingApiKeyError, ModelCapabilityError, UnknownProviderError
from rudra.llm.providers import PROVIDERS

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from rudra.config import Config, ModelConfig

logger = logging.getLogger(__name__)


def _apply_provider_profile():
    """Resolve deepagents' beta provider-profile helper.

    A1.37: `deepagents.profiles` is beta-flagged, so this joins the U.4
    monkeypatch and the U.13 private import behind the version guard. A
    deepagents bump that moves or renames it fails here, legibly, instead of
    silently dropping the provider kwargs it injects.
    """
    return require_deepagents_attr(
        "deepagents.profiles.provider.provider_profiles",
        "apply_provider_profile",
        "A1.37",
    )


def _resolve_api_key(role: str, settings: ModelConfig) -> str:
    """Read the key from the environment variable the config names.

    C1.5: config carries the variable name; the value lives only in the
    environment and never enters a message, a log line, or a repr.
    """
    if not settings.api_key_env:
        raise MissingApiKeyError(
            role, f"RUDRA_{role.upper()}_API_KEY_ENV (or RUDRA_API_KEY_ENV)"
        )
    value = os.getenv(settings.api_key_env)
    if not value:
        raise MissingApiKeyError(role, settings.api_key_env)
    return value


def build_model(role: str, cfg: Config | None = None) -> BaseChatModel:
    """Construct the chat model for `role`.

    Makes no network call — LangChain chat model constructors do no I/O.
    Every failure mode raises before inference could start (C1.7).

    Args:
        role: `planner`, `coder`, or any name; unknown roles use `default`.
        cfg: Config to read from. Defaults to the process-wide one; tests
            pass an explicit Config instead of monkeypatching os.environ.

    Returns:
        A resolved BaseChatModel.

    Raises:
        UnknownProviderError: configuration named an unimplemented provider.
        MissingApiKeyError: the provider needs a key and the named variable
            is unset.
        ModelCapabilityError: the model's profile reports tool_calling=False.
    """
    settings = (cfg or get_config()).model_for(role)

    try:
        entry = PROVIDERS[settings.provider]
    except KeyError:
        raise UnknownProviderError(settings.provider, tuple(PROVIDERS)) from None

    spec = f"{entry.lc_prefix}:{settings.model}"

    role_kwargs: dict[str, Any] = entry.build_kwargs(settings)
    if entry.needs_api_key:
        role_kwargs["api_key"] = _resolve_api_key(role, settings)

    # U.9: deepagents' built-in provider profiles supply their kwargs first
    # and our role kwargs win on collision — which is how openai_compatible
    # suppresses use_responses_api (A1.38).
    kwargs = _apply_provider_profile()(spec, role_kwargs)

    model = init_chat_model(spec, **kwargs)

    # C1.4a: deepagents' compute_summarization_defaults switches from a fixed
    # 170k trigger to ("fraction", 0.85) only when this key is an int. Merge
    # rather than replace — hosted providers ship a populated profile whose
    # tool_calling key the check below reads.
    if settings.context_tokens is not None:
        model = model.model_copy(
            update={
                "profile": {
                    **(model.profile or {}),
                    "max_input_tokens": settings.context_tokens,
                }
            }
        )

    # C1.7. Three-state: False refuses, True proceeds, absent proceeds.
    # Every local model reports profile=None, so absent must not mean "no".
    if (model.profile or {}).get("tool_calling") is False:
        raise ModelCapabilityError(role, settings.model)

    # A1.35 data: harness profiles cannot match an instance whose identifier
    # carries a colon. Recording what we actually resolved gives U.10 real
    # data instead of a re-derivation.
    logger.debug(
        "built model role=%s spec=%s provider=%s identifier=%s",
        role,
        spec,
        settings.provider,
        getattr(model, "model_name", None) or getattr(model, "model", None),
    )
    return model
```

Replace `src/rudra/llm/__init__.py` with:

```python
"""Provider-agnostic model construction. See TODO.md C1.1-C1.8."""

from rudra.llm.errors import (
    MissingApiKeyError,
    ModelCapabilityError,
    ModelConfigError,
    UnknownProviderError,
)
from rudra.llm.factory import build_model
from rudra.llm.providers import PROVIDERS, ProviderEntry

__all__ = [
    "PROVIDERS",
    "MissingApiKeyError",
    "ModelCapabilityError",
    "ModelConfigError",
    "ProviderEntry",
    "UnknownProviderError",
    "build_model",
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_llm_factory.py -q`
Expected: `16 passed`

If `test_the_profile_merge_preserves_provider_capability_data` fails because the Anthropic model id has changed upstream, check the current id with:
`.venv/bin/python -c "from langchain_anthropic import ChatAnthropic; print(ChatAnthropic(model='claude-sonnet-4-5-20250929', api_key='x').profile is not None)"`
and substitute a model id whose profile is populated. The assertion is about merge behavior, not about that specific model.

- [ ] **Step 6: Lint and format**

Run: `.venv/bin/ruff check src/rudra/llm/ tests/test_llm_factory.py && .venv/bin/ruff format src/rudra/llm/ tests/test_llm_factory.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/rudra/llm/ tests/test_llm_factory.py pyproject.toml
git commit -m "feat(llm): add build_model, the provider-agnostic factory

Closes C1.1, C1.2, C1.4a, C1.5, C1.7, U.9, and C7.6. Guards A1.37.

Construction is init_chat_model composed with deepagents'
apply_provider_profile, caller kwargs winning. resolve_model takes no
kwargs, and register_provider_profile writes a process-global registry
keyed by provider, so two roles sharing a model would overwrite each
other.

C1.4a resolves to a merged model profile carrying max_input_tokens.
deepagents' compute_summarization_defaults already switches to a 0.85
fraction trigger the moment that key is an int, so building
SummarizationMiddleware by hand would reimplement an existing branch.
That also decides C7.6, whose row deferred the choice to C1.1. The merge
preserves the provider's own profile, since blind assignment would delete
the tool_calling data the C1.7 check reads.

C1.7 is three-state: tool_calling=False refuses, True proceeds, and
absent proceeds — every local model reports profile=None, so treating
absent as unsupported would ban Ollama outright.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Migrate the agent call sites

Repairs what Task 2 knowingly broke and delivers the `C1.1` acceptance criterion.

**Files:**
- Modify: `src/rudra/agent/planner_agent.py:8` (import), `:77-84` (construction)
- Modify: `src/rudra/agent/coder_agent.py:6` (import), `:57-64` (construction)
- Modify: `src/rudra/agent/main_agent.py:543-544`
- Create: `tests/test_no_direct_provider_imports.py`

**Interfaces:**
- Consumes: `build_model` from `rudra.llm` (Task 3); `Config.model_for` (Task 2)
- Produces: no new public API. After this task, `rudra.agent.*` contains no provider-package import.

- [ ] **Step 1: Confirm the breakage Task 2 left**

Run: `.venv/bin/pytest -q 2>&1 | tail -20`
Expected: failures citing `'Config' object has no attribute 'ollama'`, in the tests recorded in Task 2 Step 5.

- [ ] **Step 2: Write the failing acceptance test**

Create `tests/test_no_direct_provider_imports.py`:

```python
"""C1.1's acceptance criterion, as a test rather than a claim.

Rudra is provider-agnostic exactly when no module outside rudra.llm knows
which provider package exists. A stray `from langchain_ollama import
ChatOllama` re-introduces the lock-in this step removes, and would
otherwise be caught only by someone reading the diff.

rudra/llm/providers.py is exempt: it names providers in strings and
comments, which is its job. It still must not import them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rudra

PROVIDER_PACKAGES = (
    "langchain_ollama",
    "langchain_openai",
    "langchain_anthropic",
    "langchain_google_genai",
)

SRC = Path(rudra.__file__).parent


def python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_there_are_python_files_to_check() -> None:
    """Guards against the glob silently matching nothing."""
    assert len(python_files()) > 10


@pytest.mark.parametrize("package", PROVIDER_PACKAGES)
def test_no_module_imports_a_provider_package_directly(package: str) -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in python_files()
        if f"import {package}" in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], (
        f"{package} is imported outside the model factory by: {offenders}. "
        f"Ask rudra.llm.build_model(role) for a model instead."
    )


def test_the_factory_is_the_only_caller_of_init_chat_model() -> None:
    offenders = [
        str(path.relative_to(SRC))
        for path in python_files()
        if "init_chat_model" in path.read_text(encoding="utf-8")
    ]

    assert offenders == ["llm/factory.py"]
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_no_direct_provider_imports.py -q`
Expected: FAIL — `langchain_ollama` is imported by `agent/planner_agent.py` and `agent/coder_agent.py`

- [ ] **Step 4: Migrate the planner**

In `src/rudra/agent/planner_agent.py`, delete line 8 (`from langchain_ollama import ChatOllama`) and add `from rudra.llm import build_model` to the `rudra` import block. Then replace the body of `create_planner_agent` from `cfg = get_config()` through the closing paren of `ChatOllama(...)` with:

```python
    model = build_model("planner")
```

Delete the now-unused `from rudra.config import get_config` import if nothing else in the file uses it. Check with:
`grep -n "get_config" src/rudra/agent/planner_agent.py`

- [ ] **Step 5: Migrate the coder**

In `src/rudra/agent/coder_agent.py`, delete line 6 (`from langchain_ollama import ChatOllama`) and add `from rudra.llm import build_model`. Replace `cfg = get_config()` through the `ChatOllama(...)` closing paren with:

```python
    model = build_model("coder")
```

Then check for a now-unused import:
`grep -n "get_config" src/rudra/agent/coder_agent.py`

- [ ] **Step 6: Migrate main_agent**

In `src/rudra/agent/main_agent.py`, replace lines 543-544:

```python
        planner_model=get_config().ollama.model_planner,
        coder_model=get_config().ollama.model_coder,
```

with:

```python
        planner_model=get_config().model_for("planner").model,
        coder_model=get_config().model_for("coder").model,
```

- [ ] **Step 7: Run the acceptance test and the full suite**

Run: `.venv/bin/pytest tests/test_no_direct_provider_imports.py -q`
Expected: `6 passed`

Run: `.venv/bin/pytest -q`
Expected: all pass. `cli.py` still reads `.ollama` at `:224-225` and `:308-309`, but those lines execute only inside a real run, so the suite is green while Task 5 remains outstanding.

- [ ] **Step 8: Verify by hand, as CLAUDE.md rule 4 requires**

Run:
```bash
grep -rn "langchain_ollama\|langchain_openai\|langchain_anthropic\|langchain_google_genai" src/
```
Expected: no matches at all — `providers.py` names providers only in strings and comments, never in an import.

- [ ] **Step 9: Lint and format**

Run: `.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add src/rudra/agent/ tests/test_no_direct_provider_imports.py
git commit -m "refactor(agent): build models through the factory

Completes C1.1. planner_agent and coder_agent construct their models with
build_model(role) instead of ChatOllama, and main_agent reads role-based
model names for its progress display.

No module outside rudra.llm now imports a provider package. That is the
whole of the provider-agnostic claim, so it is a test rather than a note
in the commit message — a stray import would otherwise be caught only by
whoever reads the diff.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: CLI model display and the A5.2 reorder

**Files:**
- Modify: `src/rudra/cli.py:211-215` (reorder), `:224-225`, `:308-309`
- Test: `tests/test_cli_smoke.py` (extend)

**Interfaces:**
- Consumes: `Config.model_for` (Task 2), `get_config(project_root)` (Task 2)
- Produces: no new API

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_smoke.py`:

```python
def test_project_path_is_resolved_before_config_loads() -> None:
    """A5.2: get_config() caches for the process, so if cli.py calls it
    before computing project_path, no later call can correct the .env
    location. This asserts source order because the ordering IS the fix —
    a behavioral test would need a full CLI run against a real backend."""
    import inspect

    from rudra import cli

    source = inspect.getsource(cli.main)
    get_config_at = source.index("get_config(")
    project_path_at = source.index("project_path = get_project_path(")

    assert project_path_at < get_config_at, (
        "cli.main() must resolve project_path before its first get_config() "
        "call, or --project-dir cannot affect which .env is read (A5.2)."
    )


def test_the_first_get_config_call_passes_the_project_path() -> None:
    """Ordering alone is not enough — the path has to be handed over."""
    import inspect

    from rudra import cli

    source = inspect.getsource(cli.main)

    assert "get_config(project_path)" in source
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_cli_smoke.py -q -k "project_path or get_config"`
Expected: FAIL — `project_path` is assigned at `cli.py:214`, after the `get_config()` at `:212`

- [ ] **Step 3: Reorder and rewire**

In `src/rudra/cli.py`, replace lines 208-215:

```python
    # A default of `config.agent.verbose` here would be evaluated when this
    # module is imported, which is the A1.15 defect. Three-state instead:
    # absent -> consult config, --verbose -> True, --no-verbose -> False.
    if verbose is None:
        verbose = get_config().agent.verbose

    project_path = get_project_path(project_dir)
    project_context = load_project_context(project_path)
```

with:

```python
    # project_path is resolved FIRST, and the Config is seeded with it
    # unconditionally, because get_config() caches for the rest of the
    # process — no later call can correct which .env was read. Seeding
    # inside the `if verbose is None` block below would skip it whenever
    # --verbose or --no-verbose is passed, leaving the first downstream
    # get_config() to fall back to the cwd. See TODO.md A5.2.
    project_path = get_project_path(project_dir)
    cfg = get_config(project_path)

    # A default of `config.agent.verbose` here would be evaluated when this
    # module is imported, which is the A1.15 defect. Three-state instead:
    # absent -> consult config, --verbose -> True, --no-verbose -> False.
    if verbose is None:
        verbose = cfg.agent.verbose

    project_context = load_project_context(project_path)
```

- [ ] **Step 4: Migrate the two display sites**

Replace `cli.py:224-225`:

```python
                f"[dim]Planner:[/dim] {get_config().ollama.model_planner}  "
                f"[dim]│  Coder:[/dim] {get_config().ollama.model_coder}",
```

with:

```python
                f"[dim]Planner:[/dim] {get_config().model_for('planner').model}  "
                f"[dim]│  Coder:[/dim] {get_config().model_for('coder').model}",
```

Apply the identical replacement at `cli.py:308-309` (the REPL panel), preserving its deeper indentation.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_smoke.py -q`
Expected: all pass

Run: `.venv/bin/pytest -q`
Expected: all pass

- [ ] **Step 6: Verify the CLI still starts**

Run: `.venv/bin/rudra --version`
Expected: `Rudra v0.2.0` — a real version, not `0.0.0+unknown`

Run: `.venv/bin/rudra --help`
Expected: help text, no traceback

- [ ] **Step 7: Lint and format**

Run: `.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/rudra/cli.py tests/test_cli_smoke.py
git commit -m "fix(cli): resolve the project path before loading config

Closes A5.2. get_config() caches the Config for the process, so calling it
at cli.py:212 before project_path was computed at :214 meant --project-dir
could never influence which .env was read. Reordering is the fix; the
config half landed with ModelConfig.

The model names in both task panels now come from model_for(role) rather
than the deleted ollama config block.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: `rudra models test` and live coverage

**Files:**
- Create: `src/rudra/llm/probe.py`
- Modify: `src/rudra/cli.py` — add the `models` sub-app
- Modify: `pyproject.toml` — register the `live` pytest marker
- Create: `tests/test_llm_live.py`
- Test: `tests/test_cli_smoke.py` (extend)

**Interfaces:**
- Consumes: `build_model` (Task 3), `Config.model_for` (Task 2)
- Produces:
  - `@dataclass(frozen=True) class ProbeResult` with fields `role: str`, `provider: str`, `model: str`, `construct: str`, `reach: str`, `tools: str`, `context_tokens: int | None`, `ok: bool`
  - `probe_role(role: str) -> ProbeResult`
  - `ROLES_TO_PROBE: tuple[str, ...] = ("planner", "coder")`
  - CLI: `rudra models test [--role ROLE]`

- [ ] **Step 1: Register the marker**

In `pyproject.toml` under `[tool.pytest.ini_options]`, after `python_files`:

```toml
markers = [
    "live: needs a reachable model backend and RUDRA_LIVE_TESTS=1; never runs in CI",
]
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_llm_live.py`:

```python
"""Live model checks. Skipped unless explicitly enabled.

Two conditions, both required: RUDRA_LIVE_TESTS=1 opts in, and the
configured role must name an API key variable that is actually set. CI sets
neither, so CI never reaches a network call and needs no secrets.

Run against OpenRouter with:
    RUDRA_LIVE_TESTS=1 \\
    RUDRA_PROVIDER=openai_compatible \\
    RUDRA_BASE_URL=https://openrouter.ai/api/v1 \\
    RUDRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \\
    RUDRA_API_KEY_ENV=OPENROUTER_API_KEY \\
    OPENROUTER_API_KEY=... \\
    .venv/bin/pytest -m live -q
"""

from __future__ import annotations

import os

import pytest

from rudra.config import get_config, reset_config
from rudra.llm import build_model
from rudra.llm.probe import probe_role

pytestmark = pytest.mark.live


def requires_live_backend() -> None:
    if os.getenv("RUDRA_LIVE_TESTS") != "1":
        pytest.skip("set RUDRA_LIVE_TESTS=1 to run live model checks")
    reset_config()
    settings = get_config().model_for("planner")
    if settings.api_key_env and not os.getenv(settings.api_key_env):
        pytest.skip(f"{settings.api_key_env} is not set")


def test_the_configured_planner_can_call_a_tool() -> None:
    """deepagents hard-requires tool calling. Reachability proves nothing
    about it — a model can answer a prompt and still never emit a tool call."""
    requires_live_backend()

    from langchain_core.tools import tool

    @tool
    def echo(text: str) -> str:
        """Echo text back."""
        return text

    model = build_model("planner")
    response = model.bind_tools([echo]).invoke("Call the echo tool with text hello.")

    assert response.tool_calls, f"model emitted no tool calls: {response.content!r}"
    assert response.tool_calls[0]["name"] == "echo"


def test_probe_role_reports_all_four_stages_ok() -> None:
    requires_live_backend()

    result = probe_role("planner")

    assert result.construct == "ok"
    assert result.reach == "ok"
    assert result.tools == "ok"
    assert result.ok is True
```

Append to `tests/test_cli_smoke.py`:

```python
def test_models_test_is_a_registered_subcommand() -> None:
    """C1.6. Runs the CLI with no backend, so it must not make a network
    call just to render help."""
    from typer.testing import CliRunner

    from rudra.cli import app

    result = CliRunner().invoke(app, ["models", "test", "--help"])

    assert result.exit_code == 0
    assert "--role" in result.output
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/pytest tests/test_llm_live.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'rudra.llm.probe'`

Run: `.venv/bin/pytest tests/test_cli_smoke.py -q -k models`
Expected: FAIL — no `models` subcommand

- [ ] **Step 4: Write the probe**

Create `src/rudra/llm/probe.py`:

```python
"""Live verification of a configured role — C1.6.

Four stages, each reported separately, because they fail for different
reasons and a single pass/fail hides which. In particular, Reach passing
says nothing about Tools: a model can answer a prompt perfectly and never
emit a tool call, and deepagents requires tool calls for every agent.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import tool

from rudra.config import get_config
from rudra.llm.factory import build_model

ROLES_TO_PROBE: tuple[str, ...] = ("planner", "coder")


@dataclass(frozen=True)
class ProbeResult:
    """One row of `rudra models test` output."""

    role: str
    provider: str
    model: str
    construct: str
    reach: str
    tools: str
    context_tokens: int | None
    ok: bool


@tool
def echo(text: str) -> str:
    """Echo text back."""
    return text


def _short(error: Exception) -> str:
    """One-line error text, so a table row stays a row."""
    text = str(error).replace("\n", " ")
    return text if len(text) <= 120 else f"{text[:117]}..."


def probe_role(role: str) -> ProbeResult:
    """Construct, reach, and tool-test one role. Never raises."""
    settings = get_config().model_for(role)
    skipped = "skipped"

    try:
        model = build_model(role)
    except Exception as error:  # noqa: BLE001 — every failure is a reportable row
        return ProbeResult(
            role=role,
            provider=settings.provider,
            model=settings.model,
            construct=_short(error),
            reach=skipped,
            tools=skipped,
            context_tokens=None,
            ok=False,
        )

    context_tokens = (model.profile or {}).get("max_input_tokens")

    try:
        model.invoke("Reply with the single word: ok")
    except Exception as error:  # noqa: BLE001
        return ProbeResult(
            role=role,
            provider=settings.provider,
            model=settings.model,
            construct="ok",
            reach=_short(error),
            tools=skipped,
            context_tokens=context_tokens,
            ok=False,
        )

    try:
        response = model.bind_tools([echo]).invoke("Call the echo tool with text hello.")
        tools = "ok" if response.tool_calls else "no tool_calls emitted"
    except Exception as error:  # noqa: BLE001
        tools = _short(error)

    return ProbeResult(
        role=role,
        provider=settings.provider,
        model=settings.model,
        construct="ok",
        reach="ok",
        tools=tools,
        context_tokens=context_tokens,
        ok=tools == "ok",
    )
```

- [ ] **Step 5: Add the subcommand**

In `src/rudra/cli.py`, immediately after the `app = typer.Typer(...)` block (`:22`), add:

```python
models_app = typer.Typer(help="Inspect and test configured models.")
app.add_typer(models_app, name="models")
```

Then add the command. Place it just before `def main(` (`:182`), so the callback still reads as the primary entry point:

```python
@models_app.command("test")
def models_test(
    role: Optional[str] = typer.Option(
        None, "--role", help="Probe one role only (default: planner and coder)"
    ),
) -> None:
    """Verify every configured model is reachable and can call tools.

    deepagents requires tool calling for every agent, and reachability does
    not imply it — hence the separate Tools stage. See TODO.md C1.6.
    """
    from rich.table import Table

    from rudra.llm.probe import ROLES_TO_PROBE, probe_role

    roles = (role,) if role else ROLES_TO_PROBE

    table = Table(title="Model check", header_style="bold")
    for column in ("Role", "Provider", "Model", "Construct", "Reach", "Tools", "Ctx"):
        table.add_column(column, overflow="fold")

    failed = False
    for name in roles:
        result = probe_role(name)
        failed = failed or not result.ok
        style = "green" if result.ok else "red"
        table.add_row(
            result.role,
            result.provider,
            result.model,
            result.construct,
            result.reach,
            result.tools,
            str(result.context_tokens) if result.context_tokens else "-",
            style=style,
        )

    console.print(table)
    if failed:
        raise typer.Exit(code=1)
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_cli_smoke.py -q`
Expected: all pass

Run: `.venv/bin/pytest -q`
Expected: all pass, with the live tests **skipped** — confirm by checking the summary reads `N passed, 2 skipped` and not `2 failed`.

Run: `.venv/bin/pytest -m live -q`
Expected: `2 skipped` (no `RUDRA_LIVE_TESTS`)

- [ ] **Step 7: Run the live check for real**

```bash
RUDRA_LIVE_TESTS=1 \
RUDRA_PROVIDER=openai_compatible \
RUDRA_BASE_URL=https://openrouter.ai/api/v1 \
RUDRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \
RUDRA_API_KEY_ENV=OPENROUTER_API_KEY \
RUDRA_CONTEXT_TOKENS=131072 \
OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
.venv/bin/pytest -m live -q
```

Expected: `2 passed`

Then the command itself:

```bash
RUDRA_PROVIDER=openai_compatible \
RUDRA_BASE_URL=https://openrouter.ai/api/v1 \
RUDRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \
RUDRA_API_KEY_ENV=OPENROUTER_API_KEY \
RUDRA_CONTEXT_TOKENS=131072 \
.venv/bin/rudra models test
```

Expected: a two-row table, every stage `ok`, `Ctx` = `131072`, exit code 0. Capture this output — Task 7's ledger entry quotes it.

If `OPENROUTER_API_KEY` is not exported in the shell, set it first. Never paste the key into a tracked file or a commit message.

- [ ] **Step 8: Lint and format**

Run: `.venv/bin/ruff check src/ tests/ && .venv/bin/ruff format src/ tests/`
Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/rudra/llm/probe.py src/rudra/cli.py tests/test_llm_live.py tests/test_cli_smoke.py pyproject.toml
git commit -m "feat(cli): add rudra models test

Closes C1.6. Four stages per role, reported separately because they fail
for different reasons: Construct catches configuration errors with no
network, Reach catches a bad base_url or key, Tools catches the case that
actually matters — deepagents requires tool calling and a model can answer
a prompt perfectly while never emitting a tool call — and Ctx reports the
context window that ended up in effect.

Live tests need both RUDRA_LIVE_TESTS=1 and a key variable that is set, so
CI reaches no network call and needs no secrets.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Documentation, ledger, and the acceptance run

**Files:**
- Modify: `.env.example`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: everything above
- Produces: Step 5 marked COMPLETE with verification output

- [ ] **Step 1: Rewrite `.env.example`**

Replace the whole file:

```bash
# Rudra Environment Configuration
# Copy this file to .env and fill in your values. .env is gitignored.

# ── Model configuration ────────────────────────────────────────────────
# Every setting below has a per-role override: prefix it with PLANNER_ or
# CODER_ (e.g. RUDRA_PLANNER_MODEL). An unset role falls back to the bare
# key. Roles Rudra does not know yet — reviewer, summarizer — also fall
# back, so they work the day they land.

# ollama | openai_compatible | openai | anthropic | google
RUDRA_PROVIDER=ollama

# Minimum supported local model size is 32B (TODO.md D6). Smaller models
# were the design center for the qwen3:14b workarounds deleted in Step 2
# and are no longer supported.
RUDRA_MODEL=qwen3:32b
RUDRA_BASE_URL=http://localhost:11434

# Dual-model: different models for planning and coding.
RUDRA_PLANNER_MODEL=qwen3:32b
RUDRA_CODER_MODEL=qwen3-coder:32b

RUDRA_TEMPERATURE=0.3
RUDRA_TIMEOUT=300

# Output-token cap. Maps to num_predict on Ollama, max_tokens elsewhere.
RUDRA_MAX_OUTPUT_TOKENS=131072

# Context window. Sets Ollama's num_ctx (which otherwise defaults to 4096
# and truncates silently) AND the model profile deepagents reads to decide
# when to compact. Leave unset to accept the provider's own default.
# RUDRA_CONTEXT_TOKENS=32768

# ── API keys ───────────────────────────────────────────────────────────
# Name the VARIABLE that holds your key, never the key itself. Rudra reads
# the value from the environment and never stores it in configuration.
# RUDRA_API_KEY_ENV=OPENROUTER_API_KEY
# OPENROUTER_API_KEY=sk-or-v1-...

# ── Example: OpenRouter, vLLM, LM Studio, Groq, Together ───────────────
# Any OpenAI-compatible endpoint uses one provider and a base_url.
# RUDRA_PROVIDER=openai_compatible
# RUDRA_BASE_URL=https://openrouter.ai/api/v1
# RUDRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
# RUDRA_API_KEY_ENV=OPENROUTER_API_KEY

# ── Agent ──────────────────────────────────────────────────────────────
# Set to false to silence detailed agent logs
VERBOSE=true

# ── Deprecated (removed next release) ──────────────────────────────────
# These still work and emit a DeprecationWarning. See TODO.md C1.3.
#   OLLAMA_BASE_URL      -> RUDRA_BASE_URL
#   OLLAMA_MODEL         -> RUDRA_MODEL
#   OLLAMA_MODEL_PLANNER -> RUDRA_PLANNER_MODEL
#   OLLAMA_MODEL_CODER   -> RUDRA_CODER_MODEL
#   OLLAMA_TEMPERATURE   -> RUDRA_TEMPERATURE
#   OLLAMA_TIMEOUT       -> RUDRA_TIMEOUT
#   OLLAMA_NUM_PREDICT   -> RUDRA_MAX_OUTPUT_TOKENS
```

- [ ] **Step 2: Run the full local verification and capture the output**

```bash
.venv/bin/ruff check src/ tests/
.venv/bin/ruff format --check src/ tests/
.venv/bin/pytest -q
.venv/bin/rudra --version
.venv/bin/rudra --help
```

Expected: `All checks passed!` · all files formatted · all tests pass with the 2 live tests skipped · `Rudra v0.2.0` · help text with a `models` subcommand listed.

Record the exact pass count. Step 4 ended at `114 passed`; this step should land well above it.

- [ ] **Step 3: Run the acceptance test against OpenRouter**

From a temporary directory **outside** the repo, so no repo `.env` can leak in:

```bash
cd "$(mktemp -d)"
RUDRA_PROVIDER=openai_compatible \
RUDRA_BASE_URL=https://openrouter.ai/api/v1 \
RUDRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free \
RUDRA_API_KEY_ENV=OPENROUTER_API_KEY \
RUDRA_CONTEXT_TOKENS=131072 \
/full/path/to/repo/.venv/bin/rudra "write a python script that reverses a string, with a CLI"
```

Expected: exit 0, a `.rudra/PLAN.md` with checked-off entries, and generated files that actually run.

Capture: the console summary line, the contents of `.rudra/PLAN.md`, the byte size of each generated file, and the result of executing the generated script.

If OpenRouter rate-limits the free tier, retry once; if it still fails, record that plainly and mark only §9.3 of the spec unmet. The step still lands — the offline suite is the gate.

- [ ] **Step 4: Update the ledger**

In `TODO.md`:

Mark `DONE` with 2026-08-10 and verification output: `C1.1`, `C1.2`, `C1.3`, `C1.4`, `C1.4a`, `C1.5`, `C1.6`, `C1.7`, `C1.8`, `U.9`, `A5.2`, `A1.34`, `A1.37`, `A1.38`.

`C1.8` note: the `[compat]` section for the two surviving middlewares has **no TOML file to live in until Step 6**. Close it as *deferred-to-C2.x with the decision recorded*, or leave it `PENDING` and say so explicitly in the Section E row — do not mark it `DONE` without a config surface. Pick one and state which.

Mark `C7.6` `DONE` as an alias of `C1.4a`, citing spec §5.4.

Leave `PENDING` with a one-line reason: `A1.35` (deferred to U.10), `A1.36` (deferred — behavior change).

Append to `A5.1` and `A2.16` the evidence from Step 3, if the acceptance run happened.

Update the Section E step 5 row: replace `STEP 5 IN PROGRESS` with `STEP 5 COMPLETE 2026-08-10`, listing rows closed, the measured diff (`git diff --stat <base>..HEAD -- src/ tests/ pyproject.toml .env.example`), the verification output from Step 2, and the acceptance-run evidence from Step 3. State that **Step 6 (`C2.1–C2.5`, TOML config) is unblocked**.

- [ ] **Step 5: Verify the ledger has no contradictions**

Run: `grep -n "C1\.\|A1\.2[89]\|A1\.3[012]\|A5\.2\|C7\.6" TODO.md | grep -c PENDING`
Expected: exactly the count of rows you deliberately left pending (`A1.35`, `A1.36`, and `C1.8` if you left it open). Any other number means a row was missed.

- [ ] **Step 6: Commit**

```bash
git add .env.example TODO.md
git commit -m "docs: close out Step 5 — provider-agnostic model factory

Rudra now builds models for any provider from configuration. No module
outside rudra.llm imports a provider package, which is the whole of the
provider-agnostic claim and is enforced by a test.

Closes C1.1-C1.8, C1.4a, U.9, A5.2, and C7.6 as an alias of C1.4a. Fixes
A1.34 and A1.38; guards A1.37. A1.35 defers to U.10 and A1.36 defers on
its own, both with reasons recorded.

.env.example documents the RUDRA_* schema, the per-role override rule, and
the seven deprecated OLLAMA_* variables. It names key VARIABLES only —
no key value belongs in a config file.

Step 6 (C2.1-C2.5, layered TOML) is unblocked.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2.1 factory shape, `apply_provider_profile` two-arg form | 3 |
| §2.2 three-state tool-calling check | 3 |
| §2.3 three roles + unknown-role fallback | 2, 3 |
| §2.4 OpenRouter via `openai_compatible` | 1, 3, 6 |
| §2.5 profile merge | 3 |
| §2.6 policy table not passthrough | 1 |
| §2.7 / N5 `use_responses_api=False` | 0, 1, 3 |
| §3 env schema | 2 |
| §3.1 deprecation shim | 2 |
| §4 module layout | 1, 3, 6 |
| §5.1 build_model algorithm | 3 |
| §5.2 provider registry | 1 |
| §5.3 `num_ctx`, omitted timeout | 1 |
| §5.4 C1.4a / C7.6 decision | 3, 7 |
| §5.5 errors | 1, 3 |
| §6 `rudra models test` | 6 |
| §7 A5.2 | 2, 5 |
| §8 call-site migration | 4, 5 |
| §9.1 offline tests | 1, 2, 3, 4, 5 |
| §9.2 live tests | 6 |
| §9.3 acceptance | 7 |
| §9.4 A5.1 / A2.16 byproduct | 7 |
| §10 ledger | 0, 7 |

No spec section is unclaimed.

**Type consistency:** `ModelConfig` fields are named identically in Task 1's `FakeSettings`, Task 2's definition, and Task 3's `make_config`: `provider`, `model`, `base_url`, `api_key_env`, `temperature`, `context_tokens`, `max_output_tokens`, `timeout`. `ProviderEntry` field names match between Task 1's definition and Task 3's use (`lc_prefix`, `needs_api_key`, `build_kwargs`). `build_model(role, cfg=None)` has one signature across Tasks 3, 4, and 6. `probe_role(role)` and `ProbeResult` fields match between Task 6's probe and its CLI consumer.

**Known ordering hazard, called out rather than hidden:** Task 2 leaves the suite red, and Task 4 turns it green. This is deliberate — splitting the config rewrite from the call-site migration keeps each reviewable — but a subagent executing Task 2 in isolation must not "fix" `agent/` to get a green run. Task 2 Step 5 says so explicitly.
