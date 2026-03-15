import asyncio
from pathlib import Path
from rudraanvil.agent.main_agent import create_main_agent

async def main():
    agent = create_main_agent(Path("."), "test task")
    print("Tools for main agent:")
    print(agent.agent.tools)
    
if __name__ == "__main__":
    asyncio.run(main())
