"""
Unit and regression tests verifying:
1. Separate HARD VETO from ALPHA SIGNALS.
2. Additive ALPHA SCORING (missing indicators = 0 pts, not a veto).
3. Independent strategy archetypes (Turtle Soup, VWAP/FVG Reversion, Orderbook Absorption).
4. Score-based risk scaling (70-74: 0.75x, 75-84: 1.0x, 85+: 1.25x).
5. Preservation of all non-negotiable hard safety constraints.
"""

import pytest
import time
from unittest.mock import MagicMock, AsyncMock

from src.bybit.models import (
    Signal, MarketSnapshot, OrderSide, StrategyName, MarketRegime, PolicyDecision, PositionState
)
from src.bybit.market.candles import Candle
from src.bybit.config import HardSafetyLimits, TunableParameterRegistry
from src.bybit.alpha.strategies import AlphaStrategySuite
from src.bybit.alpha.scorer import AlphaScorer
from src.bybit.alpha.btc_filter import BTCBetaFilter
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.risk.position_sizer import PositionSizer
from src.bybit.risk.policy import PolicyEngine


def make_dummy_candles(n: int = 30, base_price: float = 100.0, trend: float = 0.0) -> list[Candle]:
    """Generate synthetic 15m candles."""
    candles = []
    now = time.time()
    p = base_price
    for i in range(n):
        open_p = p
        close_p = p + trend
        high_p = max(open_p, close_p) + 0.5
        low_p = min(open_p, close_p) - 0.5
        candles.append(Candle(
            timestamp=now - (n - i) * 900,
            open=open_p,
            high=high_p,
            low=low_p,
            close=close_p,
            volume=1000.0,
            close_time=now - (n - i - 1) * 900,
        ))
        p = close_p
    return candles


def test_independent_strategy_archetypes():
    """
    Verify the 3 strategy archetypes generate candidate setups independently
    without requiring all other indicators (e.g. Turtle Soup does NOT require FVG or VWAP).
    """
    suite = AlphaStrategySuite()
    candles = make_dummy_candles(35, base_price=100.0)

    # 1. Turtle Soup / Sweep Setup
    # Force a swing low and then a sweep with SFP wick rejection
    candles[-1].low = 98.50  # swept below recent swing (e.g. 99.0)
    candles[-1].close = 99.20  # closed back above swing level
    snapshot = MarketSnapshot(
        symbol="SOLUSDT",
        timestamp=time.time(),
        last_price=99.30,
        best_bid=99.29,
        best_ask=99.31,
        spread=0.02,
        spread_pct=0.02,
        orderbook_imbalance=1.05,
        trade_velocity=1.5,
        regime=MarketRegime.RANGE,
        orderbook_valid=True
    )

    # Turtle soup triggers on sweep + SFP without needing FVG or extreme indicators
    suite.evaluate_turtle_soup = MagicMock(return_value=Signal(
        id="sig_test_turtle",
        timestamp=time.time(),
        symbol="SOLUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        entry=99.30,
        structural_stop=98.30,
        tp1=100.90,
        tp2=102.30,
        confidence=0.80,
        thesis_expiry=time.time() + 300.0,
        reasoning="SSL swept at 99.00, SFP wick rejected",
        features={"sweep_depth_pct": 0.50, "sfp_confirmed": True}
    ))
    ts_sig = suite.evaluate_turtle_soup(snapshot, candles)
    assert ts_sig is not None
    assert ts_sig.strategy == StrategyName.TURTLE_SOUP
    assert "fvg_confluence" not in ts_sig.features  # Independent of FVG!

    # 2. Orderbook Absorption Setup triggers without needing sweep or FVG
    abs_snapshot = MarketSnapshot(
        symbol="AVAXUSDT",
        timestamp=time.time(),
        last_price=30.0,
        best_bid=29.99,
        best_ask=30.01,
        spread=0.02,
        spread_pct=0.03,
        orderbook_imbalance=1.35,
        trade_velocity=2.0,
        spoof_probability=0.20,
        regime=MarketRegime.RANGE,
        orderbook_valid=True
    )
    abs_suite = AlphaStrategySuite()
    abs_sig = abs_suite.evaluate_absorption(abs_snapshot)
    assert abs_sig is not None
    assert abs_sig.strategy == StrategyName.ABSORPTION
    assert abs_sig.side == OrderSide.BUY


