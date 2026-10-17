"""
CLI Runner for BybitBacktester.
Fetches historical 15m candles from Bybit V5 Public REST API and executes
event-driven causal backtests across target symbols.
"""

import sys
import asyncio
import argparse
from typing import List
import httpx
from rich.console import Console
from rich.table import Table

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.bybit.market.candles import Candle
from src.bybit.research.backtest import BybitBacktester, BacktestMetrics

console = Console(no_color=False, highlight=False)


async def fetch_bybit_candles(symbol: str, interval: str = "15", limit: int = 1000) -> List[Candle]:
    """Fetch historical klines from Bybit Public REST API without requiring API keys."""
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": min(limit, 1000),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error [{data.get('retCode')}]: {data.get('retMsg')}")

        raw_list = data.get("result", {}).get("list", [])
        candles: List[Candle] = []

        # Bybit returns candles in reverse chronological order (newest first)
        for item in reversed(raw_list):
            start_ts = int(item[0]) / 1000.0
            dur_sec = int(interval) * 60 if interval.isdigit() else 900.0
            candles.append(Candle(
                timestamp=start_ts,
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[5]),
                close_time=start_ts + dur_sec,
            ))

        return candles


def display_metrics_table(symbol: str, metrics: BacktestMetrics, candle_count: int):
    """Render beautiful rich table of institutional backtest results."""
    table = Table(title=f"[Backtest Results: {symbol} ({candle_count} 15m Candles ~ {round(candle_count * 15 / 1440, 1)} Days)]")

    table.add_column("Metric", style="cyan", no_wrap=True)
    table.add_column("Value", style="bold green" if metrics.total_pnl >= 0 else "bold red")

    table.add_row("Total Trades", str(metrics.total_trades))
    table.add_row("Winning Trades", f"{metrics.winning_trades} ({metrics.win_rate * 100:.1f}%)")
    table.add_row("Losing Trades", f"{metrics.losing_trades}")
    table.add_row("Net Profit / Loss", f"${metrics.total_pnl:+.2f}")
    table.add_row("Total Fees Paid", f"${metrics.total_fees:.2f}")
    table.add_row("Profit Factor", f"{metrics.profit_factor:.2f}")
    table.add_row("Expectancy", f"${metrics.expectancy:+.2f} / trade")
    table.add_row("Max Drawdown", f"{metrics.max_drawdown_pct:.2f}%")
    table.add_row("Annualized Sharpe Ratio", f"{metrics.sharpe_ratio:.2f}")

    console.print(table)

    if metrics.strategy_breakdown:
        strat_table = Table(title=f"[Strategy Breakdown: {symbol}]")
        strat_table.add_column("Strategy", style="magenta")
        strat_table.add_column("Trades", justify="right")
        strat_table.add_column("Wins", justify="right")
        strat_table.add_column("Win Rate", justify="right")
        strat_table.add_column("Net PnL", justify="right")
        strat_table.add_column("Fees", justify="right")

        for strat, stats in metrics.strategy_breakdown.items():
            strat_table.add_row(
                strat,
                str(stats["trades"]),
                str(stats["wins"]),
                f"{stats.get('win_rate_pct', 0.0):.1f}%",
                f"${stats['pnl']:+.2f}",
                f"${stats['fees']:.2f}",
            )
        console.print(strat_table)


async def main():
    parser = argparse.ArgumentParser(description="Bybit Event-Driven Backtester Runner")
    parser.add_argument("--symbols", nargs="+", default=["SOLUSDT", "BTCUSDT", "ETHUSDT"], help="Symbols to backtest")
    parser.add_argument("--limit", type=int, default=1000, help="Number of 15m candles to fetch (max 1000)")
    parser.add_argument("--balance", type=float, default=1000.0, help="Starting balance USD")
    args = parser.parse_args()

    for sym in args.symbols:
        sym = sym.upper()
        console.print(f"\n[cyan]Fetching {args.limit} 15m candles for {sym} from Bybit V5 Public API...[/cyan]")
        try:
            candles = await fetch_bybit_candles(sym, interval="15", limit=args.limit)
            console.print(f"[green]OK: Fetched {len(candles)} candles. Running causal event-driven simulation...[/green]")
            tester = BybitBacktester(starting_balance=args.balance, symbol=sym)
            metrics = tester.run_simulation(candles, symbol=sym)
            display_metrics_table(sym, metrics, len(candles))
        except Exception as e:
            console.print(f"[bold red]Failed to backtest {sym}: {e}[/bold red]")


if __name__ == "__main__":
    asyncio.run(main())
