import sqlite3


DB_PATH = r"C:\Users\LeosK\Downloads\polybot\polybot.db"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, token_id, status, created_at FROM trades ORDER BY created_at DESC LIMIT 10"
    )
    rows = cur.fetchall()
    for row in rows:
        trade_id, token_id, status, created_at = row
        token_id_str = token_id if token_id is not None else ""
        print(
            f"id={trade_id} status={status} created_at={created_at} "
            f"token_id_len={len(token_id_str)} token_id_repr={token_id_str!r}"
        )


if __name__ == "__main__":
    main()

