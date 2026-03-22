import asyncio
from src.data.db import Database
async def test():
    db = Database('polybot.db')
    await db.connect()
    unsettled = await db.get_unsettled_trades()
    print(f'Unsettled: {len(unsettled)}')
    if not unsettled: return
    t = unsettled[0]
    print(t.market_question, t.direction)
    await db.close()

asyncio.run(test())
