import sqlite3


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT side, status, COUNT(*) FROM trades WHERE is_paper = 1 GROUP BY side, status ORDER BY COUNT(*) DESC LIMIT 50"
    )
    for side, status, cnt in cur.fetchall():
        print(f"side={side!r} status={status!r} count={cnt}")


if __name__ == "__main__":
    main()

