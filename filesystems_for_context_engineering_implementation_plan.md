# Implementation Plan: Filesystem Context Engineering

This plan outlines the steps to implement the filesystem-based context engineering strategies to optimize RudraAnvil's token usage, stability, and planning capabilities.

## Proposed Changes

### 1. Context Offloading for Planning (PLAN.md)
**Goal:** Prevent the agent's context window from bloating with large todo lists.
- We will instruct the main agent to **stop** using the `write_todos` tool.
- We will create a new tool `update_plan(plan_markdown: str)` specifically for the main agent. This tool will overwrite a `.rudraanvil/PLAN.md` file.
- The main agent will use [read_file](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudraanvil/tools/file_tools.py#23-37) to read the plan and `update_plan` to update its checklist.

#### [MODIFY] [rudraanvil/agent/main_agent.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudraanvil/agent/main_agent.py)
- Update the main agent's system prompt to forbid `write_todos` and require `update_plan`.
- Add the `update_plan` tool to the main agent's tool layout.

#### [NEW] `rudraanvil/tools/planning_tools.py`
- Create the `update_plan` tool wrapper that writes directly to `.rudraanvil/PLAN.md` using the VirtualFileSystem.

---

### 2. Storing Project Context (tech_stack.md)
**Goal:** Remove massive tech stack descriptions and architecture rules from the eternal system prompt.

#### [MODIFY] [rudraanvil/cli.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudraanvil/cli.py)
- Before creating the agent, check if a `ProjectContext` exists.
- If it does, write the tech stack details, database, and custom architecture rules to `<project_path>/.rudraanvil/tech_stack.md`.

#### [MODIFY] [rudraanvil/agent/main_agent.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudraanvil/agent/main_agent.py)
- Remove the code that appends `project_context.primary_language`, database, etc. directly into the `subagent_prompt`.
- Instead, add a single static line to the prompt: *"Read `.rudraanvil/tech_stack.md` to understand the architecture, language, and frameworks you must use."*

---

### 3. Scratchpad for Execution Logs (cmd_output.txt)
**Goal:** Provide an execution tool that doesn't crash the agent when commands produce massive output.

#### [MODIFY] [rudraanvil/tools/code_tools.py](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudraanvil/tools/code_tools.py)
- Implement a `run_command(command: str)` tool.
- Instead of returning the raw stdout/stderr directly to the agent (which balloons the context), the tool will:
  1. Execute the command.
  2. Write the full combined stdout + stderr to `<project_path>/.rudraanvil/logs/cmd_output.txt`.
  3. Return a short string to the agent: `"Command executed with exit code X. Full output saved to .rudraanvil/logs/cmd_output.txt. Use read_file or grep to analyze the results."`
- Add this `run_command` tool to the general-purpose subagent's tool list so it can test the code it writes.

## Verification Plan

### Automated Tests
Currently, RudraAnvil is tested by running it against sample projects.

### Manual Verification
1. Run `rudraanvil build "Create an express app"` in a test directory.
2. Verify that `.rudraanvil/PLAN.md` is created and actively updated by the main agent instead of using `write_todos`.
3. Verify that `.rudraanvil/tech_stack.md` is generated with the context.
4. Check the agent's behavior to ensure it calls [read_file](file:///home/a/code/ai-ml/agent/RudraAnvil/src/rudraanvil/tools/file_tools.py#23-37) on `tech_stack.md` and `run_command` (if needed) rather than trying to stuff output into context.
