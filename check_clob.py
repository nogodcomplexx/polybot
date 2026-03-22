import asyncio
import json
import httpx

async def check():
    condition_id = '0xe195e7b43759ee252bb44564b59d297fd3c7d509fe04fc5437712a21f1507ef0'
    url = f"https://clob.polymarket.com/markets/{condition_id}"
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        with open('dump_clob.json', 'w') as f:
            json.dump(resp.json(), f, indent=2)

if __name__ == "__main__":
    asyncio.run(check())
