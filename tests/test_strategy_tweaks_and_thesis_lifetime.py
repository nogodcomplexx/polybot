"""
Unit tests verifying thesis lifetime extension and strategy calibration tweaks.
"""

import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from src.bybit.models import (
    ActivePosition, PositionState, OrderSide, StrategyName, MarketRegime,
    ExecutionPlan, OrderType, MarketSnapshot
)
from src.bybit.execution.executor import ExecutionEngine
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.risk.circuit_breakers import CircuitBreakers
from src.bybit.exchange.account import BybitAccountRepository
from src.bybit.exchange.instruments import BybitInstrumentRepository
from src.bybit.storage.database import BybitDatabase
from src.bybit.market.candles import Candle
from src.bybit.alpha.strategies import AlphaStrategySuite
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.config import load_bybit_config, BybitScalperConfig, HardSafetyLimits


@pytest.mark.asyncio
async def test_max_thesis_lifetime_configuration_and_execution():
    """Verify that max_thesis_lifetime is configurable and prevents premature 600s exit."""
    # 1. Config loading check
    config = BybitScalperConfig(max_thesis_lifetime_sec=3600.0)
    assert config.max_thesis_lifetime_sec == 3600.0

    account = BybitAccountRepository(starting_equity=10000.0)
    db = BybitDatabase(":memory:")
    await db.connect()
    state_machine = PositionStateMachine(db)
    client = MagicMock()
    instrument_repo = BybitInstrumentRepository()

    executor = ExecutionEngine(
        client=client,
        account=account,
        instrument_repo=instrument_repo,
        state_machine=state_machine,
        exec_policy=MagicMock(),
        db=db,
        mode="paper",
        leverage=10,
        max_thesis_lifetime=3600.0,
    )
    assert executor.max_thesis_lifetime == 3600.0

    # Execute paper entry
    plan = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=50000.0,
        quantity=0.1,
        stop_price=49600.0,
        tp1_price=50640.0,
        tp2_price=51200.0,
        client_order_id="test_plan_001",
        ttl_seconds=300.0,
        post_only=False,
    )

    success = await executor.execute_plan(plan, StrategyName.TREND_CONTINUATION, MarketRegime.TREND_UP)
    assert success is True
    assert "BTCUSDT" in executor.paper_positions
    pos = executor.paper_positions["BTCUSDT"]
    assert pos.max_thesis_lifetime == 3600.0

    # 2. Check at 600s (old timeout): Should NOT close
    current_prices = {"BTCUSDT": 50100.0}
    # Mock entry_time to 605s ago
    pos.entry_time = time.time() - 605.0
    closed = await executor.update_positions(current_prices)
    assert len(closed) == 0, "Position should NOT be invalidated at 605s with 3600s thesis lifetime"
    assert "BTCUSDT" in executor.paper_positions

    # 3. Check at 3605s (new timeout): Should close with Time-Based Thesis Invalidation
    pos.entry_time = time.time() - 3605.0
    closed = await executor.update_positions(current_prices)
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "Time-Based Thesis Invalidation"
    assert "BTCUSDT" not in executor.paper_positions
    await db.close()


