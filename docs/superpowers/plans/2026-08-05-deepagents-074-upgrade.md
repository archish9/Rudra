# deepagents 0.7.4 Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Rudra from `deepagents 0.4.12` to `deepagents 0.7.4` with zero observable behavior change, leaving behind executable contract tests that make any future deepagents bump fail loudly and specifically.

**Architecture:** A throwaway probe venv carries 0.7.4 while the real `.venv` stays on 0.4.12 and working. Contract tests are written and run against the probe venv *before* any source edit, so every claim in `TODO.md` Section F becomes a test result rather than a trusted assertion. Version-independent source fixes land next, then the `.venv` migration, then the changes that require 0.7.4, then a live smoke run.

**Tech Stack:** Python 3.12, `uv`, `pytest`, `deepagents`, `langchain` / `langchain-core` / `langchain-ollama`, Ollama (`gemma4:31b-cloud` via Ollama cloud).

**Spec:** `docs/superpowers/specs/2026-08-05-deepagents-074-upgrade-design.md`

## Global Constraints

- Working branch is `upgrade/deepagents-0.7.4`. Never commit to `main`.
- Python `>=3.12`. Installed interpreter is 3.12.3.
- `deepagents` is pinned **exactly** to `0.7.4` — never a range — because `src/rudra/compat/deepagents_path.py` monkeypatches deepagents internals (`TODO.md` U.4 / C0.8).
- Success condition is *"Rudra behaves exactly as it does today, but on 0.7.4."* Any behavior change is a bug, not a feature.
- **Do not** adopt U.7 `permissions=`, U.9 provider profiles, U.10 harness profiles, or U.11 async subagents. They are 0.7.4 features belonging to later steps.
- **Do not** perform the D4 middleware deletions or the D7 VFS deletion. Those are Step 2 (`C0.2`).
- **Do not** fix A1.1 (version string), A1.12 (`aiosqlite` undeclared), A1.13 (duplicate `langgraph-checkpoint-sqlite`), C0.4 (ruff), or C0.7 (dev extras). Those are Step 3.
- `.venv/bin/ruff check src/` must report **≤ 213** errors at the end. No regression; fixing them is Step 3.
- `TODO.md` is updated in the same commit as the code it describes (`CLAUDE.md` session rule 6).
- Nothing gets fixed before it is listed in `TODO.md` as `PENDING` (session rule 2). U.13, U.14, U.15 are already listed. Anything new found mid-plan gets listed first.
- Every claim about the codebase cites `file.py:line` (session rule 3).
- The probe venv lives at `.venv-probe/` and must be added to `.gitignore` — never committed.
- Ollama cloud is signed in and `gemma4:31b-cloud` is confirmed to support tool calling (verified 2026-08-05).

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `tests/test_deepagents_contract.py` | **create** | Every deepagents/langchain API surface Rudra depends on, as executable assertions. No model, no network. |
| `tests/test_fix_write_params.py` | **create** | Pure unit tests for the markdown-fence regex. No deepagents import. |
| `tests/test_version_guard.py` | **create** | Unit tests for the compat guard helpers. |
| `src/rudra/compat/version_guard.py` | **create** | Single home for "Rudra reaches into deepagents internals here" checks. Raises a message naming the `TODO.md` item instead of a bare `AttributeError`. |
| `src/rudra/middleware/fix_write_params.py` | modify `:19` | Widen `_FENCE_RE` info-string class (U.15). |
| `src/rudra/agent/planner_agent.py` | modify `:12`, `:92-95` | Add `FixWriteParamsMiddleware` to the planner (U.14). |
| `src/rudra/agent/main_agent.py` | modify `:550`, `:554` | Swap `OverwriteFilesystemBackend` → `FilesystemBackend` (U.3). |
| `src/rudra/compat/overwrite_backend.py` | **delete** | Obsolete under 0.7.4 (U.3). |
| `src/rudra/compat/deepagents_path.py` | modify `:72-73` | Route the two monkeypatch targets through `version_guard` (U.4). |
| `src/rudra/middleware/task_anchor.py` | modify `:20` | Route the private `_utils` import through `version_guard` (U.13). |
| `pyproject.toml` | modify `:25-59` | Dependency block (U.1, U.2). |
| `requirements.txt` | **delete** | `pyproject.toml` + `uv.lock` become the single source of truth. |
| `.gitignore` | modify | Ignore `.venv-probe/`. |
| `TODO.md` | modify | Ledger updates, one per task. |

---

## Task 1: Probe venv and API discovery

Builds a disposable 0.7.4 environment and answers every open question about the 0.7.4 API **before** a single source line changes. No source edits in this task.

**Files:**
- Modify: `.gitignore`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: nothing.
- Produces: a working `.venv-probe/` on deepagents 0.7.4, and a recorded discovery report that Task 2 writes its assertions against. Specifically it establishes: whether `validate_path` still lives in both modules and is the same object; whether `deepagents.middleware._utils.append_to_system_message` still exists; whether `FilesystemBackend.write()` overwrites; whether it strips fences; **whether `virtual_mode=True` still writes through to disk**; the accessor path for enumerating a compiled agent's tool names; and whether `write_todos` is absent from the default stack.

- [ ] **Step 1: Ignore the probe venv**

Add to `.gitignore`, directly after the `.rudra/` line (currently `.gitignore:213`):

```
.venv-probe/
```

- [ ] **Step 2: Resolve-only dry run**

This must run before any download so a resolver conflict costs seconds, not gigabytes.

Run:
```bash
uv venv .venv-probe --python 3.12
VIRTUAL_ENV=.venv-probe uv pip install --dry-run \
  'deepagents==0.7.4' \
  'langchain>=1.3.14' \
  'langchain-core>=1.5.0' \
  'langchain-ollama>=1.1.0' \
  'langchain-anthropic>=1.5.3' \
  'langchain-google-genai>=4.3.1' \
  'langsmith>=0.10.9' \
  'wcmatch>=11.0' \
  'langgraph>=1.1.3' \
  'langgraph-checkpoint-sqlite>=3.0.3' \
  'langchain-community>=0.4.1' \
  'pytest>=9.0.2'
```

Expected: a resolution plan, exit 0.

**If it fails:** the conflict is almost certainly `langchain-community 0.4.1` or `langgraph 1.1.3` against `langchain>=1.3.14`. Record the exact resolver error in `TODO.md` as a new `U.16 PENDING` item with the error text before attempting any version change. Do not silently loosen a constraint.

- [ ] **Step 3: Install for real**

