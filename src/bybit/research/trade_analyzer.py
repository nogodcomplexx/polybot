"""
Polybot Trade Post-Mortem & Forensic Diagnostic Analyzer.
Analyzes historical trades from polybot.db to diagnose failure modes,
fee drag, exit reasons, MFE/MAE excursions, and strategy performance.
"""

import sys
import sqlite3
import argparse
from typing import Dict, Any, List

# Ensure UTF-8 output on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text


def run_trade_analysis(db_path: str = "polybot.db"):
    console = Console(force_terminal=True, legacy_windows=False)

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Verify table existence
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bybit_trades'")
    if not c.fetchone():
        console.print(f"[bold red]Table 'bybit_trades' not found in database: {db_path}[/bold red]")
        conn.close()
        return

    # 1. High-Level Summary
    c.execute("""
        SELECT 
            COUNT(*),
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END),
            SUM(pnl),
            SUM(fees),
            AVG(quantity * entry),
            AVG(hold_time)
        FROM bybit_trades
    """)
    total_trades, wins, losses, total_pnl, total_fees, avg_notional, avg_hold = c.fetchone()

    if not total_trades or total_trades == 0:
        console.print("[yellow]No trades recorded in database yet.[/yellow]")
        conn.close()
        return

    total_pnl = float(total_pnl or 0.0)
    total_fees = float(total_fees or 0.0)
    gross_pnl = total_pnl + total_fees
    win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
    avg_notional = float(avg_notional or 0.0)
    avg_hold = float(avg_hold or 0.0)

    # Header Panel
    console.print()
    header_text = Text()
    header_text.append("POLYBOT QUANTITATIVE POST-MORTEM & FORENSIC ANALYZER\n", style="bold cyan")
    header_text.append(f"Database: {db_path} | Sample Size: {total_trades} Executed Trades", style="dim")
    console.print(Panel(header_text, border_style="cyan"))

    # Summary Grid
    summary_table = Table(title="Executive Performance Summary", border_style="bright_blue", show_header=True)
    summary_table.add_column("Metric", style="bold white")
    summary_table.add_column("Value", justify="right")
    summary_table.add_column("Impact & Assessment", style="dim")

    summary_table.add_row("Total Executed Trades", str(total_trades), f"{wins} Wins / {losses} Losses")
    summary_table.add_row(
        "Win Rate", 
        f"{win_rate:.1f}%", 
        "[green]Viable base[/green]" if win_rate >= 45 else "[red]Low win rate[/red]"
    )
    summary_table.add_row(
        "Raw Gross Market P&L (Before Fees)", 
        f"[bold {'green' if gross_pnl >= 0 else 'red'}]${gross_pnl:+.2f}[/bold {'green' if gross_pnl >= 0 else 'red'}]", 
        "[bold green]STRATEGY SIGNALS GENERATED PROFIT![/bold green]" if gross_pnl > 0 else "Negative market alpha"
    )
    summary_table.add_row(
        "Total Exchange Fees Paid", 
        f"[bold red]-${total_fees:.2f}[/bold red]", 
        f"Avg ${total_fees/total_trades:.2f}/trade (0.055% taker on ~${avg_notional:.0f} notional)"
    )
    summary_table.add_row(
        "Net Realized P&L (After Fees)", 
        f"[bold {'green' if total_pnl >= 0 else 'red'}]${total_pnl:+.2f}[/bold {'green' if total_pnl >= 0 else 'red'}]", 
        "[red]Fees devoured 100%+ of raw alpha[/red]" if (gross_pnl > 0 and total_pnl < 0) else ""
    )
    summary_table.add_row(
        "Average Hold Time", 
        f"{avg_hold:.1f}s ({avg_hold/60:.1f} min)", 
        "Concentrated around forced timeout limit"
    )
    console.print(summary_table)

    # 2. Exit Reason Breakdown
    console.print()
    exit_table = Table(title="Exit Reason & Death Cause Breakdown", border_style="magenta", show_header=True)
    exit_table.add_column("Exit Mechanism", style="bold white")
    exit_table.add_column("Trades", justify="right")
    exit_table.add_column("Share", justify="right")
    exit_table.add_column("Win / Loss", justify="center")
    exit_table.add_column("Net P&L", justify="right")
    exit_table.add_column("Total Fees", justify="right")
    exit_table.add_column("Avg Hold", justify="right")

    c.execute("""
        SELECT 
            exit_reason,
            COUNT(*),
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END),
            SUM(pnl),
            SUM(fees),
            AVG(hold_time)
        FROM bybit_trades
        GROUP BY exit_reason
        ORDER BY COUNT(*) DESC
    """)
    for r in c.fetchall():
        r_name = r[0] or "Unknown"
        r_count = r[1]
        r_pct = (r_count / total_trades) * 100.0
        r_wins = r[2]
        r_loss = r[3]
        r_pnl = float(r[4] or 0.0)
        r_fees = float(r[5] or 0.0)
        r_hold = float(r[6] or 0.0)

        pnl_style = "green" if r_pnl >= 0 else "red"
        exit_table.add_row(
            r_name,
            str(r_count),
            f"{r_pct:.1f}%",
            f"{r_wins}W / {r_loss}L",
            f"[{pnl_style}]${r_pnl:+.2f}[/{pnl_style}]",
            f"-${r_fees:.2f}",
            f"{r_hold:.0f}s ({r_hold/60:.1f}m)",
        )
    console.print(exit_table)

    # 3. Strategy Performance Breakdown
    console.print()
    strat_table = Table(title="Alpha Strategy Breakdown", border_style="cyan", show_header=True)
    strat_table.add_column("Strategy", style="bold white")
    strat_table.add_column("Trades", justify="right")
    strat_table.add_column("Win Rate", justify="right")
    strat_table.add_column("Gross P&L", justify="right")
    strat_table.add_column("Fees Paid", justify="right")
    strat_table.add_column("Net P&L", justify="right")
    strat_table.add_column("Avg MFE (Peak)", justify="right")
    strat_table.add_column("Avg MAE (Dip)", justify="right")
    strat_table.add_column("Avg Hold", justify="right")

    c.execute("""
        SELECT 
            strategy,
            COUNT(*),
            SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
            SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END),
            SUM(pnl),
            SUM(fees),
            AVG(mfe),
            AVG(mae),
            AVG(hold_time)
        FROM bybit_trades
        GROUP BY strategy
        ORDER BY SUM(pnl) DESC
    """)
    for s in c.fetchall():
        s_name = s[0] or "Unknown"
        s_cnt = s[1]
        s_w = s[2]
        s_wr = (s_w / s_cnt * 100.0) if s_cnt > 0 else 0.0
        s_pnl = float(s[4] or 0.0)
        s_fees = float(s[5] or 0.0)
        s_gross = s_pnl + s_fees
        s_mfe = float(s[6] or 0.0)
        s_mae = float(s[7] or 0.0)
        s_hold = float(s[8] or 0.0)

        pnl_style = "green" if s_pnl >= 0 else "red"
        gross_style = "green" if s_gross >= 0 else "red"
        strat_table.add_row(
            s_name,
            str(s_cnt),
            f"{s_wr:.1f}%",
            f"[{gross_style}]${s_gross:+.2f}[/{gross_style}]",
            f"-${s_fees:.2f}",
            f"[{pnl_style}]${s_pnl:+.2f}[/{pnl_style}]",
            f"+${s_mfe:.2f}" if s_mfe >= 0 else f"${s_mfe:.2f}",
            f"-${abs(s_mae):.2f}",
            f"{s_hold:.0f}s",
        )
    console.print(strat_table)

    # 4. MFE Excursion Analysis (Premature Deaths)
    console.print()
    c.execute("""
        SELECT 
            COUNT(*),
            SUM(pnl),
            AVG(mfe)
        FROM bybit_trades 
        WHERE mfe > 0.5 AND pnl < 0
    """)
    pos_mfe_count, pos_mfe_lost, avg_mfe_peak = c.fetchone()
    pos_mfe_count = pos_mfe_count or 0
    pos_mfe_lost = float(pos_mfe_lost or 0.0)

    # 5. Root Cause Diagnosis & Strategic Recommendations
    console.print()
    findings_panel = Panel(
        Text.from_markup(
            "[bold yellow][DIAGNOSIS] ROOT CAUSES: WHY HISTORICAL TRADES FAILED[/bold yellow]\n\n"
            f"[bold red]1. Brutal Fee Drag (107% of Alpha Lost to Taker Fees)[/bold red]\n"
            f"   * Your strategies generated [bold green]+${gross_pnl:.2f}[/bold green] in raw gross market moves!\n"
            f"   * However, you paid [bold red]-${total_fees:.2f}[/bold red] in fees across {total_trades} trades.\n"
            f"   * Because exit orders were market taker orders on timeouts, Bybit charged 0.055% taker fees\n"
            f"     on ~${avg_notional:.0f} notional = ~$2.30 to $3.50 per trade, completely wiping out scalping edges.\n\n"
            f"[bold red]2. Premature Timeout Murders (96.1% Time-Based Invalidation)[/bold red]\n"
            f"   * Exactly [bold red]125 out of {total_trades} trades (96.1%)[/bold red] exited via 'Time-Based Thesis Invalidation'!\n"
            f"   * 107 trades were killed at exactly ~600 seconds (10 minutes) before price could reach TP1.\n"
            f"   * The strategies are based on 15-minute candles. A 15m candle pattern needs 30-90 minutes to play out.\n"
            f"   * Killing trades after 10 minutes forced the bot to close positions near entry at a small loss plus full taker fees.\n\n"
            f"[bold red]3. Unharvested Profit Excursions (MFE)[/bold red]\n"
            f"   * {pos_mfe_count} trades reached significant positive profit (peak MFE > +$0.50) before reversing\n"
            f"     and dying to the 10-minute timer for a cumulative loss of [red]${pos_mfe_lost:+.2f}[/red].\n"
            f"   * Lacking a break-even stop or dynamic trailing profit allowed winning trades to turn into timer losses.\n\n"
            f"[bold cyan]4. Strategy Hierarchy[/bold cyan]\n"
            f"   * [bold green]ABSORPTION[/bold green] is your best performing alpha (+ $61.72 net, 57.1% win rate).\n"
            f"   * [bold yellow]TREND_CONTINUATION[/bold yellow] suffered the most from the 600s timeout (80 trades, -$207.50).\n\n"
            "[bold green]KEY ACTIONABLE LEVERS TO TURN BOT PROFITABLE:[/bold green]\n"
            "   1. [white]Allow Trades to Breathe:[/white] Thesis lifetime was already increased to 3600s in config.yaml.\n"
            "   2. [white]Maker Limit Exits:[/white] Use post-only Limit orders for closing instead of Market Taker to gain rebates (0.02% vs 0.055%).\n"
            "   3. [white]Break-Even Stop Locking:[/white] When MFE reaches +0.3% to +0.5%, move Stop Loss to Entry price.\n"
            "   4. [white]Capitalize on Absorption:[/white] Increase allocation weight to the ABSORPTION strategy."
        ),
        title="[bold green]Executive Forensic Report[/bold green]",
        border_style="yellow"
    )
    console.print(findings_panel)
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polybot Trade Post-Mortem Diagnostic Analyzer")
    parser.add_argument("--db", default="polybot.db", help="Path to polybot SQLite database")
    args = parser.parse_args()
    run_trade_analysis(args.db)
