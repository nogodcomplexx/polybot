"""
Unit & Integration tests for Target Profit Harvester ($50+ Lock) and Break-Even Ratchet ($25+).
"""

import pytest
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock

from src.bybit.models import ActivePosition, PositionState, OrderSide, StrategyName, MarketRegime
from src.bybit.execution.executor import ExecutionEngine
from src.bybit.exchange.instruments import BybitInstrumentRepository


@pytest.fixture
def mock_deps():
    client = MagicMock()
    account = MagicMock()
    account.total_equity = 10000.0
    account.cached_positions = {}
    instrument_repo = BybitInstrumentRepository()
    # Mock BTCUSDT and SOLUSDT specs
    instrument_repo._specs = {
        "BTCUSDT": {"min_qty": 0.001, "qty_step": 0.001, "tick_size": 0.1, "min_notional": 5.0},
        "SOLUSDT": {"min_qty": 0.1, "qty_step": 0.1, "tick_size": 0.01, "min_notional": 5.0},
    }
    state_machine = MagicMock()
    state_machine.state = PositionState.ACTIVE
    state_machine.transition_to = AsyncMock()
    state_machine.validate_stop_update = MagicMock(return_value=True)
    exec_policy = MagicMock()
    db = MagicMock()
    db.save_trade = AsyncMock()

    return {
        "client": client,
        "account": account,
        "instrument_repo": instrument_repo,
        "state_machine": state_machine,
        "exec_policy": exec_policy,
        "db": db,
    }


@pytest.mark.asyncio
async def test_paper_target_profit_harvesting(mock_deps):
    """Verify that reaching min_take_profit_usd ($50) immediately harvests profit and closes position."""
    executor = ExecutionEngine(
        client=mock_deps["client"],
        account=mock_deps["account"],
        instrument_repo=mock_deps["instrument_repo"],
        state_machine=mock_deps["state_machine"],
        exec_policy=mock_deps["exec_policy"],
        db=mock_deps["db"],
        mode="paper",
        min_take_profit_usd=50.0,
        breakeven_trigger_usd=25.0,
    )

    # Active BUY position: 50 SOL @ $100.00 (notional = $5,000)
    pos = ActivePosition(
        symbol="SOLUSDT",
        side=OrderSide.BUY,
        size=50.0,
        entry_price=100.0,
        stop_price=98.0,
        tp1_price=105.0,
        tp2_price=110.0,
        leverage=10,
        strategy=StrategyName.ABSORPTION,
        regime=MarketRegime.RANGE,
        client_order_id="ord_test_01",
        entry_time=time.time(),
        notional=5000.0,
        state=PositionState.ACTIVE,
        entry_fee=1.0,
        fees_paid=1.0,
        max_thesis_lifetime=3600.0,
    )
    executor.paper_positions["SOLUSDT"] = pos

    # Market moves to $100.40 -> gross profit = (100.40 - 100.0) * 50 = +$20 (< $25, < $50)
    closed = await executor.update_positions({"SOLUSDT": 100.40})
    assert len(closed) == 0
    assert "SOLUSDT" in executor.paper_positions
    assert pos.stop_price == 98.0  # Stop unchanged

    # Market moves to $100.60 -> gross profit = +$30 (>= $25 break-even trigger)
    closed = await executor.update_positions({"SOLUSDT": 100.60})
    assert len(closed) == 0
    assert "SOLUSDT" in executor.paper_positions
    # Stop loss should be moved to Breakeven (100 + 0.05% = 100.05)
    assert pos.stop_price > 98.0
    assert pos.stop_price >= 100.0
    assert getattr(pos, "be_moved", False) is True

    # Market moves to $101.10 -> gross profit = +$55.00 (>= $50 target profit trigger!)
    closed = await executor.update_positions({"SOLUSDT": 101.10})
    assert len(closed) == 1
    trade = closed[0]
    assert trade["symbol"] == "SOLUSDT"
    assert trade["status"] == "WIN"
    assert "Target Profit Reached" in trade["exit_reason"]
    assert trade["exit_price"] == 101.10
    assert trade["pnl"] > 50.0  # Net profit guaranteed > $50
    assert "SOLUSDT" not in executor.paper_positions


@pytest.mark.asyncio
async def test_paper_target_profit_short(mock_deps):
    """Verify that reaching min_take_profit_usd ($50) on a SHORT position closes cleanly."""
    executor = ExecutionEngine(
        client=mock_deps["client"],
        account=mock_deps["account"],
        instrument_repo=mock_deps["instrument_repo"],
        state_machine=mock_deps["state_machine"],
        exec_policy=mock_deps["exec_policy"],
        db=mock_deps["db"],
        mode="paper",
        min_take_profit_usd=50.0,
        breakeven_trigger_usd=25.0,
    )

    # Active SELL position: 40 SOL @ $100.00
    pos = ActivePosition(
        symbol="SOLUSDT",
        side=OrderSide.SELL,
        size=40.0,
        entry_price=100.0,
        stop_price=102.0,
        tp1_price=95.0,
        tp2_price=90.0,
        leverage=10,
        strategy=StrategyName.ABSORPTION,
        regime=MarketRegime.RANGE,
        client_order_id="ord_test_02",
        entry_time=time.time(),
        notional=4000.0,
        state=PositionState.ACTIVE,
        entry_fee=0.8,
        fees_paid=0.8,
        max_thesis_lifetime=3600.0,
    )
    executor.paper_positions["SOLUSDT"] = pos

    # Market price drops to $98.70 -> profit = (100.0 - 98.70) * 40 = +$52.00 (>= $50)
    closed = await executor.update_positions({"SOLUSDT": 98.70})
    assert len(closed) == 1
    trade = closed[0]
    assert trade["symbol"] == "SOLUSDT"
    assert trade["status"] == "WIN"
    assert "Target Profit Reached" in trade["exit_reason"]
    assert trade["pnl"] > 45.0
    assert "SOLUSDT" not in executor.paper_positions
