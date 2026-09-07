"""
Unified Multi-Exchange Market Intelligence Coordinator (Section 50 & 51).
Integrates Bybit, Binance, and CoinGlass connectors with Cross-Exchange Price,
Orderflow, Liquidity, Derivatives, and Quality engines into a unified API.
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Tuple, Union, Callable

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
from src.bybit.intelligence.connectors.bybit_connector import BybitConnector
from src.bybit.intelligence.connectors.binance_connector import BinanceFuturesConnector
from src.bybit.intelligence.connectors.coinglass_connector import CoinGlassConnector
from src.bybit.intelligence.engines.price_engine import CrossExchangePriceEngine
from src.bybit.intelligence.engines.orderflow_engine import CrossExchangeOrderflowEngine
from src.bybit.intelligence.engines.liquidity_engine import CrossExchangeLiquidityEngine
from src.bybit.intelligence.engines.derivatives_engine import DerivativesFusionEngine
from src.bybit.intelligence.engines.quality_engine import DataQualityEngine

logger = logging.getLogger(__name__)


class UnifiedMarketIntelligence:
    """
    Master coordinator for multi-exchange market intelligence (Section 50).
    Provides normalized, authoritative market state to Alpha, Risk, and Telemetry layers.
    Does NOT execute trades or make trading decisions.
    """

    def __init__(
        self,
        bybit_connector: BybitConnector,
        binance_connector: Optional[BinanceFuturesConnector] = None,
        coinglass_connector: Optional[CoinGlassConnector] = None,
        bus: Optional[MarketDataBus] = None,
        symbol_mapper: Optional[SymbolMapper] = None,
        stale_threshold_sec: float = 3.0,
    ):
        self.mapper = symbol_mapper or default_symbol_mapper
        self.bus = bus or MarketDataBus()
        self.bybit_conn = bybit_connector
        self.binance_conn = binance_connector
        self.coinglass_conn = coinglass_connector

        # Engines
        self.price_engine = CrossExchangePriceEngine()
        self.orderflow_engine = CrossExchangeOrderflowEngine()
        self.liquidity_engine = CrossExchangeLiquidityEngine()
        self.derivatives_engine = DerivativesFusionEngine()
        self.quality_engine = DataQualityEngine(stale_threshold_sec=stale_threshold_sec)

        # Internal state caches per canonical symbol
        self._bybit_prices: Dict[str, float] = {}
        self._bybit_marks: Dict[str, float] = {}
        self._bybit_indices: Dict[str, float] = {}
        self._bybit_spreads: Dict[str, float] = {}
        self._bybit_obis: Dict[str, float] = {}
        self._bybit_cvds: Dict[str, float] = {}
        self._bybit_ois: Dict[str, float] = {}
        self._bybit_fundings: Dict[str, float] = {}
        self._bybit_last_ts: Dict[str, float] = {}
        self._bybit_bid_depths: Dict[str, float] = {}
        self._bybit_ask_depths: Dict[str, float] = {}
        self._bybit_walls: Dict[str, bool] = {}

        self._binance_prices: Dict[str, float] = {}
        self._binance_marks: Dict[str, float] = {}
        self._binance_spreads: Dict[str, float] = {}
        self._binance_obis: Dict[str, float] = {}
        self._binance_cvds: Dict[str, float] = {}
        self._binance_ois: Dict[str, float] = {}
        self._binance_fundings: Dict[str, float] = {}
        self._binance_last_ts: Dict[str, float] = {}
        self._binance_bid_depths: Dict[str, float] = {}
        self._binance_ask_depths: Dict[str, float] = {}
        self._binance_walls: Dict[str, bool] = {}

        self._coinglass_ois: Dict[str, float] = {}
        self._coinglass_fundings: Dict[str, float] = {}
        self._coinglass_liqs: Dict[str, float] = {}

        # Trade velocities and liquidation trackers
        self._recent_liqs_long: Dict[str, float] = {}
        self._recent_liqs_short: Dict[str, float] = {}
        self._bybit_trade_timestamps: Dict[str, List[float]] = {}
        self._binance_trade_timestamps: Dict[str, List[float]] = {}

        self._active_symbols: Dict[str, CanonicalSymbol] = {}
        self._started = False

        # Hook into bus events
        self._setup_bus_handlers()

    def _setup_bus_handlers(self):
        """Register listeners on the MarketDataBus for state caching."""
        self.bus.subscribe(EventType.TRADE_UPDATE, self._on_bus_trade)
        self.bus.subscribe(EventType.ORDERBOOK_UPDATE, self._on_bus_orderbook)
        self.bus.subscribe(EventType.OI_UPDATE, self._on_bus_oi)
        self.bus.subscribe(EventType.FUNDING_UPDATE, self._on_bus_funding)
        self.bus.subscribe(EventType.LIQUIDATION_UPDATE, self._on_bus_liquidation)

    def _canonicalize(self, symbol: Union[str, CanonicalSymbol]) -> str:
        """Resolve any symbol representation into canonical string."""
        if isinstance(symbol, CanonicalSymbol):
            return symbol.canonical
        sym_str = str(symbol).upper()
        if "-" in sym_str:
            return sym_str
        # Try from bybit or binance
        try:
            return self.mapper.from_bybit(sym_str).canonical
        except KeyError:
            try:
                return self.mapper.from_binance(sym_str).canonical
            except KeyError:
                return f"{sym_str.replace('USDT', '')}-USDT"

    def _on_bus_trade(self, evt: TradeEvent):
        """Update price and trade velocity caches from bus trades."""
        canon = evt.canonical_symbol
        now = time.time()
        if evt.source == "bybit":
            self._bybit_prices[canon] = evt.price
            self._bybit_last_ts[canon] = evt.exchange_timestamp or now
            delta = evt.quantity if evt.side == "Buy" else -evt.quantity
            self._bybit_cvds[canon] = self._bybit_cvds.get(canon, 0.0) + delta
            ts_list = self._bybit_trade_timestamps.setdefault(canon, [])
            ts_list.append(now)
            self._bybit_trade_timestamps[canon] = [t for t in ts_list if now - t <= 10.0]
        elif evt.source == "binance":
            self._binance_prices[canon] = evt.price
            self._binance_last_ts[canon] = evt.exchange_timestamp or now
            if self.binance_conn:
                self._binance_cvds[canon] = self.binance_conn.cvd.get(canon, 0.0)
            else:
                delta = evt.quantity if evt.side == "Buy" else -evt.quantity
                self._binance_cvds[canon] = self._binance_cvds.get(canon, 0.0) + delta
            ts_list = self._binance_trade_timestamps.setdefault(canon, [])
            ts_list.append(now)
            self._binance_trade_timestamps[canon] = [t for t in ts_list if now - t <= 10.0]

    def _on_bus_orderbook(self, snap: OrderBookSnapshot):
        """Update orderbook caches."""
        canon = snap.canonical_symbol
        now = time.time()
        if snap.source == "bybit":
            self._bybit_last_ts[canon] = snap.timestamp or now
            self._bybit_spreads[canon] = snap.spread
            self._bybit_obis[canon] = snap.obi
            self._bybit_bid_depths[canon] = snap.bid_depth
            self._bybit_ask_depths[canon] = snap.ask_depth
            mean_bid = snap.bid_depth / 50.0 if snap.bid_depth > 0 else 1.0
            has_wall = any(qty > mean_bid * 3.5 for _, qty in snap.bids[:5])
            self._bybit_walls[canon] = has_wall
        elif snap.source == "binance":
            self._binance_last_ts[canon] = snap.timestamp or now
            self._binance_spreads[canon] = snap.spread
            self._binance_obis[canon] = snap.obi
            self._binance_bid_depths[canon] = snap.bid_depth
            self._binance_ask_depths[canon] = snap.ask_depth
            mean_bid = snap.bid_depth / 20.0 if snap.bid_depth > 0 else 1.0
            has_wall = any(qty > mean_bid * 3.5 for _, qty in snap.bids[:5])
            self._binance_walls[canon] = has_wall

    def _on_bus_oi(self, evt: OpenInterestEvent):
        """Update Open Interest caches."""
        canon = evt.canonical_symbol
        if evt.source == "bybit":
            self._bybit_ois[canon] = evt.open_interest
        elif evt.source == "binance":
            self._binance_ois[canon] = evt.open_interest
        elif evt.source == "coinglass":
            self._coinglass_ois[canon] = evt.open_interest

    def _on_bus_funding(self, evt: FundingEvent):
        """Update Funding Rate caches."""
        canon = evt.canonical_symbol
        if evt.source == "bybit":
            self._bybit_fundings[canon] = evt.funding_rate
        elif evt.source == "binance":
            self._binance_fundings[canon] = evt.funding_rate
        elif evt.source == "coinglass":
            self._coinglass_fundings[canon] = evt.funding_rate

    def _on_bus_liquidation(self, evt: LiquidationEvent):
        """Track recent liquidation notional."""
        canon = evt.canonical_symbol
        if evt.side == "Buy":
            self._recent_liqs_short[canon] = self._recent_liqs_short.get(canon, 0.0) + evt.notional
        else:
            self._recent_liqs_long[canon] = self._recent_liqs_long.get(canon, 0.0) + evt.notional

    # ----------------------------------------------------------------------
    # Lifecycle Management
    # ----------------------------------------------------------------------

    async def start(self):
        """Start all connectors concurrently."""
        if self._started:
            return
        self._started = True
        tasks = [self.bybit_conn.connect()]
        if self.binance_conn:
            tasks.append(self.binance_conn.connect())
        if self.coinglass_conn:
            tasks.append(self.coinglass_conn.connect())

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, res in enumerate(results):
            if isinstance(res, Exception):
                logger.error(f"Error starting connector {idx}: {res}")

        logger.info("✅ [UNIFIED INTELLIGENCE] Multi-exchange coordinator started")

    async def stop(self):
        """Stop all connectors cleanly."""
        self._started = False
        tasks = [self.bybit_conn.disconnect()]
        if self.binance_conn:
            tasks.append(self.binance_conn.disconnect())
        if self.coinglass_conn:
            tasks.append(self.coinglass_conn.disconnect())

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[UNIFIED INTELLIGENCE] Multi-exchange coordinator stopped")

    async def subscribe_symbols(self, symbols: List[Union[str, CanonicalSymbol]]):
        """Subscribe to a list of symbols across all configured providers."""
        canonical_objs: List[CanonicalSymbol] = []
        for s in symbols:
            if isinstance(s, CanonicalSymbol):
                canonical_objs.append(s)
                self._active_symbols[s.canonical] = s
            else:
                canon_str = self._canonicalize(s)
                obj = self.mapper.get_or_create(canon_str)
                canonical_objs.append(obj)
                self._active_symbols[canon_str] = obj

        tasks = [self.bybit_conn.subscribe(canonical_objs)]
        if self.binance_conn:
            tasks.append(self.binance_conn.subscribe(canonical_objs))
        if self.coinglass_conn:
            tasks.append(self.coinglass_conn.subscribe(canonical_objs))

        await asyncio.gather(*tasks, return_exceptions=True)

    async def unsubscribe_symbols(self, symbols: List[Union[str, CanonicalSymbol]]):
        """Unsubscribe symbols across all providers and clean up caches."""
        canonical_objs: List[CanonicalSymbol] = []
        canons_to_clean: List[str] = []
        for s in symbols:
            canon_str = self._canonicalize(s)
            obj = self._active_symbols.pop(canon_str, None)
            if obj:
                canonical_objs.append(obj)
                canons_to_clean.append(canon_str)

        if not canonical_objs:
            return

        tasks = [self.bybit_conn.unsubscribe(canonical_objs)]
        if self.binance_conn:
            tasks.append(self.binance_conn.unsubscribe(canonical_objs))
        if self.coinglass_conn:
            tasks.append(self.coinglass_conn.unsubscribe(canonical_objs))

        await asyncio.gather(*tasks, return_exceptions=True)

        # Clean up all internal caches to prevent memory leaks
        for canon in canons_to_clean:
            for cache in (
                self._bybit_prices, self._bybit_marks, self._bybit_indices,
                self._bybit_spreads, self._bybit_obis, self._bybit_cvds,
                self._bybit_ois, self._bybit_fundings, self._bybit_last_ts,
                self._bybit_bid_depths, self._bybit_ask_depths, self._bybit_walls,
                self._binance_prices, self._binance_marks, self._binance_spreads,
                self._binance_obis, self._binance_cvds, self._binance_ois,
                self._binance_fundings, self._binance_last_ts,
                self._binance_bid_depths, self._binance_ask_depths, self._binance_walls,
                self._coinglass_ois, self._coinglass_fundings, self._coinglass_liqs,
                self._recent_liqs_long, self._recent_liqs_short,
                self._bybit_trade_timestamps, self._binance_trade_timestamps,
            ):
                cache.pop(canon, None)
            # Clean engine histories
            self.price_engine.basis_history.pop(canon, None) if hasattr(self.price_engine, 'basis_history') else None

    # ----------------------------------------------------------------------
    # Section 50 Public Query API
    # ----------------------------------------------------------------------

    def subscribe(self, event_type: EventType, callback: Callable[[Any], Any]):
        """Subscribe callback to a bus event type."""
        self.bus.subscribe(event_type, callback)

    def get_provider_health(self) -> Dict[str, ProviderHealth]:
        """Return connectivity and health status across all venues."""
        health: Dict[str, ProviderHealth] = {
            "bybit": self.bybit_conn.get_health(),
        }
        if self.binance_conn:
            health["binance"] = self.binance_conn.get_health()
        else:
            health["binance"] = ProviderHealth(provider_name="binance", status=ProviderStatus.OFFLINE)

        if self.coinglass_conn:
            health["coinglass"] = self.coinglass_conn.get_health()
        else:
            health["coinglass"] = ProviderHealth(provider_name="coinglass", status=ProviderStatus.OFFLINE)

        return health

    def get_active_universe(self) -> List[CanonicalSymbol]:
        """Return list of actively monitored canonical symbols."""
        return list(self._active_symbols.values())

    def get_cross_exchange_state(self, symbol: Union[str, CanonicalSymbol]) -> CrossExchangeState:
        """
        Evaluate real-time Bybit vs Binance basis, CVD confirmation, and liquidity classification.
        """
        canon = self._canonicalize(symbol)
        now = time.time()

        bybit_p = self._bybit_prices.get(canon, 0.0)
        binance_p = self._binance_prices.get(canon, 0.0)

        # 1. Price Engine
        price_eval = self.price_engine.record_prices(canon, bybit_p, binance_p)

        # 2. Orderflow Engine
        bybit_cvd = self._bybit_cvds.get(canon, 0.0)
        binance_cvd = self._binance_cvds.get(canon, 0.0)
        bybit_vel = float(len(self._bybit_trade_timestamps.get(canon, [])))
        binance_vel = float(len(self._binance_trade_timestamps.get(canon, [])))
        flow_eval = self.orderflow_engine.evaluate_orderflow(bybit_cvd, binance_cvd, bybit_vel, binance_vel)

        # 3. Liquidity Engine
        bybit_obi = self._bybit_obis.get(canon, 1.0)
        binance_obi = self._binance_obis.get(canon, 1.0)
        bybit_b_dep = self._bybit_bid_depths.get(canon, 0.0)
        bybit_a_dep = self._bybit_ask_depths.get(canon, 0.0)
        binance_b_dep = self._binance_bid_depths.get(canon, 0.0)
        binance_a_dep = self._binance_ask_depths.get(canon, 0.0)
        bybit_wall = self._bybit_walls.get(canon, False)
        binance_wall = self._binance_walls.get(canon, False)

        liq_eval = self.liquidity_engine.evaluate_liquidity(
            bybit_obi=bybit_obi,
            binance_obi=binance_obi,
            bybit_bid_depth=bybit_b_dep,
            bybit_ask_depth=bybit_a_dep,
            binance_bid_depth=binance_b_dep,
            binance_ask_depth=binance_a_dep,
            bybit_has_wall=bybit_wall,
            binance_has_wall=binance_wall,
        )

        # Confidence computation
        sync_state = price_eval["price_sync"]
        conf = 50.0
        if sync_state == PriceSyncState.SYNCED:
            conf += 20.0
        elif sync_state in (PriceSyncState.DIVERGING, PriceSyncState.UNSTABLE):
            conf -= 30.0

        if flow_eval["orderflow_confirmation"] in (
            OrderflowConfirmation.CONFIRMED_BULLISH,
            OrderflowConfirmation.CONFIRMED_BEARISH,
        ):
            conf += 20.0
        elif flow_eval["orderflow_confirmation"] == OrderflowConfirmation.MIXED:
            conf -= 20.0

        conf = max(0.0, min(100.0, conf))

        return CrossExchangeState(
            symbol=canon,
            bybit_price=bybit_p,
            binance_price=binance_p,
            basis=price_eval["basis"],
            basis_pct=price_eval["basis_pct"],
            basis_velocity=price_eval["basis_velocity"],
            price_sync=sync_state,
            bybit_cvd=bybit_cvd,
            binance_cvd=binance_cvd,
            orderflow_confirmation=flow_eval["orderflow_confirmation"],
            bybit_obi=bybit_obi,
            binance_obi=binance_obi,
            liquidity_classification=liq_eval["liquidity_classification"],
            confidence_score=conf,
            timestamp=now,
        )

    def get_derivatives_context(self, symbol: Union[str, CanonicalSymbol]) -> DerivativesContext:
        """
        Evaluate aggregated derivatives positioning, funding spread, and market move type.
        """
        canon = self._canonicalize(symbol)
        now = time.time()

        bybit_oi = self._bybit_ois.get(canon, 0.0)
        binance_oi = self._binance_ois.get(canon, 0.0)
        cg_oi = self._coinglass_ois.get(canon, 0.0)

        bybit_f = self._bybit_fundings.get(canon, 0.0)
        binance_f = self._binance_fundings.get(canon, 0.0)

        long_liqs = self._recent_liqs_long.get(canon, 0.0)
        short_liqs = self._recent_liqs_short.get(canon, 0.0)

        cross_state = self.get_cross_exchange_state(canon)

        deriv_eval = self.derivatives_engine.evaluate_derivatives(
            bybit_oi=bybit_oi,
            binance_oi=binance_oi,
            coinglass_aggregate_oi=cg_oi,
            bybit_funding=bybit_f,
            binance_funding=binance_f,
            recent_long_liqs=long_liqs,
            recent_short_liqs=short_liqs,
            price_sync=cross_state.price_sync,
            orderflow_confirmation=cross_state.orderflow_confirmation,
            basis_pct=cross_state.basis_pct,
        )

        return DerivativesContext(
            symbol=canon,
            bybit_oi=bybit_oi,
            binance_oi=binance_oi,
            aggregate_oi=deriv_eval["aggregate_oi"],
            oi_regime=deriv_eval["oi_regime"],
            bybit_funding=bybit_f,
            binance_funding=binance_f,
            funding_spread=deriv_eval["funding_spread"],
            funding_regime=deriv_eval["funding_regime"],
            liquidation_intensity=deriv_eval["liquidation_intensity"],
            liquidation_regime=deriv_eval["liquidation_regime"],
            move_classification=deriv_eval["move_classification"],
            derivatives_context_score=deriv_eval["derivatives_context_score"],
            timestamp=now,
        )

    def get_market_state(self, symbol: Union[str, CanonicalSymbol]) -> NormalizedMarketState:
        """
        Produce Section 51 Unified NormalizedMarketState immutable snapshot.
        Consumed by Alpha, Risk, Execution, and Telemetry layers.
        """
        canon = self._canonicalize(symbol)
        now = time.time()

        # Bybit core venue metrics
        bybit_p = self._bybit_prices.get(canon, 0.0)
        bybit_mark = self._bybit_marks.get(canon, bybit_p)
        bybit_idx = self._bybit_indices.get(canon, bybit_p)
        bybit_spd = self._bybit_spreads.get(canon, 0.0)
        bybit_obi = self._bybit_obis.get(canon, 1.0)
        bybit_cvd = self._bybit_cvds.get(canon, 0.0)
        bybit_oi = self._bybit_ois.get(canon, 0.0)
        bybit_f = self._bybit_fundings.get(canon, 0.0)

        # Cross-Exchange & Derivatives
        cross = self.get_cross_exchange_state(canon)
        deriv = self.get_derivatives_context(canon)

        # Health & Quality
        health_bybit = self.bybit_conn.get_health()
        health_binance = self.binance_conn.get_health() if self.binance_conn else ProviderHealth("binance")

        bybit_last_ts = self._bybit_last_ts.get(canon, 0.0)
        binance_last_ts = self._binance_last_ts.get(canon, 0.0)

        qual_eval = self.quality_engine.evaluate_quality(
            symbol=canon,
            bybit_last_ts=bybit_last_ts,
            binance_last_ts=binance_last_ts,
            bybit_health=health_bybit,
            binance_health=health_binance,
        )

        return NormalizedMarketState(
            symbol=canon,
            timestamp=now,
            bybit_price=bybit_p,
            bybit_mark=bybit_mark,
            bybit_index=bybit_idx,
            bybit_spread=bybit_spd,
            bybit_obi=bybit_obi,
            bybit_cvd=bybit_cvd,
            bybit_oi=bybit_oi,
            bybit_funding=bybit_f,
            binance_price=cross.binance_price if cross.binance_price > 0 else None,
            binance_spread=self._binance_spreads.get(canon),
            binance_obi=cross.binance_obi if cross.binance_price > 0 else None,
            binance_cvd=cross.binance_cvd if cross.binance_price > 0 else None,
            binance_oi=deriv.binance_oi if deriv.binance_oi > 0 else None,
            binance_funding=deriv.binance_funding if cross.binance_price > 0 else None,
            aggregate_oi=deriv.aggregate_oi,
            aggregate_funding=bybit_f,
            aggregate_liquidations=self._recent_liqs_long.get(canon, 0.0) + self._recent_liqs_short.get(canon, 0.0),
            cross_exchange_basis=cross.basis,
            cross_exchange_confidence=cross.confidence_score,
            derivatives_context_score=deriv.derivatives_context_score,
            volatility_score=50.0,
            liquidity_score=max(0.0, min(100.0, (bybit_obi / 2.0) * 100.0)),
            data_quality_score=qual_eval["data_quality_score"],
            regime=deriv.move_classification.value,
        )

    def is_entry_allowed(self, symbol: Union[str, CanonicalSymbol]) -> Tuple[bool, str]:
        """
        Check if market data quality allows new entries (Section 38 & 39).
        If Bybit data is stale or provider is offline, trading is vetoed.
        External provider issues (Binance/CoinGlass) DO NOT veto trading, only lower confidence.
        """
        canon = self._canonicalize(symbol)
        bybit_last_ts = self._bybit_last_ts.get(canon, 0.0)
        binance_last_ts = self._binance_last_ts.get(canon, 0.0)
        health_bybit = self.bybit_conn.get_health()
        health_binance = self.binance_conn.get_health() if self.binance_conn else ProviderHealth("binance")

        qual = self.quality_engine.evaluate_quality(
            symbol=canon,
            bybit_last_ts=bybit_last_ts,
            binance_last_ts=binance_last_ts,
            bybit_health=health_bybit,
            binance_health=health_binance,
        )

        if qual["disable_new_entries"]:
            return False, qual["veto_reason"]

        return True, "Data quality approved"
