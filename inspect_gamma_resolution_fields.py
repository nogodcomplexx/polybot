import asyncio
import json

from src.api.gamma_client import GammaClient


CONDITION_ID = "0x1973e0fe0dab444721b08678235cc247ed1e4fa19c76c371a37144438981a116"


async def main_async() -> None:
    client = GammaClient()
    try:
        m = await client.get_market(CONDITION_ID)
        if not m:
            print("not found")
            return

        # Print a compact set of likely resolution-related fields
        candidates = [
            "closed",
            "active",
            "outcomePrices",
            "outcome_prices",
            "winningOutcome",
            "winning_outcome",
            "resolution",
            "resolved",
            "winner",
            "outcomes",
            "clobTokenIds",
            "clob_token_ids",
            "endDate",
            "end_date_iso",
            "end_date",
            "question",
            "slug",
        ]

        for k in candidates:
            if k in m:
                v = m[k]
                if isinstance(v, (dict, list)):
                    print(k, "=", type(v).__name__, "len=", len(v))
                else:
                    s = str(v)
                    if len(s) > 140:
                        s = s[:140] + "..."
                    print(k, "=", s)

        # Also show any keys that look like resolution/winner
        print("\nKeys containing 'win'/'lose'/'res'/'outcome':")
        for k in sorted(m.keys()):
            lk = k.lower()
            if any(substr in lk for substr in ["win", "lose", "res", "outcome"]):
                vv = m[k]
                if isinstance(vv, (dict, list)):
                    print(" ", k, "=>", type(vv).__name__, "len=", len(vv))
                else:
                    print(" ", k, "=>", str(vv)[:120])
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main_async())

