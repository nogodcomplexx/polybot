import sqlite3


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT condition_id, token_id, side, entry_price, size, cost_usd, opened_at, strategy_name FROM positions ORDER BY opened_at DESC LIMIT 20"
    )
    rows = cur.fetchall()
    if not rows:
        print("No rows in positions table.")
        return
    for r in rows:
        condition_id, token_id, side, entry_price, size, cost_usd, opened_at, strategy_name = r
        token_id_str = token_id or ""
        print(
            f"condition={condition_id} side={side} entry={entry_price} "
            f"size={size} cost_usd={cost_usd} opened_at={opened_at} token_id_len={len(token_id_str)} strategy={strategy_name}"
        )


if __name__ == "__main__":
    main()