def test_trend_continuation_tightened_criteria():
    """Verify TREND_CONTINUATION requires pullback, EMA alignment, and OBI filter."""
    suite = AlphaStrategySuite()

    # Generate 30 candles
    base_time = time.time() - 30 * 900
    candles = []
    # Upward trending candles: 50000 to 52000
    for i in range(30):
        p = 50000.0 + (i * 70.0)
        candles.append(Candle(
            timestamp=base_time + (i * 900),
            open=p - 10.0,
            high=p + 20.0,
            low=p - 20.0,
            close=p,
            volume=100.0,
            close_time=base_time + ((i + 1) * 900),
        ))

    last_p = candles[-1].close  # ~52030
    closes = [c.close for c in candles]
    ema9 = suite._calculate_ema(closes, 9)
    ema21 = suite._calculate_ema(closes, 21)
    assert ema9 > ema21, "EMA9 should be greater than EMA21 in uptrend"

    # Case A: Price is extended far above EMA9 (e.g. 1.5% away) -> FOMO Chasing REJECTED
    extended_price = ema9 * 1.015
    snap_extended = MarketSnapshot(
        timestamp=time.time(),
        symbol="BTCUSDT",
        last_price=extended_price,
        orderbook_imbalance=1.30,
        regime=MarketRegime.TREND_UP,
        atr_15m=300.0,
    )
    sig = suite.evaluate_trend_continuation(snap_extended, candles)
    assert sig is None, "Must reject entry when price is extended >0.75% above EMA9"

    # Case B: Price is near EMA9 pullback (0.2% away), but OBI is weak (1.10 < 1.25) -> REJECTED
    pullback_price = ema9 * 1.002
    snap_weak_obi = MarketSnapshot(
        timestamp=time.time(),
        symbol="BTCUSDT",
        last_price=pullback_price,
        orderbook_imbalance=1.10,
        regime=MarketRegime.TREND_UP,
        atr_15m=300.0,
    )
    sig = suite.evaluate_trend_continuation(snap_weak_obi, candles)
    assert sig is None, "Must reject entry when OBI < 1.25"

    # Case C: Price is near EMA9 pullback (0.2% away) and OBI is strong (1.35 >= 1.25) -> APPROVED
    snap_valid = MarketSnapshot(
        timestamp=time.time(),
        symbol="BTCUSDT",
        last_price=pullback_price,
        orderbook_imbalance=1.35,
        regime=MarketRegime.TREND_UP,
        atr_15m=300.0,
    )
    sig = suite.evaluate_trend_continuation(snap_valid, candles)
    assert sig is not None, "Must generate signal on valid pullback with strong OBI"
    assert sig.side == OrderSide.BUY

    # Case D: Check stop bounds
    # Stop distance must be >= 0.35% and <= 0.85%
    stop_dist = sig.entry - sig.structural_stop
    stop_pct = (stop_dist / sig.entry) * 100
    assert 0.34 <= stop_pct <= 0.86, f"Stop percent {stop_pct:.2f}% must be within 0.35% - 0.85%"

    # Target 1 must be entry + 1.6 * stop_dist
    expected_tp1 = sig.entry + (stop_dist * 1.6)
    assert abs(sig.tp1 - expected_tp1) < 1e-4


@pytest.mark.asyncio
async def test_paper_mode_kill_switch_bypass():
    """Verify that paper mode and kill_switch_enabled=False bypass DRAWDOWN_KILL."""
    db = BybitDatabase(":memory:")
    await db.connect()

    # Pre-populate database with an active kill switch state (from previous losses)
    await db.save_governor_state(
        consecutive_losses=3,
        cooldown_until=0.0,
        high_watermark=10000.0,
        daily_loss=6.5,
        kill_switch=True,
    )

    # Initialize CircuitBreakers in paper mode with kill_switch_enabled=False
    cb = CircuitBreakers(
        db=db,
        high_watermark=10000.0,
        mode="paper",
        cooldown_enabled=False,
        kill_switch_enabled=False,
    )
    await cb.initialize()

    # Must NOT restore DRAWDOWN_KILL in paper mode
    assert cb.kill_switches["DRAWDOWN_KILL"] is False
    killed, reason = cb.is_any_kill_switch_active()
    assert killed is False
    assert reason == ""

    # Even if drawdown goes to 10%, paper mode must not trigger kill switch
    cb.update_equity(8000.0)  # 20% drawdown!
    assert cb.daily_drawdown_pct >= 20.0
    assert cb.kill_switches["DRAWDOWN_KILL"] is False
    killed, _ = cb.is_any_kill_switch_active()
    assert killed is False

    # Risk tier must not zero out sizing in paper mode
    risk_scalar, a_plus_only = cb.get_drawdown_risk_tier()
    assert risk_scalar == 1.0
    assert a_plus_only is False

    await db.close()