Run:
```bash
VIRTUAL_ENV=.venv-probe uv pip install \
  'deepagents==0.7.4' \
  'langchain>=1.3.14' \
  'langchain-core>=1.5.0' \
  'langchain-ollama>=1.1.0' \
  'langchain-anthropic>=1.5.3' \
  'langchain-google-genai>=4.3.1' \
  'langsmith>=0.10.9' \
  'wcmatch>=11.0' \
  'langgraph>=1.1.3' \
  'langgraph-checkpoint-sqlite>=3.0.3' \
  'langchain-community>=0.4.1' \
  'pytest>=9.0.2'
```

Expected: exit 0.

- [ ] **Step 4: Write the discovery script**

Create this file in the scratchpad — it is a one-time investigation artifact, not shipped code:

`/tmp/claude-1000/-home-a-code-ai-ml-agent-RudraAnvil/4f866706-4737-44df-bb99-16247b9eb0c1/scratchpad/probe_074.py`

```python
"""One-time discovery of the deepagents 0.7.4 API surface Rudra depends on.

Run under .venv-probe only. Every question here becomes an assertion in
tests/test_deepagents_contract.py.
"""

import importlib
import inspect
import pathlib
import tempfile
from importlib.metadata import version

print("=== versions ===")
for pkg in ("deepagents", "langchain", "langchain-core", "langchain-ollama", "langgraph"):
    try:
        print(f"{pkg:22} {version(pkg)}")
    except Exception as exc:  # noqa: BLE001
        print(f"{pkg:22} ERROR {exc}")

print("\n=== U.4: validate_path monkeypatch targets ===")
utils = importlib.import_module("deepagents.backends.utils")
fs_mw = importlib.import_module("deepagents.middleware.filesystem")
u_vp = getattr(utils, "validate_path", None)
m_vp = getattr(fs_mw, "validate_path", None)
print("backends.utils.validate_path      :", callable(u_vp))
print("middleware.filesystem.validate_path:", callable(m_vp))
print("same object (two-target patch needed):", m_vp is u_vp)

print("\n=== U.13: private _utils API ===")
try:
    du = importlib.import_module("deepagents.middleware._utils")
    fn = getattr(du, "append_to_system_message", None)
    print("append_to_system_message:", fn and inspect.signature(fn))
except Exception as exc:  # noqa: BLE001
    print("MISSING:", type(exc).__name__, exc)

print("\n=== langchain middleware base class ===")
try:
    types_mod = importlib.import_module("langchain.agents.middleware.types")
    print("AgentMiddleware present:", hasattr(types_mod, "AgentMiddleware"))
except Exception as exc:  # noqa: BLE001
    print("MISSING:", type(exc).__name__, exc)

print("\n=== U.3: FilesystemBackend.write semantics ===")
from deepagents.backends.filesystem import FilesystemBackend  # noqa: E402

plain_dir = pathlib.Path(tempfile.mkdtemp())
plain = FilesystemBackend(root_dir=str(plain_dir))
print("write #1:", plain.write("f.txt", "a"))
print("write #2:", plain.write("f.txt", "b"))
target = plain_dir / "f.txt"
print("on disk  :", target.read_text() if target.exists() else "NO FILE")

fenced = '```python\nprint("hi")\n```'
plain.write("g.py", fenced)
print("fence preserved (must be True):", (plain_dir / "g.py").read_text() == fenced)

print("\n=== U.3 blocker check: virtual_mode=True write-through ===")
virt_dir = pathlib.Path(tempfile.mkdtemp())
virt = FilesystemBackend(root_dir=str(virt_dir), virtual_mode=True)
print("virtual write result:", virt.write("f.txt", "x"))
vt = virt_dir / "f.txt"
print("landed on disk (must be True):", vt.exists())
if vt.exists():
    print("content:", vt.read_text())

print("\n=== U.6: create_deep_agent signature ===")
from deepagents import create_deep_agent  # noqa: E402

sig = inspect.signature(create_deep_agent)
print("params:", list(sig.parameters))

print("\n=== U.5: is write_todos in the default stack? ===")
from langchain_ollama import ChatOllama  # noqa: E402

model = ChatOllama(model="gemma4:31b-cloud", base_url="http://localhost:11434")
agent = create_deep_agent(
    model=model,
    tools=[],
    backend=FilesystemBackend(root_dir=str(plain_dir)),
)
found_any = False
for node_name, node in getattr(agent, "nodes", {}).items():
    bound = getattr(node, "bound", None)
    tools_by_name = getattr(bound, "tools_by_name", None)
    if tools_by_name:
        found_any = True
        print(f"node {node_name!r} tools:", sorted(tools_by_name))
if not found_any:
    print("NO ToolNode found via agent.nodes[*].bound.tools_by_name")
    print("agent.nodes keys:", list(getattr(agent, "nodes", {})))
```

- [ ] **Step 5: Run the discovery script**

Run:
```bash
.venv-probe/bin/python /tmp/claude-1000/-home-a-code-ai-ml-agent-RudraAnvil/4f866706-4737-44df-bb99-16247b9eb0c1/scratchpad/probe_074.py 2>&1 | tee /tmp/claude-1000/-home-a-code-ai-ml-agent-RudraAnvil/4f866706-4737-44df-bb99-16247b9eb0c1/scratchpad/probe_074.out
```

Expected: constructing `ChatOllama` and `create_deep_agent` does **not** contact the network — agent construction is local. If the script hangs, that assumption is wrong; record it and drop the last section.

- [ ] **Step 6: Evaluate the two blocking results**

**Blocker A — `virtual_mode=True` write-through.** `main_agent.py:556` passes `virtual_mode=True`, and the deleted subclass wrote to disk via `Path.write_text` (`overwrite_backend.py:61`), bypassing the base class entirely. If the discovery output shows `landed on disk (must be True): False`, then U.3's straight swap silently stops writing files.

If that happens: **stop**, add to `TODO.md` as `U.17 PENDING` with the script output as evidence, and re-plan U.3 — most likely as `virtual_mode=False`, which requires re-checking what `compat/deepagents_path.py` was normalizing. Do not proceed to Task 6.

**Blocker B — fence preservation.** If `fence preserved` is `False`, 0.7.4 strips fences itself and contract test 7 inverts. Record it; `FixWriteParamsMiddleware`'s fence-stripping becomes belt-and-braces rather than load-bearing, and U.15 drops to cosmetic.

- [ ] **Step 7: Record the discovery result in TODO.md**

Under `### Phase U`, append a note row after `U.15`:

```markdown
| U.16 | **DONE** 2026-08-05 | **0.7.4 API discovery, verified against a probe venv.** Resolver plan clean; `validate_path` present in both modules and the same object (U.4 two-target patch still required); `_utils.append_to_system_message` present (U.13); `FilesystemBackend.write()` overwrites and does not strip fences; `virtual_mode=True` writes through to disk. Raw output: scratchpad `probe_074.out` | `.venv-probe`, `probe_074.py` |
```

