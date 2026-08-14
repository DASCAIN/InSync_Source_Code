import asyncio
from dotenv import load_dotenv
load_dotenv()
from services.mem0_service import get_mem0_service

async def main():
    m = get_mem0_service()
    user = "test_weak_topic_parsed@example.com"
    all_mems = await m._client().get_all(user_id=user)
    print("All Memories:", all_mems)

asyncio.run(main())
