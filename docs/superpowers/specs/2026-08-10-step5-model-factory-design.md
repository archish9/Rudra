# Step 5 — Provider-Agnostic Model Factory

**Date:** 2026-08-10
**Section E step:** 5 (Stage II — Make it configurable)
**Depends on:** Step 4 (**DONE** 2026-08-07, tree at `32749b6`)
**Unblocks:** Step 6 (`C2.1–C2.5` — layered TOML config + permission mode plumbing)

Closes product requirement #1 (provider-agnostic). Also the precondition for testing anything on a
non-Ollama model, which every later step depends on.

---

## 1. Scope

Section E step 5 lists `C1.1 – C1.8, U.9`. Three additions:

| Row | Why it is here | Section |
|---|---|---|
| `C1.4a` | Listed in `TODO.md` between `C1.4` and `C1.5`, not in Section E's range text. Its own row says "belongs here, not Phase 7: only the model factory knows the model's real context size" | §5.4 |
| `C7.6` | Duplicate of `C1.4a` whose row says "**Decide which in C1.1** — it is a model-factory concern". Decided here; the row closes as an alias | §5.4 |
| `A5.2` | Its row says the fix "should land before the Step 5 model factory (`C1.x`) inherits the same cwd-vs-project-root assumption". Owner confirmed inclusion 2026-08-10 | §7 |

Five defects found while exploring are **logged as PENDING before being fixed**, per CLAUDE.md
session rule 2. Listed in §10 as `N1`–`N5`.

### Out of scope

Owner decision 2026-08-10: A5.2 plus the `C1.x` rows, nothing else.

| Deferred item | Why |
|---|---|
| Layered TOML config (`C2.1–C2.5`) | Step 6. This step reads env vars only; §3 explains why the shape is still Step-6-compatible |
| Permissions (`U.7`, `C3.3`) | Step 7 |
| Harness-profile adoption (`U.10`) | Evaluate-later row. This step records `N2` with evidence and emits one DEBUG line per build; it does not adopt, and does not attempt to detect the miss |
| A distinct summarizer model | Step 12 (`C7.x`). deepagents constructs the summarizer from whatever model `create_deep_agent` receives |
| `A1.25` (silent plan-item drop) | Orchestrator concern, unrelated to model construction. Step 9 rewrites that loop wholesale |
| `OllamaConfig.timeout` → `client_kwargs` | Behavior change, not a rename. Logged as `N3` |

---

## 2. Decisions and their evidence

Every decision below was verified against the installed `deepagents==0.7.4` /
`langchain 1.3.14` / `langchain-core 1.5.3` stack, or against the live OpenRouter endpoint. The
project `.venv` is on **Python 3.13**.

| # | Decision | Evidence |
|---|---|---|
| S5-1 | Config source is **env vars only**; TOML layers underneath in Step 6 | Owner, 2026-08-10 |
| S5-2 | Construction is `init_chat_model(spec, **apply_provider_profile(spec), **role_kwargs)` | §2.1 |
| S5-3 | Tool-calling verified **statically at startup, live only in `rudra models test`** | §2.2 |
| S5-4 | Roles are `planner`, `coder`, `default`; any unknown role resolves to `default` | §2.3 |
| S5-5 | OpenRouter is reached through the generic `openai_compatible` provider | §2.4 |
| S5-6 | `profile` is **merged**, never replaced | §2.5 |
| S5-7 | Per-provider kwarg **policy table**, not kwarg passthrough | §2.6 |

### 2.1 — Why not `resolve_model` or `register_provider_profile` (S5-2)

`U.9` says "adopt provider profiles". The naive readings both fail:

- `resolve_model(model)` (`deepagents/_models.py:35`) accepts **only** the spec. It is
  `init_chat_model(model, **apply_provider_profile(model))`. There is no kwarg passthrough, so
  per-role `base_url` / `temperature` / `profile` cannot ride it.
- `register_provider_profile` writes to a **process-global registry keyed by `provider` or
  `provider:model`** (`profiles/provider/provider_profiles.py`). Two roles pointing at the same
  model would overwrite each other's kwargs, and registration is a global side effect that leaks
  across tests.