Adjust the wording to match what the script actually printed. **Do not write a result the script did not produce.** If a check came out the other way, say so and open the follow-up item named in Step 6.

- [ ] **Step 8: Commit**

```bash
git add .gitignore TODO.md
git commit -m "chore: probe venv for deepagents 0.7.4 API discovery (U.16)

Disposable .venv-probe on 0.7.4, gitignored. Discovery script confirms
the Section F claims Rudra depends on before any source edit:
validate_path targets, the private _utils import, write() overwrite
semantics, fence preservation, and virtual_mode write-through.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Contract tests

The regression net. These lock every deepagents/langchain surface Rudra reaches into, so a future bump fails here instead of somewhere far from the cause.

**Files:**
- Create: `tests/test_deepagents_contract.py`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: Task 1's discovery output — specifically the ToolNode accessor path and the `virtual_mode` write-through result.
- Produces: `tests/test_deepagents_contract.py` with module constant `EXPECTED_DEEPAGENTS_VERSION = "0.7.4"` and helper `_tool_names(agent) -> set[str]`. Task 7 reuses the same version string via `src/rudra/compat/version_guard.EXPECTED_DEEPAGENTS_VERSION`; the two must stay equal.

- [ ] **Step 1: Write the contract tests**

Create `tests/test_deepagents_contract.py`:

```python
"""Contract tests: the exact deepagents 0.7.4 surface Rudra depends on.

Every assertion corresponds to a claim in TODO.md Section F. They exist so a
deepagents upgrade fails loudly and specifically instead of breaking Rudra
somewhere far from the cause.

None of these tests need a live model or network access. Agent construction
is local; no request is issued.

IMPORTANT: this module must never import rudra.agent or call
rudra.compat.deepagents_path.install_path_normalizer. That function
monkeypatches validate_path, which would invalidate the identity assertion in
test_validate_path_is_shared_object.
"""

from __future__ import annotations

import importlib
import inspect
from importlib.metadata import version

EXPECTED_DEEPAGENTS_VERSION = "0.7.4"


def _tool_names(agent) -> set[str]:
    """Extract bound tool names from a compiled deepagents graph.

    langgraph exposes the ToolNode as a graph node whose ``bound`` attribute
    carries ``tools_by_name``. If this stops working the langgraph internals
    moved, which is itself worth failing on.
    """
    for node in getattr(agent, "nodes", {}).values():
        tools_by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
        if tools_by_name:
            return set(tools_by_name)
    raise AssertionError(
        "Could not locate a ToolNode via agent.nodes[*].bound.tools_by_name. "
        "langgraph internals changed; update _tool_names."
    )


def _local_model():
    """A real ChatOllama instance. Construction issues no network request."""
    from langchain_ollama import ChatOllama

    return ChatOllama(model="gemma4:31b-cloud", base_url="http://localhost:11434")


# --- U.4 / C0.8: version pin -------------------------------------------------


def test_deepagents_version_is_exactly_pinned():
    assert version("deepagents") == EXPECTED_DEEPAGENTS_VERSION


# --- U.4: validate_path monkeypatch targets ----------------------------------


def test_validate_path_exists_in_backends_utils():
    utils = importlib.import_module("deepagents.backends.utils")
    assert callable(getattr(utils, "validate_path", None))


def test_validate_path_is_shared_object():
    """middleware/filesystem.py does `from ...utils import validate_path`.

    Patching one module is not enough — this identity is exactly why
    compat/deepagents_path.py patches both targets.
    """
    utils = importlib.import_module("deepagents.backends.utils")
    fs_mw = importlib.import_module("deepagents.middleware.filesystem")
    assert getattr(fs_mw, "validate_path", None) is utils.validate_path


# --- U.13: private API task_anchor depends on --------------------------------


def test_private_append_to_system_message_exists():
    module = importlib.import_module("deepagents.middleware._utils")
    fn = getattr(module, "append_to_system_message", None)
    assert callable(fn)
    assert len(inspect.signature(fn).parameters) >= 2


# --- all six Rudra middlewares depend on this import path --------------------


def test_agent_middleware_base_class_import_path():
    types_mod = importlib.import_module("langchain.agents.middleware.types")
    assert hasattr(types_mod, "AgentMiddleware")


# --- U.3: FilesystemBackend.write semantics ----------------------------------


def _backend(tmp_path, **kwargs):
    from deepagents.backends.filesystem import FilesystemBackend

    return FilesystemBackend(root_dir=str(tmp_path), **kwargs)


def test_write_overwrites_existing_file(tmp_path):
    """The entire reason compat/overwrite_backend.py existed, now upstream."""
    backend = _backend(tmp_path)

    first = backend.write("f.txt", "a")
    assert getattr(first, "error", None) is None

    second = backend.write("f.txt", "b")
    assert getattr(second, "error", None) is None

    assert (tmp_path / "f.txt").read_text() == "b"


def test_write_does_not_strip_markdown_fences(tmp_path):
    """Proves FixWriteParamsMiddleware's fence-stripping stays load-bearing."""
    fenced = '```python\nprint("hi")\n```'
    _backend(tmp_path).write("g.py", fenced)
    assert (tmp_path / "g.py").read_text() == fenced


def test_virtual_mode_writes_through_to_disk(tmp_path):
    """main_agent.py:556 passes virtual_mode=True and expects real files.

    The deleted OverwriteFilesystemBackend bypassed the base class with
    Path.write_text. If this fails, U.3's straight swap silently stops
    writing files.
    """
    _backend(tmp_path, virtual_mode=True).write("f.txt", "x")
    assert (tmp_path / "f.txt").read_text() == "x"


# --- U.6: create_deep_agent surface ------------------------------------------


def test_create_deep_agent_accepts_rudras_kwargs():
    from deepagents import create_deep_agent

    params = inspect.signature(create_deep_agent).parameters
    for name in (
        "model",
        "tools",
        "system_prompt",
        "backend",
        "checkpointer",
        "middleware",
        "memory",
    ):
        assert name in params, f"create_deep_agent lost the {name!r} parameter"


def test_create_deep_agent_accepts_a_backend_instance(tmp_path):
    """0.7.4 types `backend` as BackendProtocol only — no BackendFactory."""
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=_local_model(),
        tools=[],
        backend=_backend(tmp_path),
    )
    assert agent is not None


# --- U.5: TodoListMiddleware no longer auto-added ----------------------------


def test_default_stack_has_no_write_todos_tool(tmp_path):
    """Rudra replaced write_todos with planning_tools.py:3 — losing the
    auto-added TodoListMiddleware removes a redundant tool. Documented, not
    mourned."""
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=_local_model(),
        tools=[],
        backend=_backend(tmp_path),
    )
    assert "write_todos" not in _tool_names(agent)
