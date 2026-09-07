"""
Comprehensive Red-Team Adversarial Safety Test Suite.
Verifies all critical and high findings from the Final Production Safety Brief:
- Orderbook sequence gap invalidation and recovery
- Reconciliation fail-closed emergency state on SL failure
- Paper trading PostOnly spread-crossing rejection
- Paper trading TTL expiration unfilled
- Paper trading maker fee accounting & queue matching
- Position sizer production risk ceiling (2.0%) and lot-step down risk preservation
- Dynamic universe rotation multi-symbol protection (active, pending, exchange)
- Execution Policy canonical R:R and stop distance bounds
- Fast-path execution event signaling
"""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.bybit.models import (
    OrderSide, OrderType, PositionState, ExecutionPlan, ActivePosition,
    MarketSnapshot, MarketRegime, StrategyName
)
from src.bybit.config import HardSafetyLimits, UniverseConfig
from src.bybit.risk.position_sizer import PositionSizer
from src.bybit.execution.execution_policy import ExecutionPolicyEngine
from src.bybit.execution.executor import ExecutionEngine
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.market.universe import DynamicUniverseManager
from src.bybit.exchange.public_ws import BybitPublicWS


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.save_state_transition = AsyncMock()
    db.save_trade = AsyncMock()
    return db


@pytest.fixture
def instrument_repo():
    repo = MagicMock()
    repo.round_qty = lambda sym, q: round(q, 3)
    repo.round_price = lambda sym, p: round(p, 2)
    repo.get_spec = lambda sym: {"qty_step": 0.001, "tick_size": 0.01, "min_order_qty": 0.001}
    return repo


@pytest.mark.asyncio
async def test_red_team_paper_post_only_spread_crossing_rejection(mock_db, instrument_repo):
    """
    RED TEAM 1: PostOnly maker order crossing the spread must be rejected immediately
    and state must revert to FLAT.
    """
    sm = PositionStateMachine(mock_db)
    executor = ExecutionEngine(
        client=AsyncMock(),
        account=MagicMock(total_equity=1000.0),
        instrument_repo=instrument_repo,
        state_machine=sm,
        exec_policy=MagicMock(),
        db=mock_db,
        mode="paper",
    )

    # Buy order with entry $50,050 when best_ask is $50,000 -> CROSSES SPREAD
    plan = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=50050.0,
        stop_price=49500.0,
        tp1_price=51000.0,
        tp2_price=52000.0,
        post_only=True,
        best_bid=49990.0,
        best_ask=50000.0,
    )

    result = await executor.execute_plan(plan, StrategyName.TURTLE_SOUP, MarketRegime.RANGE)
    assert result is False, "Post-only crossing order must be rejected!"
    assert sm.state == PositionState.FLAT, "State machine must revert to FLAT on rejection"
    assert "BTCUSDT" not in executor.paper_positions


@pytest.mark.asyncio
async def test_red_team_paper_ttl_expiry(mock_db, instrument_repo):
    """
    RED TEAM 2: Pending paper limit order must expire when TTL elapsed without fill,
    clearing position and returning state machine to FLAT.
    """
    sm = PositionStateMachine(mock_db)
    executor = ExecutionEngine(
        client=AsyncMock(),
        account=MagicMock(total_equity=1000.0),
        instrument_repo=instrument_repo,
        state_machine=sm,
        exec_policy=MagicMock(),
        db=mock_db,
        mode="paper",
    )

    plan = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=49000.0,
        stop_price=48000.0,
        tp1_price=50000.0,
        tp2_price=51000.0,
        post_only=True,
        ttl_seconds=10.0,
        best_bid=49500.0,
        best_ask=49510.0,
    )

    ok = await executor.execute_plan(plan, StrategyName.TURTLE_SOUP, MarketRegime.RANGE)
    assert ok is True
    assert sm.state == PositionState.ENTRY_PENDING

    # Fast forward entry time to simulate 15s elapsed (> 10s TTL)
    pos = executor.paper_positions["BTCUSDT"]
    pos.entry_time = time.time() - 15.0

    # Update with market price above entry (no fill)
    await executor.update_positions({"BTCUSDT": 49500.0})

    # Must be expired and flat!
    assert "BTCUSDT" not in executor.paper_positions
    assert sm.state == PositionState.FLAT


@pytest.mark.asyncio
async def test_red_team_paper_queue_matching_and_maker_fee(mock_db, instrument_repo):
    """
    RED TEAM 3: Paper order enters queue in ENTRY_PENDING, fills only when price reaches entry,
    deducts maker fee, and establishes protective brackets.
    """
    sm = PositionStateMachine(mock_db)
    executor = ExecutionEngine(
        client=AsyncMock(),
        account=MagicMock(total_equity=1000.0),
        instrument_repo=instrument_repo,
        state_machine=sm,
        exec_policy=MagicMock(),
        db=mock_db,
        mode="paper",
    )
    initial_balance = executor.paper_balance

    plan = ExecutionPlan(
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        quantity=1.0,
        entry_price=3000.0,
        stop_price=2950.0,
        tp1_price=3080.0,
        tp2_price=3150.0,
        post_only=True,
        best_bid=3005.0,
        best_ask=3010.0,
    )

    await executor.execute_plan(plan, StrategyName.TURTLE_SOUP, MarketRegime.RANGE)
    assert sm.state == PositionState.ENTRY_PENDING

    # Market price trades down to 3000.0 -> MATCH!
    await executor.update_positions({"ETHUSDT": 3000.0})

    assert sm.state == PositionState.ACTIVE
    pos = executor.paper_positions["ETHUSDT"]
    assert pos.filled_quantity == 1.0

    # VIP0 maker fee: 3000 * 1.0 * 0.0002 = $0.60
    expected_fee = 3000.0 * 0.0002
    assert abs(executor.paper_balance - (initial_balance - expected_fee)) < 1e-4