@pytest.mark.asyncio
async def test_live_positions_handles_string_size_from_exchange():
    """Verify that _update_live_positions gracefully handles string sizes from exchange."""
    db = BybitDatabase(":memory:")
    await db.connect()
    account = BybitAccountRepository(starting_equity=10000.0)
    state_machine = PositionStateMachine(db)
    client = MagicMock()
    # Mock client methods
    client.cancel_order = AsyncMock(return_value={"retCode": 0})
    client.set_trading_stop = AsyncMock(return_value=True)
    instrument_repo = BybitInstrumentRepository()

    executor = ExecutionEngine(
        client=client,
        account=account,
        instrument_repo=instrument_repo,
        state_machine=state_machine,
        exec_policy=MagicMock(),
        db=db,
        mode="live",
        leverage=10,
    )

    pos = ActivePosition(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        size=0.1,
        entry_price=50000.0,
        stop_price=49600.0,
        tp1_price=50640.0,
        tp2_price=51200.0,
        leverage=10,
        strategy=StrategyName.TREND_CONTINUATION,
        regime=MarketRegime.TREND_UP,
        client_order_id="ord_live_test",
        entry_time=time.time(),
        state=PositionState.ENTRY_PENDING,
        filled_quantity=0.0,
        notional=5000.0,
    )
    executor.live_positions["BTCUSDT"] = pos

    # Simulate exchange reporting string values for size and prices
    account.update_positions([{
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": "0.1",           # String from exchange!
        "avgPrice": "50000.0",     # String from exchange!
        "stopLoss": "0.0",
        "takeProfit": "0.0",
    }])

    # Update positions should not crash with TypeError: '>' not supported between instances of 'str' and 'int'
    closed = await executor.update_positions({"BTCUSDT": 50050.0})
    assert pos.state == PositionState.ACTIVE
    assert pos.size == 0.1
    assert isinstance(pos.size, float)
    assert pos.filled_quantity == 0.1

    await db.close()


@pytest.mark.asyncio
async def test_time_invalidation_maker_exit_fee_reduction():
    """Verify Fix 1: Time invalidation exits use maker order / maker fee (0.02% instead of 0.055% taker)."""
    account = BybitAccountRepository(starting_equity=10000.0)
    db = BybitDatabase(":memory:")
    await db.connect()
    state_machine = PositionStateMachine(db)
    client = MagicMock()
    instrument_repo = BybitInstrumentRepository()

    executor = ExecutionEngine(
        client=client,
        account=account,
        instrument_repo=instrument_repo,
        state_machine=state_machine,
        exec_policy=MagicMock(),
        db=db,
        mode="paper",
        leverage=10,
        max_thesis_lifetime=1200.0,
    )

    plan = ExecutionPlan(
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        entry_price=2000.0,
        quantity=2.0,
        stop_price=1950.0,
        tp1_price=2100.0,
        tp2_price=2200.0,
        client_order_id="test_plan_eth",
        ttl_seconds=300.0,
        post_only=False,
    )

    await executor.execute_plan(plan, StrategyName.TREND_CONTINUATION, MarketRegime.TREND_UP)
    pos = executor.paper_positions["ETHUSDT"]
    # Set maker entry fee
    pos.entry_fee = 4000.0 * 0.0002  # $0.80
    pos.fees_paid = pos.entry_fee
    # Fast forward past 1200s
    pos.entry_time = time.time() - 1250.0

    current_prices = {"ETHUSDT": 2001.0}
    closed = await executor.update_positions(current_prices)
    assert len(closed) == 1
    rec = closed[0]
    assert rec["exit_reason"] == "Time-Based Thesis Invalidation"
    # Entry notional = 4000 ($0.80 maker), Exit notional = 4002 ($0.80 maker).
    # Maker total fee = $1.60. (If exit was taker, total fee would be $0.80 + $2.20 = $3.00)
    assert rec["fees"] == 1.60, f"Expected maker exit fee ($1.60), got ${rec['fees']}"
    await db.close()


