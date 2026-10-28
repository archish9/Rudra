# Vaayu ↔ Rudra — run D status: what works, and the one issue left

**Date:** 2026-10-28 · **Audience:** the Vaayu server team.
**Endpoint tested:** `https://a.taila2bfc0.ts.net/v1` (Tailscale) · model `vaayu`
(MiniMax-M2.7) · `max_seq_len` 16384.
**Short version:** tool calling and the Tailscale transport are **fixed and
confirmed** — Rudra now plans, writes correct code, and tests against Vaayu. One
issue remains, and it is **server-side**: under a sustained multi-call session
Vaayu drops a stream mid-decode and then returns a **cascade of instant `502`s**,
i.e. the backend appears to go **down**, not merely slow. Details and evidence
below.

This document is self-contained — you do not need the Rudra source.

---

## 1. What now works (confirmed this run)

Run `bfb5c7b5da07`: prompt *"write a flask todo backend with sqlite, CRUD only"*,
`--auto --allow-shell --stream`, all roles on the Tailscale endpoint.

- **Tailscale fixed the transport.** The previous ngrok *free* tunnel dropped
  every multi-minute stream. Over Tailscale, **14 model calls succeeded**,
  including several very long streams — **350 s, 674 s, 629 s, 557 s** — with no
  disconnect. The keepalive + durable tunnel change did its job.
- **Tool calling is solid** across planner, coder and tester — many tool calls,
  all parsed cleanly into OpenAI `tool_calls`, no `<minimax:tool_call>` leakage.
- **The full agent pipeline ran:** planning completed (clarify → architect →
  breakdown → 3 tasks), and the coder **wrote real, correct code** to disk:
  `models.py` (a clean SQLAlchemy `Todo` model + `init_db` + session factory) and
  `test_models.py` (pytest suite with an in-memory SQLite fixture). The tester ran.
- **Usage accounting works** — every successful streamed call reported
  `prompt_tokens` / `completion_tokens`.

In other words: **Rudra + Vaayu is functionally working.** Nothing in the request
shape, the wire protocol, or the tool handling is wrong.

---

## 2. The one issue left: backend goes down mid-session (instant `502` cascade)

The run did not finish. It stopped on the **first task**, and the failure
signature is specific and server-side.

After a **successful 629 s coder stream**, the *next* coder call behaved like this
(exact sequence from Rudra's call log, in order):

| # | role | result | wall time |
|---|---|---|---|
| … | (14 calls before this all **ok**, incl. 350/674/629/557 s streams) | ok | — |
| 15 | coder | **`RemoteProtocolError`: peer closed connection without sending complete message body** (mid-decode) | 113.8 s |
| 16 | coder | **`502`** | 0.1 s |
| 17 | coder | **`502`** | 0.1 s |
| 18 | coder | **`502`** | 0.0 s |
| 19 | coder | **`502`** | 0.1 s |
| 20 | coder | **`502`** | 0.0 s |
| 21 | coder | **`502`** | 0.1 s |
| 22 | coder | **`502`** | 0.1 s |
| 23 | coder | **`502`** | 0.1 s |

**Read the timings.** The 502s come back in **~0.0–0.1 s** — instantly, and for
*every* subsequent request. That is not a busy or slow server; a slow server
takes seconds to minutes. An **instant, persistent 502** means the request never
reached a working model backend — the upstream (the model server process behind
whatever is terminating TLS / routing on the Tailscale host) is **down or
unreachable**. The preceding `RemoteProtocolError` (the server closing a live
stream without finishing the body) is consistent with the **backend process
dying mid-generation** — after which the front door returns 502 to everything.

### What we think is happening (for you to confirm)

The server handled ~50 minutes of sustained load — fourteen calls, several of
them 6–11 minute disk-streamed MoE generations — and then, during another long
generation, the model backend **crashed or became unreachable**:

1. the in-flight stream was cut (`peer closed … incomplete chunked read`), then
2. every following request got an **immediate 502** (backend gone).

Likely causes to investigate on the Vaayu side:
- **Backend process crash / OOM** during a long disk-streamed MoE generation
  (144 GB model, expert bytes streamed per chunk) — memory pressure, a worker
  dying, or an unhandled exception mid-decode.
- **Reverse-proxy / tunnel front-end losing the upstream** — if something (a
  proxy, the Tailscale-side listener) sits in front of the model server and
  returns 502 when the upstream socket is gone, the 502 cascade is the proxy
  reporting a dead backend. Confirm whether the model process itself restarted.
- **No auto-restart / health recovery** — once down, it stayed down for all 8
  retries; nothing brought the backend back within the run.

### What would confirm it on your side

- The model server's own logs around the crash: an OOM kill, a traceback, a
  worker exit, or a restart, at the timestamp of request #15.
- Whether the process serving `/v1/chat/completions` was still alive while the
  502s were being returned (if it died, that is the bug; if it was alive, the
  502 is a routing/proxy fault).
- Resident memory during a long MoE generation vs. available RAM.

### What would fix / mitigate it

- Make a long generation **survivable** — bound/stream memory so a multi-minute
  MoE decode cannot OOM the process; handle a decode exception without killing
  the worker.
- **Auto-restart / health-check** the backend so a crash does not leave the
  endpoint returning 502 to every subsequent request.
- The doc you sent (§6) already names the real lever for this workload: **serve a
  faster model** (Qwen3-Next-80B-A3B int4, or gpt-oss-20b) instead of the 144 GB
  disk-streamed MiniMax-M2.7. Faster generations = shorter streams = far less
  time in the state that triggers the crash, and much shorter runs overall
  (MiniMax ran ~2 tok/s here; a full agent run is dozens of multi-minute calls).

---

## 3. What this is NOT

- **Not a Rudra request bug.** The same request shapes worked 14 times this run.
- **Not the transport (ngrok) problem anymore.** Tailscale held multi-minute
  streams fine; the failure is the backend itself returning 502.
- **Not mid-stream retryable.** A stream cut mid-decode cannot be replayed (the
  partial output is already consumed), so Rudra does not retry it — by design.
  The fix must keep the backend from dying, not ask the client to retry a
  half-finished stream.

---

## 4. How it surfaces in Rudra (context, not a request)

- A mid-stream disconnect (`RemoteProtocolError`) is treated as a failed turn and
  the task is left pending; the subsequent instant 502s exhaust the fix-loop's
  retries and the run stops. Files already written (`models.py`,
  `test_models.py`) stay on disk; Rudra can resume the remaining tasks with
  `rudra --continue` once the endpoint is healthy.
- Rudra's streamed-call inactivity bound was raised to 900 s for this run, so a
  long prefill no longer trips a client-side timeout — the only thing that ended
  the run was the server's 502 cascade.

---

## 5. Summary

| | |
|---|---|
| Tool calling (OpenAI `tool_calls`) | ✅ working |
| Tailscale transport, multi-minute streams | ✅ working (350–674 s calls held) |
| Full pipeline: plan → write code → test | ✅ working; correct code produced |
| **Backend stays up under a long multi-call session** | ❌ **crashes / returns instant 502 cascade after sustained load** |
| Fix location | **Vaayu server** (crash/OOM recovery, backend health, and/or a faster model) |
| Rudra changes needed | none to complete the run — resume with `--continue` once the endpoint is healthy |

**Bottom line for Vaayu:** one task away from a complete end-to-end run. The
blocker is the model backend going down mid-session (instant, persistent 502s
after a `peer closed` on a long generation). Keep the backend alive through long
MoE generations — or serve a faster model — and run D should complete.
