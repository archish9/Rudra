# Vaayu — stream-stall / stream-drop report (from Rudra live run E)

**Date:** 2026-11-08
**Endpoint:** `https://a.taila2bfc0.ts.net/v1` (Tailscale)
**Model:** `vaayu` (MiniMax-M2.7), `max_seq_len` 16384, streamed-MoE, serial
(`recommended_client_concurrency: 1`)
**Client:** Rudra (autonomous coding agent), OpenAI-wire streaming
(`POST /v1/chat/completions`, `stream: true`, `stream_options.include_usage: true`,
`extra_body.session_id` present)

This is a **Vaayu-side** report. One other issue surfaced in the same run was a
Rudra client bug (a `400` on one call path that omitted `session_id`) — it is
**ours, already being fixed**, and is described at the end only so you can ignore
that `400` line in your logs. Everything above it is for you.

---

## 1. What run E was (context)

Run E was a Rudra end-to-end agent run against Vaayu: a small FastAPI "todo
service" project, pre-written by hand and committed, that Rudra was asked to
**modify** (fix one bug, add two methods, extend the tests). Rudra drives the
model in **many sequential streamed calls** per run — a planner stage, then a
coder, then a test gate, then a reviewer. Each call is a long streamed
completion. Run E made ~20 such calls across two phases (E1, then a `--continue`
resume E2).

**The run as a whole succeeded** (final: 2/2 tasks done, 7 tests pass). It is a
realistic sustained-load client: long prompts (3k–6k input tokens), long
streamed generations, back to back, over tens of minutes. That load is what
exposed the problems below.

**Normal-call latency, for calibration (all succeeded):** individual streamed
completions took **48s – 340s** each. Example planner calls: 90.8s, 239.9s,
175.8s, 203.8s, **339.9s**. So healthy long generations already run up to ~5.5
minutes. The failures below are calls that went *past* that into a full stall.

---

## 2. The problems Vaayu needs to fix

### 2.1 Stream stalls mid-generation — starts, emits ~10 chunks, then goes silent (PRIMARY)

On a long call, Vaayu **began streaming, delivered ~10 chunks, then stopped
producing any further chunk while the TCP connection stayed open.** The client
gave up after 300s of silence.

Exact client error (Rudra run E1, `planner` role, call died at 328s wall):

```
StreamChunkTimeoutError('No streaming chunk received for 300.0s
 (model=vaayu, chunks_received=10). The connection may be alive at the TCP
 layer but is not producing content.')
```

Read literally: **10 chunks arrived, then nothing for 5 full minutes, with the
socket still alive.** This is not a slow stream (slow streams at 340s completed
fine) — it is a **hang**: generation appears to begin and then wedge.

**Why this is the important one.** A dead stall with a live socket is invisible
to TCP keepalive — only an application-level chunk timeout catches it. It wastes
the full timeout window (here 300s) and buys nothing. For an agent that makes
hundreds of calls per run, one wedged call per run is a recurring, expensive
failure.

**Likely area to investigate (your domain, our guess from the model card):** the
`/v1/models` card says batching is *"streamed MoE: a batch streams the per-chunk
routing union off disk (~N × expert bytes); measured a net loss"*. A per-chunk
disk read of expert weights that occasionally blocks (I/O stall, page cache
eviction, a slow expert-shard read) would present exactly as "N chunks then
silence." Worth checking: does the stall correlate with a particular expert
routing, a cold shard, or disk/page-cache pressure after sustained streaming?

### 2.2 Hard connection drop mid-stream (`ReadError` / peer closed)

On a different long call, the connection was **closed by the server mid-stream**
before the completion finished:

```
httpx.ReadError('')      # peer closed the connection during the streamed read
```

(Rudra run E1, `coder` role, call died at 75.2s after a few chunks.) This is the
same family as a previously observed failure on this endpoint: after ~50 minutes
of sustained long MoE streams the backend has been seen to **crash and return
instant `502`s to everything** for a period. Run E did **not** trigger the full
`502` cascade this time, but it did hit a single hard drop — consistent with the
backend being fragile under sustained long-stream load.

