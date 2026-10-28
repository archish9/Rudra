# Vaayu: enable OpenAI-style tool-call parsing

**Audience:** the Vaayu server team.
**Status:** blocker for Vaayu ↔ Rudra integration. Everything else on the wire works.
**Date verified:** 2026-10-28, against `https://parade-grasp-user.ngrok-free.dev`.

This document is self-contained. It describes one change Vaayu must make on the
server side, why it is needed, exactly how to reproduce the problem, and how to
confirm the fix.

---

## 1. Context — how Rudra talks to Vaayu

Rudra is an autonomous coding agent. It drives a model entirely through the
**OpenAI chat-completions wire protocol** (`POST /v1/chat/completions`), using
the standard `langchain-openai` client. Vaayu is configured in Rudra as an
OpenAI-compatible provider:

```toml
[model.coder]
provider = "vaayu"
base_url = "https://parade-grasp-user.ngrok-free.dev/v1"
model    = "vaayu"
context_tokens = 16384
```

Rudra is provider-agnostic by design: it does **not** and **will not** parse
provider-specific output formats. It relies on the server returning a spec-
compliant OpenAI response. So the fix belongs on the Vaayu side, not in Rudra.

### What already works (no change needed)

| Check | Result |
|---|---|
| `GET /v1/models` | `200`, advertises model `vaayu`, `max_seq_len: 16384`, and a `batching` hint (`recommended_client_concurrency: 1`). Rudra reads all of this correctly. |
| `session_id` requirement | Vaayu rejects a request with no session (`HTTP 400`, `REQUIRE_SESSION_ID`). Rudra sends `session_id` in the request body (`extra_body`), so normal agent calls pass. |
| Plain chat completion | `200`, correct content, correct `usage` block, `session_id` echoed. |
| Streaming (`stream: true`) | Works for plain text. |

**The only broken thing is tool calling.**

---

## 2. The problem

When the request includes `tools`, Vaayu returns the model's tool-call intent as
**raw template text inside `message.content`** instead of as a structured
OpenAI `tool_calls` array. `finish_reason` stays `"stop"` and `tool_calls` is
absent.

### Reproduce

```bash
curl -s https://parade-grasp-user.ngrok-free.dev/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "vaayu",
    "session_id": "repro-tools",
    "max_tokens": 128,
    "tool_choice": "auto",
    "messages": [
      {"role": "user", "content": "What is 12 times 7?"}
    ],
    "tools": [
      {"type": "function", "function": {
        "name": "multiply",
        "description": "Multiply two integers",
        "parameters": {
          "type": "object",
          "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
          "required": ["a", "b"]
        }
      }]
    }
  }'
```

### What Vaayu returns now (WRONG)

```json
{
  "choices": [{
    "index": 0,
    "finish_reason": "stop",
    "message": {
      "role": "assistant",
      "content": "12 times 7 equals 84.\n<minimax:tool_call>\n<invoke name=\"multiply\">\n<parameter name=\"a\">12</parameter>\n<parameter name=\"b\">7</parameter>\n</invoke>\n</minimax:tool_call>"
    }
  }]
}
```

The tool call is trapped in `content` as `<minimax:tool_call>` XML. There is no
`tool_calls` field.

### What a spec-compliant server must return (CORRECT)

```json
{
  "choices": [{
    "index": 0,
    "finish_reason": "tool_calls",
    "message": {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "multiply",
          "arguments": "{\"a\": 12, \"b\": 7}"
        }
      }]
    }
  }]
}
```

Note:
- `tool_calls` is an **array** on `message`.
- `function.arguments` is a **JSON-encoded string**, not an object (OpenAI spec).
- `finish_reason` is `"tool_calls"`, not `"stop"`.
- `content` is `null` (or any accompanying prose) — the tool call is **not** in it.

---

## 3. Why this breaks Rudra (and every OpenAI client)

The OpenAI chat-completions contract says a model's decision to call a tool is
delivered in `choices[].message.tool_calls`. Standard clients read exactly that
field:

- `langchain-openai` (what Rudra uses) maps the response to `message.tool_calls`.
  With the current Vaayu output that field is `None`, so the client sees only a
  chunk of prose with embedded XML.
- Rudra's agent loop advances only when it receives a parsed tool call. With no
  `tool_calls`, the agent never writes a file, never runs a command, never
  tests — the loop stalls on the first model turn.
- Rudra's capability probe (`rudra models test`) checks `response.tool_calls`
  and reports `no tool_calls emitted`, i.e. the model shows as unable to use
  tools.

The `<minimax:tool_call>` tag shows the underlying model is a MiniMax-format
model and **is** emitting tool-call tokens correctly. What is missing is the
**server-side parser** that extracts those tokens into the OpenAI `tool_calls`
structure before responding. This is a known, standard server responsibility.

---

## 4. The change Vaayu must make

**Enable server-side tool-call parsing for the MiniMax format.** The server must
detect the `<minimax:tool_call> … <invoke name="…"> … </invoke> …` block in the
model's generated tokens, convert each `invoke` into a `tool_calls[]` entry
(with a generated `id`, `type: "function"`, the function `name`, and
JSON-encoded `arguments`), strip it out of `content`, and set
`finish_reason: "tool_calls"`.

If Vaayu is built on or around vLLM, this is the equivalent of launching with:

```
--enable-auto-tool-choice --tool-call-parser minimax
```

If Vaayu has its own serving stack, implement the equivalent parser in the
response path.

### Requirements for the fix

1. **Non-streaming** (`stream: false`): return `tool_calls[]` on the message as
   shown in §2, `finish_reason: "tool_calls"`.
2. **Streaming** (`stream: true`): Rudra runs with `--stream`, so the parser must
   also work in streaming mode — emit `delta.tool_calls` chunks (with `index`,
   partial `function.arguments` strings concatenated by the client) per the
   OpenAI streaming spec, not raw `<minimax:tool_call>` text deltas.
3. **Multiple tool calls** in one turn → multiple entries in `tool_calls[]`, each
   with a distinct `index`/`id`.
4. **`arguments` must be a JSON string**, and must parse to the schema the client
   sent in `tools[].function.parameters`.
5. Plain (no-tool) responses must be unchanged — this change applies only when
   the model actually emits a tool-call block.

### Do NOT

- Do not require Rudra to parse `<minimax:tool_call>`. Rudra is provider-agnostic
  and will not add provider-specific scraping; a client-side workaround would
  also break on streaming (tags split across chunks) and on `finish_reason`.

---

## 5. Acceptance test

The fix is complete when the reproduce command in §2 returns a `tool_calls`
array with `finish_reason: "tool_calls"` (the CORRECT shape), and the streaming
variant (`"stream": true`) emits `delta.tool_calls` chunks instead of raw XML
text. At that point Rudra's `rudra models test` reports the `tools` column as
`ok` for every role, and a full agent run proceeds.

---

## 6. Summary

| | |
|---|---|
| **Wire protocol, models list, session_id, plain + streamed chat, usage** | ✅ working |
| **Tool calling** | ❌ returned as `<minimax:tool_call>` text in `content` instead of OpenAI `tool_calls` |
| **Fix location** | Vaayu server (enable MiniMax tool-call parser) |
| **Rudra changes needed** | none |
