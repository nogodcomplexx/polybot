"""
Comprehensive Test Suite for Bybit V5 Linear Perpetual Scalper.
Tests all core invariants, mathematical calculations, state transitions,
risk policies, and failure resilience.
"""

import asyncio
import pytest
import time
import os

from src.bybit.models import (
    MarketSnapshot, Signal, PolicyDecision, ExecutionPlan,
    PositionState, MarketRegime, StrategyName, OrderSide,
    FailureType, ActivePosition
)
from src.bybit.config import (
    HardSafetyLimits, TunableParameterRegistry, BybitScalperConfig, UniverseConfig
)
from src.bybit.market.universe import DynamicUniverseManager
from unittest.mock import AsyncMock, MagicMock

from src.bybit.market.candles import Candle, MultiTimeframeCandleManager
from src.bybit.market.volatility import calculate_atr, calculate_realized_volatility
from src.bybit.market.vwap import calculate_vwap, calculate_vwap_bands
from src.bybit.market.microstructure import MicrostructureEngine
from src.bybit.market.spoof import SpoofDetector
from src.bybit.alpha.indicators import (
    calculate_wavetrend, calculate_rsi, detect_fvgs, detect_swings
)
from src.bybit.alpha.btc_filter import BTCBetaFilter
from src.bybit.alpha.scorer import AlphaScorer
from src.bybit.alpha.strategies import AlphaStrategySuite
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.exchange.instruments import BybitInstrumentRepository
from src.bybit.exchange.account import BybitAccountRepository
from src.bybit.risk.position_sizer import PositionSizer
from src.bybit.risk.circuit_breakers import CircuitBreakers
from src.bybit.risk.risk_tier import RiskTierSimulator
from src.bybit.risk.policy import PolicyEngine
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.execution.execution_policy import ExecutionPolicyEngine
from src.bybit.execution.executor import ExecutionEngine
from src.bybit.storage.database import BybitDatabase
import gc


def safe_remove_db(path: str):
    gc.collect()
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass



# ----------------------------------------------------------------------
# 1. Config & Parameter Clamping Tests
# ----------------------------------------------------------------------

def test_hard_safety_limits():
    """Verify non-negotiable hard invariants."""
    safety = HardSafetyLimits()
    assert safety.MAX_ACTIVE_POSITIONS == 1
    assert safety.MIN_CASH_FORTRESS_PCT == 85.0
    assert safety.MAX_RISK_PER_TRADE_PCT == 7.5
    assert safety.MIN_TP1_RR == 1.5
    assert safety.MIN_TP2_RR == 2.8
    assert safety.CONSECUTIVE_LOSS_LIMIT == 2
    assert safety.COOLDOWN_HOURS == 4.0


def test_tunable_parameters_clamping():
    """Verify self-tuner cannot violate MIN and MAX parameter bounds."""
    registry = TunableParameterRegistry()
    assert registry.get("ob_imbalance_bullish") == 1.30

    # Attempt to clamp below min (1.10)
    clamped = registry.update("ob_imbalance_bullish", 0.80)
    assert clamped == 1.10

    # Attempt to clamp above max (2.00)
    clamped = registry.update("ob_imbalance_bullish", 5.00)
    assert clamped == 2.00


# ----------------------------------------------------------------------
# 2. Technical Indicators & SMC Tests
# ----------------------------------------------------------------------

def test_wavetrend_oscillator():
    """Verify WaveTrend WT1 and WT2 calculation and curling detection."""
    now = time.time()
    candles = []
    price = 100.0
    # Create 50 candles with downward dip then curling upward
    for i in range(50):
        if i < 40:
            price -= 0.5  # trending down into oversold
        else:
            price += 0.8  # curling up
        candles.append(Candle(
            timestamp=now + i * 900,
            open=price,
            high=price + 0.3,
            low=price - 0.3,
            close=price,
            volume=10.0,
            close_time=now + (i + 1) * 900
        ))

    wt1, wt2, wt1_prev, is_curling = calculate_wavetrend(candles)
    assert isinstance(wt1, float)
    assert isinstance(wt2, float)
    assert isinstance(is_curling, bool)


