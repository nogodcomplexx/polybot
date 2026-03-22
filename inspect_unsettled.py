import sqlite3


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT status, COUNT(*)
        FROM trades
        WHERE is_paper = 1 AND pnl = 0.0
        GROUP BY status
        ORDER BY COUNT(*) DESC
        """
    )
    print("pnl=0 counts:", cur.fetchall())

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE is_paper = 1
          AND status IN ('FILLED','PARTIALLY_FILLED')
          AND pnl = 0.0
        """
    )
    print("unsettled_total:", cur.fetchone()[0])

    cur.execute(
        """
        SELECT condition_id, token_id, status, price, size, cost_usd, created_at
        FROM trades
        WHERE is_paper = 1 AND pnl = 0.0
          AND status IN ('FILLED','PARTIALLY_FILLED')
        ORDER BY created_at DESC
        LIMIT 10
        """
    )
    rows = cur.fetchall()
    print("latest unsettled (up to 10):")
    for r in rows:
        condition_id, token_id, status, price, size, cost_usd, created_at = r
        print(
            f"  condition={condition_id} status={status} price={price} size={size} cost_usd={cost_usd} created_at={created_at} token_id_len={len(token_id or '')}"
        )


if __name__ == "__main__":
    main()