```

- [ ] **Step 2: Run against the OLD venv — expect failures**

Run:
```bash
.venv/bin/pytest tests/test_deepagents_contract.py -v
```

Expected: **FAIL**. At minimum `test_deepagents_version_is_exactly_pinned` (0.4.12 ≠ 0.7.4) and `test_write_overwrites_existing_file` (0.4.12 refuses to overwrite). This is the red half of the cycle and it proves the tests actually discriminate between versions.

Record the pass/fail split — it is the evidence that U.3's premise is real.

- [ ] **Step 3: Run against the PROBE venv — expect all green**

Run:
```bash
.venv-probe/bin/pytest tests/test_deepagents_contract.py -v
```

Expected: **all pass**.

If `_tool_names` raises its `AssertionError`, update the helper to match the accessor path Task 1's discovery script actually found, then re-run.

If any other test fails, the corresponding `TODO.md` §F claim is wrong. Log it as a new `PENDING` item with the failure output before changing anything.

- [ ] **Step 4: Commit**

```bash
git add tests/test_deepagents_contract.py TODO.md
git commit -m "test: contract tests for the deepagents 0.7.4 API surface

Locks every deepagents/langchain internal Rudra reaches into, so a
future bump fails here rather than somewhere far from the cause:
validate_path in both modules and shared by identity (U.4), the
private _utils.append_to_system_message import (U.13), write()
overwrite and fence-preservation semantics (U.3), virtual_mode
write-through, the create_deep_agent kwarg surface (U.6), and the
absence of write_todos from the default stack (U.5).

Red under .venv (0.4.12), green under .venv-probe (0.7.4).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Widen the fence regex (U.15)

Pure Python, no deepagents involvement. Safe to land while `.venv` is still on 0.4.12, and it must land **before** Task 6 deletes the backend that currently provides the wider coverage.

**Files:**
- Create: `tests/test_fix_write_params.py`
- Modify: `src/rudra/middleware/fix_write_params.py:19`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `rudra.middleware.fix_write_params._strip_fences(content: str) -> str`, unchanged signature, widened coverage.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fix_write_params.py`:

```python
"""Unit tests for markdown-fence stripping.

This is the behavior compat/overwrite_backend.py used to provide at the
backend layer (overwrite_backend.py:43, regex `^```[^\\n]*\\n(.*)\\n```$`).
U.3 deletes that backend, so the middleware must cover at least as much.
"""

from __future__ import annotations

from rudra.middleware.fix_write_params import _strip_fences


def test_strips_language_tagged_fence():
    assert _strip_fences('```python\nprint("hi")\n```') == 'print("hi")\n'


def test_strips_bare_fence():
    assert _strip_fences('```\nprint("hi")\n```') == 'print("hi")\n'


def test_leaves_unfenced_content_alone():
    source = 'print("hi")\n'
    assert _strip_fences(source) == source


def test_strips_fence_with_trailing_newline():
    assert _strip_fences('```python\nprint("hi")\n```\n') == 'print("hi")\n'


def test_strips_fence_with_info_string_attributes():
    """The backend's `[^\\n]*` matched this; the middleware's
    `[a-zA-Z0-9_\\-]*` did not. Deleting the backend must not shrink
    coverage. See TODO.md U.15."""
    assert _strip_fences('```py title="x"\nprint("hi")\n```') == 'print("hi")\n'


def test_keeps_inner_fences_when_stripping_outer():
    content = '```markdown\nSee:\n```\ninner\n```\n```'
    assert _strip_fences(content) == 'See:\n```\ninner\n```\n'


def test_leaves_unterminated_fence_alone():
    source = '```python\nprint("hi")\n'
    assert _strip_fences(source) == source
```

- [ ] **Step 2: Run the tests to verify the info-string case fails**

Run:
```bash
.venv/bin/pytest tests/test_fix_write_params.py -v
```

Expected: `test_strips_fence_with_info_string_attributes` **FAILS** — the current `[a-zA-Z0-9_\-]*` class cannot match the space in `py title="x"`, so `_strip_fences` returns the input unchanged. Every other test passes.

If a test other than that one fails, the current behavior differs from what this plan assumes. Record the actual output in `TODO.md` before changing the regex.

- [ ] **Step 3: Widen the regex**

In `src/rudra/middleware/fix_write_params.py:19`, replace:

```python
_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_\-]*\n?(.*?)```\s*$", re.DOTALL)
```

with:

```python
# Info string is anything up to the newline — matches the coverage that
# compat/overwrite_backend.py:43 used to provide before U.3 deleted it.
# See TODO.md U.15.
_FENCE_RE = re.compile(r"^```[^\n]*\n?(.*?)```\s*$", re.DOTALL)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
.venv/bin/pytest tests/test_fix_write_params.py -v
```

Expected: **all 7 pass**.

- [ ] **Step 5: Mark U.15 done in TODO.md**

Change the `U.15` row's status from `PENDING` to `**DONE** 2026-08-05`, and append the verifying command and its result to the item text.

- [ ] **Step 6: Commit**

```bash
git add tests/test_fix_write_params.py src/rudra/middleware/fix_write_params.py TODO.md
git commit -m "fix: widen fence regex info-string class to match the backend (U.15)

FixWriteParamsMiddleware used [a-zA-Z0-9_-]* for the fence info string;
compat/overwrite_backend.py:43 used [^\\n]*. Fenced blocks carrying
attributes (\`\`\`py title=\"x\") matched the backend but not the
middleware, so U.3's deletion would have shrunk coverage.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Give the planner fence-stripping (U.14)

The planner never had `FixWriteParamsMiddleware`; `OverwriteFilesystemBackend` covered it. Must land before Task 6 removes that cover.

**Files:**
- Modify: `src/rudra/agent/planner_agent.py:12`, `:92-95`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: `rudra.middleware.FixWriteParamsMiddleware` (already exported — see `coder_agent.py:12`).
- Produces: nothing new. Behavior-preserving.

- [ ] **Step 1: Read the current planner middleware wiring**

Run:
```bash
sed -n '1,20p;85,96p' src/rudra/agent/planner_agent.py
```

Expected: the import line lacks `FixWriteParamsMiddleware`, and the `middleware=[...]` list at `:92-95` contains only `TaskAnchorMiddleware(task)` and `BlockPrematureAskMiddleware(task)`.

- [ ] **Step 2: Add the import**

In `src/rudra/agent/planner_agent.py`, the middleware import line currently reads:

```python
from rudra.middleware import BlockPrematureAskMiddleware, TaskAnchorMiddleware
```

Replace with:

```python
from rudra.middleware import (
    BlockPrematureAskMiddleware,
    FixWriteParamsMiddleware,
    TaskAnchorMiddleware,
)
```

If the existing import line differs from the above, keep its actual form and add `FixWriteParamsMiddleware` to it alphabetically.

- [ ] **Step 3: Add the middleware to the planner**

At `src/rudra/agent/planner_agent.py:92-95`, replace:

```python
        middleware=[
            TaskAnchorMiddleware(task),
            BlockPrematureAskMiddleware(task),
        ],
