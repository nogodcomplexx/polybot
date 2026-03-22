import json
import sqlite3
import requests


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"
GAMMA_BASE = "https://gamma-api.polymarket.com"


def fetch_current_token_price(condition_id: str, token_id: str) -> float | None:
    resp = requests.get(
        f"{GAMMA_BASE}/markets",
        params={"condition_id": condition_id, "limit": 1},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list) or not data:
        return None
    m = data[0]
    tokens_raw = m.get("clobTokenIds") or m.get("clob_token_ids") or []
    prices_raw = m.get("outcomePrices") or m.get("outcome_prices") or []

    if isinstance(tokens_raw, str):
        tokens = json.loads(tokens_raw)
    else:
        tokens = tokens_raw

    if isinstance(prices_raw, str):
        prices = json.loads(prices_raw)
    else:
        prices = prices_raw

    try:
        idx = tokens.index(token_id)
    except ValueError:
        # Helpful for debugging token formatting/ordering mismatches.
        print(
            f"token mismatch for condition_id={condition_id} "
            f"trade_token_id_len={len(token_id or '')} "
            f"trade_token_id_sample={str(token_id)[:20]!r} "
            f"gamma_tokens_count={len(tokens) if hasattr(tokens,'__len__') else 'na'} "
            f"gamma_token0={tokens[0] if isinstance(tokens, list) and tokens else None!r} "
            f"gamma_token1={tokens[1] if isinstance(tokens, list) and len(tokens) > 1 else None!r}"
        )
        return None
    return float(prices[idx]) if idx < len(prices) else None


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, condition_id, token_id, price, created_at, status, cost_usd
        FROM trades
        WHERE is_paper = 1
        ORDER BY created_at DESC
        LIMIT 5
        """
    )
    rows = cur.fetchall()

    for (trade_id, condition_id, token_id, price, created_at, status, cost_usd) in rows:
        try:
            current_px = fetch_current_token_price(condition_id, token_id)
        except Exception:
            current_px = None

        print(
            f"id={trade_id} status={status} created_at={created_at} "
            f"limit_price={price} token_id_len={len(token_id or '')} "
            f"current_px={current_px} cost_usd={cost_usd}"
        )


if __name__ == "__main__":
    main()

