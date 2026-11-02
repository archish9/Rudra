# Vaayu — backend stalls and dies under sustained generation load

**Audience:** the Vaayu server team.
**Status:** the one open blocker for the Vaayu ↔ Rudra integration. Everything on the
wire protocol works; this is purely a **backend liveness / stability** problem.
**Endpoint:** `https://a.taila2bfc0.ts.net/v1` (Tailscale) · model `vaayu`
(MiniMax-M2.7) · `max_seq_len` 16384.
**Dates observed:** 2026-10-28 (run D) and 2026-11-01/02 (run E attempts).

This document is **self-contained** — you do not need the client's source. It
describes one problem that shows up in three surfaces, with exact evidence, how to
reproduce it, the likely causes, and what would fix it.

---

## 0. TL;DR

Vaayu serves cheap requests (`GET /v1/models`, a single short chat) perfectly, but
**cannot sustain a real generation stream**. Under a multi-minute generation the
backend **emits a handful of chunks and then freezes** — the TCP connection stays
open but no further tokens arrive for 300 s+ — and under sustained multi-call load
the backend goes further and becomes **completely unresponsive** (even `GET
/v1/models` returns 0 bytes), sometimes after a burst of instant `502`s.

The model process appears to **hang or crash mid-decode and not recover**. This is
a server-side stability bug, not a protocol or client problem: the exact same
request shapes succeed when the backend happens to stay alive (a full agent run
completed on 2026-10-28, see §5).

---

## 1. Surface A — generation stalls mid-stream (the clearest evidence)

A client sent four planner generation requests (2026-11-02). **Every one received a
few streaming chunks and then stopped producing content for 300 s**, while the
connection stayed alive at the TCP layer. Exact error objects from the client's
call log (the client aborts a stream that goes silent for 300 s):

```
model_call role=planner seconds=328.49  StreamChunkTimeoutError('No streaming chunk received for 300.0s (model=vaayu, chunks_received=10). The connection may be alive at the TCP layer but is not producing content.')
model_call role=planner seconds=308.80  StreamChunkTimeoutError('No streaming chunk received for 300.0s (model=vaayu, chunks_received=11). ...')
model_call role=planner seconds=309.28  StreamChunkTimeoutError('No streaming chunk received for 300.0s (model=vaayu, chunks_received=12). ...')
model_call role=planner seconds=308.65  StreamChunkTimeoutError('No streaming chunk received for 300.0s (model=vaayu, chunks_received=10). ...')
```

**Read `chunks_received`: 10, 11, 12, 10 — non-zero.** The backend **did start
generating**, sent ~10–12 chunks, and then **froze mid-decode**. It is not a failed
start (that would be 0) and not a slow start (300 s of total silence after real
output is a hang, not latency). The client retried 4× (3 retries + the original);
all four stalled identically; nothing was written.

## 2. Surface B — backend fully unresponsive (same session, minutes earlier)

Before the stalls above, direct `curl` probes to the **trivial, instant** endpoint
returned **0 bytes**:

```
$ curl -m 20 https://a.taila2bfc0.ts.net/v1/models
curl: (28) Operation timed out after 20011 ms with 0 bytes received   # http=000

$ curl -m 8 https://a.taila2bfc0.ts.net/           # raw connect test
connect=0.069s  http=000  (0 bytes)                # TCP connects, HTTP never answers
```

**The TCP handshake succeeds in 69 ms** (the Tailscale front door is up) **but no
HTTP bytes ever come back.** So the tunnel/listener is alive and the **model backend
behind it is hung or dead**. Minutes later the same `GET /v1/models` answered
normally in **0.2 s** — i.e. the backend recovered on its own, then stalled again
on the next real generation (§1). It flaps between *serving pings* and *unable to
generate*.

Two more client-visible variants of the same unavailability, from earlier run-E
attempts:

```
attempt 1:  ReadError                                   # stream dropped mid-read
attempt 2:  APITimeoutError ×3 (300s each) → APIConnectionError, gave up
```

## 3. Surface C — 502 cascade after sustained load (run D, 2026-10-28)

On 2026-10-28 a full agent run handled ~50 min of load (14 model calls, several
6–11 min disk-streamed MoE generations) and then, during another long generation,
the backend died:

| # | result | wall time |
|---|---|---|
| 1–14 | ok (incl. 350 s, 674 s, 629 s, 557 s streams) | — |
| 15 | **`RemoteProtocolError`: peer closed connection without sending complete message body** (mid-decode) | 113.8 s |
| 16–23 | **`502`** each | **0.0–0.1 s** each |

The `502`s come back in **~0.0–0.1 s** — instant, and for *every* subsequent
request. A busy server takes seconds; an **instant, persistent 502 means the
request never reached a working backend**. The preceding `RemoteProtocolError`
(server closing a live stream without finishing the body) is consistent with the
**model process dying mid-generation**, after which the front door returns 502 to
everything until it (sometimes) recovers.

## 4. One root cause, three surfaces

All three are the same failure at different stages:

- **Surface A (stall):** backend decodes ~10–12 chunks, then hangs — the decode
  loop blocks and stops emitting, connection kept open.
- **Surface C (crash → 502):** the hang becomes a dead/exited worker; the front
  door returns instant 502s.
- **Surface B (0 bytes):** the backend is wedged hard enough that even the cheap
  `/v1/models` handler cannot respond, though TCP still connects.

