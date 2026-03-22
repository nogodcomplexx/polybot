import asyncio
import sqlite3
from src.api.gamma_client import GammaClient

async def check():
    conn = sqlite3.connect('polybot.db')
    cur = conn.cursor()
    cur.execute("SELECT condition_id, token_id FROM trades ORDER BY created_at DESC LIMIT 5")
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        return
        
    client = GammaClient()
    
    def search_dict(d, target):
        for k, v in d.items():
            if isinstance(v, dict):
                search_dict(v, target)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, dict):
                        search_dict(item, target)
                    elif isinstance(item, str) and target in item:
                        print(f"FOUND IN LIST AT KEY: {k}")
            elif isinstance(v, str) and target in v:
                print(f"FOUND AT KEY: {k} (value: {v})")

    for condition_id, target_token in rows:
        print(f"\nSearching for {target_token[:20]}... in {condition_id}")
        raw = await client.get_market(condition_id)
        if not raw:
            continue
        search_dict(raw, target_token)

    await client.close()

if __name__ == "__main__":
    asyncio.run(check())
