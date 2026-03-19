import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).resolve().parent))

from src.api.gamma_client import GammaClient
from src.engine.discovery import MarketDiscovery
from src.config import DiscoveryConfig

logging.basicConfig(level=logging.INFO)

async def debug():
    gamma = GammaClient()
    config = DiscoveryConfig(min_liquidity_usd=0, min_volume_24h=0) # Relax config for debug
    discovery = MarketDiscovery(config)
    
    print("Fetching active markets...")
    raw_markets = await gamma.fetch_active_markets(limit=250)
    print(f"Total raw markets: {len(raw_markets)}")
    
    # 1. Check if any 5-min markets are in raw_markets
    crypto_kw = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto"]
    time_kw = ["5 min", "5-min", "five min", "up or down"]
    
    found_5min_raw = []
    for m in raw_markets:
        q = m.question.lower()
        is_crypto = any(kw in q for kw in crypto_kw)
        is_5min = any(kw in q for kw in time_kw)
        if is_crypto and is_5min:
            found_5min_raw.append(m)
    
    print(f"5-min markets in RAW data: {len(found_5min_raw)}")
    for m in found_5min_raw[:5]:
        print(f"  - {m.question} | End: {m.end_date} | Liquidity: {m.liquidity} | Volume: {m.volume_24h}")

    # 2. Check why they might fail filters
    if found_5min_raw:
        m = found_5min_raw[0]
        print(f"\nTesting filters on: {m.question}")
        print(f"  Active/Closed: {m.active}/{m.closed}")
        print(f"  Tokens: {m.yes_token_id}/{m.no_token_id}")
        print(f"  Liquidity: {m.liquidity} (min {config.min_liquidity_usd})")
        print(f"  Volume: {m.volume_24h} (min {config.min_volume_24h})")
        
        try:
            from datetime import datetime, timezone
            end_dt = datetime.fromisoformat(m.end_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            print(f"  End Date: {end_dt.date()} | Now: {now.date()}")
            print(f"  Same Day? {end_dt.date() == now.date()}")
        except Exception as e:
            print(f"  Date Error: {e}")

    await gamma.close()

if __name__ == "__main__":
    asyncio.run(debug())
