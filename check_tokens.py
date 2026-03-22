import asyncio
import sqlite3
from src.api.gamma_client import GammaClient

async def check():
    conn = sqlite3.connect('polybot.db')
    cur = conn.cursor()
    cur.execute("SELECT id, token_id, condition_id, price FROM trades WHERE status='LOSS' ORDER BY created_at DESC LIMIT 5")
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        print("No recent trades")
        return
        
    client = GammaClient()
    
    for row in rows:
        trade_id, token_id, condition_id, trade_price = row
        print(f"\nTrade Token: '{token_id}'")
        raw = await client.get_market(condition_id)
        if not raw:
            print(f"Market not found: {condition_id}")
            continue
            
        print(f"Is closed: {raw.get('closed')}")
        
        tokens_raw = raw.get("clobTokenIds") or raw.get("clob_token_ids") or []
        prices = raw.get("outcomePrices") or []
        print(f"Tokens in Gamma: {tokens_raw}")
        print(f"Prices in Gamma: {prices}")
        
        market = client.parse_market(raw)
        if market:
            print(f"Parsed YES token: '{market.yes_token_id}'")
            print(f"Parsed NO token: '{market.no_token_id}'")
            
            # Check the fallback logic
            print(f"Trade price: {trade_price}")
            try:
                candidates = [float(p) for p in prices]
                winning_price = min(candidates, key=lambda x: abs(x - trade_price))
                print(f"Fallback selected price: {winning_price} (Distance: {abs(winning_price - trade_price):.4f})")
            except Exception as e:
                print(e)
                
            try:
                idx = [market.yes_token_id, market.no_token_id].index(token_id)
                print(f"Token found at index: {idx}")
            except ValueError:
                print("Token NOT MATCHED in parsed market tokens either!")

    await client.close()

if __name__ == "__main__":
    asyncio.run(check())
