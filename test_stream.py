import asyncio
from pathlib import Path
from rudraanvil.agent import create_main_agent
from rudraanvil.state import ProjectContext
from rich.console import Console

async def main():
    console = Console()
    agent_wrapper = create_main_agent(
        project_path=Path("/home/a/code/ai-ml/agent/test"),
        task="Create a simple index.html file with a greeting.",
        project_context=ProjectContext(primary_language="HTML", framework="None"),
        command="build",
        console=console,
        verbose=True
    )
    
    # We will use the underlying deep_agent directly to test streaming
    deep_agent = agent_wrapper.agent
    
    print("Testing astream...")
    # Stream events from the subagent directly
    async for event in deep_agent.astream(
        {"messages": [{"role": "user", "content": "Create a simple index.html file with a greeting."}]},
        {"recursion_limit": 10},
        stream_mode="values"
    ):
        messages = event.get("messages", [])
        if messages:
            last = messages[-1]
            print(f"[{type(last).__name__}] {str(last)[:200]}")

if __name__ == "__main__":
    asyncio.run(main())
