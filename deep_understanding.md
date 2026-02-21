# RudraAnvil — Deep Understanding

## Architecture Overview

```
User CLI → cli.py (Typer) → create_main_agent() → create_deep_agent() (deepagents v0.4.1)
                                                    ↑
                                    FilesystemBackend(root_dir=project_path, virtual_mode=True)
                                    ChatOllama(model="gpt-oss:20b", num_predict=8192)
                                    custom_tools: run_python, run_command, lint_python, etc.
```

## Module Breakdown

| Module | Role |
|---|---|
| [cli.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py) | Typer CLI with 8 commands: [build](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py#64-120), [chat](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py#122-194), [fix](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py#196-247), [edit](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/backends/filesystem.py#334-384), [review](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py#299-343), [suggest](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py#345-382), [resume](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py#384-477), [watch](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/cli.py#479-554) |
| [agent/main_agent.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py) | [create_main_agent()](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py#344-446) factory, [RudraAnvilAgent](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py#181-342) wrapper, [build_system_prompt()](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py#56-179) per command |
| [tools/code_tools.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py) | [run_python](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#27-58), [run_command](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#59-96), [lint_python](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#97-123), [format_python](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#124-153), [run_pytest](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#154-186), [check_syntax](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#187-206) |
| [tools/file_tools.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/file_tools.py) | [read_file](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/filesystem/virtual_fs.py#159-193), [write_file](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/file_tools.py#38-51), [edit_file](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/file_tools.py#52-69), etc. — all route through VFS (but NOT used by deepagents!) |
| [filesystem/virtual_fs.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/filesystem/virtual_fs.py) | In-memory VFS — used by RudraAnvil's own tools, NOT by deepagents |
| [filesystem/sync.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/filesystem/sync.py) | [FileSyncManager](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/filesystem/sync.py#56-272) — syncs VFS → real disk (OVERWRITE/BACKUP/DIFF_ONLY) |
| [state/todo.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/state/todo.py) | [TodoList](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/state/todo.py#66-184) + [TodoItem](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/state/todo.py#23-64) with status tracking (PENDING/IN_PROGRESS/COMPLETED/FAILED/BLOCKED) |
| [state/checkpoint.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/state/checkpoint.py) | JSON-based session saves in `.rudraanvil/` folder |
| [config.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/config.py) | Loads from [.env](file:///home/a/code/ai-ml/agents/RudraAnvil/.env): `OLLAMA_MODEL`, `OLLAMA_BASE_URL`, etc. |

## Key Data Flow for `rudraanvil build "..."`

1. CLI calls [create_main_agent(project_path, task, command="build")](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py#344-446)
2. Loads [VirtualFileSystem](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/filesystem/virtual_fs.py#51-394) from disk (just for display/tree), creates checkpoint
3. Builds command-specific [system_prompt](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py#56-179) via [build_system_prompt()](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py#56-179)
4. Creates [FilesystemBackend(root_dir=project_path, virtual_mode=True)](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/backends/filesystem.py#28-669) — writes DIRECTLY to disk
5. Creates `ChatOllama(model="gpt-oss:20b", num_predict=8192, temperature=0.7)`
6. [create_deep_agent()](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/graph.py#51-301) wires up middleware stack:
   - `TodoListMiddleware` — manages `write_todos` tool
   - `FilesystemMiddleware` — provides [ls](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/backends/filesystem.py#148-254), [read_file](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/filesystem/virtual_fs.py#159-193), [write_file](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/file_tools.py#38-51), [edit_file](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/file_tools.py#52-69), [glob](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/backends/filesystem.py#529-599), [grep](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/backends/filesystem.py#385-423)
   - `SubAgentMiddleware` — enables `task` tool for spawning sub-agents
   - `SummarizationMiddleware` — compresses long message history
   - `AnthropicPromptCachingMiddleware` — no-op for Ollama (ignored)
   - `PatchToolCallsMiddleware` — normalizes tool call formats
7. `agent.invoke({"messages": [...]}, {"recursion_limit": 100})` — LangGraph runs the agent loop
8. deepagents handles its own file tools (NOT the [file_tools.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/file_tools.py) custom tools)
9. After invoke: RudraAnvil's own VFS sync runs (mostly no-op since deepagents wrote files already)
10. Result extracted from last message and displayed as Rich panel

## Important Architecture Notes

- **VFS is NOT used by deepagents**: [create_file_tools()](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/file_tools.py#13-143) is defined but **never passed to [create_deep_agent()](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/graph.py#51-301)**. Only [create_code_tools(vfs)](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#16-215) is passed as the [tools](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/tools/code_tools.py#16-215) parameter. deepagents uses its own [FilesystemBackend](file:///home/a/.local/share/pipx/venvs/rudraanvil/lib/python3.12/site-packages/deepagents/backends/filesystem.py#28-669) for all file I/O.
- **Previous bug fixed**: `num_predict=8192` was added to prevent Ollama from truncating tool call JSON mid-generation (default was 128 tokens which caused "unexpected end of JSON input" errors).
- **deepagents v0.4.1 vs workflow.md**: [workflow.md](file:///home/a/code/ai-ml/agents/RudraAnvil/workflow.md) references deepagents 0.3.8 API but installed version is 0.4.1. API has changed significantly.
- **[workflow.md](file:///home/a/code/ai-ml/agents/RudraAnvil/workflow.md) references FastAPI integration** — this is design documentation, not actual code.
- **Indentation bug in [main_agent.py](file:///home/a/code/ai-ml/agents/RudraAnvil/src/rudraanvil/agent/main_agent.py)**: Line 434 has wrong indentation (`   virtual_mode=True` — 3 spaces instead of 8).
- **Ollama model**: `gpt-oss:20b` (a GPT4-class local model via Ollama)
- **Environment**: Installed via `pipx install -e .` (editable mode), runs in `/home/a/.local/share/pipx/venvs/rudraanvil/`

## deepagents v0.4.1 Built-in Tools

When `create_deep_agent()` is called with `backend=FilesystemBackend(...)`:
- `write_todos(todos: list)` — create/update todo list
- `ls(path)` — list directory
- `read_file(file_path, offset, limit)` — read file content with line numbers
- `write_file(file_path, content)` — create new file (fails if exists!)
- `edit_file(file_path, old_string, new_string, replace_all)` — string replacement
- `glob(pattern, path)` — glob file search
- `grep(pattern, path, glob)` — literal text search
- `execute(command)` — shell commands (only if `SandboxBackendProtocol` implemented)
- `task(...)` — spawn sub-agents

## Config (`.env`)
```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gpt-oss:20b
OLLAMA_TEMPERATURE=0.7
OLLAMA_TIMEOUT=300
MAX_AGENTS=6
MAX_ITERATIONS=100
CHECKPOINT_INTERVAL=5
```