def test_rsi_calculation():
    """Verify RSI Wilder calculation."""
    now = time.time()
    candles = [
        Candle(now + i * 60, 100 + i, 100 + i + 0.5, 100 + i - 0.5, 100 + i, 5.0, now + (i + 1) * 60)
        for i in range(25)
    ]
    rsi = calculate_rsi(candles, period=14)
    assert rsi > 70.0  # Monotonically increasing prices -> overbought


def test_fvg_detection():
    """Verify Fair Value Gap 3-candle imbalance detection."""
    now = time.time()
    c1 = Candle(now, 100, 102, 99, 101, 10, now + 60)
    c2 = Candle(now + 60, 101, 108, 101, 107, 20, now + 120)  # displacement
    c3 = Candle(now + 120, 107, 110, 105, 109, 15, now + 180) # c3.low (105) > c1.high (102)

    fvgs = detect_fvgs([c1, c2, c3])
    assert len(fvgs) == 1
    assert fvgs[0].fvg_type == "BULLISH"
    assert fvgs[0].lower == 102.0
    assert fvgs[0].upper == 105.0
    assert fvgs[0].midpoint == 103.5


# ----------------------------------------------------------------------
# 3. Microstructure & Spoofing Tests
# ----------------------------------------------------------------------

def test_microstructure_cvd_and_absorption():
    """Verify CVD delta accumulation and passive absorption detection."""
    micro = MicrostructureEngine(symbols=["BTCUSDT"])
    now = time.time()

    # 10 aggressive sell trades that fail to push price down
    for i in range(12):
        micro.process_trade("BTCUSDT", price=100.0, size=5.0, side="Sell", ts=now - (12 - i) * 0.5)

    book_summary = {"orderbook_imbalance": 1.35, "mid_price": 100.0}
    metrics = micro.get_metrics("BTCUSDT", book_summary)

    assert metrics["cvd"] == -60.0
    assert metrics["trade_velocity"] > 0
    assert metrics["absorption_bullish"] is True


def test_spoof_detector():
    """Verify large wall tracking and high spoof probability assignment."""
    detector = SpoofDetector(wall_multiple=2.5, max_spoof_prob=0.65)
    book = {
        "bids": {99.0: 1.0, 98.5: 1.0, 98.0: 100.0},  # 100.0 is a massive wall
        "asks": {100.0: 1.0, 100.5: 1.0, 101.0: 1.0},
    }
    spoof_prob = detector.evaluate_walls("BTCUSDT", book, current_price=99.5)
    assert spoof_prob > 0.0


# ----------------------------------------------------------------------
# 4. Position Sizing & Liquidation Safety Tests
# ----------------------------------------------------------------------

def test_position_sizing_from_invalidation():
    """Verify: Quantity = Risk Budget / abs(Entry - Stop)."""
    repo = BybitInstrumentRepository()
    repo.update_spec("BTCUSDT", {
        "tick_size": 0.10,
        "qty_step": 0.001,
        "min_order_qty": 0.001,
        "max_order_qty": 100.0,
    })
    sizer = PositionSizer(repo)

    equity = 1000.0
    allowed_risk_pct = 5.0  # $50 risk
    entry = 50000.0
    stop = 49000.0  # $1,000 stop distance

    # Expected qty = 50 / 1000 = 0.05 BTC
    qty = sizer.size_position("BTCUSDT", equity, allowed_risk_pct, entry, stop)
    assert qty == 0.05

    # Verify dollar risk does not exceed $50
    assert qty * abs(entry - stop) <= 50.0