def test_vwap_fvg_reversion_regimes_restricted():
    """Verify Fix 2: VWAP_FVG_REVERSION is strictly restricted to RANGE and LOW_VOL."""
    engine = MarketRegimeEngine()
    assert engine.is_strategy_allowed(StrategyName.VWAP_FVG_REVERSION, MarketRegime.RANGE) is True
    assert engine.is_strategy_allowed(StrategyName.VWAP_FVG_REVERSION, MarketRegime.LOW_VOL) is True
    # Must NOT be allowed in trending or high-vol regimes
    assert engine.is_strategy_allowed(StrategyName.VWAP_FVG_REVERSION, MarketRegime.TREND_UP) is False
    assert engine.is_strategy_allowed(StrategyName.VWAP_FVG_REVERSION, MarketRegime.TREND_DOWN) is False
    assert engine.is_strategy_allowed(StrategyName.VWAP_FVG_REVERSION, MarketRegime.HIGH_VOL) is False


@pytest.mark.asyncio
async def test_macro_trend_alignment_gate_for_longs():
    """Verify Fix 3: Counter-trend Longs are vetoed in bearish macro/local regimes."""
    from src.bybit.risk.policy import PolicyEngine
    from src.bybit.risk.position_sizer import PositionSizer
    from src.bybit.models import Signal

    account = MagicMock()
    account.total_equity = 10_000.0
    account.available_balance = 9_500.0
    account.fortress_percent = 95.0
    account.is_fortress_secure = MagicMock(return_value=True)

    instrument_repo = MagicMock()
    instrument_repo.round_qty = MagicMock(side_effect=lambda sym, q: round(q, 3))
    instrument_repo.get_spec = MagicMock(return_value={"qty_step": 0.001, "min_qty": 0.001})

    sizer = PositionSizer(instrument_repo, max_risk_ceiling_pct=2.0)
    db = MagicMock()
    db.set_state = AsyncMock()
    db.save_rejection = AsyncMock()
    db.get_state = AsyncMock(return_value=None)
    cb = CircuitBreakers(db)
    tier_sim = MagicMock()
    tier_sim.simulate_order = MagicMock(return_value=(True, ""))
    regime_engine = MarketRegimeEngine()

    policy = PolicyEngine(account, sizer, cb, tier_sim, regime_engine, db, HardSafetyLimits())

    # Case A: Long in TREND_DOWN regime -> Must be REJECTED
    long_signal_down = Signal(
        id="sig_long_down",
        timestamp=time.time(),
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TREND_CONTINUATION,
        regime=MarketRegime.TREND_DOWN,
        entry=2000.0,
        structural_stop=1980.0,
        tp1=2040.0,
        tp2=2080.0,
        alpha_score=85.0,
        expected_ev=0.0035,
        confidence=0.8,
        features={"execution_score": 80.0}
    )
    snap_neutral = MarketSnapshot(symbol="ETHUSDT", timestamp=time.time(), last_price=2000.0, best_bid=1999.9, best_ask=2000.1, spread_pct=0.01, btc_state="NEUTRAL")
    dec_a, _ = await policy.evaluate_trade(long_signal_down, snap_neutral)
    assert dec_a.approved is False
    assert "TREND_MISALIGNMENT" in dec_a.rejection_reason

    # Case B: Long in RANGE regime but BTC macro state is BEARISH -> Must be REJECTED
    long_signal_range = Signal(
        id="sig_long_range",
        timestamp=time.time(),
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        entry=2000.0,
        structural_stop=1980.0,
        tp1=2040.0,
        tp2=2080.0,
        alpha_score=85.0,
        expected_ev=0.0035,
        confidence=0.8,
        features={"execution_score": 80.0}
    )
    snap_btc_bearish = MarketSnapshot(symbol="ETHUSDT", timestamp=time.time(), last_price=2000.0, best_bid=1999.9, best_ask=2000.1, spread_pct=0.01, btc_state="BEARISH")
    dec_b, _ = await policy.evaluate_trade(long_signal_range, snap_btc_bearish)
    assert dec_b.approved is False
    assert "TREND_MISALIGNMENT" in dec_b.rejection_reason

    # Case C: Short in TREND_DOWN regime with BTC BEARISH -> Must PASS
    short_signal = Signal(
        id="sig_short_down",
        timestamp=time.time(),
        symbol="ETHUSDT",
        side=OrderSide.SELL,
        strategy=StrategyName.TREND_CONTINUATION,
        regime=MarketRegime.TREND_DOWN,
        entry=2000.0,
        structural_stop=2020.0,
        tp1=1960.0,
        tp2=1920.0,
        alpha_score=85.0,
        expected_ev=0.0035,
        confidence=0.8,
        features={"execution_score": 80.0}
    )
    dec_c, _ = await policy.evaluate_trade(short_signal, snap_btc_bearish)
    assert dec_c.approved is True, f"Aligned short must pass, got: {dec_c.rejection_reason}"


