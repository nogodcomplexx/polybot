"""
Trade Performance & Winrate Diagnostics Script.
Analyzes trades persisted in polybot.db to evaluate strategy winrate, P&L, fees, and execution efficiency.
Usage:
    python scripts/analyze_trades.py
"""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# Find polybot.db
_REPO_ROOT = Path(__file__).resolve().parent.parent
db_path = _REPO_ROOT / "polybot.db"

if not db_path.exists():
    print(f"Database not found at {db_path}")
    sys.exit(1)


def analyze():
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query trades
    rows = cursor.execute("""
        SELECT
            id, timestamp, symbol, side, strategy, regime, entry, exit_price,
            stop_price, tp1, tp2, quantity, fees, pnl, pnl_pct, hold_time,
            exit_reason, attribution, status
        FROM bybit_trades
        ORDER BY timestamp ASC
    """).fetchall()

    if not rows:
        print("=" * 65)
        print("  NO TRADES RECORDED YET IN polybot.db")
        print("=" * 65)
        print("As trades execute, they will be logged here and visible on the dashboard.")
        conn.close()
        return

    total_trades = len(rows)
    wins = [r for r in rows if r["pnl"] > 0]
    losses = [r for r in rows if r["pnl"] <= 0]

    win_count = len(wins)
    loss_count = len(losses)
    winrate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

    total_net_pnl = sum(r["pnl"] for r in rows)
    total_fees = sum(r["fees"] for r in rows)
    gross_wins = sum(r["pnl"] for r in wins)
    gross_losses = abs(sum(r["pnl"] for r in losses))

    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0)
    avg_win = (gross_wins / win_count) if win_count > 0 else 0.0
    avg_loss = (gross_losses / loss_count) if loss_count > 0 else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

    print("=" * 70)
    print("           POLYBOT QUANTITATIVE TRADE PERFORMANCE AUDIT")
    print("=" * 70)
    print(f"Total Trades Closed:    {total_trades}")
    print(f"Winrate:                {winrate:.1f}% ({win_count} Wins / {loss_count} Losses)")
    print(f"Net Realized PnL:       ${total_net_pnl:+.2f}")
    print(f"Total Fees Paid:        ${total_fees:.2f}")
    print(f"Profit Factor:          {profit_factor:.2f}")
    print(f"Avg Win:                ${avg_win:.2f}")
    print(f"Avg Loss:               ${avg_loss:.2f}")
    print(f"Payoff Ratio (W/L):     {payoff_ratio:.2f}")
    print("-" * 70)

    # Strategy Breakdown
    print("STRATEGY BREAKDOWN:")
    strat_stats = {}
    for r in rows:
        st = r["strategy"] or "UNKNOWN"
        if st not in strat_stats:
            strat_stats[st] = {"count": 0, "wins": 0, "pnl": 0.0, "fees": 0.0}
        strat_stats[st]["count"] += 1
        if r["pnl"] > 0:
            strat_stats[st]["wins"] += 1
        strat_stats[st]["pnl"] += r["pnl"]
        strat_stats[st]["fees"] += r["fees"]

    for st, s in sorted(strat_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        st_wr = (s["wins"] / s["count"] * 100) if s["count"] > 0 else 0
        print(f"  * {st:<24} | Trades: {s['count']:>2} | Winrate: {st_wr:>5.1f}% | Net PnL: ${s['pnl']:>+7.2f} | Fees: ${s['fees']:>5.2f}")

    print("-" * 70)

    # Exit Reason Breakdown
    print("EXIT REASON BREAKDOWN:")
    exit_stats = {}
    for r in rows:
        reason = r["exit_reason"] or "UNKNOWN"
        exit_stats[reason] = exit_stats.get(reason, 0) + 1
    for reason, cnt in sorted(exit_stats.items(), key=lambda x: x[1], reverse=True):
        print(f"  * {reason:<35} : {cnt} trades")

    print("-" * 70)
    print("RECENT TRADES (Last 10):")
    print(f"{'Time (UTC)':<19} | {'Symbol':<10} | {'Side':<4} | {'Net PnL':<8} | {'Fees':<6} | {'Hold':<6} | {'Exit Reason'}")
    print("-" * 70)
    for r in rows[-10:]:
        dt_str = datetime.fromtimestamp(r["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        hold_s = f"{r['hold_time']:.0f}s"
        pnl_s = f"${r['pnl']:+.2f}"
        print(f"{dt_str:<19} | {r['symbol']:<10} | {r['side']:<4} | {pnl_s:<8} | ${r['fees']:<5.2f} | {hold_s:<6} | {r['exit_reason']}")

    print("=" * 70)
    conn.close()


if __name__ == "__main__":
    analyze()