Composing at the call site gets the built-in profiles (OpenRouter attribution, NVIDIA NIM headers,
the OpenAI Responses-API default) with no global writes and no role collisions.

`apply_provider_profile` is **beta-flagged** (`provider_profiles.py` module docstring: "exposes beta
APIs that may receive minor changes in future releases"). It therefore joins the U.4 monkeypatch on
the upgrade-hazard list and gets a version guard — logged as `N4`, implemented in §9.1.

Its two-argument form does the merging for us: `apply_provider_profile(spec, role_kwargs)` returns
profile defaults with `role_kwargs` layered on top, caller winning on collision. Measured:

```
apply_provider_profile('openai:gpt-5.4')                          -> {'use_responses_api': True}
apply_provider_profile('openai:gpt-5.4', {'temperature': 0.3})    -> {'use_responses_api': True,
                                                                      'temperature': 0.3}
apply_provider_profile('openai:gpt-5.4', {'use_responses_api': False})
                                                                  -> {'use_responses_api': False}
```

### 2.7 — `openai_compatible` must suppress the Responses API (N5)

The `count(":") > 1` rejection of §2.4 is **not** limited to harness profiles — the provider
registry rejects the same specs:

```
openai:gpt-5.4                                  -> {'use_responses_api': True}
openai:nvidia/nemotron-3-ultra-550b-a55b:free   -> {}
ollama:qwen3:32b                                -> {}
```

`openai_compatible` maps to the `openai` lc prefix (§5.2), so it inherits deepagents' built-in
OpenAI profile — `ProviderProfile(init_kwargs={"use_responses_api": True})`
(`profiles/provider/_openai.py:21-23`). Every OpenAI-compatible endpoint Rudra targets serves
`/chat/completions`; none of vLLM, LM Studio, Groq, or Together serves OpenAI's `/responses`, and
deepagents' own OpenRouter profile documents OpenRouter's `/responses` as a stateless beta that
breaks multi-turn reasoning.

The dev model escapes purely because its identifier contains a colon. A user configuring
`openai_compatible` with a colon-free model id would get `use_responses_api=True` injected into an
endpoint that cannot serve it.

**Therefore `openai_compatible` sets `use_responses_api=False` explicitly**, relying on the
caller-wins semantics measured above. The real `openai` provider keeps the profile default.

### 2.2 — Startup check is static; the live probe is opt-in (S5-3)

Measured: `ChatOllama(model=…).profile` is `None`. Hosted providers carry a populated profile —
`ChatAnthropic(model='claude-sonnet-4-5-20250929').profile` has 24 keys including
`tool_calling: True` and `max_input_tokens: 1000000`.

So the startup check is three-state and costs nothing:

| `profile["tool_calling"]` | Action |
|---|---|
| `False` | Refuse — `ModelCapabilityError` naming role and model (`C1.7`) |
| `True` | Proceed |
| absent, or `profile is None` | **Proceed.** Every local model lands here; refusing would ban Ollama |

A real round-trip happens only in `rudra models test` (§6), keeping normal runs free of a
per-invocation latency tax and usable offline.

### 2.3 — Three roles, not four (S5-4)

`C1.3` names `planner`, `coder`, `reviewer`, `summarizer`. `reviewer` has no consumer until Step 9
(`C6.3`), and the summarizer is built internally by deepagents from the model handed to
`create_deep_agent`. Shipping config keys for either now means unreachable, untestable code and
`rudra models test` probing models nobody uses.

`default` plus unknown-role fallback means Step 9's `build_model("reviewer")` works the day it
lands, with zero factory changes and zero dead config. Mirrors the existing
`OLLAMA_MODEL` → `OLLAMA_MODEL_PLANNER`/`_CODER` fallback shape.

### 2.4 — OpenRouter via `openai_compatible` (S5-5)

Two routes exist. The rejected one, for the record: deepagents ships an OpenRouter *provider*
profile (`profiles/provider/_openrouter.py`) that injects app-attribution headers and an
`openrouter_provider={"ignore": ["azure"]}` rule. Using it requires `langchain-openrouter>=0.2.0`
(latest 0.2.7), which pulls `openrouter>=0.9.2` — two extra runtime deps for header injection.

`openai_compatible` is required by `C1.1` regardless (vLLM / LM Studio / Groq / Together), adds
only `langchain-openai` (already `C1.2` as written), and makes OpenRouter configuration rather than
code. Verified live against `nvidia/nemotron-3-ultra-550b-a55b:free`:

```
class          : ChatOpenAI
identifier     : nvidia/nemotron-3-ultra-550b-a55b:free
profile        : {'max_input_tokens': 131072}
ls_provider    : openai
tool_calls     : [{'name': 'echo', 'args': {'text': 'hello'}, ...}]
finish_reason  : tool_calls
```

Note the model identifier contains a colon and `init_chat_model` still splits correctly on the
**first** colon — separately confirmed with `init_chat_model('ollama:qwen3:32b')` → `ChatOllama`,
`model='qwen3:32b'`.

### 2.5 — `profile` merges, never replaces (S5-6)

`compute_summarization_defaults` (`deepagents/middleware/summarization.py:249-286`) switches from
the fixed `("tokens", 170000)` trigger to `("fraction", 0.85)` **iff**
`model.profile["max_input_tokens"]` is an `int`. That single condition is the whole of `C1.4a` /
`A1.17`: without it every local model silently inherits a 170k trigger it can never reach and never
compacts.

Blind assignment would destroy the capability data §2.2 depends on. Measured:

```
before: max_input_tokens=1000000  tool_calling=True
merged = {**a.profile, 'max_input_tokens': 200000}
after : max_input_tokens=200000   tool_calling=True     # preserved
original untouched: 1000000                             # model_copy is non-mutating
```

`model_copy(update={"profile": merged})` is therefore the mechanism, applied post-construction so
the provider's own profile is visible to merge into.

### 2.6 — Policy table, not passthrough (S5-7)

```
ChatOllama(model='q', base_url='http://x', timeout=42)
  -> accepted, no exception, attr = ABSENT
```

Ollama has no `timeout` field and no alias; passing it is a **silent no-op**. The other three
accept `base_url` / `api_key` / `timeout` as pydantic aliases despite differing field names —
measured: `anthropic_api_url`, `default_request_timeout`, `google_api_key`, `openai_api_base`,
`request_timeout`.

Blanket passthrough therefore works on three providers and silently drops settings on the fourth.
An explicit per-provider policy makes the gating auditable and each provider one table row.

---

## 3. Env schema

Roles: `planner`, `coder`, `default`. Per-role keys override the bare form.

```
RUDRA_PROVIDER          ollama | openai_compatible | openai | anthropic | google
RUDRA_MODEL             nvidia/nemotron-3-ultra-550b-a55b:free
RUDRA_BASE_URL          https://openrouter.ai/api/v1
RUDRA_API_KEY_ENV       OPENROUTER_API_KEY        # variable NAME, never a key value  [C1.5]
RUDRA_TEMPERATURE       0.3
RUDRA_CONTEXT_TOKENS    131072                    # -> profile["max_input_tokens"]    [C1.4a]
RUDRA_MAX_OUTPUT_TOKENS 131072                    # -> num_predict, ollama only       [C1.4]
RUDRA_TIMEOUT           300

RUDRA_PLANNER_*         same eight keys
RUDRA_CODER_*           same eight keys
```

`PROVIDER` and `MODEL` are **separate keys**, not a combined `provider:model` string, because
CLAUDE.md §6's TOML schema has them separate. One spelling now means Step 6 has one spelling to
layer. The factory composes `f"{lc_prefix}:{model}"` internally, which is the only place the colon
form is correct.

**API keys never appear in config.** `RUDRA_API_KEY_ENV` names the variable; the value lives in the
environment or a gitignored `.env` (`C1.5`). Error messages name the variable and never print a
value — asserted by a test (§9.1).

### 3.1 — `C1.3` deprecation shim

Seven `OLLAMA_*` variables keep working for one release. Each maps to its `RUDRA_*` equivalent,
forces `provider = ollama`, and emits one `DeprecationWarning` per variable on first read.
`RUDRA_*` wins on collision.

| Deprecated | Replacement |
|---|---|
| `OLLAMA_BASE_URL` | `RUDRA_BASE_URL` |
| `OLLAMA_MODEL` | `RUDRA_MODEL` |
| `OLLAMA_MODEL_PLANNER` | `RUDRA_PLANNER_MODEL` |
| `OLLAMA_MODEL_CODER` | `RUDRA_CODER_MODEL` |
| `OLLAMA_TEMPERATURE` | `RUDRA_TEMPERATURE` |
| `OLLAMA_TIMEOUT` | `RUDRA_TIMEOUT` |
| `OLLAMA_NUM_PREDICT` | `RUDRA_MAX_OUTPUT_TOKENS` |

---

## 4. Module layout

```
src/rudra/config.py          ModelConfig replaces OllamaConfig; Config.model_for(role)
src/rudra/llm/__init__.py    exports build_model
src/rudra/llm/providers.py   provider registry (§5.2)
src/rudra/llm/factory.py     build_model(role, cfg=None) -> BaseChatModel        [C1.1]
src/rudra/llm/probe.py       probe_role(role) -> ProbeResult                     [C1.6]
src/rudra/llm/errors.py      MissingApiKeyError, ModelCapabilityError, UnknownProviderError
```

`ModelConfig` lives in `config.py`, not `llm/`, so Step 6 rewrites one module to add TOML layering
and the factory needs no edit. `build_model` takes an optional `cfg` so tests inject settings
instead of monkeypatching `os.environ`.

Each unit in isolation: `providers.py` is pure data plus one pure function per entry and has no
imports from the rest of Rudra. `factory.py` depends only on `providers` + `config` + langchain.
`probe.py` depends only on `factory`. Nothing in `llm/` imports `agent/` or `cli/`.

---

## 5. The factory

### 5.1 — `build_model(role, cfg=None) -> BaseChatModel`

```
1.  settings = (cfg or get_config()).model_for(role)      # unknown role -> default
2.  entry    = PROVIDERS[settings.provider]               # unknown -> UnknownProviderError
3.  spec     = f"{entry.lc_prefix}:{settings.model}"
4.  kwargs   = apply_provider_profile(spec, entry.build_kwargs(settings))            # U.9, §2.1
5.  if entry.needs_api_key: kwargs["api_key"] = env[settings.api_key_env] or raise   # C1.5
6.  model    = init_chat_model(spec, **kwargs)
7.  if settings.context_tokens:                                                      # C1.4a
        model = model.model_copy(update={"profile":
                  {**(model.profile or {}), "max_input_tokens": settings.context_tokens}})
8.  if (model.profile or {}).get("tool_calling") is False:                           # C1.7
        raise ModelCapabilityError(role, settings.model)
9.  log.debug the spec, resolved ls_provider, and model identifier                   # N2 data
10. return model
```

Step 4 is where `U.9` lands: deepagents' built-in kwargs first, Rudra's role kwargs overriding on
collision. Steps 7 and 8 are ordered — the merge must precede the capability read, or a
freshly-merged profile would not be the one inspected.

### 5.2 — Provider registry (`llm/providers.py`)

Each row is a frozen dataclass. Adding a provider is one row, not a new code path.

| rudra provider | lc prefix | max-output kwarg | context kwarg | key kwarg | timeout | extra |
|---|---|---|---|---|---|---|
| `ollama` | `ollama` | `num_predict` | `num_ctx` **+ profile** | — | **omit** | `reasoning=True` |
| `openai_compatible` | `openai` | `max_tokens` | profile only | `api_key` | `timeout` | `use_responses_api=False` (§2.7) |
| `openai` | `openai` | `max_tokens` | profile only | `api_key` | `timeout` | — |
| `anthropic` | `anthropic` | `max_tokens` | profile only | `api_key` | `timeout` | — |
| `google` | `google_genai` | `max_output_tokens` | profile only | `api_key` | `timeout` | — |

### 5.3 — Two rows that need justifying

**`ollama` sets `num_ctx` as well as the profile.** `profile["max_input_tokens"]` tells *deepagents*
when to summarize. `num_ctx` tells the *Ollama server* how much context to allocate; it defaults to
4096 and truncates silently above that. Setting only one means either the agent compacts against a
window the server never granted, or the server allocates a window deepagents never uses. One
`RUDRA_CONTEXT_TOKENS` drives both.

**`ollama` omits `timeout` deliberately** — §2.6 measured it as a silent no-op. Faking support
would be worse than omitting it. Logged as `N3`.

### 5.4 — `C1.4a` / `C7.6`, resolved

`C7.6` offers two routes: construct `SummarizationMiddleware` explicitly with
`trigger=("fraction", 0.85)`, or register a model profile carrying `max_input_tokens`. Its row says
"Decide which in `C1.1`".

**Decided: the profile route.** Evidence in §2.5 — deepagents already computes
`("fraction", 0.85)` itself the moment `max_input_tokens` is an int, so the explicit-middleware
route reimplements a branch that already exists and would need re-checking on every deepagents
upgrade. The profile route is one merged dict in `build_model` step 7. `C7.6` closes as an alias of
`C1.4a`, not as separate Step 12 work.

### 5.5 — Errors (`llm/errors.py`)

| Condition | Behavior |
|---|---|
| Unknown provider name | `UnknownProviderError`, listing the five valid names |
| `api_key_env` names an unset variable | `MissingApiKeyError`, naming the variable, never a value |
| Provider needs a key, none configured | Same, at build time, before any network call |
| `profile["tool_calling"] is False` | `ModelCapabilityError`, naming role and model (`C1.7`) |
| `langchain-openai` not installed | `init_chat_model`'s `ImportError`, re-raised naming the pip package |

All raise at construction, so a misconfigured run dies before inference starts. That is the whole
point of `C1.7`.

---

## 6. `rudra models test` (C1.6)

The first subcommand in the CLI — `watch` was deleted by D17 and nothing replaced it. The
machinery already exists: `main()` returns early when `ctx.invoked_subcommand is not None`
(`cli.py:205-206`).

Four stages per role, reported independently so a failure names the stage:

```
Role     Provider           Model                        Construct  Reach  Tools  Ctx
planner  openai_compatible  nvidia/nemotron-3-...:free   ok         ok     ok     131072
coder    openai_compatible  nvidia/nemotron-3-...:free   ok         ok     ok     131072
```

1. **Construct** — `build_model(role)`. Catches unknown provider, missing key variable, missing
   package. No network.
2. **Reach** — one minimal `invoke`. Catches bad `base_url`, bad key, unknown model.
3. **Tools** — `bind_tools([echo]).invoke(...)`, assert `tool_calls` non-empty. The check that
   matters: deepagents hard-requires tool calling, and stage 2 passing says nothing about stage 3.
4. **Ctx** — report the effective `profile["max_input_tokens"]`, so a user can see whether they got
   their configured window or a provider default.

Non-zero exit if any role fails any stage. `--role planner` narrows it.

---

## 7. `A5.2` — `.env` resolution follows `--project-dir`

`Config.load` reads `load_dotenv(Path.cwd() / ".env")` (`config.py:67`), but the CLI separately
accepts `--project-dir`/`-d`. Running `rudra -d /path/to/proj "task"` from elsewhere reads the
wrong `.env` entirely.

The compounding constraint: `cli.py:212` calls `get_config()` — which caches the `Config` for the
process — **before** `project_path` is computed at `cli.py:214`. Changing the path expression alone
cannot fix it.

Fix, both halves required:

1. `Config.load(project_root: Path)` takes the root explicitly. No-argument `get_config()` still
   falls back to `Path.cwd()`, so tests and `rudra models test` keep working.
2. `cli.py` reorders so `project_path = get_project_path(project_dir)` runs before the first
   `get_config()`.

`override=False` is retained — a real environment variable still beats `.env` (A5.1's decision,
unchanged).

---

## 8. Call-site migration — complete, verified

| Site | Today | After |
|---|---|---|
| `planner_agent.py:78-84` | `ChatOllama(...)` | `build_model("planner")` |
| `coder_agent.py:58-64` | `ChatOllama(...)` | `build_model("coder")` |
| `cli.py:224-225` | `get_config().ollama.model_planner` / `_coder` | `cfg.model_for(role).model` |
| `cli.py:308-309` | same | same |
| `main_agent.py:543-544` | same | same |
| `tests/test_config.py:73,76,79,120,130` | `ollama.model`, `ollama.num_predict` | role-based equivalents plus shim tests |
| `pyproject.toml` | — | `+langchain-openai>=1.4.1` (`C1.2`); `markers = ["live"]` |
| `.env.example` | `OLLAMA_*` block | `RUDRA_*` block; `OLLAMA_*` documented as deprecated |

Both agent modules lose `from langchain_ollama import ChatOllama`. After this step **no Rudra module
imports a provider package directly** — that is the `C1.1` acceptance criterion, and it is
greppable:

```bash
grep -rn "langchain_ollama\|langchain_openai\|langchain_anthropic\|langchain_google_genai" src/
# expected: only src/rudra/llm/providers.py, and only in comments
```

---

## 9. Verification

### 9.1 — Offline, runs in CI with no key and no network

- Provider registry: each of five providers yields the expected spec and kwarg set
- Kwarg gating: `num_predict` / `reasoning` present on ollama, **absent** on the other four;
  `timeout` absent on ollama, present on the other four
- `num_ctx` **and** `profile["max_input_tokens"]` both set from one `RUDRA_CONTEXT_TOKENS`
- Profile merge preserves pre-existing capability keys (the Anthropic case in §2.5)
- Role fallback: `build_model("reviewer")` resolves through `default`
- Deprecation shim: each of seven `OLLAMA_*` variables maps correctly, warns once, loses to
  `RUDRA_*`
- `N5`: `openai_compatible` never emits `use_responses_api=True`, including for a colon-free model
  id where the built-in OpenAI profile does apply; the real `openai` provider still gets it
- Errors: unknown provider / unset key variable / `tool_calling: False` each raise their own type,
  **and the key value never appears in any message**
- `A5.2`: `Config.load(root)` reads `root/.env`, not the cwd's, when `-d` names another directory
- Version guard (`N4`): `apply_provider_profile` still importable and callable, alongside the
  existing U.4 guard in `tests/test_version_guard.py`

### 9.2 — Live, marked `@pytest.mark.live`

Skipped unless `RUDRA_LIVE_TESTS=1` **and** the configured key variable is set, so CI stays green
without secrets. Requires `markers = ["live: requires a reachable model backend"]` in
`[tool.pytest.ini_options]`.

- Construct + `bind_tools` + `invoke` against OpenRouter; assert real `tool_calls`
- `rudra models test` exits 0 with both roles passing all four stages

### 9.3 — Acceptance

One real `rudra "<task>"` run against OpenRouter from a `mktemp -d` outside the repo, with no
environment overrides, producing files on disk.

Plus the Step 4 net, unchanged: `ruff check src/ tests/`, `ruff format --check src/ tests/`,
`pytest -q`, `rudra --version`.

### 9.4 — Two blocked items that close as a byproduct

`A5.1`'s no-override evidence and `A2.16`'s definitive dependency-drop proof have been outstanding
since Step 3 purely because no model backend was reachable (`TODO.md:649`). §9.3's run satisfies
both. Evidence gets recorded when it happens.

**They are not gates on this step.** If OpenRouter is unavailable on the day, Step 5 still lands on
§9.1 and the rows stay open — the same state they are in now.

---

## 10. Ledger changes

### Rows closed

`C1.1`, `C1.2`, `C1.3`, `C1.4`, `C1.4a`, `C1.5`, `C1.6`, `C1.7`, `C1.8`, `U.9`, `A5.2`, and `C7.6`
(as an alias of `C1.4a`, §5.4).

### New rows — logged PENDING before any fix, per session rule 2

| ID | Finding | Evidence | Disposition |
|---|---|---|---|
| `N1` | `config.py:19` defaults `OLLAMA_MODEL` to `qwen3:14b`, below the D6 32B floor, while `.env.example:9` says `qwen3:32b`. Same class as `A1.23`, different file | `src/rudra/config.py:19` vs `.env.example:9` | **Fixed in-step** — the default moves into `ModelConfig` regardless |
| `N2` | Built-in harness profiles can never match a Rudra model. `graph.py:584` sets `_model_spec = model if isinstance(model, str) else None`, and Rudra passes instances; the instance fallback in `_harness_profile_for_model` then fails because `_get_harness_profile` rejects any spec with `count(":") > 1`, which every Ollama tag (`qwen3:32b`) and the OpenRouter `:free` suffix produce. Measured for the dev model: `ls_provider=openai`, identifier `nvidia/nemotron-3-ultra-550b-a55b:free` → `HarnessProfile()` default, so the shipped Nemotron 3 Ultra profile (`profiles/harness/_nvidia_nemotron_3_ultra.py:52`, registered as `openrouter:nvidia/nemotron-3-ultra-550b-a55b`) never loads | `deepagents/graph.py:584,605`; `profiles/harness/harness_profiles.py:1078,1086-1087,1283-1301`; live probe §2.4 | **Deferred to `U.10`.** Detecting the miss would require the factory to guess a canonical spec from user config (is `:free` a tag or half a model name?), which is exactly the judgment `U.10` exists to make. Step 5 instead emits one DEBUG line per build recording spec, `ls_provider`, and identifier, so `U.10` starts with real data rather than re-deriving it |
| `N3` | `OllamaConfig.timeout` (`config.py:33`) is dead config — no call site passes it, and `ChatOllama` would silently swallow it if one did (§2.6). Honest support means `client_kwargs={"timeout": n}` | `src/rudra/config.py:33`; `planner_agent.py:78-84`, `coder_agent.py:58-64` pass only model/base_url/temperature/num_predict/reasoning | **Deferred** — behavior change, not a rename |
| `N5` | The `count(":") > 1` spec rejection applies to **provider** profiles as well as harness profiles, so `openai_compatible` silently inherits `use_responses_api=True` for any colon-free model id — injecting OpenAI's Responses API into vLLM / LM Studio / Groq / Together / OpenRouter endpoints that serve only `/chat/completions`. The dev model escapes only because its identifier contains a colon | measured: `apply_provider_profile('openai:gpt-5.4')` → `{'use_responses_api': True}` vs `apply_provider_profile('openai:nvidia/nemotron-3-ultra-550b-a55b:free')` → `{}`; `profiles/provider/_openai.py:21-23` | **Fixed in-step** — `openai_compatible` sets `use_responses_api=False`, §2.7 |
| `N4` | `apply_provider_profile` is a beta-flagged deepagents API now load-bearing in `build_model`, joining the U.4 monkeypatch on the upgrade-hazard list | `deepagents/profiles/provider/provider_profiles.py` module docstring; `pyproject.toml:30` pins `deepagents==0.7.4` | **Guarded in-step** — version-guard test, §9.1 |

### Rows updated, not closed

- `A5.1` — append §9.3's no-override evidence if the acceptance run happens
- `A2.16` — append the dependency-drop proof if the acceptance run happens
- `A4.10` — this spec cites stable symbol names alongside line numbers, per that row's request

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| `apply_provider_profile` changes shape in a future deepagents release | `N4` version guard; `deepagents` is pinned exactly (`pyproject.toml:30`) |
| The `.env`/`get_config()` reorder in `cli.py` (§7) changes what config a run sees | Config caching is the actual hazard, not the path expression. Covered by the `A5.2` test in §9.1 plus the CLI smoke test added in Step 4 |
| A provider silently swallows a kwarg the way Ollama swallows `timeout` | The §9.1 gating tests assert kwarg **presence and absence** per provider, not just presence |
| Deleting `OllamaConfig` breaks a user's existing `.env` | §3.1 shim, one release, with a `DeprecationWarning` per variable |
| The dev API key is exposed in the session that produced this spec | Rotate at openrouter.ai after Step 5 lands. The key is referenced only by variable name in config, and `.env` is gitignored (`.gitignore:139`) |
| OpenRouter's free tier rate-limits during the acceptance run | §9.4 — live checks are not gates; §9.1 stands alone |