def test_risk_tier_liquidation_safety():
    """Verify structural stop must lie safely before estimated liquidation."""
    sim = RiskTierSimulator()
    entry = 100.0
    # For 10x long with MMR 1%, est liq is around 100 * (1 - 0.1 + 0.01) = 91.0
    # Stop at 85.0 is below liquidation -> UNSAFE!
    safe, reason = sim.simulate_order(
        "SOLUSDT", OrderSide.BUY, quantity=1.0, entry_price=entry, stop_price=85.0,
        account_equity=1000.0, leverage=10
    )
    assert safe is False
    assert "liquidation" in reason.lower()

    # Stop at 96.0 is safely above 91.0 -> SAFE!
    safe_ok, _ = sim.simulate_order(
        "SOLUSDT", OrderSide.BUY, quantity=1.0, entry_price=entry, stop_price=96.0,
        account_equity=1000.0, leverage=10
    )
    assert safe_ok is True


# ----------------------------------------------------------------------
# 5. Policy Engine Constitutional Gatekeeper Tests (Section 62)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_policy_engine_rejection_on_cash_fortress():
    """Verify Cash Fortress breach (< 85%) immediately rejects trade."""
    db = BybitDatabase("test_polybot.db")
    await db.connect()
    account = BybitAccountRepository(starting_equity=1000.0)
    account.total_equity = 1000.0
    account.available_balance = 800.0  # Fortress = 80% (< 85% requirement!)

    repo = BybitInstrumentRepository()
    sizer = PositionSizer(repo)
    breakers = CircuitBreakers(db)
    sim = RiskTierSimulator()
    regime_eng = MarketRegimeEngine()

    policy = PolicyEngine(account, sizer, breakers, sim, regime_eng, db)

    now = time.time()
    snapshot = MarketSnapshot(
        timestamp=now,
        symbol="BTCUSDT",
        last_price=50000.0,
        mark_price=50000.0,
        orderbook_imbalance=1.40,
        spread_pct=0.02,
        regime=MarketRegime.RANGE,
    )
    signal = Signal(
        id="sig_test_1",
        timestamp=now,
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        entry=50000.0,
        structural_stop=49500.0,
        tp1=50800.0,
        tp2=51600.0,
        alpha_score=80.0,
        expected_ev=0.0035,
        confidence=0.75,
        features={"execution_score": 75.0}
    )

    decision, qty = await policy.evaluate_trade(signal, snapshot)
    assert decision.approved is False
    assert "CASH_FORTRESS" in decision.rejection_reason

    await db.close()
    if os.path.exists("test_polybot.db"):
        os.remove("test_polybot.db")


# ----------------------------------------------------------------------
# 6. Consecutive Loss Cooldown & Persistence Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consecutive_losses_4h_cooldown_persistence():
    """Verify 2 consecutive losses activates 4-hour cooldown and persists across restarts."""
    db = BybitDatabase("test_cooldown.db")
    await db.connect()

    breakers = CircuitBreakers(db, high_watermark=1000.0)
    await breakers.initialize()
    assert breakers.is_cooldown_active() is False

    # Loss 1
    await breakers.record_trade_result(-10.0, current_equity=990.0)
    assert breakers.consecutive_losses == 1
    assert breakers.is_cooldown_active() is False

    # Loss 2 -> 4-hour cooldown triggers!
    await breakers.record_trade_result(-15.0, current_equity=975.0)
    assert breakers.consecutive_losses == 2
    assert breakers.is_cooldown_active() is True
    assert breakers.cooldown_until > time.time() + 3.9 * 3600

    # Simulate process restart by reloading from database
    breakers_reloaded = CircuitBreakers(db, high_watermark=1000.0)
    await breakers_reloaded.initialize()
    assert breakers_reloaded.consecutive_losses == 2
    assert breakers_reloaded.is_cooldown_active() is True

    await db.close()
    safe_remove_db("test_cooldown.db")