**What to check:** server-side logs at the moment of the drop — did a worker
crash / OOM / get killed? Is there a request or wall-time cap that closes the
socket without sending a terminating chunk or an error body? A clean error
response (even a 5xx with a body) is far better for clients than a bare socket
close.

### 2.3 (Secondary) Overall latency is very high

Not a bug per se, but context: 48–340s per call, serial (concurrency 1), makes a
multi-call agent run take tens of minutes of model time. The stalls in 2.1 are
much worse *because* the baseline is already slow — the client cannot tell "slow
but alive" from "wedged" except by a long timeout. Any reduction in per-chunk
latency, or an explicit server-side heartbeat/keepalive chunk during long
expert-routing gaps, would both speed runs and make stalls detectable sooner.

---

## 3. What would make this debuggable from the server side

1. **Emit a heartbeat/keepalive SSE chunk** during long internal gaps (e.g. while
   streaming expert weights off disk). A zero-content keepalive every few seconds
   lets a client distinguish "still working" from "wedged" and keeps the 2.1
   stall from looking identical to healthy slowness.
2. **On an internal error or cap, send a terminating error frame**, not a bare
   socket close. The `ReadError('')` in 2.2 carries no information; an OpenAI-wire
   error object (or at least an SSE `error` event) would tell the client what
   happened.
3. **Log the stall path.** For 2.1, server logs around a request that delivered N
   tokens then stopped would show whether the hang is in routing, a disk read, or
   the sampler.

---

## 4. Reproduction shape

- Point any OpenAI-wire streaming client at `.../v1/chat/completions`, `stream:true`,
  with `session_id` in the body (required — see §5).
- Send **large prompts (3k–6k input tokens) and request long generations**, many
  calls **back to back, serially**, for 20–40+ minutes.
- Set an application-level stream-chunk timeout (Rudra used 300s). Watch for: a
  call that emits a handful of chunks then goes silent (2.1), or a bare connection
  close mid-stream (2.2). Both appeared within a single ~20-call run under this
  load.

The exact project and prompts used in run E can be supplied on request, but the
shape above is the whole trigger — sustained long serial MoE streams.

---

## 5. The one issue that is NOT yours (ignore this `400` in your logs)

You will see in your access log, from this client:

```
POST /v1/chat/completions  "HTTP/1.1 400 Bad Request"
  {"error":{"message":"this request names no session. Send `session_id` ..."}}
```

That `400` is **correct behavior on your side** — you required `session_id` and
one Rudra code path (our AGENTS.md summariser call) failed to send it. **That is
a Rudra bug, tracked as OPEN-167 and being fixed on our end.** Your
`REQUIRE_SESSION_ID` enforcement is working as intended; no action needed from
Vaayu for this line. Every *other* Rudra call in the run correctly sent
`session_id`.

---

## 6. Summary for the Vaayu team

| # | Symptom | Client evidence | Severity | Owner |
|---|---|---|---|---|
| 2.1 | Stream starts (~10 chunks) then stalls, TCP alive, no content for 300s | `StreamChunkTimeoutError … chunks_received=10` | High — recurring, wastes full timeout | **Vaayu** |
| 2.2 | Hard connection drop mid-stream; related to the known ~50-min `502` crash | `httpx.ReadError('')` | High — fragile under sustained load | **Vaayu** |
| 2.3 | Very high per-call latency (48–340s), serial | run timings | Medium — context, worsens 2.1 | Vaayu (nice-to-have) |
| 5 | `400` on one call path | `"names no session. Send session_id"` | — | **Rudra (OPEN-167), already fixing** |

The run succeeded overall thanks to client-side retry + resume, but 2.1 and 2.2
are real server-side reliability issues under the sustained long-stream load a
coding agent produces.
