"""
Tests for Portfolio Performance, ROI Calculation, and BybitDatabase Stats.
"""

import pytest
import time
import os
import tempfile
from src.bybit.storage.database import BybitDatabase
from src.bybit.telemetry.events import PerformanceTelemetryEvent


@pytest.mark.asyncio
async def test_database_performance_stats():
    """Verify calculation of aggregate P&L, win rate, and profit factor."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_path = tf.name

    try:
        db = BybitDatabase(db_path=temp_path)
        await db.connect()

        # 1. Initially empty
        stats0 = await db.get_performance_stats()
        assert stats0["total_trades"] == 0
        assert stats0["total_pnl"] == 0.0
        assert stats0["win_rate_pct"] == 0.0

        # 2. Insert winning trade
        await db.save_trade({
            "id": "t1",
            "timestamp": time.time(),
            "symbol": "BTCUSDT",
            "side": "Buy",
            "strategy": "SFP",
            "regime": "TREND",
            "entry": 60000.0,
            "exit_price": 60500.0,
            "stop_price": 59700.0,
            "tp1": 60500.0,
            "tp2": 61000.0,
            "quantity": 1.0,
            "pnl": 500.0,
            "pnl_pct": 0.83,
            "fees": 5.0,
            "status": "WIN",
        })

        # 3. Insert losing trade
        await db.save_trade({
            "id": "t2",
            "timestamp": time.time(),
            "symbol": "ETHUSDT",
            "side": "Sell",
            "strategy": "OBI",
            "regime": "RANGE",
            "entry": 3000.0,
            "exit_price": 3050.0,
            "stop_price": 3060.0,
            "tp1": 2950.0,
            "tp2": 2900.0,
            "quantity": 2.0,
            "pnl": -100.0,
            "pnl_pct": -1.67,
            "fees": 4.0,
            "status": "LOSS",
        })

        stats = await db.get_performance_stats()
        assert stats["total_trades"] == 2
        assert stats["total_pnl"] == 400.0
        assert stats["gross_profit"] == 500.0
        assert stats["gross_loss"] == 100.0
        assert stats["win_count"] == 1
        assert stats["loss_count"] == 1
        assert stats["win_rate_pct"] == 50.0
        assert stats["profit_factor"] == 5.0
        assert stats["daily_pnl"] == 400.0
        assert stats["total_fees"] == 9.0

        # Baseline equity check
        b1 = await db.get_or_set_baseline_equity(250000.0)
        assert b1 == 250000.0
        # Second call returns existing baseline
        b2 = await db.get_or_set_baseline_equity(260000.0)
        assert b2 == 250000.0

        await db.close()
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_performance_telemetry_event():
    """Verify PerformanceTelemetryEvent contract."""
    evt = PerformanceTelemetryEvent(
        timestamp=time.time(),
        starting_equity=200000.0,
        current_equity=205000.0,
        total_pnl_usd=5000.0,
        total_roi_pct=2.5,
        daily_pnl_usd=1200.0,
        daily_roi_pct=0.6,
        realized_pnl_usd=4000.0,
        unrealized_pnl_usd=1000.0,
        win_count=8,
        loss_count=2,
        total_trades=10,
        win_rate_pct=80.0,
        profit_factor=3.5,
        total_fees=50.0,
    )
    d = evt.to_dict()
    assert d["type"] == "PERFORMANCE_TELEMETRY"
    assert d["total_roi_pct"] == 2.5
    assert d["total_pnl_usd"] == 5000.0
    assert d["win_rate_pct"] == 80.0
