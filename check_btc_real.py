import asyncio
import json
from src.api.gamma_client import GammaClient

async def check():
    client = GammaClient()
    condition_id = '0xe195e7b43759ee252bb44564b59d297fd3c7d509fe04fc5437712a21f1507ef0'
    raw = await client.get_market(condition_id)
    with open('dump_btc_real.json', 'w') as f:
        json.dump(raw, f, indent=2)
    await client.close()

if __name__ == "__main__":
    asyncio.run(check())
