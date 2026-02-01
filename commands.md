### Detailed Explanation of codekraken CLI Commands

Hey Archish, great question — diving into the CLI commands will help solidify how users interact with codekraken beyond the initial build. Since we're shaping this deepagent together, I'll explain each command we discussed earlier (from the interaction modes table), including what it does, typical usage, and how the LLM/agent (built with deepagents) processes/uses it internally. I'll focus on the "resume" one as you highlighted, especially around checkpoints.

Remember, all commands default to the current working directory (cwd) as the project root (no need for `--project-dir` most times), and they leverage your core workflow: main supervisor agent + sub-agents, virtual FS, todo lists, tools (including the new MCP commit tool we added), and FS syncing. Options like `--dry-run`, `--verbose`, `--no-commit` can apply to most.

I'll structure this as a list for clarity, with sub-sections for each command.

#### 1. `codekraken build --task "Your description here"` (Core Command for One-Shot Tasks)
   - **What it Does**: This is the primary "workhorse" command for creating new projects or making isolated changes to existing ones. It takes a high-level task description and runs the full agent workflow to plan, decompose, execute, and sync changes. For new projects: Builds from scratch. For existing: Analyzes current files, adds/enhances/fixes based on the task.
     - Example: `codekraken build "Add rate limiting to all endpoints using slowapi"`
     - Output: Streams progress (e.g., "Planning...", "Spawning auth sub-agent...", "Committing changes via MCP"), then syncs files to disk, generates/updates README, and commits (if enabled).
     - Use Cases: Bootstrapping a project, adding a feature, refactoring a module. It's "one-and-done" — runs once and exits.
   - **How the LLM/Agent Uses It**: 
     - CLI parses the `--task` into the agent's input config.
     - Main agent starts with planning node: Generates/adapts todo list based on task (e.g., "Scan existing routers.py", "Add middleware").
     - Spawns 2-6 sub-agents for parallelism (e.g., one for code edits, one for tests).
     - Iterative loop: Agents use tools (file read/write, code execution for testing, MCP commit).
     - On completion: Final review, MCP commit, FS sync.
     - LLM "uses" it by reasoning over the task in cycles — no user input mid-run.
   - **Flags/Options**: `--task` (required), `--stack "FastAPI+Redis"` (optional tech prefs), `--max-agents 4` (limit subs), `--resume-from checkpoint.json` (links to resume command below).

#### 2. `codekraken chat` (Interactive/Iterative Mode)
   - **What it Does**: Enters a REPL-like (read-eval-print-loop) chat session where users can have ongoing conversations with the agent. Each message is treated as a mini-task, allowing back-and-forth refinements. Session persists context (e.g., previous changes/todo state) until exit.
     - Example: 
       ```
       codekraken chat
       codekraken> Add email verification to user registration
       [agent works, syncs, commits]
       codekraken> Tests failed — debug and fix
       [agent iterates]
       codekraken> /exit
       ```
     - Output: Real-time responses with progress, plus FS syncs/commits after each meaningful reply.
     - Use Cases: Daily dev workflow, debugging chains of issues, exploratory changes. Feels like Cursor/Claude in terminal.
   - **How the LLM/Agent Uses It**: 
     - Loads/creates persistent agent state (from deepagents checkpoint if exists).
     - Each user message → new input to main agent: Treats it as a task, updates todo list (e.g., appends "Fix test failure in auth.py").
     - Runs partial workflow: Planning + sub-agents + tools, but shorter cycles since context is warm.
     - LLM reasons conversationally (prompt includes chat history), calls tools as needed, and decides when to commit/sync.
     - On exit: Autosaves checkpoint for resume.
   - **Flags/Options**: `--verbose` for more logs, `--no-sync` to preview changes without writing.

#### 3. `codekraken fix --issue "Description of problem"` (Targeted Bugfix Mode)
   - **What it Does**: Focuses on diagnosing and repairing specific issues, like errors or failures. Scans relevant files/logs, proposes fixes, applies them, and tests.
     - Example: `codekraken fix --issue "JWT token refresh returns 401 unauthorized"`
     - Output: "Analyzing error...", "Found issue in auth.py line 45", applies fix, runs tests (via tool), commits.
     - Use Cases: Quick patches for bugs, test failures, or runtime errors. Narrower than `build`.
   - **How the LLM/Agent Uses It**: 
     - `--issue` becomes the primary task input.
     - Main agent prioritizes "debug" todos: e.g., "Run code execution tool on failing endpoint", "Edit file to fix".
     - Spawns fewer subs (1-2, e.g., debugger + tester).
     - LLM uses reasoning to hypothesize causes (prompt: "You are a debugger — analyze error and fix iteratively").
     - Ends with MCP commit if fixed.
   - **Flags/Options**: `--file "auth.py"` (limit scope), `--run-tests` (force test tool).

#### 4. `codekraken edit --file "path/to/file.py" --instruction "What to change"` (Precise Edit Mode)
   - **What it Does**: Makes targeted modifications to specific files, without full project rebuild. Reads the file, applies instructions, and validates.
     - Example: `codekraken edit --file app/routers/todos.py --instruction "Add pagination with limit=20 offset"`
     - Output: Shows diff/preview, applies edit, commits.
     - Use Cases: Small tweaks, like updating a function or adding a line. Complements `fix` for non-bug changes.
   - **How the LLM/Agent Uses It**: 
     - Loads only the specified file into virtual FS.
     - Task input: `--instruction` + file context.
     - Main agent uses `edit_file` tool directly; minimal sub-agents (0-1).
     - LLM prompt: "You are a code editor — make precise changes without side effects".
     - Syncs/commits only the edited file.
   - **Flags/Options**: `--preview` (show diff before apply).

