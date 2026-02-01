### Overview of the Coding/Project Development Agent API

This agent is designed to take a high-level project request (e.g., "Build a FastAPI app with JWT authentication, PostgreSQL database, and CRUD endpoints for a todo list") and autonomously produce a complete, working project structure with code files, dependencies, and instructions.

It leverages **deepagents==0.3.8** core features:
- **Hierarchical planning** via dynamic todo lists
- **Virtual filesystem** for reading/writing/editing files
- **Sub-agent spawning** for delegation and parallelism
- **Tool integration** (e.g., code execution, linting, testing if you add those tools)
- **State persistence** and checkpointing for long-running tasks

The system uses **one main agent** as the orchestrator, which dynamically spawns **multiple sub-agents** (typically 2–6 depending on project complexity) to handle specialized subtasks in parallel.

### Detailed Flow of Execution

When a user sends a request to the FastAPI endpoint:

1. **API Request Received**
    - FastAPI endpoint (e.g., `POST /develop-project`) receives:
        - `task`: the project description (required)
        - Optional: existing files (uploaded), tech stack preferences, constraints
    - The endpoint initializes a new agent state (including a fresh virtual filesystem and empty todo list).

2. **Main Agent Starts – Initial Planning Phase**
    - The main agent (created via `create_deep_agent()`) receives the user task.
    - It first enters a **planning node**:
        - Generates a high-level plan as a structured todo list.
        - Example todo items:
            - Research and decide on architecture/patterns
            - Set up project structure and dependencies
            - Implement authentication module
            - Implement database models and migrations
            - Implement CRUD endpoints
            - Write tests
            - Generate README and deployment instructions
        - The plan is adaptive: the agent can add, reorder, or mark items complete as work progresses.

3. **Task Decomposition and Sub-Agent Spawning**
    - The main agent analyzes the todo list and decides which tasks can be delegated.
    - It **spawns sub-agents** dynamically for parallel execution:
        - Each sub-agent gets its own focused prompt (e.g., "You are an expert backend developer. Implement JWT auth using these libraries…")
        - Sub-agents share the same virtual filesystem, so they can read/write the same files without conflicts.
        - Typical sub-agents spawned (2–6 total):
            - Architecture/Setup agent (1)
            - Authentication/Security agent (1)
            - Database/Models agent (1)
            - API Routes/Business Logic agent (1–2)
            - Testing agent (1)
            - Documentation agent (1)
        - The main agent acts as supervisor: it monitors sub-agent outputs, resolves conflicts, and updates the global todo list.

4. **Execution Loop (Iterative Development)**
    - Agents (main + subs) work in cycles:
        - Select next todo item
        - Reason about how to complete it
        - Use tools:
            - Write/create files (`write_file`, `create_directory`)
            - Read/edit existing files (`read_file`, `edit_file`)
            - List directory contents
            - Optional custom tools you add: code execution (run tests), linting, git simulation
        - If errors occur (e.g., syntax error detected via tool or reasoning), the agent:
            - Adds a new todo: "Fix error in auth.py line 45"
            - Iterates until resolved
    - Sub-agents report back completions or request clarification from the main agent.
    - Progress is streamed (via `agent.astream_events()` or `astream()`) so the API can send real-time updates to the client (e.g., "Planning complete", "Writing main.py", "Sub-agent fixing test failure").

5. **Convergence and Final Synthesis**
    - When the todo list is empty (or the main agent decides the project is complete):
        - Main agent runs a final review pass
        - Generates a summary report
        - Creates a `README.md` with setup/run instructions
        - Optionally runs final tests
    - The agent outputs:
        - All files in the virtual filesystem
        - A manifest of the project structure
        - Any errors/warnings

6. **API Response**
    - FastAPI collects the final state:
        - Zips the entire virtual filesystem
        - Returns:
            - JSON with summary, project structure tree, and download link
            - Or streams files directly
            - Or provides a temporary download endpoint
    - For long-running tasks, use background tasks + WebSockets/SSE for live progress.

### How Many Agents Are Needed?

- **1 Main Agent (Supervisor)**: Always required. Handles overall planning, todo management, delegation, conflict resolution, and final review.
- **N Sub-Agents (2–6 typically)**: Spawned dynamically based on complexity.
    - Small project (e.g., single script): 0–1 sub-agents (main agent does everything)
    - Medium project (e.g., FastAPI CRUD app): 3–4 sub-agents
    - Large project (e.g., full-stack with frontend): 5–6 sub-agents
- The library handles spawning automatically based on the main agent's decisions – you don't hard-code the number.

### Key Advantages of This Design

- **Adaptivity**: Todo list evolves; if tests fail, new fix tasks appear automatically.
- **Parallelism**: Sub-agents work concurrently on independent modules.
- **Safety**: All file operations are in a virtual sandbox.
- **Iterative Improvement**: Natural error correction loop without manual intervention.

### Example FastAPI Integration Snippet

```python
from fastapi import FastAPI, UploadFile, File
from deepagents import create_deep_agent
from langchain_openai import ChatOpenAI
import asyncio
import zipfile
import io

app = FastAPI()

llm = ChatOpenAI(model="gpt-4o")
# Add any custom tools here (e.g., code execution tool)
agent = create_deep_agent(llm=llm, tools=[])

@app.post("/develop-project")
async def develop_project(task: str, files: list[UploadFile] = File(None)):
    # Optional: load uploaded files into virtual FS
    config = {"input": task}
    
    result_files = {}
    async for event in agent.astream_events(config, version="v2"):
        # Stream progress to client if using SSE/WebSocket
        if event["event"] == "on_tool_end" and "file" in event["data"]:
            result_files.update(event["data"]["files"])
    
    # After completion, zip files
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for path, content in result_files.items():
            zip_file.writestr(path, content)
    
    # Return zip or save to storage and give link
    return {"status": "complete", "download": "..." }
```

This flow produces reliable, production-ready small-to-medium projects with minimal human oversight. For larger projects, you can add more specialized tools (testing, linting, deployment scripts). Let me know if you want help implementing specific parts!