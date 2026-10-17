import sqlite3
from pathlib import Path
from datetime import datetime, timezone

db_path = Path("polybot.db")
conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

print("=" * 80)
print("DEEP TRADE AUDIT: 108 TRADES ANALYSIS")
print("=" * 80)

# Check state transitions
tp1_transitions = cursor.execute("SELECT COUNT(*) FROM bybit_state_transitions WHERE to_state = 'TP1_FILLED'").fetchone()[0]
print(f"Total times TP1_FILLED state was reached: {tp1_transitions}")

# Inspect trades
trades = cursor.execute("""
    SELECT symbol, side, strategy, entry, exit_price, stop_price, tp1, tp2, 
           fees, pnl, hold_time, exit_reason, mae, mfe
    FROM bybit_trades
    ORDER BY timestamp ASC
""").fetchall()

# Check MFE vs TP1
mfe_exceeded_tp1 = 0
positive_mfe_count = 0
avg_mae = 0.0
avg_mfe = 0.0
for t in trades:
    entry = t["entry"]
    tp1 = t["tp1"]
    side = t["side"]
    mfe = t["mfe"]
    mae = t["mae"]
    avg_mae += mae
    avg_mfe += mfe
    if mfe > 0:
        positive_mfe_count += 1
    
    # Calculate target distance in dollars
    tp1_dist_dollars = abs(tp1 - entry)
    # Compare with MFE (which is stored in dollars per unit or total)
    # Let's inspect how mae/mfe is stored

print(f"Average MAE: ${avg_mae / len(trades):.2f}")
print(f"Average MFE: ${avg_mfe / len(trades):.2f}")

# Sample 10 trades with entry, exit, stop, tp1, hold time, fees, pnl, mae, mfe
print("-" * 80)
print("DETAILED SAMPLE OF 10 RECENT TRADES:")
print(f"{'Symbol':<10} | {'Side':<4} | {'Entry':<9} | {'Exit':<9} | {'SL':<9} | {'TP1':<9} | {'MFE':<7} | {'MAE':<7} | {'PnL':<8} | {'Fees':<6} | {'Exit Reason'}")
print("-" * 80)
for t in trades[-10:]:
    pnl_s = f"${t['pnl']:+.2f}"
    fee_s = f"${t['fees']:.2f}"
    mfe_s = f"${t['mfe']:.2f}"
    mae_s = f"${t['mae']:.2f}"
    print(f"{t['symbol']:<10} | {t['side']:<4} | {t['entry']:<9.4f} | {t['exit_price']:<9.4f} | {t['stop_price']:<9.4f} | {t['tp1']:<9.4f} | {mfe_s:<7} | {mae_s:<7} | {pnl_s:<8} | {fee_s:<6} | {t['exit_reason']}")

print("=" * 80)

# Check fee impact:
gross_pnl_total = sum(t["pnl"] + t["fees"] for t in trades)
net_pnl_total = sum(t["pnl"] for t in trades)
fees_total = sum(t["fees"] for t in trades)
print(f"Total Gross PnL (before fees): ${gross_pnl_total:+.2f}")
print(f"Total Fees Paid:               ${fees_total:.2f}")
print(f"Total Net PnL (after fees):    ${net_pnl_total:+.2f}")

# Winners vs losers before fees:
gross_wins = [t for t in trades if (t["pnl"] + t["fees"]) > 0]
print(f"Gross Winrate (before fees):   {len(gross_wins) / len(trades) * 100:.1f}% ({len(gross_wins)} / {len(trades)})")
print(f"Net Winrate (after fees):     {len([t for t in trades if t['pnl'] > 0]) / len(trades) * 100:.1f}% ({len([t for t in trades if t['pnl'] > 0])} / {len(trades)})")

conn.close()