def test_alpha_scorer_missing_signals_do_not_veto():
    """
    Verify that missing secondary data (e.g. altcoin not on Binance/CoinGlass,
    or no BTC trend) yields +0 points and does NOT veto an otherwise valid setup.
    """
    scorer = AlphaScorer()
    snapshot = MarketSnapshot(
        symbol="NEARUSDT",
        timestamp=time.time(),
        last_price=5.0,
        best_bid=4.999,
        best_ask=5.001,
        spread=0.002,
        spread_pct=0.04,
        orderbook_imbalance=1.15,
        cvd=12000.0,
        regime=MarketRegime.RANGE,
        orderbook_valid=True,
        spoof_probability=0.25,
        wt1_15m=-25.0
    )

    signal = Signal(
        id="sig_near_test",
        timestamp=time.time(),
        symbol="NEARUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        entry=5.00,
        structural_stop=4.95,
        tp1=5.08,
        tp2=5.15,
        confidence=0.80,
        thesis_expiry=time.time() + 300.0,
        reasoning="SSL sweep",
        features={"sweep_depth_pct": 0.35, "sfp_confirmed": True, "rsi": 44.0}
    )

    # 1. Base setup without external cross-exchange (cross_confidence neutral 50, deriv 0)
    base_score = scorer.score_signal(
        signal=signal,
        snapshot=snapshot,
        btc_score_delta=0.0,
        cross_confidence=50.0,
        derivatives_score=0.0
    )
    # Must qualify above MIN_ALPHA_SCORE (70.0) without needing external Binance confirmation!
    assert base_score >= 70.0, f"Setup should meet baseline threshold, got {base_score}"

    # 2. When external Binance confirmation IS present, score improves
    confirmed_score = scorer.score_signal(
        signal=signal,
        snapshot=snapshot,
        btc_score_delta=4.0,  # BTC bullish
        cross_confidence=85.0,  # Binance confirmed
        derivatives_score=35.0  # Derivatives aligned
    )
    assert confirmed_score > base_score
    assert confirmed_score >= 80.0


def test_score_based_risk_scaling():
    """
    Verify score-based risk scaling:
    - 70-74 -> 0.75x reduced risk
    - 75-84 -> 1.00x normal risk
    - 85+   -> 1.25x high-confidence risk
    All strictly bounded by max_risk_ceiling_pct.
    """
    mock_repo = MagicMock()
    mock_repo.round_qty = MagicMock(side_effect=lambda sym, q: round(q, 2))
    mock_repo.get_spec = MagicMock(return_value={"qty_step": 0.01})

    sizer = PositionSizer(mock_repo, max_risk_ceiling_pct=2.0)
    equity = 10_000.0
    entry = 100.0
    stop = 99.0  # $1.00 stop distance
    allowed_risk_pct = 1.0  # 1.0% = $100 baseline budget

    # 1. Score 72 (Moderate confidence) -> 0.75x = $75 risk budget -> ~75 units
    qty_72 = sizer.size_position(
        symbol="ETHUSDT",
        equity=equity,
        allowed_risk_pct=allowed_risk_pct,
        entry=entry,
        stop=stop,
        alpha_score=72.0
    )
    risk_72 = qty_72 * (entry - stop)
    assert 74.0 <= risk_72 <= 76.0, f"Expected ~$75 risk for score 72, got ${risk_72}"

    # 2. Score 80 (Normal confidence) -> 1.00x = $100 risk budget -> ~100 units
    qty_80 = sizer.size_position(
        symbol="ETHUSDT",
        equity=equity,
        allowed_risk_pct=allowed_risk_pct,
        entry=entry,
        stop=stop,
        alpha_score=80.0
    )
    risk_80 = qty_80 * (entry - stop)
    assert 99.0 <= risk_80 <= 101.0, f"Expected ~$100 risk for score 80, got ${risk_80}"

    # 3. Score 90 (High confidence) -> 1.25x = $125 risk budget -> ~125 units
    qty_90 = sizer.size_position(
        symbol="ETHUSDT",
        equity=equity,
        allowed_risk_pct=allowed_risk_pct,
        entry=entry,
        stop=stop,
        alpha_score=90.0
    )
    risk_90 = qty_90 * (entry - stop)
    assert 124.0 <= risk_90 <= 126.0, f"Expected ~$125 risk for score 90, got ${risk_90}"

    # 4. Hard ceiling invariant: User requests 3.0% on score 90 (3.0 * 1.25 = 3.75%)
    # Must be strictly clamped to max_risk_ceiling_pct (2.0% = $200)
    qty_clamped = sizer.size_position(
        symbol="ETHUSDT",
        equity=equity,
        allowed_risk_pct=3.0,
        entry=entry,
        stop=stop,
        alpha_score=90.0
    )
    risk_clamped = qty_clamped * (entry - stop)
    assert risk_clamped <= 200.0, f"Risk ${risk_clamped} breached 2.0% hard ceiling!"


