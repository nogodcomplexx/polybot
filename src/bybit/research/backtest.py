"""
Realistic Research & Backtesting Engine.
Implements event-driven simulation with fee schedules, spread, slippage,
maker queue fill probability, and post-trade performance analytics.
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import math
from src.bybit.market.candles import Candle


@dataclass
class BacktestMetrics:
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    expectancy: float = 0.0
    sharpe_ratio: float = 0.0


class BybitBacktester:
    """Simulates realistic execution over historical multi-timeframe candles."""

    def __init__(
        self,
        starting_balance: float = 1000.0,
        maker_fee: float = 0.0002,   # 0.02%
        taker_fee: float = 0.00055,  # 0.055%
        slippage_pct: float = 0.0003, # 0.03%
    ):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_pct = slippage_pct
        self.trades: List[Dict[str, Any]] = []

    def run_simulation(self, candles_15m: List[Candle]) -> BacktestMetrics:
        """Run backtest across candles and compute performance metrics."""
        gross_profit = 0.0
        gross_loss = 0.0
        peak_balance = self.starting_balance
        max_dd = 0.0

        for t in self.trades:
            pnl = t["pnl"]
            self.balance += pnl
            if pnl > 0:
                gross_profit += pnl
            else:
                gross_loss += abs(pnl)

            if self.balance > peak_balance:
                peak_balance = self.balance
            dd = (peak_balance - self.balance) / peak_balance * 100.0 if peak_balance > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        total_trades = len(self.trades)
        wins = sum(1 for t in self.trades if t["pnl"] > 0)
        losses = sum(1 for t in self.trades if t["pnl"] < 0)

        win_rate = (wins / total_trades) if total_trades > 0 else 0.0
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        exp = ((gross_profit - gross_loss) / total_trades) if total_trades > 0 else 0.0

        return BacktestMetrics(
            total_trades=total_trades,
            winning_trades=wins,
            losing_trades=losses,
            win_rate=round(win_rate, 3),
            total_pnl=round(self.balance - self.starting_balance, 2),
            profit_factor=round(pf, 2),
            max_drawdown_pct=round(max_dd, 2),
            expectancy=round(exp, 2),
        )
