import asyncio
from src.data.db import Database

async def reset():
    db = Database('polybot.db')
    await db.connect()
    
    # Delete stuck/future paper trades
    await db._db.execute("DELETE FROM trades WHERE is_paper = 1 AND status NOT IN ('WIN', 'LOSS', 'CANCELLED')")
    await db._db.execute("DELETE FROM positions")
    await db._db.commit()
    
    print("Database cleared of stuck/unsettled paper trades and positions.")
    await db.close()

asyncio.run(reset())
