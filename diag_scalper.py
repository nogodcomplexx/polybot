import asyncio
import logging
from src.config import load_config
from src.main import PolyBot

async def diag():
    config = load_config()
    bot = PolyBot(config, show_dashboard=False)
    await bot.setup()
    
    # Run one discovery cycle
    raw_markets = await bot.gamma.fetch_active_markets(limit=200)
    markets = bot.discovery.filter_markets(raw_markets)
    crypto_markets = bot.discovery.filter_crypto_5min(raw_markets)
    
    print(f"Total Markets: {len(raw_markets)}")
    print(f"Filtered Markets: {len(markets)}")
    print(f"Crypto 5-min Markets: {len(crypto_markets)}")
    
    # Check data feed
    for sym in bot.crypto_feed.SYMBOLS:
        snap = bot.crypto_feed.get_snapshot(sym)
        if snap:
            print(f"\n--- {sym} ---")
            print(f"Price: {snap.price}")
            print(f"Candles 5m: {len(snap.candles_5m)}")
            print(f"Candles 1m: {len(snap.candles_1m)}")
            print(f"Candles 15m: {len(snap.candles_15m)}")
            
            # Predict
            prediction = bot.orchestrator.crypto_scalper.prob_engine.predict(snap)
            if prediction:
                print(f"Direction: {prediction.direction}")
                print(f"Combined Prob: {prediction.combined_probability:.4f}")
                print(f"Edge: {prediction.edge:.4f}")
                print(f"Realized Vol: {prediction.realized_vol:.4f}")
                print(f"MTF Status: {prediction.mtf_status}")
                if prediction.signal_5m:
                    print(f"Consensus Strength: {prediction.signal_5m.consensus_strength:.4f}")
                    print(f"Volume Ratio: {prediction.signal_5m.volume_ratio:.4f}")
            else:
                print("No prediction generated.")
    
    await bot.shutdown()

if __name__ == "__main__":
    asyncio.run(diag())
