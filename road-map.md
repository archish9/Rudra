Honest feedback:

### Don't fine-tune qwen3:14b on trajectory data. It's not needed for your architecture.

Here's why. The trajectory weakness we've been debugging all session is this:

> Model calls tool → gets result → **forgets what to do next** → writes Hello World / stops / asks user

This is a **code-writing loop problem**. In your dual-model architecture, **qwen3:14b doesn't do the writing loop.** Its job becomes:

```
Receive task → call update_plan() with correct file list → DONE
```

That's one tool call. No multi-turn loop. No continuation. No trajectory to lose. The middleware we built (`ContinueAfterWriteMiddleware`, `TaskAnchorMiddleware`) handles the coder's loop, and qwen3-coder:30b at 30B parameters will retain context much better anyway.

### What you SHOULD fine-tune qwen3:14b on

Fine-tune it on **planning data** — the mapping from task descriptions to correct project structures. The dataset you showed is bug reports/issue descriptions. That's valuable for the FIX/DEBUG intent, but for planning you need examples like:

| Input | Output (what `update_plan()` should receive) |
|-------|-------|
| "Flask REST API with SQLite for a todo app" | `app.py, models.py, routes.py, config.py, requirements.txt` |
| "FastAPI app with JWT authentication" | `main.py, auth.py, models.py, schemas.py, database.py, requirements.txt` |
| "React dashboard with charts" | `package.json, src/App.jsx, src/components/Dashboard.jsx, src/components/Chart.jsx, ...` |

**This is the 14b model's actual weakness right now** — it created a 3-file plan (`main.py, models.py, requirements.txt`) for a Flask REST API that needs at least 5-6 files. It doesn't know that a Flask app needs route handlers, database config, and a proper project structure. That's a knowledge gap, not a trajectory gap.

### Your roadmap, prioritized

| Priority | What | Why |
|----------|------|-----|
| **1** | Implement dual-model architecture | Eliminates the code quality problem by using a purpose-built coder model |
| **2** | Fine-tune qwen3:14b on **planning data** (framework → file structure + patterns) | Fixes the "3-file plan for a 6-file project" problem |
| **3** | Context management via filesystem | Prevents context overflow in both models |
| **4** | Fine-tune qwen3:14b on **your bug/issue dataset** | Improves FIX/DEBUG intent handling |
| **5** | MCPs (search, git, etc.) | Adds capabilities after the core is solid |
| ~~6~~ | ~~Trajectory fine-tuning~~ | ~~Not needed — dual-model + middleware handles this~~ |

### One warning about the dual-model implementation

The hardest part won't be the model split — it'll be the **handoff format** between planner and coder. The plan that qwen3:14b generates needs to contain enough context for qwen3-coder:30b to write correct code **without seeing the original user task**. If the plan just says `- [ ] main.py`, the coder model has the same problem — it doesn't know what to put in main.py.

The plan should look more like:
```
Task: Flask REST API with SQLite for a todo app
Framework: Python + Flask + SQLAlchemy + SQLite

- [ ] config.py       # Database URI, secret key, app config
- [ ] models.py       # Todo model: id, title, description, completed, created_at
- [ ] routes.py       # CRUD endpoints: GET /todos, POST /todos, PUT /todos/<id>, DELETE /todos/<id>
- [ ] app.py           # Flask app factory, register blueprints, init DB
- [ ] requirements.txt # flask, flask-sqlalchemy, flask-marshmallow
```

Each filename with a brief description of **what goes in it**. This is what the planning fine-tune should teach.