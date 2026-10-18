"""
CLI Runner for BybitBacktester.
Fetches historical 15m candles from Bybit V5 Public REST API and executes
event-driven causal backtests across target symbols.
"""

import sys
import asyncio
import argparse
from typing import List, Dict, Any
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


async def fetch_bybit_orderbook(symbol: str, limit: int = 50) -> Dict[str, Any]:
    """Fetch authoritative real-time L50 orderbook snapshot from Bybit V5 REST API."""
    url = "https://api.bybit.com/v5/market/orderbook"
    params = {
        "category": "linear",
        "symbol": symbol.upper(),
        "limit": min(limit, 200),
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        if data.get("retCode") != 0:
            return {}
        res = data.get("result", {})
        bids = res.get("b", [])
        asks = res.get("a", [])
        if not bids or not asks:
            return {}

        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
        mid = (best_bid + best_ask) / 2.0
        spread_pct = ((best_ask - best_bid) / mid * 100.0) if mid > 0 else 0.0

        bid_depth_usd = sum(float(p) * float(q) for p, q in bids[:50])
        ask_depth_usd = sum(float(p) * float(q) for p, q in asks[:50])
        imbalance = (bid_depth_usd / ask_depth_usd) if ask_depth_usd > 0 else 1.0

        return {
            "symbol": symbol.upper(),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread_pct": spread_pct,
            "bid_levels": len(bids),
            "ask_levels": len(asks),
            "bid_depth_usd": bid_depth_usd,
            "ask_depth_usd": ask_depth_usd,
            "orderbook_imbalance": round(imbalance, 3),
        }


def display_orderbook_card(ob: Dict[str, Any]):
    """Render a clean summary of the live Bybit L50 orderbook."""
    if not ob:
        return
    table = Table(title=f"[Live Bybit V5 L50 Orderbook: {ob['symbol']}]")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="bold yellow")

    table.add_row("Best Bid / Ask", f"${ob['best_bid']:.4f} / ${ob['best_ask']:.4f}")
    table.add_row("Orderbook Spread", f"{ob['spread_pct']:.4f}% ({'Tight / Safe' if ob['spread_pct'] <= 0.035 else 'Wide / Risk'})")
    table.add_row("L50 Bid Depth (Liquidity)", f"${ob['bid_depth_usd']:,.2f}")
    table.add_row("L50 Ask Depth (Liquidity)", f"${ob['ask_depth_usd']:,.2f}")
    imb = ob['orderbook_imbalance']
    bias = "Bullish Skew" if imb > 1.15 else ("Bearish Skew" if imb < 0.85 else "Balanced Neutral")
    table.add_row("L50 Orderbook Imbalance", f"{imb:.3f} ({bias})")
    console.print(table)


def display_metrics_table(symbol: str, metrics: BacktestMetrics, candle_count: int, min_alpha: float):
    """Render beautiful rich table of institutional backtest results."""
    table = Table(title=f"[Backtest Results: {symbol} (Min Alpha: {min_alpha} | {candle_count} 15m Candles ~ {round(candle_count * 15 / 1440, 1)} Days)]")

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
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["SOLUSDT", "BTCUSDT", "ETHUSDT", "DOGEUSDT", "AVAXUSDT", "SUIUSDT", "NEARUSDT", "LINKUSDT"],
        help="Symbols to backtest",
    )
    parser.add_argument("--limit", type=int, default=1000, help="Number of 15m candles to fetch (max 1000)")
    parser.add_argument("--balance", type=float, default=1000.0, help="Starting balance USD")
    parser.add_argument("--min-alpha", type=float, default=76.0, help="Minimum alpha score threshold (default: 76.0)")
    args = parser.parse_args()

    console.print(f"\n[bold magenta]================================================================[/bold magenta]")
    console.print(f"[bold green]>> POLYBOT V5 EVENT-DRIVEN RESEARCH & BACKTESTING ENGINE[/bold green]")
    console.print(f"   * Scope: [bold yellow]{len(args.symbols)} tokens[/bold yellow] ({', '.join(args.symbols)})")
    console.print(f"   * Minimum Alpha Score Filter: [bold cyan]{args.min_alpha}[/bold cyan]")
    console.print(f"   * Candle History: [bold cyan]{args.limit} x 15m bars (~{round(args.limit * 15 / 1440, 1)} days)[/bold cyan]")
    console.print(f"[bold magenta]================================================================[/bold magenta]\n")

    from src.bybit.config import TunableParameterRegistry

    for sym in args.symbols:
        sym = sym.upper()
        console.print(f"\n[cyan]Fetching real L50 orderbook & {args.limit} 15m candles for {sym} from Bybit V5 API...[/cyan]")
        try:
            # 1. Fetch live L50 orderbook from Bybit REST API
            ob = await fetch_bybit_orderbook(sym, limit=50)
            if ob:
                display_orderbook_card(ob)

            # 2. Fetch historical candles
            candles = await fetch_bybit_candles(sym, interval="15", limit=args.limit)
            console.print(f"[green]OK: Fetched {len(candles)} candles. Running causal event-driven simulation...[/green]")

            tunable = TunableParameterRegistry()
            tunable.update("min_alpha_score", args.min_alpha)

            tester = BybitBacktester(starting_balance=args.balance, symbol=sym, tunable=tunable)
            metrics = tester.run_simulation(candles, symbol=sym)
            display_metrics_table(sym, metrics, len(candles), args.min_alpha)
        except Exception as e:
            console.print(f"[bold red]Failed to backtest {sym}: {e}[/bold red]")


if __name__ == "__main__":
    asyncio.run(main())
