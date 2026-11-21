# 16. laya — the local decision model Rudra requires

Rudra makes some of its decisions with **[laya](https://github.com/NandhaKishorM/laya)**,
a small model that runs **on your machine**. laya is **required**: Rudra will not start a
run without it. This page says what it is, why it's required, and how to install it for
your operating system.

If you hit the message *"laya is required but not installed"*, you are in the right place —
jump to [Install](#install).

---

## What laya is

Not everything an agent does between your prompt and the code is *writing*. A lot of it is
small, **typed decisions**:

- Is this request too vague to start, or clear enough to run?
- A task is blocked — which recovery should it try?
- Does this task actually need tests?
- Which of the available tools or skills fit this task?

laya answers exactly that shape of question — pick-one, a 0–N score, or yes/no — in a
single fast pass (tens of milliseconds), locally, and it reports when it is **not sure**.
It cannot write code, plans, or tool arguments; it only decides.

Rudra uses laya to make those decisions **on your side** instead of spending a full
round-trip to your main model (Vaayu, or whatever you configured) on each one. When laya
is confident it takes the fast path; when it is unsure it hands the decision back to your
model. Either way the work still happens — laya only ever *shortcuts* a path that already
works.

---

## Why it's required (and not just bundled)

laya is a hard requirement because Rudra's decision flow is built around it. But it is
**not** a normal Python dependency that `pip install rudra` pulls in automatically, for two
concrete reasons:

- The Apple-Silicon build, `laya-mlx`, pins **NumPy below 2**. If it were a core
  dependency, *every* Rudra install — on every machine — would be forced down to that
  NumPy, whether or not it ever used laya.
- The portable build, `laya[onnx]`, pulls in **PyTorch** and NVIDIA CUDA packages (well
  over a gigabyte). That has no business landing in an install that didn't ask for it.
- There is no single wheel that works everywhere: `laya-mlx` is Apple-Silicon-only, and the
  ONNX build is for everyone else.

So Rudra keeps laya out of its locked dependencies to keep the base install small and
NumPy-2-clean, and instead asks you to install the right build for **your** OS, once. That
is the tradeoff that lets laya be both required *and* cheap for people on platforms it would
otherwise bloat.

---

## Install

Install laya into the **same environment you installed Rudra into** (its virtualenv, or
wherever `rudra` runs from).

| Your machine | Command |
|---|---|
| **Apple Silicon** (M1/M2/M3/M4 Mac) | `pip install laya-mlx` |
| **Linux** (x86-64 or ARM) | `pip install 'laya[onnx]'` |
| **Windows** | `pip install 'laya[onnx]'` |
| **Intel Mac** (x86-64) | `pip install 'laya[onnx]'` |

**If you installed Rudra with `uv`**, prefix with `uv pip`:

```bash
uv pip install laya-mlx        # Apple Silicon
uv pip install 'laya[onnx]'    # everywhere else
```

**A plain `uv sync` uninstalls laya.** laya is deliberately kept out of `uv.lock`
(above), so `uv sync` sees it as extraneous and removes it — its plan shows
`- laya-mlx==…`. Keep it one of two ways:

```bash
uv sync --inexact        # leaves packages that are not in the lock alone
./scripts/dev-sync.sh    # uv sync --inexact, then reinstalls laya if it is missing
```

If a plain `uv sync` already dropped it, re-run the install line above (or
`scripts/dev-sync.sh`).

Notes:

- The `laya[onnx]` wheel is **large** — it pulls PyTorch and CUDA packages. This is
  expected, and is exactly why Rudra does not bundle it.
- On first use laya downloads its model checkpoint (**~850 MB**) from the Hugging Face Hub,
  once per machine. After that it runs fully offline.
- `laya-mlx` requires macOS 14+ on Apple Silicon. Intel Macs use the `laya[onnx]` build.

### Confirm it's installed

```bash
rudra doctor
```

`doctor` reports whether laya is importable for your platform, and `rudra config list`
shows your `[decide]` settings. Once laya is present, `rudra "<task>"` runs normally.

---

## Configuring it — `[decide]`

laya is tuned under a `[decide]` section in `.rudra/config.toml`:

```toml
[decide]
enabled = true                      # the decision layer as a whole
backend = "auto"                    # auto | mlx | onnx | off
mlx_repo = "convaiinnovations/laya"  # Apple-Silicon checkpoint
onnx_model = "convaiinnovations/laya"
```

- `backend = "auto"` (the default) picks the right build for your machine — MLX on Apple
  Silicon, ONNX elsewhere. Set `mlx` or `onnx` to force one. **`backend = "off"` does not
  remove the requirement** — laya must still be installed; `off` only tells Rudra not to
  *use* it for decisions.
- **`enabled = false` is the deliberate opt-out.** If you genuinely cannot run laya on your
  platform, set this and Rudra runs exactly as it did before laya — every decision goes to
  your main model, and the install requirement is lifted. This is the one supported way to
  run Rudra without laya.

---

## Status

This is an **early feature.** The decision runtime and the local model are in place; the
individual decisions are being moved onto laya one at a time. Installing laya now is safe
and required to run; where a decision hasn't been moved onto it yet, Rudra simply keeps
asking your main model for that one.

---

## Troubleshooting

**"laya is required but not installed."** Install the build for your OS (above), into the
environment `rudra` runs from. If you installed Rudra with `uv`, remember the `uv pip`
prefix. Verify with `rudra doctor`.

**It installed but Rudra still says it's missing.** You likely installed it into a
different environment than the one `rudra` runs from. Run `rudra doctor` — it prints which
interpreter Rudra is using — and `pip install` into that same one (or `uv pip install` from
the project).

**It worked, then disappeared after `uv sync`.** Expected: laya is not in `uv.lock`, so a
plain `uv sync` uninstalls it. Reinstall with the line for your OS, and from now on sync
with `uv sync --inexact` or `scripts/dev-sync.sh` so it is left in place.

**The download is slow or blocked.** The ~850 MB checkpoint comes from the Hugging Face
Hub on first use. On a restricted network, pre-fetch it on a machine that can reach the Hub,
or set `HF_HOME` to a cache you control.

**I can't install laya at all on this machine.** Set `[decide] enabled = false` in your
config. Rudra will run without laya, using your configured model for everything, as it did
before this feature.

See also: **[Configuration](02-configuration.md)**, **[Troubleshooting](06-troubleshooting.md)**.
