import sqlite3


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT token_id FROM positions")
    existing_tokens = {r[0] for r in cur.fetchall() if r and r[0]}

    cur.execute(
        """
        SELECT
            condition_id, token_id, market_question, side,
            price, size, cost_usd,
            strategy_name, slug, underlying_symbol,
            filled_at
        FROM trades
        WHERE is_paper = 1
          AND status IN ('FILLED', 'PARTIALLY_FILLED')
          AND cost_usd > 0
        ORDER BY created_at DESC
        """
    )
    rows = cur.fetchall()

    inserted = 0
    for (
        condition_id,
        token_id,
        market_question,
        side,
        price,
        size,
        cost_usd,
        strategy_name,
        slug,
        underlying_symbol,
        filled_at,
    ) in rows:
        if not token_id:
            continue
        if token_id in existing_tokens:
            continue

        try:
            entry_price = float(price)
            stop_loss_price = max(0.01, entry_price * 0.5)
            take_profit_price = min(0.99, entry_price * 1.5)
            current_price = entry_price
            unrealized_pnl = 0.0
            opened_at = float(filled_at or 0.0)
            size_f = float(size or 0.0)
            cost_f = float(cost_usd or 0.0)
        except Exception:
            continue

        cur.execute(
            """
            INSERT OR REPLACE INTO positions
              (condition_id, token_id, market_question, side, entry_price,
               current_price, size, cost_usd, unrealized_pnl,
               stop_loss_price, take_profit_price, strategy_name, opened_at, slug, underlying_symbol)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                condition_id,
                token_id,
                market_question,
                side,
                entry_price,
                current_price,
                size_f,
                cost_f,
                unrealized_pnl,
                stop_loss_price,
                take_profit_price,
                strategy_name,
                opened_at,
                slug or "",
                underlying_symbol or "",
            ),
        )
        inserted += 1
        existing_tokens.add(token_id)

    conn.commit()
    print(f"Inserted positions: {inserted}")


if __name__ == "__main__":
    main()

