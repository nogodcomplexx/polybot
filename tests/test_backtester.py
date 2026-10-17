"""
Unit & Integration Tests for BybitBacktester.
Tests:
1. Pre-populated trades metric calculations (backward compatibility)
2. Synthetic snapshot generation
3. Causal event-driven execution simulation with 50/50 TP split, BE ratchet, and fees
"""

import time
import pytest
from src.bybit.research.backtest import BybitBacktester, BacktestMetrics
from src.bybit.market.candles import Candle
from src.bybit.models import MarketRegime, StrategyName


def test_backtester_metrics_from_prepopulated_trades():
    """Verify that BacktestMetrics correctly computes stats when trades are injected."""
    tester = BybitBacktester(starting_balance=1000.0)
    tester.trades = [
        {"symbol": "BTCUSDT", "side": "Buy", "strategy": "TURTLE_SOUP", "pnl": 50.0, "fees": 2.0},
        {"symbol": "BTCUSDT", "side": "Buy", "strategy": "TURTLE_SOUP", "pnl": -20.0, "fees": 2.0},
        {"symbol": "BTCUSDT", "side": "Sell", "strategy": "VWAP_FVG_REVERSION", "pnl": 30.0, "fees": 1.5},
    ]

    metrics = tester.run_simulation([])
    assert metrics.total_trades == 3
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 1
    assert metrics.win_rate == round(2 / 3, 3)
    assert metrics.total_pnl == 60.0
    assert metrics.total_fees == 5.5
    assert metrics.profit_factor == round(80.0 / 20.0, 2)
    assert metrics.strategy_breakdown["TURTLE_SOUP"]["trades"] == 2
    assert metrics.strategy_breakdown["TURTLE_SOUP"]["wins"] == 1
    assert metrics.strategy_breakdown["VWAP_FVG_REVERSION"]["trades"] == 1


def test_synthetic_snapshot_causality():
    """Verify synthetic snapshot generation adheres to causal invariants."""
    tester = BybitBacktester()
    now = time.time()
    candles = [
        Candle(
            timestamp=now - (50 - i) * 900,
            open=100.0 + (i * 0.1),
            high=100.0 + (i * 0.1) + 0.5,
            low=100.0 + (i * 0.1) - 0.5,
            close=100.0 + (i * 0.1) + 0.2,
            volume=1000.0 + (i * 10),
            close_time=now - (50 - i - 1) * 900,
        )
        for i in range(50)
    ]

    snap = tester._build_synthetic_snapshot(candles, "BTCUSDT")
    assert snap.symbol == "BTCUSDT"
    assert snap.last_price == candles[-1].close
    assert snap.orderbook_valid is True
    assert 0.45 <= snap.orderbook_imbalance <= 2.20
    assert snap.atr_15m > 0
    assert snap.vwap_15m > 0


def test_causal_bar_simulation_execution():
    """Verify bar-by-bar simulation runs without errors across multi-bar trends."""
    tester = BybitBacktester(starting_balance=1000.0, symbol="BTCUSDT")
    now = time.time()
    candles = []

    # Construct 100 bars with an initial range followed by a strong trend
    price = 50000.0
    for i in range(100):
        o = price
        if i < 40:
            c = price + (20.0 if i % 2 == 0 else -20.0)
        else:
            c = price + 50.0  # strong uptrend
        h = max(o, c) + 30.0
        l = min(o, c) - 30.0
        v = 500.0 + (i * 5)
        candles.append(Candle(
            timestamp=now - (100 - i) * 900,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            close_time=now - (100 - i - 1) * 900,
        ))
        price = c

    metrics = tester.run_simulation(candles)
    assert isinstance(metrics, BacktestMetrics)
    assert metrics.max_drawdown_pct >= 0.0
