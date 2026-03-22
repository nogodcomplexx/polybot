import asyncio
import httpx
import json

async def test():
    # March 21st market
    cid = "0x2774de451efd630deaffb85e05a5a1f1dfcfdb0fdf8de83fe9534dc9bce055a6"
    url = f"https://gamma-api.polymarket.com/markets?condition_id={cid}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        data = resp.json()
        with open("dump.json", "w") as f:
            json.dump(data, f, indent=2)

asyncio.run(test())