```

with:

```python
        middleware=[
            # First in the list: cleans tool args before anything else sees
            # them. The planner previously got fence-stripping from
            # OverwriteFilesystemBackend, which U.3 deletes. See TODO.md U.14.
            FixWriteParamsMiddleware(),
            TaskAnchorMiddleware(task),
            BlockPrematureAskMiddleware(task),
        ],
```

- [ ] **Step 4: Verify the module still imports and middleware order is right**

Run:
```bash
.venv/bin/python -c "
import ast, pathlib
src = pathlib.Path('src/rudra/agent/planner_agent.py').read_text()
ast.parse(src)
print('parses OK')
print('FixWriteParams present:', 'FixWriteParamsMiddleware()' in src)
"
.venv/bin/ruff check src/rudra/agent/planner_agent.py
```

Expected: `parses OK`, `FixWriteParams present: True`, and ruff reporting no *new* errors for this file.

A full import of `planner_agent` is not attempted here — it pulls in `ChatOllama` and the whole agent stack, which is Task 5's concern.

- [ ] **Step 5: Mark U.14 done in TODO.md**

Change the `U.14` row's status from `PENDING` to `**DONE** 2026-08-05` with the verifying output.

- [ ] **Step 6: Commit**

```bash
git add src/rudra/agent/planner_agent.py TODO.md
git commit -m "fix: add FixWriteParamsMiddleware to the planner agent (U.14)

The planner carried only TaskAnchorMiddleware and
BlockPrematureAskMiddleware; OverwriteFilesystemBackend was what gave it
markdown-fence stripping. U.3 deletes that backend, so wire the
middleware in explicitly first, before the deletion lands.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Migrate the real venv to 0.7.4 (U.1, U.2)

**Files:**
- Modify: `pyproject.toml:25-59`
- Delete: `requirements.txt`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: Task 1's verified resolution plan.
- Produces: `.venv` on deepagents 0.7.4, with `tests/test_deepagents_contract.py` green against it.

- [ ] **Step 1: Update the dependency block**

In `pyproject.toml`, replace these lines inside `dependencies` (currently `:26-35`):

```toml
    # Core Agent Framework
    "deepagents>=0.4.12",
    "langgraph>=1.1.3",
    "langgraph-checkpoint>=4.0.1",
    "langgraph-checkpoint-sqlite>=3.0.3",
    "langgraph-prebuilt>=1.0.8",
    # LLM Integration - Ollama
    "langchain>=1.2.13",
    "langchain-core>=1.2.23",
    "langchain-ollama>=1.0.1",
    "langchain-community>=0.4.1",
```

with:

```toml
    # Core Agent Framework
    # EXACT pin: src/rudra/compat/deepagents_path.py monkeypatches deepagents
    # internals and src/rudra/middleware/task_anchor.py imports a private
    # module. Re-verify both before bumping. See TODO.md U.4, U.13, C0.8.
    "deepagents==0.7.4",
    "langgraph>=1.1.3",
    "langgraph-checkpoint>=4.0.1",
    "langgraph-checkpoint-sqlite>=3.0.3",
    "langgraph-prebuilt>=1.0.8",
    # LLM Integration
    "langchain>=1.3.14",
    "langchain-core>=1.5.0",
    "langchain-ollama>=1.1.0",
    "langchain-community>=0.4.1",
    # Transitive hard dependencies of deepagents 0.7.4, declared
    # intentionally rather than inherited silently. See TODO.md U.2.
    "langchain-anthropic>=1.5.3",
    "langchain-google-genai>=4.3.1",
    "langsmith>=0.10.9",
    "wcmatch>=11.0",
    "packaging>=23.2",
```

Leave every other dependency untouched. In particular do **not** add `langchain-openai`, do **not** add `aiosqlite`, do **not** deduplicate `langgraph-checkpoint-sqlite`, and do **not** move `ruff`/`pytest` — those are Step 3 and Step 5 items per the Global Constraints.

- [ ] **Step 2: Sync the real venv**

Run:
```bash
uv sync
```

Expected: exit 0, `deepagents 0.7.4` installed.

Verify:
```bash
.venv/bin/python -c "from importlib.metadata import version; print(version('deepagents'))"
```
Expected: `0.7.4`

- [ ] **Step 3: Run the contract tests against the real venv**

Run:
```bash
.venv/bin/pytest tests/test_deepagents_contract.py tests/test_fix_write_params.py -v
```

Expected: **all pass** — the same green Task 2 saw under the probe venv.

- [ ] **Step 4: Delete requirements.txt**

`pyproject.toml` plus `uv.lock` are now the single source of truth. The pinned `requirements.txt` lists a different set and would drift (`TODO.md` A4.4).

```bash
git rm requirements.txt
```

- [ ] **Step 5: Confirm the CLI still starts**

Run:
```bash
.venv/bin/rudra --version
```

Expected: prints `0.1.0` with no traceback. The wrong number is A1.1, a Step 3 item — **do not fix it here.**

If this raises, the most likely cause is `ChatOllama` rejecting `reasoning=True` or `num_predict` under `langchain-ollama 1.1.0` (`planner_agent.py:78-80`, `coder_agent.py:64-66`). Log it in `TODO.md` as a new `PENDING` item with the traceback before touching those call sites.

- [ ] **Step 6: Confirm no lint regression**

Run:
```bash
.venv/bin/ruff check src/ 2>&1 | tail -3
```

Expected: **≤ 213** errors.

- [ ] **Step 7: Mark U.1 and U.2 done in TODO.md**

Set both to `**DONE** 2026-08-05` with the `version('deepagents')` output and the pytest summary line. Also add a line to `A4.4` noting `requirements.txt` was deleted and `pyproject.toml` + `uv.lock` are now authoritative.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock TODO.md
git commit -m "deps: upgrade to deepagents 0.7.4 (U.1, U.2)

deepagents is pinned EXACTLY, not ranged: compat/deepagents_path.py
monkeypatches deepagents internals and middleware/task_anchor.py
imports a private module, so a silent minor bump can break Rudra far
from the cause (TODO.md U.4, U.13, C0.8).

Declares the transitive hard deps 0.7.4 introduces rather than
inheriting them silently: langchain-anthropic, langchain-google-genai,
langsmith, wcmatch, packaging.