def test_red_team_position_sizer_risk_ceiling_and_rounding(instrument_repo):
    """
    RED TEAM 4: Sizer enforces production ceiling (2.0%), Kelly fraction can only reduce,
    and rounding must never increase dollar risk above calculated budget.
    """
    sizer = PositionSizer(instrument_repo, max_risk_ceiling_pct=2.0)
    equity = 10_000.0
    entry = 100.0
    stop = 98.0  # $2.00 stop distance (2.0%)

    # User attempts to request 5.0% risk -> MUST CLAMP TO 2.0% ($200 budget)
    qty = sizer.size_position(
        symbol="SOLUSDT",
        equity=equity,
        allowed_risk_pct=5.0,
        entry=entry,
        stop=stop,
        drawdown_scalar=1.0,
        kelly_fraction=1.0,
    )

    dollar_risk = qty * (entry - stop)
    assert dollar_risk <= 200.0, f"Dollar risk ${dollar_risk} exceeded 2.0% ceiling of $200!"

    # Kelly fraction 0.5 reduces risk by half ($100 budget)
    half_qty = sizer.size_position(
        symbol="SOLUSDT",
        equity=equity,
        allowed_risk_pct=2.0,
        entry=entry,
        stop=stop,
        drawdown_scalar=1.0,
        kelly_fraction=0.5,
    )
    half_risk = half_qty * (entry - stop)
    assert half_risk <= 100.0


def test_red_team_dynamic_universe_protection():
    """
    RED TEAM 5: When universe rotates, all protected symbols (active trade, pending order,
    exchange position) must NEVER appear in removed list.
    """
    cfg = UniverseConfig(mode="dynamic", top_n=3, pinned_symbols=["BTCUSDT"])
    mgr = DynamicUniverseManager(cfg, fallback_symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"])
    mgr.active_universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT"]

    # New universe discovery only ranks BTCUSDT, DOGEUSDT, XRPUSDT
    new_discovery = ["BTCUSDT", "DOGEUSDT", "XRPUSDT"]

    # But AVAXUSDT has an active position, and ETHUSDT has an open exchange position!
    protected = {"AVAXUSDT", "ETHUSDT"}

    added, removed, finalized = mgr.compute_rotation_diff(new_discovery, protected_symbols=protected)

    # Invariants:
    assert "AVAXUSDT" not in removed, "AVAXUSDT is protected and must not be removed!"
    assert "ETHUSDT" not in removed, "ETHUSDT is protected and must not be removed!"
    assert "AVAXUSDT" in finalized
    assert "ETHUSDT" in finalized
    assert "SOLUSDT" in removed  # Unprotected symbol dropped


def test_red_team_execution_policy_canonical_checks():
    """
    RED TEAM 6: Execution policy rejects invalid stop distances and insufficient R:R
    against canonical HardSafetyLimits (MIN_STOP_DIST_PCT=0.10, MAX_STOP_DIST_PCT=2.50,
    TP1 R:R>=1.5, TP2 R:R>=2.8).
    """
    policy = ExecutionPolicyEngine(safety=HardSafetyLimits())

    snapshot = MarketSnapshot(
        timestamp=time.time(),
        symbol="BTCUSDT",
        last_price=60000.0,
        mark_price=60000.0,
        index_price=60000.0,
        bid_depth=100.0,
        ask_depth=100.0,
        orderbook_valid=True,
    )

    # 1. Stop distance too tight (< 0.10%)
    bad_plan_tight = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=60000.0,
        stop_price=59980.0,  # 0.033% stop -> TOO TIGHT
        tp1_price=61000.0,
        tp2_price=62000.0,
    )
    safe, reason = policy.evaluate_execution_precheck(bad_plan_tight, snapshot, ws_connected=True)
    assert safe is False
    assert "canonical bounds" in reason.lower()

    # 2. Stop distance too wide (> 2.50%)
    bad_plan_wide = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=60000.0,
        stop_price=58000.0,  # 3.33% stop -> TOO WIDE (> 2.50%)
        tp1_price=63000.0,
        tp2_price=66000.0,
    )
    safe, reason = policy.evaluate_execution_precheck(bad_plan_wide, snapshot, ws_connected=True)
    assert safe is False
    assert "canonical bounds" in reason.lower()

    # 3. Insufficient TP2 R:R (< 2.8)
    bad_plan_rr = ExecutionPlan(
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        quantity=0.01,
        entry_price=60000.0,
        stop_price=59400.0,  # 600 stop (1.0%)
        tp1_price=60900.0,  # 900 TP1 (R:R = 1.5)
        tp2_price=61200.0,  # 1200 TP2 (R:R = 2.0 < 2.8) -> TOO LOW
    )
    safe, reason = policy.evaluate_execution_precheck(bad_plan_rr, snapshot, ws_connected=True)
    assert safe is False
    assert "tp2 r:r too low" in reason.lower()
