import sqlite3


def main():
    con = sqlite3.connect("polybot.db")
    cur = con.cursor()
    resolved = cur.execute(
        "select count(*) from trades where status in ('WIN','LOSS')"
    ).fetchone()[0]
    unsettled = cur.execute(
        "select count(*) from trades where is_paper=1 and status='FILLED' and pnl=0.0"
    ).fetchone()[0]
    print("resolved", resolved, "unsettled", unsettled)
    rows = cur.execute(
        """
        select status, pnl, cost_usd, underlying_symbol, session, btc_dominance_change_1h_pct, market_question
        from trades
        where status in ('WIN','LOSS')
        order by created_at desc
        limit 5
        """
    ).fetchall()
    print("sample_resolved", rows)
    con.close()


if __name__ == "__main__":
    main()

