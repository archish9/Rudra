# MCP

MCP (Model Context Protocol) servers give Rudra tools it does not ship: a design-system
checker, a search index, a company's internal API. A server is a separate program that
Rudra starts, asks what it offers, and calls on the agent's behalf.

Rudra ships **no MCP server enabled**. Nothing here happens until you configure one.

---

## 1. How MCP works in Rudra

Most MCP clients load every tool from every configured server into the model's prompt.
A dozen servers is then a dozen schemas the model pays for on every single call,
whether it uses them or not — which a 32B context cannot afford.

Rudra does it the other way round. The model gets **three fixed tools**, no matter how
many servers you configure:

| Tool | What it does |
|---|---|
| `list_mcp_tools` | Lists tool ids and one-line descriptions. No argument schemas |
| `describe_mcp_tool` | The full argument schema for **one** tool, on request |
| `call_mcp_tool` | Runs one tool, by id, with arguments |

So the prompt cost of MCP is fixed: one line naming your servers, plus those three
schemas. Adding a fourth server does not make the prompt bigger.

Tools are named **`server__tool`** — the server name from your `.mcp.json`, two
underscores, then the tool's own name. That is what lets you run the same server twice
under different names without ambiguity, and it is why a server name may not itself
contain `__`.

A server's process is started **the first time a tool on it is actually called**. A
server you configure and never use costs nothing but its catalog line.

---

## 2. Configuring servers: `.mcp.json`

Servers live in `.mcp.json` at your project root, in the same schema Claude Code uses —
so an existing config can be pasted in unchanged.

```json
{
  "mcpServers": {
    "kala": {
      "command": "npx",
      "args": ["-y", "-p", "kala-mcp@0.3.0", "kala-mcp"]
    },
    "internal": {
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer ..." }
    }
  }
}
```

`command` means stdio (a local subprocess). `url` means HTTP or SSE. `transport` is
inferred from which one you give, and can be set explicitly (`stdio`, `http`, `sse`,
`websocket`).

**Only stdio has been proven against a real server.** The HTTP/SSE path is implemented
and unit-tested but has not been exercised end to end.

Manage the file from the CLI rather than by hand if you prefer:

```bash
rudra mcp add kala -- npx -y -p kala-mcp@0.3.0 kala-mcp
rudra mcp add internal --url https://mcp.example.com/mcp
rudra mcp list
rudra mcp test          # start each server, list what it offers
rudra mcp remove kala
```

`rudra doctor` also reports each configured server and whether its command is on `PATH`.

---

## 3. Policy: `[mcp]` in `config.toml`

`.mcp.json` holds servers and nothing else, so it stays paste-compatible. Every
Rudra-specific setting lives in `config.toml`:

```toml
[mcp]
enabled     = true       # false disables MCP entirely
mcp_in_auto = false      # may an unattended run call MCP tools? (--allow-mcp)
timeout     = 60         # seconds per MCP call
allow = []               # server__tool glob patterns; empty means all
deny  = []               # deny beats allow
readonly = []            # ids the reviewer subagent may see
disabled_servers = []    # names from .mcp.json to leave unloaded
```

`allow`/`deny` here decide what an agent can **see and name**. They are not the
permission system — that is next, and it applies regardless of what this section says.

---

## 4. Permissions

An MCP server is a separate program Rudra does not confine. It can write files, open
network connections, and do anything the tool's author implemented. So MCP calls are
gated exactly like shell commands:

| Mode | What happens to `call_mcp_tool` |
|---|---|
| `ask` (default) | You are prompted, and shown the server, the tool, and the arguments |
| `--auto` | **Denied**, unless you opt in with `--allow-mcp` or `[mcp] mcp_in_auto = true` |
| `--plan` | Denied — plan mode changes nothing |

`list_mcp_tools` and `describe_mcp_tool` are never gated: they only read a server's
description of itself.

Rules use the tool id, and work like every other permission rule:

```toml
[permissions]
allow = ["call_mcp_tool:kala__system_status"]   # this one id, even under --auto
deny  = ["call_mcp_tool:*__system_bootstrap"]   # never, in any mode
```

Naming a specific id in `allow` **is** the opt-in for that id — the same way
`allow = ["execute:pytest*"]` works for shell.

The reviewer subagent is restricted further: it sees only ids listed in
`[mcp] readonly`, so a tool that writes is not merely denied to it but invisible.

---

## 5. A worked example: kala

[kala](https://github.com/archish9/Kala) checks frontend code against a project's
design system. It is a **separate project by the same author as Rudra**, Apache-2.0
licensed. It is **not bundled with Rudra, not a dependency, and not supported by
Rudra** — it is used here because it is a real server with a real workload, and you
install it yourself.

Requirements: Node 20 or newer. No clone, no build.

```bash
cd /path/to/a/scratch/project
rudra mcp add kala -- npx -y -p kala-mcp@0.3.0 kala-mcp
rudra mcp test
```

```
                                 rudra mcp test
┏━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Server ┃ Status ┃ Detail                                                     ┃
┡━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ kala   │ ok     │ 8 tools: system_status, verify, explain, surface_brief,    │
│        │        │ guide, inspect, critique, system_bootstrap                 │
└────────┴────────┴────────────────────────────────────────────────────────────┘
```

### ⚠️ Two of kala's tools are not passive

- **`system_bootstrap` is the only kala tool that writes files.** Called with a brief
  alone it returns three proposals and writes nothing; called again with `choice` it
  writes a Tailwind config, a global stylesheet, and a `design.lock.json`. Its `force`
  option **rewrites an existing palette, type scale, and spacing scale**.
- **`inspect` launches a real browser and opens network connections** to whatever URL
  it is given, and writes screenshots outside your project.

Point it at a scratch project before pointing it at anything you care about, and
consider:

```toml
[permissions]
deny = ["call_mcp_tool:*__system_bootstrap"]
```

### What a session looks like

Asking a design question first, then building against the answer:

```
kala__system_status  →  { "hasLock": false, "degraded": [ { "code": "NO_DESIGN_SOURCE" } ] }
kala__system_bootstrap { dir, brief: "a calm invoicing tool for freelancers" }
                     →  { "mode": "proposed", "proposals": [ { "id": "warm-utility", ... } ] }
kala__system_bootstrap { dir, brief, choice: 1 }
                     →  { "mode": "applied", "system": "warm-utility",
                          "files": [ "tailwind.config.mjs", "src/styles/globals.css",
                                     "design.lock.json" ] }
kala__system_status  →  { "hasLock": true, "space": [0,4,8,12,16,24,32,48,64], ... }
```

After that, `kala__verify` checks the files Rudra writes against that system, and its
findings go back into Rudra's fix loop like any other failure report.

---

## 6. When a server is missing or broken

Rudra degrades rather than failing. With a server whose command does not exist:

- other servers still list and call normally;
- the failing server is reported once, in a sentence the model can read
  (`MCP server 'ghost' is unavailable: FileNotFoundError: ...`);
- the failure is remembered, so a retrying model does not respawn a doomed process;
- a task that needs no MCP completes exactly as it would with no `.mcp.json` at all.

A malformed `.mcp.json` prints a warning and disables MCP for the run, rather than
stopping it.

---

## 7. Which agents can use MCP

| Agent | Access |
|---|---|
| coder | every enabled server |
| general-purpose | every enabled server |
| reviewer | only ids in `[mcp] readonly` |
| tester | none |
| planner (clarify / architect / breakdown) | none |

The planner stages are excluded deliberately: deciding *what to build* should not
depend on reaching a network service.
