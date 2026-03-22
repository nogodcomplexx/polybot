import json
import requests


CONDITION_ID = "0xc0ad3e2ad1d067f6e2ead9cc695d1a57596d6b240cefdcab77d9a27360c182f6"


def main() -> None:
    url = "https://gamma-api.polymarket.com/markets"
    resp = requests.get(url, params={"condition_id": CONDITION_ID, "limit": 1}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        print("No market data returned")
        return
    m = data[0]

    for k in ["clobTokenIds", "clob_token_ids"]:
        if k in m:
            v = m[k]
            s = str(v)
            print(f"{k} type={type(v).__name__} len={len(v) if hasattr(v,'__len__') else 'na'} sample={s[:120]!r}")
    # Also print outcomePrices type
    if "outcomePrices" in m:
        op = m["outcomePrices"]
        print(f"outcomePrices type={type(op).__name__} sample={str(op)[:120]!r}")


if __name__ == "__main__":
    main()

