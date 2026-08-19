# MCP — giving Rudra tools it doesn't ship with

**In one sentence:** MCP lets you plug an outside program into Rudra so the agent can
use *its* tools — a design-system checker, a database browser, your company's internal
API — without you writing any code.

Rudra ships with **no MCP server turned on**. Nothing in this guide happens until you
add one.

**Contents**

1. [What MCP actually is](#1-what-mcp-actually-is)
2. [Five-minute quickstart](#2-five-minute-quickstart)
3. [Adding a server](#3-adding-a-server)
4. [What the agent sees](#4-what-the-agent-sees)
5. [Controlling what a server may do](#5-controlling-what-a-server-may-do)
6. [All the settings](#6-all-the-settings)
7. [Which agents can use MCP](#7-which-agents-can-use-mcp)
8. [When something goes wrong](#8-when-something-goes-wrong)
9. [A full worked example: kala](#9-a-full-worked-example-kala)

---

## 1. What MCP actually is

MCP (Model Context Protocol) is a standard way for a program to say *"here are the
tools I offer"* — and for an agent like Rudra to call them.

A **server** is just a program. It might be a published package you run with `npx`, a
Python script on your disk, or a URL someone hosts for you. Rudra starts it, asks what
it can do, and calls it when the task needs it.

Think of it as installing an app for your agent.

**You already know the format.** Rudra reads `.mcp.json`, the same file Claude Code
uses — so any server config you already have works here unchanged, copy and paste.

---

## 2. Five-minute quickstart

We'll add [kala](https://github.com/archish9/Kala), a real server that checks frontend
code against a design system. You need **Node 20 or newer** and nothing else.

**Step 1 — make a scratch project.** Do not use a project you care about yet:

```bash
mkdir ~/mcp-demo && cd ~/mcp-demo
rudra init
```

**Step 2 — add the server:**

```bash
rudra mcp add kala -- npx -y -p kala-mcp@0.3.0 kala-mcp
```

Everything after `--` is the command Rudra will run to start the server.

**Step 3 — check it works** *before* involving a model:

```bash
rudra mcp test
```

```
                                 rudra mcp test
┏━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Server ┃ Status ┃ Detail                                                     ┃
┡━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
┃ kala   ┃ ok     ┃ 8 tools: system_status, verify, explain, surface_brief,    ┃
┃        ┃        ┃ guide, inspect, critique, system_bootstrap                 ┃
└────────┴────────┴────────────────────────────────────────────────────────────┘
```

`ok` means Rudra started it, it answered, and the agent can now use those eight tools.

**Step 4 — use it.** Just talk normally; you never name tools yourself:

```bash
rudra "set up a design system for a calm invoicing tool, then build me a settings page that follows it"
```

Rudra will ask your approval before each MCP call, showing you which server, which
tool, and with what arguments.

That's it. `rudra mcp list` shows what you've added, `rudra mcp remove kala` takes it
away again.

---

## 3. Adding a server

### The easy way

```bash
# A local program (most servers)
rudra mcp add <name> -- <command> [args...]

# A hosted one
rudra mcp add <name> --url https://mcp.example.com/mcp
```

The **name** is yours to choose. It becomes the prefix on that server's tools, so keep
it short: `kala`, `db`, `jira`.

### The file it writes

`.mcp.json`, in your project root:

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

Edit it by hand if you prefer — Rudra reads whatever is there. Rules:

- `command` (+ optional `args`, `env`) → a local program, started by Rudra.
- `url` (+ optional `headers`) → a hosted server.
- `transport` is worked out for you; set it explicitly (`stdio`, `http`, `sse`) only if
  you need to.

> **Local servers are the well-trodden path.** Hosted (`url`) servers are supported and
> unit-tested, but have not been exercised end to end. If one misbehaves, that's worth
> reporting.

> **Don't put `__` in a server name.** Two underscores separate the server from the tool
> in a tool's full name, so `my__server` would be ambiguous. Rudra refuses it with an
> error rather than guessing.

### Checking your setup

```bash
rudra mcp list     # what's configured, and whether Rudra will load it
rudra mcp test     # actually start each one and list its tools
rudra doctor       # among everything else: does each server's command exist?
```

`rudra doctor` catches the most common mistake — a command that isn't installed:

```
│ .mcp.json   │ ok      │ kala                              │
│ mcp: kala   │ missing │ 'npx' is not on PATH              │
```

---

## 4. What the agent sees

This part is unusual, and it's why MCP stays cheap in Rudra.

Most tools load *every* tool from *every* server into the model's prompt. Ten servers
with ten tools each means a hundred tool descriptions paid for on every single message,
used or not. On a local 32B model that alone can fill the context.

Rudra gives the model **three tools, no matter how many servers you add**:

| Tool | What it does |
|---|---|
| `list_mcp_tools` | "What's available?" — names and one-line descriptions |
| `describe_mcp_tool` | "How do I call *this* one?" — the full argument list, on demand |
| `call_mcp_tool` | "Run it." |

So the cost of MCP is one line naming your servers, plus those three. **Adding a fourth
server does not make the prompt bigger.**

Tools are named **`server__tool`** — your server name, two underscores, the tool's own
name. `kala__verify`, `db__query`. That's the name you'll see in approval prompts and
the name you use in permission rules.

**Servers start only when used.** A server you configured but the agent never calls is
never even launched.

---

## 5. Controlling what a server may do

An MCP server is a separate program. Rudra cannot confine it the way it confines file
writes — it can write files, reach the network, do whatever its author built. So Rudra
treats MCP calls exactly like shell commands.

| How you run Rudra | What happens when the agent wants an MCP tool |
|---|---|
| **Default** (`ask` mode) | You're asked, and shown the server, tool, and arguments |
| **`--auto`** | **Refused** — unless you add `--allow-mcp` |
| **`--plan`** | Refused. Plan mode changes nothing |

An approval looks like this:

```
╭─ approval required ────────────────────────────────╮
│ MCP  kala → system_bootstrap                       │
╰────────────────────────────────────────────────────╯
  {
    "dir": "/home/you/mcp-demo",
    "brief": "a calm invoicing tool for freelancers",
    "choice": 1
  }
```

Looking things up — `list_mcp_tools` and `describe_mcp_tool` — is never gated. Those
only read a server's description of itself.

### Saying yes once, permanently

In `.rudra/config.toml`:

```toml
[permissions]
# Always allow this one tool, even unattended
allow = ["call_mcp_tool:kala__system_status"]

# Never allow this one, in any mode
deny  = ["call_mcp_tool:*__system_bootstrap"]
```

`*` is a wildcard. **`deny` always beats `allow`.** Naming a specific tool in `allow`
*is* your opt-in for it — you don't also need `--allow-mcp` for that one.

### Unattended runs

```bash
rudra --auto --allow-shell --allow-mcp "check the settings page against our design system and fix what it flags"
```

Every decision is recorded in `.rudra/run/logs/permissions.jsonl`, so you can see
afterwards exactly which MCP calls ran.

---

## 6. All the settings

In `.rudra/config.toml`. Every one is optional:

```toml
[mcp]
enabled     = true       # false switches MCP off completely
mcp_in_auto = false      # may unattended runs call MCP? (same as --allow-mcp)
timeout     = 60         # seconds to wait for one call before giving up

# Which tools agents may see and use. Patterns are server__tool, * is a wildcard.
allow = []               # empty = all of them
deny  = []               # deny wins over allow

# Tools the reviewer subagent may see. See section 7.
readonly = []

# Servers in .mcp.json to leave switched off without deleting them
disabled_servers = []
```

**Two different things, easy to confuse:**

- `[mcp] allow`/`deny` decide what an agent can **see and name at all**.
- `[permissions] allow`/`deny` decide whether a call is **permitted to run**.

The permission system always applies. `[mcp]` settings never grant anything on their
own.

---

## 7. Which agents can use MCP

Rudra splits work between subagents. They don't all get MCP:

| Agent | MCP access | Why |
|---|---|---|
| **coder** | all servers | It writes the code, so it needs the tools |
| **general-purpose** | all servers | Answers open questions about your project |
| **reviewer** | only ids in `[mcp] readonly` | It reviews; it must not be able to change anything |
| **tester** | none | It runs your test suite; that's a command, not an MCP call |
| **planner** | none | Deciding *what* to build shouldn't depend on a network service |

The reviewer's restriction is structural, not a polite instruction. A tool outside
`readonly` isn't merely denied to it — the reviewer never sees the tool exists:

```toml
[mcp]
readonly = ["kala__verify", "kala__system_status", "kala__explain", "kala__guide"]
```

With that set, the reviewer can check your code against the design system and cannot
rewrite the design system.

---

## 8. When something goes wrong

Rudra never crashes a run because of MCP. A broken server is reported and the run
carries on.

| What you see | What it means | Fix |
|---|---|---|
| `mcp: kala │ missing │ 'npx' is not on PATH` (in `doctor`) | The program isn't installed | Install it — for kala, install Node 20+ |
| `MCP server 'x' is unavailable: FileNotFoundError` | Same thing, hit at run time | As above. Other servers keep working |
| `Permission denied: MCP tools are disabled in unattended ('auto') mode` | You used `--auto` without opting in | Add `--allow-mcp`, or `[mcp] mcp_in_auto = true` |
| `'x__y' is not available to you` | That tool is filtered out by `[mcp] allow`/`deny`/`readonly` | Widen the pattern, or use a different tool |
| `MCP call 'x__y' exceeded the 60s [mcp] timeout` | The server took too long | Raise `[mcp] timeout` |
| `Warning: MCP disabled — .mcp.json could not be read as JSON` | Typo in the file | Fix the JSON. The run continues without MCP |
| `No MCP servers configured` | There's no `.mcp.json`, or it's empty | `rudra mcp add ...` |

A server that fails once is remembered for the rest of the run, so the agent doesn't
retry a program that isn't there.

**With no `.mcp.json` at all, Rudra behaves exactly as it always did.** MCP is additive.

---

## 9. A full worked example: kala

[kala](https://github.com/archish9/Kala) holds a project's design system as data and
checks your frontend code against it.

> **kala is a separate project by the same author as Rudra**, licensed Apache-2.0. It
> is **not bundled with Rudra, not a dependency, and not supported by Rudra.** It is
> used here because it's a real server doing real work. You install it yourself.

### ⚠️ Read this before pointing it at real code

Six of kala's eight tools only read. Two do not:

- **`system_bootstrap` writes files** — a Tailwind config, a stylesheet, and a
  `design.lock.json`. Called with just a brief it only *proposes* and writes nothing;
  called again with `choice` it writes. Its `force` option **overwrites an existing
  design system** — palette, type scale, spacing scale.
- **`inspect` launches a real browser**, opens network connections to the URL it's
  given, and writes screenshots outside your project.

Try it on a scratch project first. If you want to be certain it can never overwrite
your design tokens:

```toml
[permissions]
deny = ["call_mcp_tool:*__system_bootstrap"]
```

### What a session looks like

Setting up a design system, one call at a time (Rudra asks before each):

```
kala__system_status   { dir }
  → { "hasLock": false, "degraded": [ { "code": "NO_DESIGN_SOURCE" } ] }
     …no design system here yet.

kala__system_bootstrap { dir, brief: "a calm invoicing tool for freelancers" }
  → { "mode": "proposed", "proposals": [ { "id": "warm-utility", … }, … ] }
     …three options. Nothing written.

kala__system_bootstrap { dir, brief, choice: 1 }
  → { "mode": "applied", "system": "warm-utility",
      "files": [ "tailwind.config.mjs", "src/styles/globals.css", "design.lock.json" ] }

kala__system_status   { dir }
  → { "hasLock": true, "space": [0,4,8,12,16,24,32,48,64], … }
     …now it exists.
```

From there, `kala__verify` checks the files Rudra writes against that system, and its
findings feed back into Rudra's normal fix loop — the same path a failing test takes.

*(Transcript captured from a live `kala-mcp@0.3.0` server, tool by tool. A full
model-driven walkthrough will replace it once one has been recorded.)*

### Running the same server twice

Useful for comparing two projects, and a good way to see the naming work:

```json
{
  "mcpServers": {
    "kala":     { "command": "npx", "args": ["-y", "-p", "kala-mcp@0.3.0", "kala-mcp"] },
    "kala-old": { "command": "npx", "args": ["-y", "-p", "kala-mcp@0.2.0", "kala-mcp"] }
  }
}
```

`kala__verify` and `kala-old__verify` are different tools on different processes, and
Rudra keeps them apart.

---

**See also:** [Permissions](09-permissions.md) · [Configuration](02-configuration.md) ·
[CLI Reference](04-cli-reference.md) · [Tools](11-tools.md)
