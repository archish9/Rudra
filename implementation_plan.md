# Code Fixes: Remove Subagent + Better read_plan Redirect

## What's Broken

Two issues from the latest log:

1. **Model calls `task` tool first** (step [2]) → `BlockTaskToolMiddleware` catches it, but the model has already wasted a turn and is now confused
2. **Model responds conversationally** (step [6]) — after `read_plan()` returns "No plan found. Call update_plan()", model asks the USER for filenames instead of generating them itself

**Root causes:**
- The `task` tool **exists** because a subagent is registered at L596-604. qwen3:14b sees it in its tool list and uses it despite "NEVER" in the system prompt.
- The `read_plan()` no-plan message ("Call update_plan() to create one first.") is too vague. The model doesn't know what to pass to `update_plan()`, so it asks the user.

---

## Proposed Changes

### Fix 1: Remove Subagent Registration

#### [MODIFY] [main_agent.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/agent/main_agent.py)

**Delete L551-603** — the entire subagent prompt and the `subagents=[...]` parameter from `create_deep_agent()`.

Change from:
```python
deep_agent = create_deep_agent(
    model=model,
    tools=custom_tools,
    system_prompt=system_prompt,
    backend=filesystem_backend,
    checkpointer=checkpointer,
    memory=[".rudra/AGENTS.md"],
    middleware=main_agent_middleware,
    subagents=[                          # ← DELETE THIS
        {
            "name": "general-purpose",
            ...
        }
    ],
)
```

To:
```python
deep_agent = create_deep_agent(
    model=model,
    tools=custom_tools,
    system_prompt=system_prompt,
    backend=filesystem_backend,
    checkpointer=checkpointer,
    memory=[".rudra/AGENTS.md"],
    middleware=main_agent_middleware,
    # No subagents — model writes all files directly with write_file().
)
```

Also delete the entire `subagent_prompt` string (L554-583) — it's dead code once the subagent is removed.

> [!IMPORTANT]
> This is the most important fix. No subagent = no `task` tool in the model's tool list = **impossible** to call it. The model will only see `update_plan`, `read_plan`, `write_file`, `edit_file`, `read_file`.

---

### Fix 2: Make `read_plan()` No-Plan Response Actionable

#### [MODIFY] [planning_tools.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/tools/planning_tools.py#L121)

Change the "no plan found" return from:
```python
return "No plan found. Call update_plan() to create one first."
```

To a response that includes the task and a concrete example so the model can act immediately:
```python
return (
    f"No plan found. You MUST call update_plan() now with a filename checklist.\n\n"
    f"Your task: {task}\n\n"
    f"Example — call update_plan() with something like:\n"
    f"  update_plan(plan_markdown='- [ ] app.py\\n- [ ] models.py\\n- [ ] requirements.txt')\n\n"
    f"List ONLY filenames. Do NOT ask the user — decide the files yourself based on the task above."
)
```

This gives the model: (a) the task to reference, (b) the exact tool call format to use, (c) explicit instruction NOT to ask the user.

---

### Fix 3: Clean Up Dead Code

#### [MODIFY] [planning_tools.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/tools/planning_tools.py)

The `delegate_to_subagent` parameter and the `if delegate_to_subagent:` branch (L81-98) are dead code — only `False` is ever passed now. Remove the parameter and keep only the direct-write branch.

#### [MODIFY] [main_agent.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/agent/main_agent.py)

Remove `delegate_to_subagent=False` from the `create_planning_tools()` call (L542) since the parameter no longer exists.

#### [DELETE content only] `BlockTaskToolMiddleware` — keep or remove?

With no subagent, the `task` tool won't exist, so `BlockTaskToolMiddleware` will never trigger. **Keep it as a safety net** (deepagents might register internal tools) but it's no longer the primary defense. No code change needed.

---

## File Change Summary

| # | File | Change | Lines Affected |
|---|------|--------|---------------|
| 1 | [main_agent.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/agent/main_agent.py) | Remove `subagents=[...]` param and delete `subagent_prompt` | L551-604 |
| 2 | [main_agent.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/agent/main_agent.py) | Remove `delegate_to_subagent=False` from `create_planning_tools()` call | L542 |
| 3 | [planning_tools.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/tools/planning_tools.py) | Better no-plan message with task + example | L121 |
| 4 | [planning_tools.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudra/tools/planning_tools.py) | Remove `delegate_to_subagent` param and dead `if` branch | L27, L81-98 |

**Total: ~65 lines deleted, ~10 lines added/changed across 2 files.**

---

## Verification Plan

### Test 1: No `task` Tool Call
```bash
rm -rf coding-files/.rudra coding-files/*.py coding-files/.env
rudra --project-dir coding-files
# Type: Flask REST API with SQLite for a todo app
```
**Expected:** Step [2] should be `CALL update_plan`, NOT `CALL task`. No "ERROR: The task subagent tool is disabled" message anywhere.

### Test 2: No Conversational Fallback
**Expected:** After `read_plan()` returns no-plan message, model calls `update_plan()` with filenames — does NOT output a text message asking the user for filenames.

### Test 3: Full File Generation
**Expected:** Agent creates plan → writes all files → completes with ✅.
