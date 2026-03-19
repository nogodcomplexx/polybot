import asyncio

from src.api.gamma_client import GammaClient
from src.crypto.data_feed import CryptoDataFeed
from src.engine.discovery import MarketDiscovery
from src.config import load_config
from src.strategies.crypto_scalper import CryptoScalperStrategy


async def main():
    cfg = load_config()

    feed = CryptoDataFeed()
    await feed.start()
    await feed.refresh()

    gamma = GammaClient(cfg.polymarket.gamma_host)
    markets = await gamma.fetch_active_markets(limit=300)
    discovery = MarketDiscovery(cfg.discovery)
    markets = discovery.filter_markets(markets)

    strat = CryptoScalperStrategy(feed, budget=cfg.starting_budget, clob=None)
    signals = strat.evaluate(markets, predictions=[], signals=[], available_cash=cfg.starting_budget)

    print("signals", len(signals))
    for s in signals[:10]:
        print(s.market.question[:80], s.side.value, s.target_price, s.size_usd, s.edge, s.token_id[:8])

    await gamma.close()
    await feed.close()


if __name__ == "__main__":
    asyncio.run(main())

