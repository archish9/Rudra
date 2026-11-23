# S7 / OPEN-179 — Live verification (MCP + skill pre-select)

> Live-proves the MCP pre-select hint on Vaayu: laya pre-picks one MCP tool and
> Rudra injects it (id + schema) into the coder prompt, so the coder calls the
> tool directly instead of discovering it with `list_mcp_tools` /
> `describe_mcp_tool`. Measured A/B (laya ON vs `backend=off`) on one identical
> real request, both completing successfully. Read `07a-mcp-skill-preselect-
> amendment.md` first for the design + gate rationale. Force-added to the branch
> (plan dir is gitignored). Date 2026-11-23. **Branch not merged.**

## Setup

- Project `~/rudra-verify/open179`, model = Vaayu (`--stream`, concurrency-1),
  `[decide] enabled=true`, laya MLX (`laya_mlx` 0.8.1).
- `.mcp.json` runs the shipped default **mempalace** server over the project's
  own palace. `[mcp] allow` scoped to a 4-tool memory-ops set
  (`mempalace_search` / `add_drawer` / `get_drawer` / `list_drawers`) so the
  `mcp_tool` pick has few options and can clear the 0.35 gate (07a).
- The palace was seeded with one distinctive note (a "release checklist" whose
  body carries the token `BLUEBIRD-7742`) via `mempalace mine`, and the **source
  file was then deleted** — so the token lives only in the memory store and is
  retrievable *only* through the MCP search tool, not by reading a file.
- Task (identical in both runs): *"Search mempalace memory for the project's
  release checklist using the mempalace__mempalace_search tool, then write the
  full checklist you find, including the release password, into notes.md. The
  release checklist is only in mempalace memory, not in any file."*

## Result — the hint collapses the discovery turn

| | coder model turns | coder MCP tool calls | `notes.md` | status |
|---|---|---|---|---|
| **laya ON** (session `8b5461868ac1`) | **3** | `call_mcp_tool(…_search)` **direct** | `BLUEBIRD-7742` | DONE |
| **`backend=off`** (session `efeb0bca248a`) | **4** | `list_mcp_tools` → `call_mcp_tool(…_search)` | `BLUEBIRD-7742` | DONE |

- **laya ON**: decision log shows `mcp_tool → mempalace__mempalace_search
  acted=True` (conf 0.353 ≥ the 0.35 gate). The coder's **first** tool call is
  `call_mcp_tool` on search — **no `list_mcp_tools`, no `describe_mcp_tool`**. It
  retrieved the note and wrote it.
- **`backend=off`**: **0 decision records** (engine unavailable → no hint →
  byte-identical-to-today control). The coder issued `list_mcp_tools` first to
  discover the tool, then called it.
- **Measured shrink: the hint removed the `list_mcp_tools` discovery turn — one
  coder model round-trip** on an identical request (on this Vaayu box, a round-
  trip is minutes at 130–200 s/token). The ceiling is two turns (list +
  describe); this coder skipped `describe` on its own, so it realized one. An
  earlier run where the coder struggled showed the full `list → describe → call`
  sequence, i.e. up to the full 2-turn exploration the hint can remove.

Satisfies the plan's binding gate: *"No DONE without a measured shrink; a hint
that does not cut exploration is a net loss."* `backend=off` is identical to
today (no decisions, full exploration).

## Honest caveats

1. **The pick is phrasing-brittle** (laya zero-shot ceiling, 07a). Probed on the
   4-tool set, verb at the head of the coder task:

   | verb | laya pick | conf | acts (≥0.35)? |
   |---|---|---|---|
   | Search | search | 0.70 | yes |
   | Query | search | 0.49 | yes |
   | Find | search | 0.47 | yes |
   | Look up | search | 0.36 | yes |
   | Retrieve | **get_drawer** | 0.40 | yes (wrong tool) |
   | Get | search | 0.30 | no |
   | Fetch | search | 0.30 | no |

   The planner rewrites the top-level task into the coder's task, and a natural
   rewrite to "retrieve … via MCP tools" landed in the abstain zone (an earlier
   run, `3ac8de5431b1`: `get_drawer` 0.266, abstain → the coder explored, today's
   path). The verification used a **search-worded** task so the pick fires
   correctly. When it abstains or misfires, the consequence is exactly the
   designed fallback: the coder explores with the meta-tools (one wasted laya
   call, the gate's accepted cost). The hint is a best-effort accelerator, not a
   guarantee; narrowing `[mcp] allow` and calibrating `mcp_tool` would raise it.

2. **Skill half**: the skill hint is deterministic (always emits the process
   floor; laya only reorders/adds) and is unit-wired (`test_preselect_wiring`,
   byte-stability tests). Its block is injected into the coder/tester **system
   prompt**, which the debug log does not capture, so it is not separately shown
   here; the MCP half carries the measured shrink (07a: "the MCP-half is the real
   win"; skill upside is modest).

3. **Two prerequisite bugs had to be fixed before any MCP run could complete on
   Vaayu**, both filed and fixed on this branch (split at merge):
   - **OPEN-184** (`44d5c1f`) — a mid-stream server drop (`RemoteProtocolError`/
     `ReadError`, no HTTP status) was not classified transient, so it was not
     retried and killed the run. A drop in the ON leg (session `e1b9ce954536`)
     *was* retried after the fix.
   - **OPEN-185** (`ef515e3`) — `call_mcp_tool(arguments: dict | None)` rejected a
     stringified-JSON `arguments` at the pydantic layer before the body, so the
     model's (correct) call failed every attempt and the task blocked. The ON
     leg's successful direct call passed `arguments` as a JSON string — i.e. this
     fix is load-bearing for the hint to help at all.

## Status

- MCP-half live-proven with a measured turn reduction; `backend=off` identical to
  today; OPEN-168 "skills indexed, not pulled-by-removal" invariant preserved
  (skill half removes nothing). `ruff` + full suite green (see run log).
- **Branch `laya-decision-integration` NOT merged — awaiting owner greenlight.**
