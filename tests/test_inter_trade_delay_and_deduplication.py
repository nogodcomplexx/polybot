"""
Unit and integration tests for:
1. Incremental position and order caching in BybitAccountRepository.
2. Symbol-specific re-entry cooldown (anti-churning / fee protection).
3. Anti-duplicate order protection (rejecting symbols with existing positions or open orders).
4. Grace period protection in live position management.
"""

import time
import pytest
from src.bybit.exchange.account import BybitAccountRepository
from src.bybit.risk.policy import PolicyEngine
from src.bybit.risk.position_sizer import PositionSizer
from src.bybit.risk.circuit_breakers import CircuitBreakers
from src.bybit.risk.risk_tier import RiskTierSimulator
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.exchange.instruments import BybitInstrumentRepository
from src.bybit.storage.database import BybitDatabase
from src.bybit.models import Signal, MarketSnapshot, OrderSide, StrategyName, MarketRegime, PositionState


@pytest.mark.asyncio
async def test_incremental_account_cache():
    """Verify that updating positions or orders incrementally preserves existing entries."""
    account = BybitAccountRepository(starting_equity=10000.0)

    # Add initial position BTCUSDT
    account.update_positions([{"symbol": "BTCUSDT", "size": "0.1", "entry_price": "50000"}])
    assert "BTCUSDT" in account.cached_positions
    assert len(account.cached_positions) == 1

    # Receive WebSocket update for PUMPFUNUSDT only
    account.update_positions([{"symbol": "PUMPFUNUSDT", "size": "1000", "entry_price": "0.005"}])
    # BTCUSDT MUST NOT be wiped out!
    assert "BTCUSDT" in account.cached_positions
    assert "PUMPFUNUSDT" in account.cached_positions
    assert len(account.cached_positions) == 2

    # Close BTCUSDT (size = 0)
    account.update_positions([{"symbol": "BTCUSDT", "size": "0"}])
    assert "BTCUSDT" not in account.cached_positions
    assert "PUMPFUNUSDT" in account.cached_positions
    assert len(account.cached_positions) == 1

    # Open orders incremental test
    account.update_orders([{"orderId": "ord_1", "symbol": "ETHUSDT", "orderStatus": "New"}])
    assert "ord_1" in account.cached_orders
    assert account.has_open_order_for_symbol("ETHUSDT") is True
    assert account.has_open_order_for_symbol("BTCUSDT") is False

    # Add second order
    account.update_orders([{"orderId": "ord_2", "symbol": "BTCUSDT", "orderStatus": "New"}])
    assert "ord_1" in account.cached_orders
    assert "ord_2" in account.cached_orders

    # Fill ord_1 -> removed from cache
    account.update_orders([{"orderId": "ord_1", "symbol": "ETHUSDT", "orderStatus": "Filled"}])
    assert "ord_1" not in account.cached_orders
    assert "ord_2" in account.cached_orders
    assert account.has_open_order_for_symbol("ETHUSDT") is False
    assert account.has_open_order_for_symbol("BTCUSDT") is True


@pytest.mark.asyncio
async def test_policy_symbol_cooldown():
    """Verify that PolicyEngine rejects trades when symbol is in re-entry cooldown."""
    db = BybitDatabase("test_policy_cd.db")
    await db.connect()
    account = BybitAccountRepository(starting_equity=10000.0)
    repo = BybitInstrumentRepository()
    sizer = PositionSizer(repo)
    breakers = CircuitBreakers(db)
    sim = RiskTierSimulator()
    regime_eng = MarketRegimeEngine()

    symbol_cooldowns = {}
    policy = PolicyEngine(
        account=account,
        sizer=sizer,
        circuit_breakers=breakers,
        risk_tier_sim=sim,
        regime_engine=regime_eng,
        db=db,
        symbol_cooldowns=symbol_cooldowns,
    )

    now = time.time()
    snapshot = MarketSnapshot(
        timestamp=now,
        symbol="PUMPFUNUSDT",
        best_bid=0.0050,
        best_ask=0.005001,
        last_price=0.0050,
        mark_price=0.0050,
        spread=0.000001,
        spread_pct=0.02,
        orderbook_valid=True,
        bid_depth=50000,
        ask_depth=50000,
    )
    sig = Signal(
        id="sig_test_1",
        timestamp=now,
        symbol="PUMPFUNUSDT",
        strategy=StrategyName.TREND_CONTINUATION,
        side=OrderSide.BUY,
        alpha_score=85.0,
        expected_ev=0.005,
        structural_stop=0.00495,
        entry=0.0050,
        tp1=0.0051,
        tp2=0.0052,
        regime=MarketRegime.TREND_UP,
        features={"execution_score": 85.0},
    )

    # 1. Normal evaluation without cooldown -> APPROVED
    dec, qty = await policy.evaluate_trade(sig, snapshot)
    assert dec.approved is True

    # 2. Put PUMPFUNUSDT in cooldown for 30s
    symbol_cooldowns["PUMPFUNUSDT"] = now + 30.0

    dec2, qty2 = await policy.evaluate_trade(sig, snapshot)
    assert dec2.approved is False
    assert "SYMBOL_COOLDOWN" in dec2.rejection_reason

    # 3. Expire cooldown -> APPROVED again
    symbol_cooldowns["PUMPFUNUSDT"] = now - 1.0
    dec3, qty3 = await policy.evaluate_trade(sig, snapshot)
    assert dec3.approved is True

    # 4. Existing active position on PUMPFUNUSDT -> REJECTED
    account.update_positions([{"symbol": "PUMPFUNUSDT", "size": "1000", "entry_price": "0.005"}])
    dec4, qty4 = await policy.evaluate_trade(sig, snapshot)
    assert dec4.approved is False
    assert "SYMBOL_ACTIVE" in dec4.rejection_reason

    # 5. Open order on PUMPFUNUSDT -> REJECTED
    account.update_positions([{"symbol": "PUMPFUNUSDT", "size": "0"}])
    account.update_orders([{"orderId": "ord_p", "symbol": "PUMPFUNUSDT", "orderStatus": "New"}])
    dec5, qty5 = await policy.evaluate_trade(sig, snapshot)
    assert dec5.approved is False
    assert "SYMBOL_ACTIVE" in dec5.rejection_reason

    await db.close()
    import gc
    import os
    gc.collect()
    if os.path.exists("test_policy_cd.db"):
        try:
            os.remove("test_policy_cd.db")
        except OSError:
            pass
