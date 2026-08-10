# 3. Choosing a Model

Rudra talks to five kinds of backend. Pick one, copy the block, run `rudra models test`.

- [Which should I use?](#which-should-i-use)
- [What a model must be able to do](#what-a-model-must-be-able-to-do)
- [Ollama — local](#ollama--local)
- [OpenAI-compatible — OpenRouter, vLLM, LM Studio, Groq, Together](#openai-compatible--openrouter-vllm-lm-studio-groq-together)
- [Anthropic](#anthropic)
- [OpenAI](#openai)
- [Google](#google)
- [Switching between them](#switching-between-them)

---

## Which should I use?

| You want | Use | Notes |
|---|---|---|
| Everything on my own machine, no bills | **Ollama** | Needs a capable GPU or lots of RAM |
| To try Rudra on a laptop right now | **OpenAI-compatible** → OpenRouter | Free models available; sign-up required |
| The strongest coding models | **Anthropic** or **OpenAI** | Paid, and your code goes to their servers |
| A model I'm already self-hosting | **OpenAI-compatible** → your vLLM endpoint | One `base_url` change |
| Gemini | **Google** | Paid |

Not sure? Start with **OpenRouter** — no hardware needed and free models to test with. Move to Ollama once you know Rudra suits you.

---

## What a model must be able to do

Two hard requirements:

**1. Tool calling.** Rudra works by calling tools — reading files, writing files, updating the plan. A model that can't call tools can't drive Rudra at all, no matter how well it writes code. `rudra models test` checks this explicitly in its **Tools** column.

**2. At least 32B parameters.** Rudra used to carry workarounds for small models — blocking tools they misused, re-injecting instructions they forgot. Those were removed. Smaller models will produce broken plans and half-written files. 7B and 14B models are not supported.

---

## Ollama — local

Everything stays on your machine. No key, no bill, no network.

**Setup**

```bash
# 1. Install from https://ollama.com/download
# 2. Start the server (own terminal, leave running)
ollama serve

# 3. Pull a model
ollama pull qwen3:32b

# 4. Confirm
ollama list
```

**`.rudra/config.toml`**

```toml
[model.default]
provider = "ollama"
base_url = "http://localhost:11434"
model = "qwen3:32b"
context_tokens = 32768
```

**Two things people get wrong here**

*Set `RUDRA_CONTEXT_TOKENS`.* Ollama allocates a **4096-token** window by default and silently truncates past it — no error, just a confused agent that forgets its own plan. Setting this raises the allocation to match your model.

*Model names contain colons, and that's fine.* `qwen3:32b` is one name; the `:32b` is a tag, not a separator. Rudra handles it.

**Good choices**

| Model | Size | Best at |
|---|---|---|
| `qwen3:32b` | ~20GB | General planning and coding |
| `qwen3-coder:32b` | ~20GB | Writing code specifically |

A nice combination — reason with one, write with the other:

```toml

[model.planner]
model = "qwen3:32b"

[model.coder]
model = "qwen3-coder:32b"
```

**Remote Ollama**

Running Ollama on another machine? Just change the URL:

```toml
[model.default]
base_url = "http://192.168.1.50:11434"
```

---

## OpenAI-compatible — OpenRouter, vLLM, LM Studio, Groq, Together

One provider covers every service speaking the OpenAI chat API. Only `base_url` and the model name change.

### OpenRouter

Hundreds of models behind one key, including free ones. Get a key at [openrouter.ai](https://openrouter.ai).

```toml
# .rudra/config.toml
[model.default]
provider = "openai_compatible"
base_url = "https://openrouter.ai/api/v1"
model = "nvidia/nemotron-3-ultra-550b-a55b:free"
api_key_env = "OPENROUTER_API_KEY"
context_tokens = 131072
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

> **About the free tier.** Models ending in `:free` are genuinely free but heavily shared. Expect two failure modes: `429 Rate limit exceeded: free-models-per-day` when your daily allowance runs out, and `502 ResourceExhausted` when the upstream provider is momentarily saturated. The 502 usually clears on a retry; the 429 doesn't. Adding a small credit balance lifts the daily cap. Fine for trying Rudra out — for real work, use a paid model.

### vLLM (self-hosted)

```toml
# .rudra/config.toml
[model.default]
provider = "openai_compatible"
base_url = "http://localhost:8000/v1"
model = "Qwen/Qwen3-32B"
api_key_env = "VLLM_API_KEY"
context_tokens = 32768
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
VLLM_API_KEY=not-used-but-required
```

Most vLLM deployments ignore the key but still expect the header, so give it any non-empty value.

### LM Studio

Start the local server from LM Studio's Developer tab:

```toml
# .rudra/config.toml
[model.default]
provider = "openai_compatible"
base_url = "http://localhost:1234/v1"
model = "qwen3-32b"
api_key_env = "LMSTUDIO_API_KEY"
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
LMSTUDIO_API_KEY=lm-studio
```

### Groq

```toml
# .rudra/config.toml
[model.default]
provider = "openai_compatible"
base_url = "https://api.groq.com/openai/v1"
model = "llama-3.3-70b-versatile"
api_key_env = "GROQ_API_KEY"
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
GROQ_API_KEY=gsk_...
```

### Together

```toml
# .rudra/config.toml
[model.default]
provider = "openai_compatible"
base_url = "https://api.together.xyz/v1"
model = "Qwen/Qwen3-32B-Instruct"
api_key_env = "TOGETHER_API_KEY"
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
TOGETHER_API_KEY=...
```

> **Use `openai_compatible`, not `openai`, for all of these** — even though they speak OpenAI's API. The `openai` provider enables OpenAI's Responses API, which these servers don't implement. `openai_compatible` deliberately turns it off.

---

## Anthropic

```toml
# .rudra/config.toml
[model.default]
provider = "anthropic"
model = "claude-sonnet-4-5-20250929"
api_key_env = "ANTHROPIC_API_KEY"
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
ANTHROPIC_API_KEY=sk-ant-...
```

No `base_url` needed. Anthropic reports its own context window, so `RUDRA_CONTEXT_TOKENS` is optional — set it only to deliberately use less.

---

## OpenAI

```toml
# .rudra/config.toml
[model.default]
provider = "openai"
model = "gpt-5.4"
api_key_env = "OPENAI_API_KEY"
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
OPENAI_API_KEY=sk-...
```

Use this only for OpenAI's own API. Anything else that merely speaks the same protocol wants `openai_compatible`.

---

## Google

```toml
# .rudra/config.toml
[model.default]
provider = "google"
model = "gemini-2.5-pro"
api_key_env = "GOOGLE_API_KEY"
```

```bash
# .env  (gitignored — the key itself never goes in config.toml)
GOOGLE_API_KEY=...
```

---

## Switching between them

Editing `.rudra/config.toml` is the usual way. For a one-off, override on the command line:

```bash
RUDRA_PROVIDER=ollama RUDRA_MODEL=qwen3:32b rudra "write a parser"
```

Environment variables beat both config files, so this works without touching either.

To see which layer a setting actually came from:

```bash
rudra config list
```

After any change:

```bash
rudra models test
```

If the table shows all `ok`, you're set. If not, [Troubleshooting](06-troubleshooting.md) decodes the message.

---

**Next:** [CLI Reference](04-cli-reference.md) · [Configuration](02-configuration.md)
