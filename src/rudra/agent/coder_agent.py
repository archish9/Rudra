"""Coder agent — reads task assignment and writes the code file."""

from __future__ import annotations

from deepagents import create_deep_agent

from rudra.llm import build_model
from rudra.middleware import FixWriteParamsMiddleware, TaskAnchorMiddleware

_CODER_ANCHOR = (
    "Read .rudra/run/current_task.md, then write the file specified there using write_file(). "
    "Write ONE complete file with raw source code. Stop after write_file() succeeds."
)

_CODER_SYSTEM_PROMPT = """You are an expert code generator for Rudra.

Your ONLY job: Read .rudra/run/current_task.md and write the file specified there.

## WORKFLOW
1. Call read_file(".rudra/run/current_task.md") to get your assignment
2. Call read_file(".rudra/run/tech_stack.md") for technology constraints
3. Read any context files listed in the task assignment (use read_file for each)
4. Write ONE complete file: write_file(file_path="<exact path>", content="<complete source>")
5. STOP — do not write any additional files

## FILE PATH RULES
- Use RELATIVE paths only: "src/main.rs", "package.json"
- NEVER use absolute paths or paths starting with "/" or a drive letter
- Use the exact file_path from the task assignment

## CODE QUALITY RULES
- write_file content MUST be RAW source code — NEVER wrap in ```markdown fences```
- Write COMPLETE, working code — no placeholders, stubs, or TODO comments
- No bare pass, no unimplemented methods, no Hello World shortcuts
- All imports at the top of the file

## STOP CONDITION
After write_file() returns successfully, STOP. Do not write more files.
Do NOT call run_command. Do NOT call task. Do NOT call update_plan.
"""


def create_coder_agent(
    tech_stack_content: str,
    filesystem_backend,
    checkpointer,
):
    """Create a fresh coder deep agent. Call once per file.

    The coder is steered to a single file by .rudra/run/current_task.md, by
    _CODER_ANCHOR, and by the per-file user message the orchestrator sends
    (main_agent.py). EnforceTargetFileMiddleware used to enforce this as
    well, but it matched on basename — target `src/models.py` permitted a
    write to `tests/models.py` (TODO.md A1.6) — and is deleted in D4.
    """
    model = build_model("coder")

    middleware = [
        FixWriteParamsMiddleware(),
        TaskAnchorMiddleware(_CODER_ANCHOR),
    ]

    return create_deep_agent(
        model=model,
        tools=[],
        system_prompt=_CODER_SYSTEM_PROMPT,
        backend=filesystem_backend,
        checkpointer=checkpointer,
        middleware=middleware,
    )
