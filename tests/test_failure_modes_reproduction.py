"""
Regression test file demonstrating and reproducing the specific failure modes identified
in the Polybot Production Safety Brief:
1. Orderbook sequence gap not tracked / invalidated.
2. Reconciliation adopting an untracked position into ACTIVE even when emergency SL fails.
3. ExecutionPolicy vs Policy R:R constraint divergence.
4. Paper trading instant fill without queue / orderbook check.
5. Duplicate execution events double-counting filled quantity.
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
from src.bybit.exchange.instruments import BybitInstrumentRepository
from src.bybit.risk.policy import PolicyEngine
from src.bybit.risk.position_sizer import PositionSizer
from src.bybit.risk.circuit_breakers import CircuitBreakers
from src.bybit.config import HardSafetyLimits
from src.bybit.models import (
    PositionState, OrderSide, OrderType, StrategyName, MarketRegime,
    ExecutionPlan, ActivePosition, MarketSnapshot, Signal
)


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.save_state_transition = AsyncMock()
    db.save_trade = AsyncMock()
    db.save_governor_state = AsyncMock()
    db.set_state = AsyncMock()
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
    return repo


def test_failure_mode_1_orderbook_sequence_gap():
    """
    FAILURE MODE 1:
    When a delta update has a sequence gap (e.g. u=100 -> u=105),
    the current BybitPublicWS has no sequence validator or validity state.
    It simply applies the gapped delta and leaves the book 'valid' / usable!
    """
    ws = BybitPublicWS(["BTCUSDT"])
    
    # 1. Snapshot with u=100
    snapshot_msg = {
        "topic": "orderbook.50.BTCUSDT",
        "type": "snapshot",
        "ts": time.time(),
        "data": {
            "s": "BTCUSDT",
            "b": [["50000.0", "1.0"]],
            "a": [["50010.0", "1.0"]],
            "u": 100,
            "seq": 1000,
        }
    }
    ws._handle_message(snapshot_msg)
    
    # Check if ws tracks validity state
    assert hasattr(ws, "is_orderbook_valid"), "BybitPublicWS lacks is_orderbook_valid method"
    assert ws.is_orderbook_valid("BTCUSDT") is True
    
    # 2. Delta with a GAP: u=105 (missing 101, 102, 103, 104)
    gap_msg = {
        "topic": "orderbook.50.BTCUSDT",
        "type": "delta",
        "ts": time.time(),
        "data": {
            "s": "BTCUSDT",
            "b": [["49990.0", "2.0"]],
            "a": [],
            "u": 105,
            "seq": 1050,
        }
    }
    ws._handle_message(gap_msg)
    
    # INVARIANT: Book MUST be marked invalid upon sequence gap!
    assert ws.is_orderbook_valid("BTCUSDT") is False, "Book must be INVALID after sequence gap!"


@pytest.mark.asyncio
async def test_failure_mode_2_reconciliation_fails_closed_on_sl_error(mock_db, instrument_repo):
    """
    FAILURE MODE 2:
    When an external position has no SL and set_trading_stop fails,
    the current reconciliation engine still adopts the position and transitions
    the state machine to ACTIVE! It should FAIL CLOSED (not ACTIVE).
    """
    mock_client = AsyncMock()
    mock_client.get_wallet_balance.return_value = {"equity": 2000.0, "available": 2000.0}
    mock_client.get_positions.return_value = [{
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": "0.05",
        "avgPrice": "60000.0",
        "stopLoss": "0",  # No stop loss!
    }]
    # Simulate set_trading_stop FAILING on exchange
    mock_client.set_trading_stop.side_effect = Exception("API Error: Network timeout")
    mock_client.place_order.return_value = {"retCode": 0}
    mock_client.cancel_all_orders = AsyncMock()

    mock_account = MagicMock()
    mock_account.cached_positions = {
        "BTCUSDT": {"size": 0.05, "entry_price": 60000.0, "side": "Buy", "stop_loss": 0.0}
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

    # Reconcile when SL fails
    res = await reconciler.reconcile()

    # INVARIANT: Must NOT be safely ACTIVE!
    assert sm.state != PositionState.ACTIVE, "Unprotected position MUST NOT be transitioned to ACTIVE!"
    assert reconciler.reconcile_frozen is True or sm.state in (PositionState.RECONCILING, PositionState.DAMAGE_CONTROL, PositionState.FLAT)


def test_failure_mode_5_duplicate_execution_events(mock_db, instrument_repo):
    """
    FAILURE MODE 5:
    When the same execution fill event arrives twice (duplicate WS message),
    handle_execution_fill currently adds execQty twice.
    """
    mock_client = AsyncMock()
    mock_account = MagicMock()
    mock_account.cached_positions = {}
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
        size=0.01,
        entry_price=60000.0,
        stop_price=59500.0,
        tp1_price=61000.0,
        tp2_price=62000.0,
        leverage=10,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        client_order_id="ord_1",
        entry_time=time.time(),
        state=PositionState.ENTRY_PENDING,
        filled_quantity=0.0,
        notional=600.0,
    )
    executor.live_positions["BTCUSDT"] = pos

    fill_event = {
        "execId": "exec_abc123",
        "symbol": "BTCUSDT",
        "execQty": "0.005",
        "execPrice": "60000.0",
    }

    # First event
    executor.handle_execution_fill(fill_event)
    assert pos.filled_quantity == 0.005

    # Duplicate event
    executor.handle_execution_fill(fill_event)
    # INVARIANT: Duplicate fill must be ignored! filled_quantity must remain 0.005, NOT 0.010!
    assert pos.filled_quantity == 0.005, f"Duplicate fill was counted twice! filled_quantity={pos.filled_quantity}"
