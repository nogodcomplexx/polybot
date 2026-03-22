import json
import asyncio

from src.api.gamma_client import GammaClient


CONDITION_IDS = [
    # From earlier `inspect_positions` output / UI screenshots:
    "0x1973e0fe0dab444721b08678235cc247ed1e4fa19c76c371a37144438981a116",
    "0xef0ebaeabf14fd6b5429407c5ca91411a563665ff14dbd52a8e56059f4579b57",
    "0x2b62bc8588d0126ac20c55a32b904fc06671713723ed10474e8634578388e45c",
    "0x64a20c862ba06a51a174c3cd494f21c45521a4febf21bfb7633895f551bf9953",
    # Add a few more likely-open ones if present:
    "0x57ab9e093af4cfc3e755b3954701459495b5ae37fa6608ebafce73e61cfb221f",
    "0x3b5c8174aff5d7712630174ae8ee6e6f01c61aabaa4e1cb8c3d73f8861051404",
]


async def main_async() -> None:
    client = GammaClient()
    try:
        for cid in CONDITION_IDS:
            m = await client.get_market(cid)
            if not m:
                print(cid, "not found")
                continue

            closed = bool(m.get("closed"))
            tokens_raw = m.get("clobTokenIds") or m.get("clob_token_ids") or []
            prices_raw = m.get("outcomePrices") or m.get("outcome_prices") or []

            if isinstance(tokens_raw, str):
                try:
                    tokens_raw = json.loads(tokens_raw)
                except Exception:
                    tokens_raw = []
            if isinstance(prices_raw, str):
                try:
                    prices_raw = json.loads(prices_raw)
                except Exception:
                    prices_raw = []

            # Keep output compact (don’t print huge token arrays)
            token0 = tokens_raw[0] if isinstance(tokens_raw, list) and tokens_raw else None
            token1 = tokens_raw[1] if isinstance(tokens_raw, list) and len(tokens_raw) > 1 else None
            price0 = prices_raw[0] if isinstance(prices_raw, list) and prices_raw else None
            price1 = prices_raw[1] if isinstance(prices_raw, list) and len(prices_raw) > 1 else None

            print(
                cid,
                "closed=",
                closed,
                "outcomePrices=",
                price0,
                "/",
                price1,
                "tokens_len=",
                (len(tokens_raw) if isinstance(tokens_raw, list) else None),
            )
            # Optional: show token sample if closed
            if closed:
                print("  token0=", str(token0)[:12] if token0 else None, "token1=", str(token1)[:12] if token1 else None)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main_async())

