"""
Automated Verification of Critical & High Audit Fixes.
Tests:
1. Live TP2 runner execution and desk return to FLAT.
2. Live partial fill remainder cancellation to prevent orphan fills.
3. Live TP1 order failure resilience (no premature state advance).
4. Reconciliation auto-adoption of external exchange positions with emergency stop loss.
5. Reconciliation emergency liquidation when auto-adoption is disabled.
6. Public WS orderbook depth pruning (preventing phantom levels).
7. Public WS crossed orderbook detection and invalidation.
8. Execution policy 15-point checks (geometry, notional, depth).
9. Circuit breaker UTC midnight drawdown kill switch reset.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock
import pytest

from src.bybit.execution.executor import ExecutionEngine
from src.bybit.execution.execution_policy import ExecutionPolicyEngine
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.state.reconciliation import ReconciliationEngine
from src.bybit.exchange.public_ws import BybitPublicWS
from src.bybit.risk.circuit_breakers import CircuitBreakers
from src.bybit.exchange.instruments import BybitInstrumentRepository
from src.bybit.models import (
    PositionState, OrderSide, OrderType, StrategyName, MarketRegime,
    ExecutionPlan, ActivePosition, MarketSnapshot
)


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.save_state_transition = AsyncMock()
    db.save_trade = AsyncMock()
    db.save_governor_state = AsyncMock()
    db.load_governor_state = AsyncMock(return_value={
        "consecutive_losses": 0,
        "cooldown_until": 0.0,
        "high_watermark": 1000.0,
        "kill_switch_active": False,
    })
    return db


@pytest.fixture
def instrument_repo():
    repo = BybitInstrumentRepository()
    repo.update_spec("BTCUSDT", {
        "tick_size": 0.10,
        "qty_step": 0.001,
        "min_order_qty": 0.001,
        "max_order_qty": 100.0,
        "max_leverage": 100.0,
    })
    repo.update_spec("ETHUSDT", {
        "tick_size": 0.01,
        "qty_step": 0.01,
        "min_order_qty": 0.01,
        "max_order_qty": 500.0,
        "max_leverage": 50.0,
    })
    return repo


@pytest.mark.asyncio
async def test_live_tp2_runner_close(mock_db, instrument_repo):
    """Verify C1: In live mode, runner is executed at TP2 and desk resets to FLAT."""
    mock_client = AsyncMock()
    mock_client.place_order.return_value = {"retCode": 0, "result": {"orderId": "tp2_order_id"}}
    mock_client.set_trading_stop = AsyncMock()

    mock_account = MagicMock()
    mock_account.cached_positions = {"BTCUSDT": {"size": 0.01, "entry_price": 60000.0, "stop_loss": 60030.0}}

    sm = PositionStateMachine(mock_db)
    sm.state = PositionState.PROTECTED_RUNNER

    executor = ExecutionEngine(
        client=mock_client,
        account=mock_account,
        instrument_repo=instrument_repo,
        state_machine=sm,
        exec_policy=MagicMock(),
        db=mock_db,
        mode="live",
    )

    pos = ActivePosition(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        size=0.005,  # remaining 50% runner
        entry_price=60000.0,
        stop_price=60030.0,
        tp1_price=61000.0,
        tp2_price=62000.0,
        leverage=10,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        client_order_id="ord_live_test",
        entry_time=time.time() - 30.0,
        state=PositionState.PROTECTED_RUNNER,
        filled_quantity=0.01,
        notional=600.0,
        tp1_filled=True,  # TP1 already completed
    )
    executor.live_positions["BTCUSDT"] = pos
    sm.active_position = pos

    # Current price hits TP2 ($62,100 >= $62,000)
    closed = await executor.update_positions({"BTCUSDT": 62100.0})

    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "TP2 Target Reached (Live)"
    assert closed[0]["status"] == "WIN"
    assert sm.state == PositionState.FLAT
    assert "BTCUSDT" not in executor.live_positions
    mock_client.place_order.assert_called_with(
        symbol="BTCUSDT",
        side="Sell",
        qty=0.005,
        order_type="Market",
        reduce_only=True,
    )


@pytest.mark.asyncio
async def test_live_partial_fill_cancels_remainder(mock_db, instrument_repo):
    """Verify C2: When an entry partially fills, the remaining limit order is cancelled."""
    mock_client = AsyncMock()
    mock_client.cancel_order.return_value = {"retCode": 0}
    mock_client.set_trading_stop.return_value = {"retCode": 0}

    # Bybit reports 0.004 filled out of 0.010
    mock_account = MagicMock()
    mock_account.cached_positions = {"BTCUSDT": {"size": 0.004, "entry_price": 60000.0, "stop_loss": 0}}

    sm = PositionStateMachine(mock_db)
    sm.state = PositionState.ENTRY_PENDING

    executor = ExecutionEngine(
        client=mock_client,
        account=mock_account,
        instrument_repo=instrument_repo,
        state_machine=sm,
        exec_policy=MagicMock(),
        db=mock_db,
        mode="live",
    )

    pos = ActivePosition(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        size=0.010,
        entry_price=60000.0,
        stop_price=59500.0,
        tp1_price=61000.0,
        tp2_price=62000.0,
        leverage=10,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        client_order_id="limit_ord_123",
        entry_time=time.time(),
        state=PositionState.ENTRY_PENDING,
        filled_quantity=0.0,
        notional=600.0,
    )
    executor.live_positions["BTCUSDT"] = pos
    sm.active_position = pos

    # Update positions checks fill
    await executor.update_positions({"BTCUSDT": 60000.0})

    # Remaining order should have been cancelled
    mock_client.cancel_order.assert_called_with("BTCUSDT", order_link_id="limit_ord_123")
    # Position size synchronized to filled amount
    assert pos.size == 0.004
    assert sm.state == PositionState.ACTIVE


@pytest.mark.asyncio
async def test_live_tp1_failed_order_no_state_advance(mock_db, instrument_repo):
    """Verify C3: Failed TP1 market order does NOT mark tp1_filled or advance state to PROTECTED_RUNNER."""
    mock_client = AsyncMock()
    # Simulate Bybit rejecting the TP1 order
    mock_client.place_order.return_value = {"retCode": 10001, "retMsg": "Internal error"}
    mock_client.set_trading_stop = AsyncMock()

    mock_account = MagicMock()
    mock_account.cached_positions = {"BTCUSDT": {"size": 0.01, "entry_price": 60000.0, "stop_loss": 59500.0}}

    sm = PositionStateMachine(mock_db)
    sm.state = PositionState.ACTIVE

    executor = ExecutionEngine(
        client=mock_client,
        account=mock_account,
        instrument_repo=instrument_repo,
        state_machine=sm,
        exec_policy=MagicMock(),
        db=mock_db,
        mode="live",
    )

    pos = ActivePosition(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        size=0.01,
        entry_price=60000.0,
        stop_price=59500.0,
        tp1_price=61000.0,
        tp2_price=62000.0,
        leverage=10,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        client_order_id="ord_live_tp1_fail",
        entry_time=time.time() - 10.0,
        state=PositionState.ACTIVE,
        filled_quantity=0.01,
        notional=600.0,
        tp1_filled=False,
    )
    executor.live_positions["BTCUSDT"] = pos
    sm.active_position = pos

    # Price crosses TP1 ($61,050 >= $61,000)
    await executor.update_positions({"BTCUSDT": 61050.0})

    # Must NOT have marked tp1_filled as True
    assert pos.tp1_filled is False
    # State machine must NOT advance to PROTECTED_RUNNER
    assert sm.state == PositionState.ACTIVE
    # Stop loss must NOT have been moved to breakeven
    assert pos.stop_price == 59500.0


@pytest.mark.asyncio
async def test_reconciliation_auto_adopt_position(mock_db, instrument_repo):
    """Verify C4: Reconciliation adopts untracked exchange position and sets emergency SL."""
    mock_client = AsyncMock()
    mock_client.get_wallet_balance.return_value = {"equity": 2000.0, "available": 2000.0}
    mock_client.get_positions.return_value = [{
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": "0.05",
        "avgPrice": "60000.0",
        "stopLoss": "0",  # No stop loss set!
        "takeProfit": "0",
        "leverage": "10",
    }]
    mock_client.set_trading_stop.return_value = {"retCode": 0}

    mock_account = MagicMock()
    mock_account.cached_positions = {
        "BTCUSDT": {"size": 0.05, "entry_price": 60000.0, "side": "Buy", "stop_loss": 0.0, "take_profit": 0.0}
    }

    sm = PositionStateMachine(mock_db)
    sm.state = PositionState.FLAT

    executor = ExecutionEngine(
        client=mock_client,
        account=mock_account,
        instrument_repo=instrument_repo,
        state_machine=sm,
        exec_policy=MagicMock(),
        db=mock_db,
        mode="live",
    )

    reconciler = ReconciliationEngine(
        client=mock_client,
        account=mock_account,
        state_machine=sm,
        executor=executor,
        auto_adopt=True,
    )

    success = await reconciler.reconcile()

    assert success is True
    assert reconciler.reconcile_frozen is False
    assert sm.state == PositionState.ACTIVE
    assert "BTCUSDT" in executor.live_positions
    # Emergency 1% SL must have been placed on Bybit ($59,400)
    mock_client.set_trading_stop.assert_called_with(symbol="BTCUSDT", stop_loss=59400.0)


@pytest.mark.asyncio
async def test_reconciliation_emergency_close(mock_db, instrument_repo):
    """Verify C4: When auto_adopt=False, reconciler emergency liquidates untracked position."""
    mock_client = AsyncMock()
    mock_client.get_wallet_balance.return_value = {"equity": 2000.0, "available": 2000.0}
    mock_client.get_positions.return_value = [{
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": "1.5",
        "avgPrice": "3000.0",
    }]
    mock_client.cancel_all_orders = AsyncMock()
    mock_client.place_order = AsyncMock(return_value={"retCode": 0})

    mock_account = MagicMock()
    mock_account.cached_positions = {
        "ETHUSDT": {"size": 1.5, "entry_price": 3000.0, "side": "Buy"}
    }

    sm = PositionStateMachine(mock_db)
    sm.state = PositionState.RECONCILING

    reconciler = ReconciliationEngine(
        client=mock_client,
        account=mock_account,
        state_machine=sm,
        executor=None,
        auto_adopt=False,
    )

    success = await reconciler.reconcile()

    assert success is True
    assert reconciler.reconcile_frozen is False
    assert sm.state == PositionState.FLAT
    mock_client.cancel_all_orders.assert_called_with(symbol="ETHUSDT")
    mock_client.place_order.assert_called_with(
        symbol="ETHUSDT",
        side="Sell",
        qty=1.5,
        order_type="Market",
        reduce_only=True,
    )


def test_public_ws_orderbook_depth_pruning():
    """Verify H1: Orderbook levels are pruned to top 50 when size exceeds 75."""
    ws = BybitPublicWS(["BTCUSDT"])
    # Seed snapshot with 50 levels
    initial_bids = [[float(50000 - i * 10), 1.0] for i in range(50)]
    initial_asks = [[float(50100 + i * 10), 1.0] for i in range(50)]
    ws.orderbooks["BTCUSDT"] = {
        "bids": {p: s for p, s in initial_bids},
        "asks": {p: s for p, s in initial_asks},
        "ts": time.time(),
    }

    # Add 40 new deeper bid delta updates without deletion
    deltas = [[float(48000 - i * 10), 1.0] for i in range(40)]
    ws._update_orderbook("BTCUSDT", {"b": deltas, "a": []}, is_snapshot=False)

    # Dictionary must be pruned down to 50 levels
    assert len(ws.orderbooks["BTCUSDT"]["bids"]) == 50
    # Top bid must still be 50,000.0
    assert max(ws.orderbooks["BTCUSDT"]["bids"].keys()) == 50000.0


def test_public_ws_crossed_book_invalidation():
    """Verify H1: Crossed orderbook (bid >= ask) triggers cache invalidation."""
    ws = BybitPublicWS(["BTCUSDT"])
    ws.orderbooks["BTCUSDT"] = {
        "bids": {61000.0: 1.0, 60900.0: 2.0},
        "asks": {60500.0: 1.0, 60600.0: 2.0},  # Crossed! Bid 61000 >= Ask 60500
        "ts": time.time(),
    }

    summary = ws.get_orderbook_summary("BTCUSDT")

    # Orderbook must be cleared
    assert "BTCUSDT" not in ws.orderbooks
    assert summary["best_bid"] == 0.0
    assert summary["best_ask"] == 0.0


def test_execution_policy_15_checks():
    """Verify H3: Execution policy enforces bracket geometry, minimum notional, and depth."""
    policy = ExecutionPolicyEngine()

    valid_snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=time.time(),
        last_price=60000.0,
        mark_price=60005.0,
        index_price=60002.0,
        bid_depth=100.0,
        ask_depth=100.0,
        spread=1.0,
        spread_pct=0.002,
        atr_15m=300.0,
    )

    # 1. Valid BUY plan
    valid_plan = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=60000.0,
        stop_price=59500.0,
        tp1_price=61000.0,
        tp2_price=62000.0,
    )
    ok, reason = policy.evaluate_execution_precheck(valid_plan, valid_snapshot, ws_connected=True)
    assert ok is True, f"Valid plan failed: {reason}"

    # 2. Inverted Buy bracket (stop > entry)
    inverted_buy = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=60000.0,
        stop_price=60500.0,  # Invalid stop above entry!
        tp1_price=61000.0,
        tp2_price=62000.0,
    )
    ok, reason = policy.evaluate_execution_precheck(inverted_buy, valid_snapshot, ws_connected=True)
    assert ok is False
    assert "Inverted Buy bracket" in reason

    # 3. Minimum notional violation (< $5.00)
    low_notional = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.00005,  # 0.00005 * 60000 = $3.00 < $5.00
        entry_price=60000.0,
        stop_price=59500.0,
        tp1_price=61000.0,
        tp2_price=62000.0,
    )
    ok, reason = policy.evaluate_execution_precheck(low_notional, valid_snapshot, ws_connected=True)
    assert ok is False
    assert "below exchange minimum" in reason

    # 4. Zero depth violation
    empty_depth_snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=time.time(),
        last_price=60000.0,
        mark_price=60000.0,
        index_price=60000.0,
        bid_depth=0.0,
        ask_depth=0.0,
        spread=1.0,
        spread_pct=0.002,
        atr_15m=300.0,
    )
    ok, reason = policy.evaluate_execution_precheck(valid_plan, empty_depth_snapshot, ws_connected=True)
    assert ok is False
    assert "Insufficient orderbook depth" in reason


def test_circuit_breakers_midnight_drawdown_reset(mock_db):
    """Verify H4: UTC midnight rollover resets DRAWDOWN_KILL switch."""
    cb = CircuitBreakers(mock_db, high_watermark=1000.0)
    # Simulate active drawdown kill
    cb.kill_switches["DRAWDOWN_KILL"] = True
    cb.daily_drawdown_pct = 5.5

    # Simulate UTC midnight rollover
    cb.today_start = time.time() - 100000  # yesterday

    cb.update_equity(1000.0)

    # DRAWDOWN_KILL must be reset to False
    assert cb.kill_switches["DRAWDOWN_KILL"] is False
    assert cb.daily_drawdown_pct == 0.0