Deletes requirements.txt — pyproject.toml plus uv.lock are now the
single source of truth (A4.4).

Contract tests green against the real venv.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Delete OverwriteFilesystemBackend (U.3)

Requires 0.7.4, so it lands after Task 5. Requires Tasks 3 and 4, which restore the fence coverage this deletion removes.

**Files:**
- Modify: `src/rudra/agent/main_agent.py:550`, `:554`
- Delete: `src/rudra/compat/overwrite_backend.py`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: `deepagents.backends.filesystem.FilesystemBackend`, whose overwrite and `virtual_mode` write-through semantics Task 2 locked by test.
- Produces: `main_agent.py`'s `filesystem_backend` local is now a plain `FilesystemBackend`. `rudra.compat.overwrite_backend` no longer exists — nothing else may import it.

- [ ] **Step 1: Confirm there is exactly one instantiation**

Run:
```bash
grep -rn "OverwriteFilesystemBackend\|overwrite_backend" src/ tests/ --include=*.py
```

Expected: exactly two hits, both in `main_agent.py` (the import at `:550` and the construction at `:554`), plus the file's own definition.

If anything else references it, **stop** and list the extra call sites in `TODO.md` before proceeding.

- [ ] **Step 2: Swap the import**

In `src/rudra/agent/main_agent.py:550`, replace:

```python
    from rudra.compat.overwrite_backend import OverwriteFilesystemBackend
```

with:

```python
    from deepagents.backends.filesystem import FilesystemBackend
```

- [ ] **Step 3: Swap the construction**

At `src/rudra/agent/main_agent.py:554-557`, replace:

```python
    filesystem_backend = OverwriteFilesystemBackend(
        root_dir=str(project_path),
        virtual_mode=True,
    )
```

with:

```python
    # 0.7.4's FilesystemBackend.write() creates or overwrites (O_TRUNC +
    # O_NOFOLLOW), which is the only reason OverwriteFilesystemBackend
    # existed. Markdown-fence stripping now lives solely in
    # FixWriteParamsMiddleware, wired into both agents. See TODO.md U.3.
    filesystem_backend = FilesystemBackend(
        root_dir=str(project_path),
        virtual_mode=True,
    )
```

- [ ] **Step 4: Delete the obsolete backend**

```bash
git rm src/rudra/compat/overwrite_backend.py
```

- [ ] **Step 5: Verify nothing references it and the tree is clean**

Run:
```bash
grep -rn "OverwriteFilesystemBackend\|overwrite_backend" src/ tests/ --include=*.py; echo "grep exit=$?"
.venv/bin/ruff check src/ 2>&1 | tail -3
.venv/bin/pytest tests/ -v
```

Expected: grep exit `1` (no matches), ruff **≤ 213** errors, all tests pass.

- [ ] **Step 6: Mark U.3 done in TODO.md**

Set `U.3` to `**DONE** 2026-08-05`. Keep the existing correction note about fence-stripping already living in the middleware — it is the reason this task was smaller than originally scoped.

- [ ] **Step 7: Commit**

```bash
git add src/rudra/agent/main_agent.py TODO.md
git commit -m "refactor: drop OverwriteFilesystemBackend for upstream FilesystemBackend (U.3)

0.7.4's FilesystemBackend.write() creates or overwrites using O_TRUNC
plus O_NOFOLLOW (backends/filesystem.py:489), which is the whole reason
the 65-line subclass existed. Its markdown-fence stripping was never
unique — FixWriteParamsMiddleware already did it — and the planner now
carries that middleware explicitly (U.14).

main_agent.py:554 was the sole instantiation.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Guard the two internal-API dependencies (U.4, U.13)

Rudra reaches into deepagents internals in two places. Both currently fail late and cryptically on a version bump. This makes them fail immediately, naming the `TODO.md` item.

**Files:**
- Create: `src/rudra/compat/version_guard.py`
- Create: `tests/test_version_guard.py`
- Modify: `src/rudra/compat/deepagents_path.py:72-73`
- Modify: `src/rudra/middleware/task_anchor.py:20`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `rudra.compat.version_guard` exporting `EXPECTED_DEEPAGENTS_VERSION: str`, `DeepagentsCompatError(RuntimeError)`, `require_deepagents_version(todo_ref: str) -> None`, and `require_deepagents_attr(module_path: str, attr: str, todo_ref: str) -> Any`. `EXPECTED_DEEPAGENTS_VERSION` must equal the constant of the same name in `tests/test_deepagents_contract.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_version_guard.py`:

```python
"""Unit tests for the deepagents internal-API guards.

Rudra monkeypatches deepagents internals (compat/deepagents_path.py, TODO
U.4) and imports a private module (middleware/task_anchor.py, TODO U.13).
Both must fail immediately and legibly on a version bump.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from rudra.compat.version_guard import (
    EXPECTED_DEEPAGENTS_VERSION,
    DeepagentsCompatError,
    require_deepagents_attr,
    require_deepagents_version,
)


def test_expected_version_matches_the_contract_tests():
    from tests.test_deepagents_contract import (
        EXPECTED_DEEPAGENTS_VERSION as contract_version,
    )

    assert EXPECTED_DEEPAGENTS_VERSION == contract_version


def test_version_check_passes_on_the_pinned_version():
    require_deepagents_version("U.4")


def test_version_check_raises_on_mismatch():
    with patch("rudra.compat.version_guard.version", return_value="0.9.9"):
        with pytest.raises(DeepagentsCompatError) as excinfo:
            require_deepagents_version("U.4")

    message = str(excinfo.value)
    assert "0.9.9" in message
    assert EXPECTED_DEEPAGENTS_VERSION in message
    assert "U.4" in message


def test_require_attr_returns_the_attribute():
    fn = require_deepagents_attr(
        "deepagents.middleware._utils", "append_to_system_message", "U.13"
    )
    assert callable(fn)


def test_require_attr_raises_on_missing_attribute():
    with pytest.raises(DeepagentsCompatError) as excinfo:
        require_deepagents_attr(
            "deepagents.middleware._utils", "no_such_function", "U.13"
        )

    message = str(excinfo.value)
    assert "no_such_function" in message
    assert "U.13" in message


def test_require_attr_raises_on_missing_module():
    with pytest.raises(DeepagentsCompatError) as excinfo:
        require_deepagents_attr("deepagents.no_such_module", "anything", "U.13")

    message = str(excinfo.value)
    assert "deepagents.no_such_module" in message
    assert "U.13" in message
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
.venv/bin/pytest tests/test_version_guard.py -v
```

Expected: **collection error** — `ModuleNotFoundError: No module named 'rudra.compat.version_guard'`.

- [ ] **Step 3: Write the guard module**

Create `src/rudra/compat/version_guard.py`:

```python
"""Guards for the deepagents internals Rudra depends on.

