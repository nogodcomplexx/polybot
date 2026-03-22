import sqlite3
import time


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    now = time.time()
    cur.execute(
        "SELECT id, status, created_at, intended_cost_usd FROM trades ORDER BY created_at DESC LIMIT 8"
    )
    for trade_id, status, created_at, intended_cost_usd in cur.fetchall():
        if created_at is None:
            age = None
        else:
            age = now - float(created_at)
        print(f"id={trade_id} status={status} age_sec={age} intended_cost_usd={intended_cost_usd}")


if __name__ == "__main__":
    main()