# ----------------------------------------------------------------------
# 7. State Machine & Stop Immutability Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_machine_and_stop_immutability():
    """Verify stop can only move into profit, never widen."""
    db = BybitDatabase("test_sm.db")
    await db.connect()
    sm = PositionStateMachine(db)

    assert sm.state == PositionState.FLAT
    await sm.transition_to(PositionState.SIGNAL_DETECTED, "Signal found")
    assert sm.state == PositionState.SIGNAL_DETECTED

    await sm.transition_to(PositionState.POLICY_PENDING, "Auditing")
    assert sm.state == PositionState.POLICY_PENDING

    # Stop Immutability for Long: Entry=100, Stop=95.
    # Moving stop to 98 (closer/tighter) is ALLOWED
    assert sm.validate_stop_update(current_stop=95.0, proposed_stop=98.0, is_long=True) is True
    # Moving stop to 92 (widening/farther) is STRICTLY FORBIDDEN!
    assert sm.validate_stop_update(current_stop=95.0, proposed_stop=92.0, is_long=True) is False

    await db.close()
    safe_remove_db("test_sm.db")


# ----------------------------------------------------------------------
# 8. Execution Engine Paper Lifecycle Tests (Section 63)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execution_engine_paper_bracket_lifecycle():
    """
    Verify complete bracket execution:
    Entry -> ACTIVE -> TP1 (+1.5%, 50% closed, stop moved to entry+buffer) -> TP2 (closed).
    """
    db = BybitDatabase("test_exec.db")
    await db.connect()

    account = BybitAccountRepository(starting_equity=1000.0)
    repo = BybitInstrumentRepository()
    repo.update_spec("SOLUSDT", {"tick_size": 0.01, "qty_step": 0.01, "min_order_qty": 0.01})
    sm = PositionStateMachine(db)
    exec_pol = ExecutionPolicyEngine()

    from src.bybit.exchange.bybit_client import BybitClient
    client = BybitClient(testnet=True)

    executor = ExecutionEngine(
        client=client,
        account=account,
        instrument_repo=repo,
        state_machine=sm,
        exec_policy=exec_pol,
        db=db,
        mode="paper",
        leverage=10
    )

    plan = ExecutionPlan(
        symbol="SOLUSDT",
        side=OrderSide.BUY,
        quantity=1.0,
        entry_price=100.0,
        stop_price=98.0,
        tp1_price=101.6,
        tp2_price=103.0,
    )

    # 1. Execute Entry
    success = await executor.execute_plan(plan, StrategyName.TURTLE_SOUP, MarketRegime.RANGE)
    assert success is True
    assert sm.state == PositionState.ACTIVE
    assert "SOLUSDT" in executor.paper_positions

    # 2. Simulate price reaching TP1 ($101.60)
    await executor.update_positions({"SOLUSDT": 101.65})
    assert sm.state == PositionState.PROTECTED_RUNNER
    pos = executor.paper_positions["SOLUSDT"]
    assert pos.tp1_filled is True
    assert pos.stop_price >= 100.05  # Breakeven + buffer!

    # 3. Simulate price reaching TP2 ($103.00)
    closed = await executor.update_positions({"SOLUSDT": 103.05})
    assert len(closed) == 1
    assert closed[0]["status"] == "WIN"
    assert sm.state == PositionState.FLAT
    assert "SOLUSDT" not in executor.paper_positions

    await client.close()
    await db.close()
    safe_remove_db("test_exec.db")


