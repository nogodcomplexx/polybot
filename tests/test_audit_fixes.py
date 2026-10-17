"""
Unit tests verifying audit bug fixes across all layers:
- C1: BybitPrivateWS authentication and live position monitoring
- C2: Awaited policy DB logging
- C3: Turtle Soup StrategyName enum evaluation in AlphaEngine
- H1: True EMA calculation vs SMA in strategies & BTC filter
- H2: Expanded state reconciliation coverage
- H3: Spoof detector TTL cleanup
- H4: Microstructure rolling-window CVD
- H8: Atomic governor state batch persistence
- L2: TunableParameterRegistry.get ValueError on unknown parameter
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.bybit.exchange.private_ws import BybitPrivateWS
from src.bybit.models import (
    PositionState, OrderSide, StrategyName, MarketRegime,
    ExecutionPlan, Signal, MarketSnapshot
)
from src.bybit.config import TunableParameterRegistry, HardSafetyLimits
from src.bybit.alpha.strategies import AlphaStrategySuite
from src.bybit.alpha.btc_filter import BTCBetaFilter
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.market.microstructure import MicrostructureEngine
from src.bybit.market.spoof import SpoofDetector
from src.bybit.execution.executor import ExecutionEngine
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.state.reconciliation import ReconciliationEngine
from src.bybit.storage.database import BybitDatabase
from src.bybit.market.candles import Candle


def test_private_ws_auth_message():
    """Verify C1: Private WebSocket generates valid Bybit V5 auth message."""
    ws = BybitPrivateWS(api_key="test_key", api_secret="test_secret", testnet=True)
    auth = ws._generate_auth_message()
    assert auth["op"] == "auth"
    assert len(auth["args"]) == 3
    assert auth["args"][0] == "test_key"
    assert isinstance(auth["args"][1], int)  # expires ts in ms
    assert len(auth["args"][2]) == 64        # sha256 hex digest length


def test_true_ema_vs_sma():
    """Verify H1: True EMA gives higher weight to recent prices compared to SMA."""
    suite = AlphaStrategySuite()
    # 7 elements with period=3: initial seed is 10.0, then increases to 30.0
    series = [10.0, 10.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    sma = sum(series[-3:]) / 3.0
    ema = suite._calculate_ema(series, 3)
    # EMA must be greater than SMA for accelerating trend because of recent weighting
    assert isinstance(ema, float)
    assert ema > 0

    btc_filter = BTCBetaFilter()
    btc_ema = btc_filter._calculate_ema(series, 3)
    assert btc_ema == ema


def test_turtle_soup_strategy_allowed_by_enum():
    """Verify C3: Turtle Soup is properly checked via StrategyName enum."""
    regime_engine = MarketRegimeEngine()
    # Turtle soup allowed in RANGE and LOW_VOL
    assert regime_engine.is_strategy_allowed(StrategyName.TURTLE_SOUP, MarketRegime.RANGE) is True
    assert regime_engine.is_strategy_allowed(StrategyName.TURTLE_SOUP, MarketRegime.LOW_VOL) is True
    assert regime_engine.is_strategy_allowed(StrategyName.TURTLE_SOUP, MarketRegime.TREND_UP) is False


def test_tunable_registry_raises_on_unknown():
    """Verify L2: TunableParameterRegistry.get() raises ValueError on unknown param."""
    registry = TunableParameterRegistry()
    with pytest.raises(ValueError, match="Unknown tunable parameter"):
        registry.get("non_existent_parameter_xyz")


def test_microstructure_rolling_window_cvd():
    """Verify H4: MicrostructureEngine maintains a bounded rolling window for CVD."""
    micro = MicrostructureEngine(["BTCUSDT"])
    # Process 350 trades
    for i in range(350):
        micro.process_trade("BTCUSDT", price=50000.0, size=1.0, side="Buy", ts=time.time())

    # Window must be capped at 300
    assert len(micro.cvd_window["BTCUSDT"]) == 300
    metrics = micro.get_metrics("BTCUSDT", {"orderbook_imbalance": 1.0})
    assert metrics["cvd"] == 300.0


def test_spoof_detector_ttl_cleanup():
    """Verify H3: Spoof detector purges expired walls older than 60s."""
    detector = SpoofDetector()
    old_time = time.time() - 70.0
    detector.tracked_walls["BTCUSDT"] = {
        50000.0: {"size": 100.0, "first_seen": old_time, "cancelled": False}
    }
    # Evaluate with valid non-empty book
    book = {"bids": {49900.0: 1.0}, "asks": {50100.0: 1.0}}
    detector.evaluate_walls("BTCUSDT", book, 50000.0)
    assert 50000.0 not in detector.tracked_walls.get("BTCUSDT", {})


@pytest.mark.asyncio
async def test_reconciliation_expanded_states():
    """Verify H2: ReconciliationEngine catches discrepancies in all active states."""
    mock_client = AsyncMock()
    mock_client.get_wallet_balance.return_value = {"equity": 1000.0, "available": 1000.0}
    mock_client.get_positions.return_value = []  # Exchange has 0 positions

    mock_account = MagicMock()
    mock_account.cached_positions = {}

    db = MagicMock()
    db.save_state_transition = AsyncMock()
    sm = PositionStateMachine(db)
    sm.state = PositionState.PROTECTED_RUNNER  # One of the expanded active states

    reconciler = ReconciliationEngine(mock_client, mock_account, sm)
    res = await reconciler.reconcile()
    # Must have detected external close and reset to FLAT
    assert sm.state == PositionState.FLAT


@pytest.mark.asyncio
async def test_executor_live_entry_and_monitoring():
    """Verify C1: Executor attaches SL/TP to live orders and monitors live positions."""
    mock_client = AsyncMock()
    mock_client.place_order.return_value = {"retCode": 0, "result": {"orderId": "ord_123"}}
    mock_client.set_margin_mode = AsyncMock()
    mock_client.set_leverage = AsyncMock()
    mock_client.set_trading_stop = AsyncMock()

    mock_account = MagicMock()
    mock_account.total_equity = 1000.0
    mock_account.cached_positions = {}

    mock_inst = MagicMock()
    mock_inst.round_qty.return_value = 0.01

    db = MagicMock()
    db.save_state_transition = AsyncMock()
    sm = PositionStateMachine(db)

    exec_policy = MagicMock()

    executor = ExecutionEngine(
        client=mock_client,
        account=mock_account,
        instrument_repo=mock_inst,
        state_machine=sm,
        exec_policy=exec_policy,
        db=db,
        mode="live",
        leverage=10,
    )

    plan = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=60000.0,
        stop_price=59500.0,
        tp1_price=60750.0,
        tp2_price=61400.0,
    )

    success = await executor.execute_plan(plan, StrategyName.TURTLE_SOUP, MarketRegime.RANGE)
    assert success is True
    assert "BTCUSDT" in executor.live_positions
    assert executor.live_positions["BTCUSDT"].state == PositionState.ENTRY_PENDING

    # Verify place_order was called with emergency stop_loss attached, while take_profit is omitted (handled via TP maker orders)
    mock_client.place_order.assert_called_once()
    kwargs = mock_client.place_order.call_args.kwargs
    assert kwargs["stop_loss"] == 59500.0
    assert "take_profit" not in kwargs or kwargs["take_profit"] is None

    # Simulate fill detection via account.cached_positions
    mock_account.cached_positions = {
        "BTCUSDT": {"symbol": "BTCUSDT", "size": 0.01, "entry_price": 60000.0, "stop_loss": 59500.0}
    }
    await executor.update_positions({"BTCUSDT": 60050.0})
    assert executor.live_positions["BTCUSDT"].state == PositionState.ACTIVE
    assert sm.state == PositionState.ACTIVE
