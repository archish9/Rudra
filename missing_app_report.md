# Why app.py Is Missing: Subagent Stops Early

## What Was Generated (Good News: Quality Is Excellent!)

```
coding-files/
├── .env                 (4 lines)   ✅ DB URL, secret key, algorithm, expiry
├── requirements.txt     (7 lines)   ✅ fastapi, uvicorn, sqlalchemy, asyncpg, python-jose, passlib, python-dotenv
├── database/
│   └── models.py        (30 lines)  ✅ User + Todo SQLAlchemy models with relationships
├── auth/
│   ├── utils.py         (50 lines)  ✅ Password hashing, JWT create/verify, OAuth2 scheme
│   └── router.py        (55 lines)  ✅ Login endpoint, /users/me, proper error handling
├── .rudra/                     ← State files (PLAN.md, checkpoints.db, etc.)
└── ❌ app.py            MISSING     ← The main entry point!
    ❌ database.py       MISSING     ← The database connection setup!
```

The code is legitimately good — proper bcrypt hashing, JWT tokens, SQLAlchemy ORM, async DB, FastAPI routers. **But the app can't run without `app.py` and `database.py`.**

---

## Problem 1 — Subagent stopped after 5 files, skipping app.py

### The timeline

| Step | File written | In plan as | Status |
|------|-------------|-----------|--------|
| [5] | `/requirements.txt` | `requirements.txt` | ✅ Match |
| [7] | `/.env` | `.env` | ✅ Match |
| [9] | `/database/models.py` | `models.py` | ⚠️ Renamed to subdirectory |
| [11] | `/auth/utils.py` | `dependencies.py` | ⚠️ Renamed |
| [13] | `/auth/router.py` | — | ❗ Not in plan — bonus file |
| [14] | **Subagent returns** | | **app.py and database.py never written** |

The subagent wrote 5 files then returned with:
```
"I'm ready to help with any task you'd like me to perform."
```

This is qwen3's **Chinese-trained greeting behavior** in English — it thinks the conversation is starting fresh. The subagent lost track of the task list after 5 writes.

### Why it stopped

The subagent prompt says:
> "Keep going until EVERY file in the task description is written."

But with `reasoning=True`, qwen3:14b's thinking tokens consume context. After 5 `write_file` calls (each generating 30-115 lines of code), the subagent's effective context has:
- System prompt (~1500 tokens)
- Task description (~200 tokens)  
- 5 write_file calls with full code content (~2000+ tokens each)
- 5 tool results (~100 tokens each)
- Reasoning blocks (~500+ tokens each)

**Total: ~15,000-20,000 tokens consumed.** With a 40K context window, the model still has room, but the **task description from step [4] is now far back in context**. qwen3:14b has poor attention over long distances and "forgets" which files it hasn't written yet.

---

## Problem 2 — Subagent deviated from the plan

The plan said: `models.py, dependencies.py`
The subagent wrote: `database/models.py, auth/utils.py, auth/router.py`

The subagent **reorganized the project structure** on its own initiative, creating `database/` and `auth/` subdirectories. This is actually a better architecture, but:
1. It doesn't match the plan items, making check-offs impossible
2. It forgot to also write the files that the subdirectories replaced (`database.py` for DB connection setup)

This happens because the subagent prompt includes:
```
"## WRITE ORDER — dependencies first
Write files in this order so imports resolve correctly:
requirements.txt / pyproject.toml → database/models → auth/utils → routes → main/app"
```

The model interpreted `database/models` and `auth/utils` as **directory/file paths** and created that structure. It then followed `routes → main/app` as "next" but stopped before getting there.

---

## Problem 3 — "Files created: 7" includes .rudra state files

The count comes from [main_agent.py:377-383](file:///home/a/code/ai-ml/agent/Rudra/src/rudra/agent/main_agent.py#L377-L383):

```python
new_vfs = VirtualFileSystem(self.context.project_path)
new_vfs.load_from_disk()
original_paths = set(self.context.vfs.files.keys())
new_paths = set(new_vfs.files.keys())
files_created = list(new_paths - original_paths)  # 7 files
```

It scans the entire project directory and counts ALL new files, including:
1. `requirements.txt` ← actual code file
2. `.env` ← actual code file
3. `database/models.py` ← actual code file
4. `auth/utils.py` ← actual code file
5. `auth/router.py` ← actual code file
6. `.rudra/PLAN.md` ← state file, not code!
7. `.rudra/tech_stack.md` ← state file, not code!

So **5 actual code files + 2 state files = 7**. The VFS `_is_ignored` pattern for `.rudra/*` should filter these out, but `load_from_disk()` may not be excluding them consistently from the count.

---

## Problem 4 — Main agent doesn't verify completeness

After the subagent returns (step [14]), the main agent immediately finishes. It never:
1. Reads the plan to check which items are still `- [ ]` unchecked
2. Compares files on disk against the plan
3. Re-runs the subagent for missing files

The entire PLAN.md is still **all unchecked** — no item was marked `[x]`. The main agent ignores this completely.

---

## Root Causes and Fixes

| # | Root Cause | Fix |
|---|-----------|-----|
| **1** | Subagent stops early — forgets remaining files as context grows | **Inject remainder list into each write_file result** (like ContinueAfterWriteMiddleware did, but simpler — just: "Files still to write: app.py, database.py") |
| **2** | Main agent doesn't verify completeness after task() returns | **Add post-task verification**: read plan, check if all items exist on disk, re-run subagent for missing files |
| **3** | Subagent deviates from planned filenames | **Include explicit filenames in the subagent system prompt**, not just examples in WRITE ORDER. Or: don't mandate plan filenames at all — let the subagent decide its own structure |
| **4** | File count includes .rudra/ state files | **Filter `.rudra/` from `files_created` count** |
| **5** | Subagent prompt's WRITE ORDER example looks like directory paths | Remove `database/models → auth/utils` from the template — let the model decide structure based on the task |

### Most impactful fix: Post-task verification in the main agent

After `task()` returns, the main agent should:
```
1. Read PLAN.md  
2. For each pending item, check if file exists on disk
3. If files are missing → call task() again with only the missing files
4. Repeat until all files exist
```

This turns the main agent into a **supervisor** that ensures completeness, rather than blindly trusting the subagent.