#### 5. `codekraken review` (Code Review/Audit Mode)
   - **What it Does**: Analyzes the entire project (or subset) for issues like best practices, security, performance. Outputs a report with suggestions, but doesn't auto-apply changes.
     - Example: `codekraken review`
     - Output: Markdown report e.g., "Vuln in auth: Use bcrypt over md5 [建议 fix]", "Optimize DB queries".
     - Use Cases: Pre-commit checks, code quality passes.
   - **How the LLM/Agent Uses It**: 
     - No task input needed — default to full scan.
     - Main agent spawns review subs (e.g., security, perf).
     - LLM reasons over files (prompt: "Act as code reviewer — list issues with severity").
     - Uses tools like linting/code execution, but no writes/commits (read-only).
   - **Flags/Options**: `--focus "security"` (narrow scope).

#### 6. `codekraken suggest --task "Idea or improvement"` (Suggestion-Only Mode)
   - **What it Does**: Similar to review, but proposes enhancements for a specific task without applying. Outputs code snippets, diffs, or plans.
     - Example: `codekraken suggest --task "Make the API more secure against SQL injection"`
     - Output: "Suggested: Add SQLAlchemy filters in models.py — here's the diff patch".
     - Use Cases: Brainstorming ideas, getting alternatives before committing.
   - **How the LLM/Agent Uses It**: 
     - Like `build` but skips sync/commit — outputs to console only.
     - Agent plans todos but executes in simulation (virtual FS, no real write).
     - LLM generates suggestions via reasoning (prompt: "Propose but don't apply").
   - **Flags/Options**: `--detail high` (more verbose diffs).

#### 7. `codekraken resume --session-id "123abc"` (Resume Interrupted Session)
   - **What it Does**: Picks up a paused or interrupted long-running task exactly where it left off. Loads saved state (todos, virtual FS, agent memory) and continues.
     - Example: `codekraken resume --session-id my-long-build`
     - Output: "Resuming from checkpoint: Todo 3/7 complete...", then proceeds like `build`.
     - Use Cases: Handling crashes, timeouts, or manual pauses in complex projects (e.g., full-stack build taking hours).
   - **How the LLM/Agent Uses It**: 
     - Loads deepagents checkpoint (state dict with todos, FS, sub-agent outputs).
     - Main agent restarts from last node (e.g., mid-execution loop).
     - LLM continues reasoning from saved context (no re-planning from scratch).
     - Finishes, syncs, commits as normal.
   - **Flags/Options**: `--session-id` (required, or path to checkpoint file like `checkpoint.json`).

#### 8. `codekraken watch` (Live/Auto Mode — Advanced/Optional)
   - **What it Does**: Monitors the project folder for changes (e.g., user edits files or git commits) and automatically suggests/applies fixes or improvements.
     - Example: `codekraken watch`
     - Output: Runs in background — "Detected change in main.py: Suggesting lint fixes [apply? y/N]".
     - Use Cases: Hot-reload-like agent assistance during manual coding.
   - **How the LLM/Agent Uses It**: 
     - Uses a library like `watchdog` to detect FS events.
     - On event: Triggers mini-agent cycle (e.g., review changed file, suggest edits).
     - LLM prompt: "Watch for issues — propose fixes on the fly".
     - Can auto-commit if user approves.
   - **Flags/Options**: `--auto-apply` (risky, auto-sync without ask).

### Deep Dive on Resume and Checkpoints (Your Main Question)
- **What is Resume?**: It's for continuity in long tasks. Without it, users restart from zero (wasteful for big projects). Deepagents supports this natively via "state persistence and checkpointing" (from your original workflow) — it saves the graph state (todos, FS, configs) as JSON or pickle.
- **Who Creates Checkpoints?**:
  - **Primarily the System/Agent**: Automatic creation is best.
    - During long runs: Agent saves periodically (e.g., after every 5 todos or 10min via deepagents `checkpoint` hook).
    - On interrupt: Catch Ctrl+C, autosave before exit (use Python's `signal` module).
    - In chat mode: Save on each response or /exit.
    - Where stored: In project dir as `.codekraken/checkpoint-<id>.json` (git-ignored).
  - **User Can Trigger Manually**: For control.
    - Add a subcommand: `codekraken save --session-id "my-build"` — Dumps current state.
    - Or in chat: Type `/save` to checkpoint mid-session.
    - How user creates: Just run the command — system handles serialization (e.g., `agent.save_state("checkpoint.json")` if deepagents has that; otherwise, custom dict dump).
- **Implementation Tip**: Use deepagents' built-in persistence (e.g., `agent.with_checkpoint()` or LangGraph's state saver). Session-ID can be a hash of timestamp/task for uniqueness.

This setup makes codekraken feel polished and user-friendly. If we add more (e.g., `codekraken test` for standalone testing), it fits the pattern. What's your take — want code snippets for checkpointing, or tweak any command?