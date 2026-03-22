import sqlite3


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT status, COUNT(*) FROM trades GROUP BY status ORDER BY COUNT(*) DESC")
    for status, cnt in cur.fetchall():
        print(f"status={status!r} count={cnt}")


if __name__ == "__main__":
    main()