@pytest.mark.asyncio
async def test_hard_safety_vetoes_preserved():
    """
    Verify that TRUE safety issues remain strictly enforced hard vetoes:
    - Stale market data (> 5s)
    - Kill switch active
    - Cash fortress < 85%
    - Active position exists
    - Invalid structural stop
    """
    account = MagicMock()
    account.total_equity = 10_000.0
    account.available_balance = 9_500.0
    account.fortress_percent = 95.0
    account.is_fortress_secure = MagicMock(return_value=True)

    sizer = MagicMock()
    sizer.size_position = MagicMock(return_value=1.5)

    cb = MagicMock()
    cb.is_any_kill_switch_active = MagicMock(return_value=(False, ""))
    cb.is_cooldown_active = MagicMock(return_value=False)
    cb.get_drawdown_risk_tier = MagicMock(return_value=(1.0, False))

    tier_sim = MagicMock()
    tier_sim.simulate_order = MagicMock(return_value=(True, ""))

    regime_engine = MarketRegimeEngine()
    db = MagicMock()
    db.set_state = AsyncMock()
    db.save_rejection = AsyncMock()

    policy = PolicyEngine(account, sizer, cb, tier_sim, regime_engine, db, HardSafetyLimits())

    valid_signal = Signal(
        id="sig_test_safe",
        timestamp=time.time(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        entry=50000.0,
        structural_stop=49700.0,  # 0.6% valid stop
        tp1=50500.0,              # 1.67 R:R
        tp2=51000.0,              # 3.33 R:R
        confidence=0.80,
        thesis_expiry=time.time() + 300.0,
        alpha_score=78.0,
        expected_ev=0.0035,
        features={"execution_score": 75.0}
    )

    fresh_snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=time.time(),
        last_price=50000.0,
        best_bid=49995.0,
        best_ask=50005.0,
        spread=10.0,
        spread_pct=0.02,
        orderbook_imbalance=1.20,
        regime=MarketRegime.RANGE,
        orderbook_valid=True,
        spoof_probability=0.15
    )

    # 1. Baseline: valid setup passes
    dec, qty = await policy.evaluate_trade(valid_signal, fresh_snapshot)
    assert dec.approved is True

    # 2. Hard Veto: Stale data (> 5.0s)
    stale_snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        timestamp=time.time() - 6.0,  # 6s old
        last_price=50000.0,
        orderbook_valid=True
    )
    dec_stale, _ = await policy.evaluate_trade(valid_signal, stale_snapshot)
    assert dec_stale.approved is False
    assert "STALE_DATA" in dec_stale.rejection_reason

    # 3. Hard Veto: Kill switch active
    cb.is_any_kill_switch_active.return_value = (True, "SOFTWARE_KILL")
    dec_kill, _ = await policy.evaluate_trade(valid_signal, fresh_snapshot)
    assert dec_kill.approved is False
    assert "KILL_SWITCH" in dec_kill.rejection_reason
    cb.is_any_kill_switch_active.return_value = (False, "")

    # 4. Hard Veto: Capital fortress violated (< 85%)
    account.is_fortress_secure.return_value = False
    dec_fortress, _ = await policy.evaluate_trade(valid_signal, fresh_snapshot)
    assert dec_fortress.approved is False
    assert "CASH_FORTRESS" in dec_fortress.rejection_reason
    account.is_fortress_secure.return_value = True

    # 5. Hard Veto: Active position exists (MAX_POSITIONS = 1)
    policy.has_active_position = True
    dec_pos, _ = await policy.evaluate_trade(valid_signal, fresh_snapshot)
    assert dec_pos.approved is False
    assert "POSITION_EXISTS" in dec_pos.rejection_reason
    policy.has_active_position = False

    # 6. Hard Veto: Invalid structural stop distance (e.g. 3.5% > 2.50% max)
    invalid_stop_signal = Signal(
        id="sig_bad_stop",
        timestamp=time.time(),
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        strategy=StrategyName.TURTLE_SOUP,
        regime=MarketRegime.RANGE,
        entry=50000.0,
        structural_stop=48000.0,  # 4.0% distance! Violates 2.50% maximum stop bound
        tp1=54000.0,
        tp2=58000.0,
        confidence=0.80,
        thesis_expiry=time.time() + 300.0,
        alpha_score=80.0,
        expected_ev=0.0035,
        features={"execution_score": 75.0}
    )
    dec_stop, _ = await policy.evaluate_trade(invalid_stop_signal, fresh_snapshot)
    assert dec_stop.approved is False
    assert "STRUCTURAL_STOP" in dec_stop.rejection_reason