# ----------------------------------------------------------------------
# 9. Dynamic Universe Screener & Rotation Tests (Section 25)
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dynamic_universe_ranking_and_filters():
    """Verify dynamic discovery filters out illiquid/wide-spread pairs and prioritizes top liquid ones."""
    cfg = UniverseConfig(
        mode="dynamic",
        top_n=3,
        min_turnover_usd=50_000_000.0,
        max_spread_pct=0.04,
        min_volatility_pct=2.5,
        pinned_symbols=["BTCUSDT"]
    )
    mgr = DynamicUniverseManager(cfg)

    mock_client = AsyncMock()
    mock_client.get_tickers.return_value = [
        # Pinned anchor
        {"symbol": "BTCUSDT", "lastPrice": "68000", "bid1Price": "67999", "ask1Price": "68001", "turnover24h": "1000000000", "highPrice24h": "69000", "lowPrice24h": "67000"},
        # High turnover, tight spread, good volatility (SHOULD BE SELECTED)
        {"symbol": "SOLUSDT", "lastPrice": "150", "bid1Price": "149.98", "ask1Price": "150.02", "turnover24h": "500000000", "highPrice24h": "158", "lowPrice24h": "145"},
        # High turnover, tight spread, good volatility (SHOULD BE SELECTED)
        {"symbol": "ETHUSDT", "lastPrice": "3500", "bid1Price": "3499.5", "ask1Price": "3500.5", "turnover24h": "800000000", "highPrice24h": "3600", "lowPrice24h": "3400"},
        # Fails turnover filter ($10M < $50M floor)
        {"symbol": "LOWVOLUSDT", "lastPrice": "10", "bid1Price": "9.99", "ask1Price": "10.01", "turnover24h": "10000000", "highPrice24h": "11", "lowPrice24h": "9.5"},
        # Fails spread filter (0.20% > 0.04%)
        {"symbol": "WIDESPREADUSDT", "lastPrice": "5", "bid1Price": "4.995", "ask1Price": "5.005", "turnover24h": "200000000", "highPrice24h": "5.5", "lowPrice24h": "4.5"},
        # Non-USDT pair (e.g. USDC or INVERSE)
        {"symbol": "BTCUSDC", "lastPrice": "68000", "bid1Price": "67999", "ask1Price": "68001", "turnover24h": "900000000", "highPrice24h": "69000", "lowPrice24h": "67000"},
    ]

    universe = await mgr.discover_universe(mock_client)
    # top_n = 3, pinned = BTCUSDT, top candidates = ETHUSDT, SOLUSDT
    assert len(universe) == 3
    assert "BTCUSDT" in universe
    assert "ETHUSDT" in universe
    assert "SOLUSDT" in universe
    assert "LOWVOLUSDT" not in universe
    assert "WIDESPREADUSDT" not in universe
    assert "BTCUSDC" not in universe


@pytest.mark.asyncio
async def test_dynamic_universe_rotation_protects_active_position():
    """Verify that an active position symbol is never evicted during universe rotation."""
    cfg = UniverseConfig(
        mode="dynamic",
        top_n=2,
        pinned_symbols=["BTCUSDT"]
    )
    mgr = DynamicUniverseManager(cfg, fallback_symbols=["BTCUSDT", "SOLUSDT"])
    mgr.active_universe = ["BTCUSDT", "SOLUSDT"]

    # In new scan, DOGEUSDT displaces SOLUSDT
    new_universe = ["BTCUSDT", "DOGEUSDT"]

    # Case 1: SOLUSDT is currently in an active trade!
    added, removed, finalized = mgr.compute_rotation_diff(new_universe, protected_symbol="SOLUSDT")
    assert "DOGEUSDT" in added
    assert "SOLUSDT" not in removed  # Protected!
    assert "SOLUSDT" in finalized

    # Case 2: No active trade on SOLUSDT
    added2, removed2, finalized2 = mgr.compute_rotation_diff(new_universe, protected_symbol=None)
    assert "DOGEUSDT" in added2
    assert "SOLUSDT" in removed2


@pytest.mark.asyncio
async def test_ws_dynamic_subscription_and_unsubscription():
    """Verify WebSocket dynamic subscription and unsubscription topic management."""
    from src.bybit.exchange.public_ws import BybitPublicWS
    ws = BybitPublicWS(symbols=["BTCUSDT"], testnet=True)

    assert ws.symbols == ["BTCUSDT"]

    # Subscribe new symbols
    await ws.subscribe_symbols(["ETHUSDT", "SOLUSDT"])
    assert "ETHUSDT" in ws.symbols
    assert "SOLUSDT" in ws.symbols
    assert len(ws.symbols) == 3

    # Seed some dummy cache data
    ws.latest_prices["SOLUSDT"] = 150.0
    ws.orderbooks["SOLUSDT"] = {"bids": {}, "asks": {}}

    # Unsubscribe SOLUSDT
    await ws.unsubscribe_symbols(["SOLUSDT"])
    assert "SOLUSDT" not in ws.symbols
    assert "SOLUSDT" not in ws.latest_prices
    assert "SOLUSDT" not in ws.orderbooks

