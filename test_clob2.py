import asyncio
import httpx
from src.data.db import Database

async def test():
    db = Database('polybot.db')
    await db.connect()
    unsettled = await db.get_unsettled_trades()
    if not unsettled:
        return
        
    t = unsettled[0]
    
    # Check CLOB
    url = f'https://clob.polymarket.com/markets/{t.condition_id}'
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        data = resp.json()
        print(data)
    await db.close()

asyncio.run(test())
