import asyncio
from deepagents import create_deep_agent
from langchain_core.messages import HumanMessage

async def main():
    agent = create_deep_agent('openai:gpt-4o-mini', subagents=[{
        'name': 'writer',
        'description': 'writes stuff',
        'system_prompt': 'you write stuff',
        'tools': [],
        'model': 'openai:gpt-4o-mini'
    }])
    
    async for event, namespace in agent.astream(
        {'messages': [HumanMessage(content='Use the writer subagent to write a poem.')]},
        stream_mode='messages',
        subgraphs=True
    ):
        print(f"Subtask Stream ({namespace}): {event}")

asyncio.run(main())
