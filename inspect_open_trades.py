import sqlite3


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, condition_id, token_id, price, created_at, status, intended_cost_usd
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY created_at DESC
        LIMIT 20
        """
    )
    rows = cur.fetchall()
    if not rows:
        print("No OPEN paper trades found.")
        return
    for (tid, condition_id, token_id, price, created_at, status, intended_cost_usd) in rows:
        token_id_str = token_id or ""
        print(
            f"id={tid} condition={condition_id} price={price} created_at={created_at} "
            f"token_id_len={len(token_id_str)} intended_cost_usd={intended_cost_usd}"
        )


if __name__ == "__main__":
    main()