@pytest.mark.asyncio
async def test_dynamic_leverage_selection_and_liquidation_safety():
    """Verify autonomous AI dynamic leverage (10x - 100x) based on conviction and stop distance."""
    from src.bybit.risk.policy import PolicyEngine
    from src.bybit.risk.position_sizer import PositionSizer
    from src.bybit.models import Signal
    from src.bybit.risk.risk_tier import RiskTierSimulator

    account = MagicMock()
    account.total_equity = 10_000.0
    account.available_balance = 9_500.0
    account.fortress_percent = 95.0
    account.is_fortress_secure = MagicMock(return_value=True)

    instrument_repo = MagicMock()
    instrument_repo.round_qty = MagicMock(side_effect=lambda sym, q: round(q, 3))
    instrument_repo.get_spec = MagicMock(return_value={"qty_step": 0.001, "min_qty": 0.001, "max_leverage": 100.0})

    sizer = PositionSizer(instrument_repo, max_risk_ceiling_pct=2.0)
    db = MagicMock()
    db.set_state = AsyncMock()
    db.save_rejection = AsyncMock()
    db.get_state = AsyncMock(return_value=None)
    cb = CircuitBreakers(db)
    tier_sim = RiskTierSimulator()
    regime_engine = MarketRegimeEngine()

    policy = PolicyEngine(account, sizer, cb, tier_sim, regime_engine, db, HardSafetyLimits())

    # Case A: High conviction setup (Alpha 92) with very tight stop (0.25% stop distance)
    # Entry: 50,000, Stop: 49,875 (0.25% away)
    lev_high = policy.calculate_dynamic_leverage(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        entry=50000.0,
        stop=49875.0,
        alpha_score=92.0,
        min_leverage=10,
        max_leverage=100,
    )
    assert lev_high >= 50, f"Expected high leverage (>=50x) for tight stop + 92 alpha, got {lev_high}x"
    assert lev_high <= 100

    # Case B: Standard conviction setup (Alpha 78) with moderate stop (0.80% stop distance)
    # Entry: 50,000, Stop: 49,600 (0.80% away)
    lev_med = policy.calculate_dynamic_leverage(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        entry=50000.0,
        stop=49600.0,
        alpha_score=78.0,
        min_leverage=10,
        max_leverage=100,
    )
    assert 20 <= lev_med <= 50, f"Expected balanced leverage (20x-50x), got {lev_med}x"

    # Case C: Conservative setup (Alpha 71) with wide stop (1.80% stop distance)
    # Entry: 50,000, Stop: 49,100 (1.80% away)
    lev_low = policy.calculate_dynamic_leverage(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        entry=50000.0,
        stop=49100.0,
        alpha_score=71.0,
        min_leverage=10,
        max_leverage=100,
    )
    assert 10 <= lev_low <= 25, f"Expected conservative leverage (10x-25x), got {lev_low}x"

    # Case D: End-to-end policy evaluation approves with dynamic leverage
    sig = Signal(
        id="sig_dyn_lev",
        timestamp=time.time(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        entry=50000.0,
        structural_stop=49850.0,  # 0.30% tight stop
        tp1=50300.0,
        tp2=50600.0,
        alpha_score=88.0,
        expected_ev=0.0035,
        confidence=0.85,
        features={"execution_score": 85.0}
    )
    snap = MarketSnapshot(symbol="BTCUSDT", timestamp=time.time(), last_price=50000.0, best_bid=49995.0, best_ask=50005.0, spread_pct=0.01, btc_state="BULLISH")
    dec, qty = await policy.evaluate_trade(sig, snap)
    assert dec.approved is True
    assert dec.leverage >= 25, f"Expected dynamic leverage >= 25x, got {dec.leverage}x"
    assert qty > 0.0


