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

**`.env`**

```bash
RUDRA_PROVIDER=ollama
RUDRA_BASE_URL=http://localhost:11434
RUDRA_MODEL=qwen3:32b
RUDRA_CONTEXT_TOKENS=32768
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

```bash
RUDRA_PLANNER_MODEL=qwen3:32b
RUDRA_CODER_MODEL=qwen3-coder:32b
```

**Remote Ollama**

Running Ollama on another machine? Just change the URL:

```bash
RUDRA_BASE_URL=http://192.168.1.50:11434
```

---

## OpenAI-compatible — OpenRouter, vLLM, LM Studio, Groq, Together

One provider covers every service speaking the OpenAI chat API. Only `base_url` and the model name change.

### OpenRouter

Hundreds of models behind one key, including free ones. Get a key at [openrouter.ai](https://openrouter.ai).

```bash
RUDRA_PROVIDER=openai_compatible
RUDRA_BASE_URL=https://openrouter.ai/api/v1
RUDRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
RUDRA_API_KEY_ENV=OPENROUTER_API_KEY
RUDRA_CONTEXT_TOKENS=131072

OPENROUTER_API_KEY=sk-or-v1-your-key-here
```

> **About the free tier.** Models ending in `:free` are genuinely free but heavily shared. Expect two failure modes: `429 Rate limit exceeded: free-models-per-day` when your daily allowance runs out, and `502 ResourceExhausted` when the upstream provider is momentarily saturated. The 502 usually clears on a retry; the 429 doesn't. Adding a small credit balance lifts the daily cap. Fine for trying Rudra out — for real work, use a paid model.

### vLLM (self-hosted)

```bash
RUDRA_PROVIDER=openai_compatible
RUDRA_BASE_URL=http://localhost:8000/v1
RUDRA_MODEL=Qwen/Qwen3-32B
RUDRA_API_KEY_ENV=VLLM_API_KEY
RUDRA_CONTEXT_TOKENS=32768

VLLM_API_KEY=not-used-but-required
```

Most vLLM deployments ignore the key but still expect the header, so give it any non-empty value.

### LM Studio

Start the local server from LM Studio's Developer tab:

```bash
RUDRA_PROVIDER=openai_compatible
RUDRA_BASE_URL=http://localhost:1234/v1
RUDRA_MODEL=qwen3-32b
RUDRA_API_KEY_ENV=LMSTUDIO_API_KEY

LMSTUDIO_API_KEY=lm-studio
```

### Groq

```bash
RUDRA_PROVIDER=openai_compatible
RUDRA_BASE_URL=https://api.groq.com/openai/v1
RUDRA_MODEL=llama-3.3-70b-versatile
RUDRA_API_KEY_ENV=GROQ_API_KEY

GROQ_API_KEY=gsk_...
```

### Together

```bash
RUDRA_PROVIDER=openai_compatible
RUDRA_BASE_URL=https://api.together.xyz/v1
RUDRA_MODEL=Qwen/Qwen3-32B-Instruct
RUDRA_API_KEY_ENV=TOGETHER_API_KEY

TOGETHER_API_KEY=...
```

> **Use `openai_compatible`, not `openai`, for all of these** — even though they speak OpenAI's API. The `openai` provider enables OpenAI's Responses API, which these servers don't implement. `openai_compatible` deliberately turns it off.

---

## Anthropic

```bash
RUDRA_PROVIDER=anthropic
RUDRA_MODEL=claude-sonnet-4-5-20250929
RUDRA_API_KEY_ENV=ANTHROPIC_API_KEY

ANTHROPIC_API_KEY=sk-ant-...
```

No `base_url` needed. Anthropic reports its own context window, so `RUDRA_CONTEXT_TOKENS` is optional — set it only to deliberately use less.

---

## OpenAI

```bash
RUDRA_PROVIDER=openai
RUDRA_MODEL=gpt-5.4
RUDRA_API_KEY_ENV=OPENAI_API_KEY

OPENAI_API_KEY=sk-...
```

Use this only for OpenAI's own API. Anything else that merely speaks the same protocol wants `openai_compatible`.

---

## Google

```bash
RUDRA_PROVIDER=google
RUDRA_MODEL=gemini-2.5-pro
RUDRA_API_KEY_ENV=GOOGLE_API_KEY

GOOGLE_API_KEY=...
```

---

## Switching between them

Editing `.env` is the usual way. For a one-off, override on the command line:

```bash
RUDRA_PROVIDER=ollama RUDRA_MODEL=qwen3:32b rudra "write a parser"
```

Real environment variables beat `.env`, so this works without touching any file.

After any change:

```bash
rudra models test
```

If the table shows all `ok`, you're set. If not, [Troubleshooting](06-troubleshooting.md) decodes the message.

---

**Next:** [CLI Reference](04-cli-reference.md) · [Configuration](02-configuration.md)