Rudra reaches past deepagents' public API in exactly two places:

  * ``compat/deepagents_path.py`` monkeypatches ``validate_path`` in both
    ``deepagents.backends.utils`` and ``deepagents.middleware.filesystem``
    (TODO.md U.4).
  * ``middleware/task_anchor.py`` imports
    ``deepagents.middleware._utils.append_to_system_message`` — a
    leading-underscore module with no stability guarantee (TODO.md U.13).

Left unguarded, a deepagents bump breaks Rudra somewhere far from the cause.
These helpers make it break at the point of contact, with a message naming
the ledger item to re-verify.

``pyproject.toml`` pins ``deepagents`` exactly for the same reason.
"""

from __future__ import annotations

import importlib
from importlib.metadata import version
from typing import Any

EXPECTED_DEEPAGENTS_VERSION = "0.7.4"

_UPGRADE_HINT = (
    "Rudra reaches into deepagents internals here. Re-verify the monkeypatch "
    "and private-import sites, then update EXPECTED_DEEPAGENTS_VERSION in "
    "src/rudra/compat/version_guard.py and tests/test_deepagents_contract.py "
    "together."
)


class DeepagentsCompatError(RuntimeError):
    """A deepagents internal Rudra depends on has moved or disappeared."""


def require_deepagents_version(todo_ref: str) -> None:
    """Raise unless the installed deepagents matches the exact pin.

    Args:
        todo_ref: The TODO.md item to name in the error, e.g. ``"U.4"``.
    """
    found = version("deepagents")
    if found != EXPECTED_DEEPAGENTS_VERSION:
        raise DeepagentsCompatError(
            f"Rudra pins deepagents=={EXPECTED_DEEPAGENTS_VERSION} but found "
            f"{found}. {_UPGRADE_HINT} See TODO.md {todo_ref}."
        )


def require_deepagents_attr(module_path: str, attr: str, todo_ref: str) -> Any:
    """Import ``module_path`` and return ``attr``, or raise legibly.

    Args:
        module_path: Fully qualified module, e.g.
            ``"deepagents.middleware._utils"``.
        attr: Attribute name to fetch from it.
        todo_ref: The TODO.md item to name in the error, e.g. ``"U.13"``.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise DeepagentsCompatError(
            f"deepagents {version('deepagents')} has no module {module_path!r}, "
            f"which Rudra depends on. {_UPGRADE_HINT} See TODO.md {todo_ref}."
        ) from exc

    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise DeepagentsCompatError(
            f"deepagents {version('deepagents')} has no {module_path}.{attr}, "
            f"which Rudra depends on. {_UPGRADE_HINT} See TODO.md {todo_ref}."
        ) from exc
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
.venv/bin/pytest tests/test_version_guard.py -v
```

Expected: **all 6 pass**.

If `test_expected_version_matches_the_contract_tests` fails on import, add an empty `tests/__init__.py` so `tests.test_deepagents_contract` is importable, and re-run.

- [ ] **Step 5: Wire the guard into the path monkeypatch (U.4)**

In `src/rudra/compat/deepagents_path.py`, the body of `install_path_normalizer` currently begins:

```python
    import deepagents.backends.utils as _utils
    import deepagents.middleware.filesystem as _fs_mw
```

Replace with:

```python
    from rudra.compat.version_guard import (
        require_deepagents_attr,
        require_deepagents_version,
    )

    # This function rewrites a deepagents internal in two places. Fail here,
    # naming the ledger item, rather than deep inside a tool call later.
    require_deepagents_version("U.4")
    require_deepagents_attr("deepagents.backends.utils", "validate_path", "U.4")
    require_deepagents_attr(
        "deepagents.middleware.filesystem", "validate_path", "U.4"
    )

    import deepagents.backends.utils as _utils
    import deepagents.middleware.filesystem as _fs_mw
```

The existing patching logic below is unchanged — `_utils` and `_fs_mw` keep their current meaning.

- [ ] **Step 6: Wire the guard into task_anchor (U.13)**

In `src/rudra/middleware/task_anchor.py:20`, replace:

```python
from deepagents.middleware._utils import append_to_system_message
```

with:

```python
from rudra.compat.version_guard import require_deepagents_attr

# Private deepagents module — no stability guarantee. See TODO.md U.13.
append_to_system_message = require_deepagents_attr(
    "deepagents.middleware._utils", "append_to_system_message", "U.13"
)
```

Everything using `append_to_system_message` (`task_anchor.py:54`) is unchanged.

- [ ] **Step 7: Verify both guards work in situ**

Run:
```bash
.venv/bin/python -c "
from rudra.middleware.task_anchor import append_to_system_message
print('task_anchor import OK:', callable(append_to_system_message))
"
.venv/bin/python -c "
import pathlib, tempfile
from rudra.compat.deepagents_path import install_path_normalizer
install_path_normalizer(pathlib.Path(tempfile.mkdtemp()))
print('install_path_normalizer OK')
"
.venv/bin/ruff check src/ 2>&1 | tail -3
```

Expected: both print OK, ruff **≤ 213** errors.

- [ ] **Step 8: Run the whole suite**

Run:
```bash
.venv/bin/pytest tests/ -v
```

Expected: everything passes.

Note `install_path_normalizer` patches `validate_path`, so if pytest ever runs it before `test_validate_path_is_shared_object`, that identity assertion breaks. No test in this suite calls it — the Step 7 check is a one-off subprocess. If a future test does, it must restore the original.

- [ ] **Step 9: Mark U.4 and U.13 done in TODO.md**

Set both to `**DONE** 2026-08-05` with the verifying output.

- [ ] **Step 10: Commit**

```bash
git add src/rudra/compat/version_guard.py tests/test_version_guard.py \
        src/rudra/compat/deepagents_path.py src/rudra/middleware/task_anchor.py TODO.md
git commit -m "feat: guard the two deepagents internal-API dependencies (U.4, U.13)

Rudra reaches past deepagents' public API twice: deepagents_path.py
monkeypatches validate_path in two modules, and task_anchor.py imports
the private middleware._utils. Unguarded, a version bump breaks Rudra
somewhere far from the cause.

version_guard raises DeepagentsCompatError at the point of contact,
naming the TODO.md item to re-verify and the version actually found.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Live smoke run and close-out (U.5, U.6, U.12)

**Files:**
- Modify: `TODO.md`

**Interfaces:**
- Consumes: everything above.
- Produces: recorded U.12 evidence and a branch ready to merge.

- [ ] **Step 1: Confirm the smoke model is reachable and tool-calling**

