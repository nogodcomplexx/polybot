import asyncio
import json
from src.api.gamma_client import GammaClient

async def check():
    client = GammaClient()
    markets = await client.get_markets(limit=20, active=True, closed=False)
    
    for raw in markets:
        if "Bitcoin" in raw.get("question", ""):
            print(f"Question: {raw.get('question')}")
            print(f"conditionId: {raw.get('conditionId')}")
            tokens = raw.get("clobTokenIds")
            print(f"clobTokenIds type: {type(tokens)}")
            print(f"clobTokenIds: {tokens}")
            parsed = client.parse_market(raw)
            print(f"Parsed YES: {parsed.yes_token_id}")
            print(f"Parsed NO: {parsed.no_token_id}")
            print("---")

    await client.close()

if __name__ == "__main__":
    asyncio.run(check())
