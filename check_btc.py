import asyncio
import sqlite3
import json
from src.api.gamma_client import GammaClient

async def check():
    conn = sqlite3.connect('polybot.db')
    cur = conn.cursor()
    cur.execute("SELECT condition_id, market_question FROM trades WHERE market_question LIKE 'Bitcoin%' ORDER BY created_at DESC LIMIT 1")
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        return
        
    client = GammaClient()
    condition_id, question = rows[0]
    
    raw = await client.get_market(condition_id)
    with open('dump_btc.json', 'w') as f:
        json.dump(raw, f, indent=2)

    await client.close()

if __name__ == "__main__":
    asyncio.run(check())
