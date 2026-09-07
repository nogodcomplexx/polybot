"""
Comprehensive Multi-Exchange Market Intelligence Test Suite.
Tests Symbol Mapping, Bus Pub/Sub, Connectors, Engines (Price, Orderflow, Liquidity,
Derivatives, Quality), Section 50/51 Unified API, and Failover Resilience.
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.bybit.intelligence.models import (
    CanonicalSymbol, SymbolMapper, default_symbol_mapper,
    EventType, DataQuality, DataPoint, TradeEvent, OrderBookSnapshot,
    LiquidationEvent, OpenInterestEvent, FundingEvent,
    PriceSyncState, OrderflowConfirmation, LiquidityClassification,
    OIRegime, FundingRegime, LiquidationRegime, MarketMoveClassification,
    ProviderStatus, ProviderHealth, CrossExchangeState, DerivativesContext,
    NormalizedMarketState
)
from src.bybit.intelligence.bus import MarketDataBus
from src.bybit.intelligence.engines.price_engine import CrossExchangePriceEngine
from src.bybit.intelligence.engines.orderflow_engine import CrossExchangeOrderflowEngine
from src.bybit.intelligence.engines.liquidity_engine import CrossExchangeLiquidityEngine
from src.bybit.intelligence.engines.derivatives_engine import DerivativesFusionEngine
from src.bybit.intelligence.engines.quality_engine import DataQualityEngine
from src.bybit.intelligence.coordinator import UnifiedMarketIntelligence


# ==============================================================================
# 1. Canonical Symbol Mapping Tests
# ==============================================================================

def test_symbol_mapping_forward_and_reverse():
    mapper = SymbolMapper()
    btc = mapper.get("BTC-USDT")
    assert btc is not None
    assert btc.bybit == "BTCUSDT"
    assert btc.binance == "BTCUSDT"
    assert btc.coinglass == "BTC"

    # Reverse Lookups
    from_bybit = mapper.from_bybit("ETHUSDT")
    assert from_bybit.canonical == "ETH-USDT"

    from_binance = mapper.from_binance("SOLUSDT")
    assert from_binance.canonical == "SOL-USDT"

    from_cg = mapper.from_coinglass("DOGE")
    assert from_cg.canonical == "DOGE-USDT"


def test_symbol_mapping_dynamic_registration():
    mapper = SymbolMapper()
    sui = mapper.get_or_create("SUI-USDT")
    assert sui.canonical == "SUI-USDT"
    assert sui.bybit == "SUIUSDT"
    assert sui.binance == "SUIUSDT"
    assert sui.coinglass == "SUI"

    # Lookup newly registered symbol from venue string
    assert mapper.from_bybit("SUIUSDT").canonical == "SUI-USDT"


# ==============================================================================
# 2. MarketDataBus Pub/Sub Tests
# ==============================================================================

def test_market_data_bus_pubsub_and_filtering():
    bus = MarketDataBus()
    received_btc = []
    received_all = []

    def handle_btc(evt: TradeEvent):
        received_btc.append(evt)

    def handle_all(evt: TradeEvent):
        received_all.append(evt)

    bus.subscribe(EventType.TRADE_UPDATE, handle_btc, symbol="BTC-USDT")
    bus.subscribe(EventType.TRADE_UPDATE, handle_all)

    trade1 = TradeEvent(
        source="bybit",
        canonical_symbol="BTC-USDT",
        price=65000.0,
        quantity=1.5,
        side="Buy",
        exchange_timestamp=time.time(),
        receive_timestamp=time.time(),
    )
    trade2 = TradeEvent(
        source="binance",
        canonical_symbol="ETH-USDT",
        price=3500.0,
        quantity=5.0,
        side="Sell",
        exchange_timestamp=time.time(),
        receive_timestamp=time.time(),
    )

    bus.publish(EventType.TRADE_UPDATE, trade1, symbol="BTC-USDT")
    bus.publish(EventType.TRADE_UPDATE, trade2, symbol="ETH-USDT")

    # Filtered subscriber gets only BTC-USDT
    assert len(received_btc) == 1
    assert received_btc[0].canonical_symbol == "BTC-USDT"
    assert received_btc[0].price == 65000.0

    # Wildcard subscriber gets both
    assert len(received_all) == 2


# ==============================================================================
# 3. Cross-Exchange Price & Basis Engine Tests
# ==============================================================================

def test_price_engine_basis_and_sync():
    engine = CrossExchangePriceEngine()

    # 1. Perfectly synced prices
    res1 = engine.record_prices("BTC-USDT", bybit_price=60000.0, binance_price=60000.0)
    assert res1["basis"] == 0.0
    assert res1["basis_pct"] == 0.0
    assert res1["price_sync"] == PriceSyncState.SYNCED

    # 2. Binance leading upwards (Bybit lagging behind, basis negative)
    res2 = engine.record_prices("BTC-USDT", bybit_price=60000.0, binance_price=60060.0)
    assert res2["basis"] == -60.0
    assert round(res2["basis_pct"], 2) == -0.10
    assert res2["price_sync"] == PriceSyncState.BINANCE_LEADING

    # 3. Bybit leading upwards (Bybit higher than Binance, basis positive)
    res3 = engine.record_prices("BTC-USDT", bybit_price=60060.0, binance_price=60000.0)
    assert res3["basis"] == 60.0
    assert res3["basis_pct"] == 0.10
    assert res3["price_sync"] == PriceSyncState.BYBIT_LEADING

    # 4. Extreme dislocation (> 0.30%)
    res4 = engine.record_prices("BTC-USDT", bybit_price=60300.0, binance_price=60000.0)
    assert res4["basis_pct"] == 0.50
    assert res4["price_sync"] == PriceSyncState.DIVERGING


# ==============================================================================
# 4. Cross-Exchange Orderflow Engine Tests
# ==============================================================================

def test_orderflow_engine_cvd_confirmation():
    engine = CrossExchangeOrderflowEngine()

    # 1. Both venues buying aggressively -> CONFIRMED_BULLISH
    res1 = engine.evaluate_orderflow(
        bybit_cvd=50.0,
        binance_cvd=120.0,
        bybit_velocity=10.0,
        binance_velocity=15.0,
    )
    assert res1["orderflow_confirmation"] == OrderflowConfirmation.CONFIRMED_BULLISH
    assert res1["both_bullish"] is True

    # 2. Both venues selling aggressively -> CONFIRMED_BEARISH
    res2 = engine.evaluate_orderflow(
        bybit_cvd=-40.0,
        binance_cvd=-110.0,
        bybit_velocity=8.0,
        binance_velocity=12.0,
    )
    assert res2["orderflow_confirmation"] == OrderflowConfirmation.CONFIRMED_BEARISH
    assert res2["both_bearish"] is True

    # 3. Opposing flows -> MIXED
    res3 = engine.evaluate_orderflow(
        bybit_cvd=50.0,
        binance_cvd=-80.0,
        bybit_velocity=10.0,
        binance_velocity=10.0,
    )
    assert res3["orderflow_confirmation"] == OrderflowConfirmation.MIXED

    # 4. Bybit local burst (Bybit CVD 4x Binance CVD)
    res4 = engine.evaluate_orderflow(
        bybit_cvd=120.0,
        binance_cvd=20.0,
        bybit_velocity=15.0,
        binance_velocity=4.0,
    )
    assert res4["orderflow_confirmation"] == OrderflowConfirmation.BYBIT_LOCAL


# ==============================================================================
# 5. Cross-Exchange Liquidity Engine & Spoof Confirmation Tests (Section 41)
# ==============================================================================

def test_liquidity_engine_and_spoof_confirmation():
    engine = CrossExchangeLiquidityEngine()

    # 1. Both venues bid skewed -> BROAD_SUPPORT
    res1 = engine.evaluate_liquidity(
        bybit_obi=1.40,
        binance_obi=1.35,
        bybit_bid_depth=100.0,
        bybit_ask_depth=70.0,
        binance_bid_depth=300.0,
        binance_ask_depth=220.0,
        bybit_has_wall=True,
        binance_has_wall=True,
    )
    assert res1["liquidity_classification"] == LiquidityClassification.BROAD_SUPPORT
    assert res1["spoof_confirmation_score"] == 0.90  # Confirmed on both venues

    # 2. Wall only on Bybit -> High spoof probability / low confirmation score
    res2 = engine.evaluate_liquidity(
        bybit_obi=1.40,
        binance_obi=1.00,
        bybit_bid_depth=100.0,
        bybit_ask_depth=70.0,
        binance_bid_depth=250.0,
        binance_ask_depth=250.0,
        bybit_has_wall=True,
        binance_has_wall=False,
    )
    assert res2["spoof_confirmation_score"] == 0.35  # Section 41 spoof alert

    # 3. Opposing book pressure -> LIQUIDITY_CONFLICT
    res3 = engine.evaluate_liquidity(
        bybit_obi=1.30,
        binance_obi=0.70,
        bybit_bid_depth=100.0,
        bybit_ask_depth=70.0,
        binance_bid_depth=150.0,
        binance_ask_depth=220.0,
    )
    assert res3["liquidity_classification"] == LiquidityClassification.LIQUIDITY_CONFLICT


# ==============================================================================
# 6. Derivatives Fusion Engine Tests
# ==============================================================================

def test_derivatives_fusion_score_and_regimes():
    engine = DerivativesFusionEngine()

    # Bullish broad positioning, positive funding, synced prices
    res = engine.evaluate_derivatives(
        bybit_oi=5000.0,
        binance_oi=12000.0,
        coinglass_aggregate_oi=20000.0,
        bybit_funding=0.0001,
        binance_funding=0.0001,
        recent_long_liqs=10000.0,
        recent_short_liqs=80000.0,  # Short liquidations dominated
        price_sync=PriceSyncState.SYNCED,
        orderflow_confirmation=OrderflowConfirmation.CONFIRMED_BULLISH,
        basis_pct=0.02,
    )

    assert res["aggregate_oi"] == 20000.0
    assert res["oi_regime"] == OIRegime.BROAD_POSITION_BUILD
    assert res["funding_regime"] == FundingRegime.NEUTRAL
    assert res["funding_spread"] == 0.0
    assert res["move_classification"] == MarketMoveClassification.GLOBAL_MOVE
    assert res["cross_exchange_confidence"] >= 80.0
    assert res["derivatives_context_score"] > 0.0  # Bullish context


# ==============================================================================
# 7. Data Quality & Critical Venue Staleness Tests (Section 38 & 39)
# ==============================================================================

def test_quality_engine_staleness_veto():
    engine = DataQualityEngine(stale_threshold_sec=3.0)
    now = time.time()

    h_healthy = ProviderHealth(provider_name="bybit", status=ProviderStatus.HEALTHY)
    h_bin_healthy = ProviderHealth(provider_name="binance", status=ProviderStatus.HEALTHY)

    # 1. Fresh data on all venues
    res1 = engine.evaluate_quality(
        symbol="BTC-USDT",
        bybit_last_ts=now - 0.2,
        binance_last_ts=now - 0.3,
        bybit_health=h_healthy,
        binance_health=h_bin_healthy,
    )
    assert res1["data_quality_score"] == 100.0
    assert res1["disable_new_entries"] is False

    # 2. Bybit market data stalled (> 3.0s) -> VETO NEW ENTRIES
    res2 = engine.evaluate_quality(
        symbol="BTC-USDT",
        bybit_last_ts=now - 3.5,
        binance_last_ts=now - 0.2,
        bybit_health=h_healthy,
        binance_health=h_bin_healthy,
    )
    assert res2["disable_new_entries"] is True
    assert "Bybit market data stale" in res2["veto_reason"]
    assert res2["data_quality_score"] < 80.0

    # 3. Binance offline but Bybit fresh -> NO VETO, only reduced score
    h_bin_offline = ProviderHealth(provider_name="binance", status=ProviderStatus.OFFLINE)
    res3 = engine.evaluate_quality(
        symbol="BTC-USDT",
        bybit_last_ts=now - 0.2,
        binance_last_ts=now - 15.0,
        bybit_health=h_healthy,
        binance_health=h_bin_offline,
    )
    assert res3["disable_new_entries"] is False  # Bybit is fine, execution allowed
    assert res3["data_quality_score"] == 70.0    # Penalized for external venue missing


# ==============================================================================
# 8. Unified Coordinator (UnifiedMarketIntelligence) Section 50 & 51 Contract
# ==============================================================================

@pytest.mark.asyncio
async def test_unified_market_intelligence_pipeline():
    bus = MarketDataBus()
    mapper = default_symbol_mapper

    # Mock Connectors
    mock_bybit_conn = MagicMock()
    mock_bybit_conn.connect = AsyncMock()
    mock_bybit_conn.disconnect = AsyncMock()
    mock_bybit_conn.subscribe = AsyncMock()
    mock_bybit_conn.unsubscribe = AsyncMock()
    mock_bybit_conn.get_health.return_value = ProviderHealth(
        provider_name="bybit", connected=True, status=ProviderStatus.HEALTHY
    )

    mock_binance_conn = MagicMock()
    mock_binance_conn.connect = AsyncMock()
    mock_binance_conn.disconnect = AsyncMock()
    mock_binance_conn.subscribe = AsyncMock()
    mock_binance_conn.unsubscribe = AsyncMock()
    mock_binance_conn.cvd = {"BTC-USDT": 150.0}
    mock_binance_conn.get_health.return_value = ProviderHealth(
        provider_name="binance", connected=True, status=ProviderStatus.HEALTHY
    )

    mock_cg_conn = MagicMock()
    mock_cg_conn.connect = AsyncMock()
    mock_cg_conn.disconnect = AsyncMock()
    mock_cg_conn.subscribe = AsyncMock()
    mock_cg_conn.unsubscribe = AsyncMock()
    mock_cg_conn.get_health.return_value = ProviderHealth(
        provider_name="coinglass", connected=True, status=ProviderStatus.DEGRADED
    )

    coord = UnifiedMarketIntelligence(
        bybit_connector=mock_bybit_conn,
        binance_connector=mock_binance_conn,
        coinglass_connector=mock_cg_conn,
        bus=bus,
        symbol_mapper=mapper,
    )

    await coord.start()
    await coord.subscribe_symbols(["BTC-USDT"])

    # Simulate incoming Bybit & Binance events onto bus
    now = time.time()
    b_trade = TradeEvent(
        source="bybit",
        canonical_symbol="BTC-USDT",
        price=60020.0,
        quantity=2.0,
        side="Buy",
        exchange_timestamp=now,
        receive_timestamp=now,
    )
    bin_trade = TradeEvent(
        source="binance",
        canonical_symbol="BTC-USDT",
        price=60000.0,
        quantity=3.0,
        side="Buy",
        exchange_timestamp=now,
        receive_timestamp=now,
    )
    bus.publish(EventType.TRADE_UPDATE, b_trade, symbol="BTC-USDT")
    bus.publish(EventType.TRADE_UPDATE, bin_trade, symbol="BTC-USDT")

    # Simulate orderbooks
    b_book = OrderBookSnapshot(
        source="bybit",
        canonical_symbol="BTC-USDT",
        bids=[(60019.0, 10.0)],
        asks=[(60021.0, 8.0)],
        best_bid=60019.0,
        best_ask=60021.0,
        mid=60020.0,
        spread=2.0,
        spread_pct=0.0033,
        timestamp=now,
        sequence=1001,
        bid_depth=50.0,
        ask_depth=40.0,
        obi=1.25,
        microprice=60020.2,
    )
    bin_book = OrderBookSnapshot(
        source="binance",
        canonical_symbol="BTC-USDT",
        bids=[(59999.0, 20.0)],
        asks=[(60001.0, 18.0)],
        best_bid=59999.0,
        best_ask=60001.0,
        mid=60000.0,
        spread=2.0,
        spread_pct=0.0033,
        timestamp=now,
        sequence=2001,
        bid_depth=120.0,
        ask_depth=110.0,
        obi=1.10,
        microprice=60000.1,
    )
    bus.publish(EventType.ORDERBOOK_UPDATE, b_book, symbol="BTC-USDT")
    bus.publish(EventType.ORDERBOOK_UPDATE, bin_book, symbol="BTC-USDT")

    # Simulate OI and funding
    bus.publish(
        EventType.OI_UPDATE,
        OpenInterestEvent(
            source="bybit",
            canonical_symbol="BTC-USDT",
            open_interest=5000.0,
            notional_usd=300000000.0,
            exchange_timestamp=now,
            receive_timestamp=now,
        ),
        symbol="BTC-USDT",
    )
    bus.publish(
        EventType.OI_UPDATE,
        OpenInterestEvent(
            source="coinglass",
            canonical_symbol="BTC-USDT",
            open_interest=25000.0,
            notional_usd=1500000000.0,
            exchange_timestamp=now,
            receive_timestamp=now,
        ),
        symbol="BTC-USDT",
    )

    # 1. Section 50: Test get_cross_exchange_state
    cross = coord.get_cross_exchange_state("BTC-USDT")
    assert cross.symbol == "BTC-USDT"
    assert cross.bybit_price == 60020.0
    assert cross.binance_price == 60000.0
    assert cross.basis == 20.0
    assert cross.confidence_score > 0.0

    # 2. Section 50: Test get_derivatives_context
    deriv = coord.get_derivatives_context("BTC-USDT")
    assert deriv.symbol == "BTC-USDT"
    assert deriv.aggregate_oi == 25000.0
    assert deriv.oi_regime == OIRegime.LOCAL_POSITION_BUILD or deriv.oi_regime == OIRegime.BROAD_POSITION_BUILD

    # 3. Section 51: Test get_market_state full snapshot
    state: NormalizedMarketState = coord.get_market_state("BTC-USDT")
    assert state.symbol == "BTC-USDT"
    assert state.bybit_price == 60020.0
    assert state.binance_price == 60000.0
    assert state.cross_exchange_basis == 20.0
    assert state.aggregate_oi == 25000.0
    assert state.data_quality_score == 100.0

    # 4. Test is_entry_allowed
    allowed, reason = coord.is_entry_allowed("BTC-USDT")
    assert allowed is True

    # 5. Section 50: Test provider health
    health = coord.get_provider_health()
    assert "bybit" in health
    assert "binance" in health
    assert "coinglass" in health
    assert health["bybit"].status == ProviderStatus.HEALTHY

    await coord.stop()