The common thread: **a long MiniMax-M2.7 MoE generation wedges or kills the model
process**, and the endpoint does not reliably auto-recover.

## 5. What is NOT the problem (so you can rule these out)

Everything on the wire is correct — proven by a **complete, successful run** on
2026-10-28 (`bfb5c7b5da07`) and a second one on 2026-11-01 (`bd5dc94e4554`, 2/2
tasks, 14 tests passing) when the backend stayed alive:

| Check | Result |
|---|---|
| `GET /v1/models` | `200`, advertises `vaayu`, `max_seq_len 16384`, batching hint — correct |
| `session_id` requirement | enforced (`400 REQUIRE_SESSION_ID` without it); client sends it — correct |
| Plain chat completion | `200`, correct content + `usage`, `session_id` echoed |
| Streaming (short) | works; chunks + usage correct |
| **Tool calling** | **works** — proper OpenAI `tool_calls` array, `finish_reason:"tool_calls"`, no `<minimax:tool_call>` leakage (this was fixed; confirmed live) |
| Long streams **when the backend survives** | 350 s / 557 s / 629 s / 674 s streams have all completed cleanly |

So this is **not** a request-shape, protocol, tool-call, or client bug. It is
**backend liveness under sustained generation load.**

## 6. Reproduce

Short requests always pass:

```bash
curl -s https://a.taila2bfc0.ts.net/v1/models        # 200, instant
curl -s https://a.taila2bfc0.ts.net/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"vaayu","session_id":"probe","max_tokens":8,
       "messages":[{"role":"user","content":"say ok"}]}'     # 200
```

The failure needs a **long generation** (the model's slow MoE disk-streaming is the
trigger). Either:

- send a request with a large `max_tokens` / a prompt that forces a multi-minute
  answer, streamed, and watch the stream: it emits a few chunks then goes silent
  for minutes; **or**
- drive sustained load — a dozen+ multi-minute streamed generations back to back —
  and watch for the `RemoteProtocolError` → instant-`502` cascade in §3.

Capture, on the **server** side, at the moment the client sees the stall/502:
whether the model process is still alive, its resident memory, and its last log
lines.

## 7. Likely causes to investigate (server side)

- **Worker hang mid-decode** — the MoE decode loop blocks (stuck reading expert
  bytes off disk, a lock, a stuck I/O) and stops emitting tokens while holding the
  connection open. Matches Surface A exactly (`chunks_received` 10–12 then silence).
- **Process crash / OOM** during a long disk-streamed MoE generation (144 GB model,
  expert bytes streamed per chunk). Matches Surface C (`RemoteProtocolError` →
  instant 502s = dead upstream).
- **No health-check / auto-restart** — once wedged, it stays wedged for every
  subsequent request (all 8 of run D's retries got 502; §1's 4 retries all stalled)
  until it happens to recover.
- **Reverse-proxy / Tailscale listener outliving the upstream** — the front door
  accepts TCP and returns 502 or 0 bytes because the model socket behind it is
  gone. Confirm whether the model process itself restarted.

## 8. What would confirm it on your side

- Model-server logs around a stall/crash: a traceback, an OOM kill, a worker exit,
  or a decode that stopped progressing.
- Whether the process serving `/v1/chat/completions` was **alive** while the client
  saw the stall / 0-byte / 502 (if dead → crash; if alive but not emitting → decode
  hang).
- Resident memory during a long MoE generation vs. available RAM.

## 9. What would fix / mitigate it

1. **Make a long generation survivable.** Bound/stream memory so a multi-minute MoE
   decode cannot OOM the worker; add a watchdog that detects a decode that has
   stopped progressing and fails the request instead of hanging the worker; handle
   a decode exception without killing the process.
2. **Health-check + auto-restart** the backend so a crash/hang does not leave the
   endpoint returning 502/0-bytes to every subsequent request.
3. **Serve a faster model.** The real lever for this workload: a faster model
   (e.g. Qwen3-Next-80B-A3B int4, or gpt-oss-20b) instead of the 144 GB
   disk-streamed MiniMax-M2.7. MiniMax ran ~2 tok/s here; a full agent run is dozens
   of multi-minute calls, which is exactly the sustained load that triggers the
   crash. Faster generations = shorter streams = far less time in the state that
   wedges the worker, and much shorter runs overall.

## 10. Impact on the client (context, not a request)

The client is an autonomous coding agent. It drives the model over the OpenAI
chat-completions protocol, retries a failed call up to 4×, and **writes nothing on
failure** — so a stalled/crashed backend simply ends the run cleanly with a provider
error (`StreamChunkTimeoutError`, `APIConnectionError`, `502`). A mid-stream stall
cannot be safely replayed (the partial output is already consumed), so the client
does **not** retry a half-finished stream — the fix must keep the backend from
hanging/dying, not ask the client to retry. Files already written stay on disk and
the client can resume once the endpoint is healthy.

**Bottom line:** the integration is complete and correct on the wire. The single
blocker is that **Vaayu's model backend hangs mid-decode (~10–12 chunks in, then
300 s of silence) and crashes under sustained load (instant-502 cascade / 0-byte
unresponsiveness), without reliable auto-recovery.** Keep the backend alive through
long MoE generations — or serve a faster model — and the integration runs
end-to-end.
