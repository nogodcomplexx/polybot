import asyncio

from src.api.gamma_client import GammaClient
from src.engine.discovery import MarketDiscovery
from src.config import load_config


async def main():
    cfg = load_config()
    discovery = MarketDiscovery(cfg.discovery)
    g = GammaClient("https://gamma-api.polymarket.com")
    markets = await g.fetch_active_markets(limit=300)
    filtered = discovery.filter_markets(markets)

    def is_crypto_5m(m):
        q = (m.question or "").lower()
        return (
            ("up or down" in q or "5 min" in q or "5-min" in q or "five min" in q)
            and ("bitcoin" in q or "btc" in q)
        )

    crypto_5m = [m for m in markets if is_crypto_5m(m)]
    crypto_5m_filtered = [m for m in filtered if is_crypto_5m(m)]
    print(f"total={len(markets)} filtered={len(filtered)} crypto_5m={len(crypto_5m)} crypto_5m_filtered={len(crypto_5m_filtered)}")
    priced = [m for m in crypto_5m_filtered if 0.01 < m.yes_price < 0.99]
    print(f"crypto_5m_filtered_with_price={len(priced)}")
    for m in crypto_5m_filtered[:20]:
        print(
            f"{m.question[:90]} | yes={m.yes_price:.4f} no={m.no_price:.4f} "
            f"yes_token={bool(m.yes_token_id)} no_token={bool(m.no_token_id)} slug={m.slug}"
        )

    await g.close()


if __name__ == "__main__":
    asyncio.run(main())

