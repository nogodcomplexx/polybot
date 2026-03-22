import asyncio
import httpx
from src.data.db import Database

async def test():
    db = Database('polybot.db')
    await db.connect()
    unsettled = await db.get_unsettled_trades()
    print(f'Unsettled: {len(unsettled)}')
    if not unsettled:
        await db.close()
        return
        
    for t in unsettled[:3]:
        print(f'\n--- Trade: {t.market_question} | condition: {t.condition_id} ---')
        
        # Check CLOB
        url = f'https://clob.polymarket.com/markets/{t.condition_id}'
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            data = resp.json()
            print(f'CLOB closed: {data.get("closed", None)}')
            print(f'CLOB active: {data.get("active", None)}')
            tokens = data.get("tokens", [])
            for tok in tokens:
                print(f'Token {tok.get("token_id")[:6]}... winner: {tok.get("winner", None)}')

        # Check Gamma
        gamma_url = f'https://gamma-api.polymarket.com/markets?condition_id={t.condition_id}'
        async with httpx.AsyncClient() as client:
            resp = await client.get(gamma_url)
            data = resp.json()
            if data:
                print(f'Gamma closed: {data[0].get("closed", None)}')
                print(f'Gamma active: {data[0].get("active", None)}')
    
    await db.close()

asyncio.run(test())
