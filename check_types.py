import asyncio
import sqlite3
import json
from src.api.gamma_client import GammaClient

async def check():
    conn = sqlite3.connect('polybot.db')
    cur = conn.cursor()
    cur.execute("SELECT id, token_id, condition_id, price FROM trades WHERE status='LOSS' ORDER BY created_at DESC LIMIT 1")
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        return
        
    client = GammaClient()
    trade_id, token_id, condition_id, trade_price = rows[0]
    raw = await client.get_market(condition_id)
    
    print("--- RAW API RESPONSE ---")
    tokens_raw = raw.get("clobTokenIds") or raw.get("clob_token_ids")
    prices_raw = raw.get("outcomePrices")
    
    print(f"clobTokenIds TYPE: {type(tokens_raw)}")
    print(f"clobTokenIds VALUE: {repr(tokens_raw)}")
    print(f"outcomePrices TYPE: {type(prices_raw)}")
    print(f"outcomePrices VALUE: {repr(prices_raw)}")
    
    print("--- OUR PARSER ---")
    market = client.parse_market(raw)
    print(f"yes_token: {repr(market.yes_token_id)}")
    print(f"no_token: {repr(market.no_token_id)}")
    print(f"yes_price: {market.yes_price}")
    print(f"no_price: {market.no_price}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(check())