Run:
```bash
timeout 180 curl -s http://localhost:11434/api/chat -d '{
 "model":"gemma4:31b-cloud",
 "messages":[{"role":"user","content":"What is 17*23? Use the calc tool."}],
 "stream":false,
 "tools":[{"type":"function","function":{"name":"calc","description":"Evaluate arithmetic","parameters":{"type":"object","properties":{"expr":{"type":"string"}},"required":["expr"]}}}]
}'
```

Expected: a response containing `"tool_calls"`. deepagents hard-requires tool calling; without it the smoke run is meaningless.

If it returns `{"error":"unauthorized"}`, Ollama cloud signin has lapsed. Ask the user to run `ollama signin`. Do not substitute a different model without asking — it changes what U.12 attests.

- [ ] **Step 2: Point Rudra at the smoke model**

Rudra still reads Ollama settings from env (`config.py:12-22`); the provider-agnostic factory is Step 5 of the execution order, not this one.

Run:
```bash
SMOKE_DIR=$(mktemp -d)
echo "$SMOKE_DIR"
cd "$SMOKE_DIR" && \
OLLAMA_MODEL_PLANNER=gemma4:31b-cloud \
OLLAMA_MODEL_CODER=gemma4:31b-cloud \
OLLAMA_BASE_URL=http://localhost:11434 \
timeout 900 /home/a/code/ai-ml/agent/RudraAnvil/.venv/bin/rudra \
  "build a python CLI that reverses a string" 2>&1 | tee "$SMOKE_DIR/smoke.log"
```

Confirm the env var names against `src/rudra/config.py:12-22` before running — if they differ, use the actual names.

Expected: the run completes without a traceback.

- [ ] **Step 3: Check the smoke artifacts**

Run:
```bash
ls -la "$SMOKE_DIR" "$SMOKE_DIR/.rudra"
cat "$SMOKE_DIR/.rudra/PLAN.md"
find "$SMOKE_DIR" -name '*.py' -not -path '*/.rudra/*' -size +0 -exec ls -la {} \;
```

Expected: `.rudra/PLAN.md` exists and is non-empty; at least one non-empty `.py` file outside `.rudra/`.

**Non-empty files are the load-bearing check.** `virtual_mode=True` plus the deleted subclass is exactly the combination that could silently stop writing to disk (Task 1, Blocker A). A run that "succeeds" with zero-byte files is a U.3 failure, not a pass.

Do not judge the code's quality — Step 1's success criterion is *"behaves exactly as it does today"*, and today's file-existence check is A1.8, a Step 9 item.

- [ ] **Step 4: Run the full acceptance checklist**

Run:
```bash
cd /home/a/code/ai-ml/agent/RudraAnvil
.venv/bin/python -c "from importlib.metadata import version; print('deepagents', version('deepagents'))"
.venv/bin/pytest tests/ -v
.venv/bin/rudra --version
.venv/bin/ruff check src/ 2>&1 | tail -3
git status --short
```

Expected, in order: `deepagents 0.7.4`; all tests pass; `0.1.0` printed with no traceback; **≤ 213** ruff errors; a clean tree.

- [ ] **Step 5: Record U.12 evidence and close out the ledger**

In `TODO.md`:

1. Set `U.5` to `**DONE** 2026-08-05` — no code change needed; `planning_tools.py:3` already replaced `write_todos`, and `test_default_stack_has_no_write_todos_tool` documents the delta.
2. Set `U.6` to `**DONE** 2026-08-05` — `main_agent.py:554` always passed an instance; `test_create_deep_agent_accepts_a_backend_instance` locks it.
3. Set `U.12` to `**DONE** 2026-08-05` and paste the actual smoke output: the model used, `PLAN.md` size, the generated files and their byte counts, and the pytest summary line.
4. In Section E Stage I, mark **Step 1** complete and note that Step 2 (`C0.2`, `C0.3`, `A2.1–A2.6`, D7) is unblocked.

Paste real command output. Do not paraphrase a result you did not see.

- [ ] **Step 6: Remove the probe venv**

```bash
rm -rf .venv-probe
```

Its `.gitignore` entry stays — the next upgrade will want it.

- [ ] **Step 7: Commit**

```bash
git add TODO.md
git commit -m "docs(todo): close out Step 1 — deepagents 0.7.4 upgrade (U.5, U.6, U.12)

U.5 and U.6 needed no code change: planning_tools.py:3 already replaced
write_todos, and main_agent.py:554 always passed a backend instance.
Both are now locked by contract tests rather than assumed.

U.12 smoke run against gemma4:31b-cloud produced a non-empty PLAN.md
and non-empty source files, confirming FilesystemBackend under
virtual_mode=True still writes through to disk after U.3.

Step 2 (C0.2, C0.3, A2.1-A2.6, D7) is unblocked.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Report merge readiness**

Summarize for the user: commits on the branch, the acceptance-criteria results with real output, and anything logged as new `PENDING` along the way. Do **not** merge to `main` or push without being asked.

---

## Spec Coverage Check

| Spec section | Covered by |
|---|---|
| §5 phase 1 branch | Already done — branch `upgrade/deepagents-0.7.4` exists |
| §5 phase 2 probe venv | Task 1 |
| §5 phase 3 contract tests | Task 2 |
| §5 phase 4 source edits | Tasks 3, 4, 6, 7 |
| §5 phase 5 migrate `.venv` | Task 5 |
| §5 phase 6 smoke run | Task 8 |
| §6.1 contract tests 1–10 | Task 2 Step 1 — plus `test_virtual_mode_writes_through_to_disk`, added after the spec was written |
| §6.1 `_strip_fences` unit tests | Task 3 Step 1 |
| §6.2 U.3a/U.3b | Task 6 |
| §6.2 U.4 guard | Task 7 |
| §6.2 F2 / U.13 guard | Task 7 |
| §6.2 F1a / U.14 | Task 4 |
| §6.2 F1b / U.15 | Task 3 |
| §6.2 U.5, U.6 no-change | Task 8 Step 5, locked by Task 2 tests |
| §6.3 dependencies | Task 5 |
| §7 acceptance criteria 1–6 | Task 8 Step 4 |
| §8 ledger additions | Already committed in `670c5c3` |
| §9 risks | Task 1 Steps 2, 6; Task 5 Step 5; Task 8 Step 1 |

**Deviation from the spec, deliberate:** §5 orders source edits (phase 4) before the `.venv` migration (phase 5). This plan migrates first, because deleting `OverwriteFilesystemBackend` while `.venv` is on 0.4.12 restores the `write → error → edit no-op → write` loop the subclass existed to prevent. Version-independent edits (Tasks 3, 4) still precede the migration so the planner is never left without fence coverage.
