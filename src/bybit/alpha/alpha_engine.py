"""
Master Alpha Engine (Brain 1).
Orchestrates continuous symbol scanning, strategy evaluation, alpha scoring,
net EV estimation, and opportunity ranking.
"""

from typing import Dict, Any, List, Optional
import time

from src.bybit.models import Signal, MarketSnapshot, MarketRegime, StrategyName
from src.bybit.market.candles import MultiTimeframeCandleManager
from src.bybit.market.market_state import MarketStateEngine
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.alpha.strategies import AlphaStrategySuite
from src.bybit.alpha.scorer import AlphaScorer
from src.bybit.alpha.btc_filter import BTCBetaFilter


class AlphaEngine:
    """Detects, scores, and ranks intraday perpetual scalp opportunities."""

    def __init__(
        self,
        symbols: List[str],
        market_state: MarketStateEngine,
        candle_mgr: MultiTimeframeCandleManager,
        regime_engine: MarketRegimeEngine,
        btc_filter: BTCBetaFilter,
        intelligence: Optional[Any] = None,
    ):
        self.symbols = [s.upper() for s in symbols]
        self.market_state = market_state
        self.candle_mgr = candle_mgr
        self.regime_engine = regime_engine
        self.btc_filter = btc_filter
        self.intelligence = intelligence

        self.strategy_suite = AlphaStrategySuite()
        self.scorer = AlphaScorer()

    def update_symbols(self, symbols: List[str]):
        """Dynamically update active symbol list."""
        self.symbols = [s.upper() for s in symbols]

    def scan_all_symbols(self) -> List[Signal]:
        """
        Actively scans all configured symbols across all strategies,
        scores candidates, filters against minimum thresholds, and returns
        ranked opportunities.
        """
        candidates: List[Signal] = []

        # 1. Warm up BTC state for macro veto
        btc_c15m = self.candle_mgr.get_candles("BTCUSDT", "15m")
        btc_c4h = self.candle_mgr.get_candles("BTCUSDT", "4h")
        btc_snap = self.market_state.get_snapshot("BTCUSDT")
        btc_vwap = btc_snap.vwap_15m

        for sym in self.symbols:
            c15m = self.candle_mgr.get_candles(sym, "15m")
            c5m = self.candle_mgr.get_candles(sym, "5m")
            c1m = self.candle_mgr.get_candles(sym, "1m")

            if len(c15m) < 25:
                continue

            # Classify regime
            atr_15m = self.market_state.get_snapshot(sym).atr_15m
            spread_pct = self.market_state.get_snapshot(sym).spread_pct
            obi = self.market_state.get_snapshot(sym).orderbook_imbalance
            regime = self.regime_engine.classify(c15m, atr_15m, spread_pct, obi)

            # Staleness & Data Quality Veto Check (Section 38)
            if self.intelligence:
                allowed, veto_reason = self.intelligence.is_entry_allowed(sym)
                if not allowed:
                    continue

            # Generate Snapshot
            snapshot = self.market_state.get_snapshot(sym, regime=regime)
            if not snapshot.orderbook_valid:
                continue

            # Evaluate Strategies
            raw_signals: List[Signal] = []

            if self.regime_engine.is_strategy_allowed(StrategyName.TURTLE_SOUP, regime):
                s1 = self.strategy_suite.evaluate_turtle_soup(snapshot, c15m)
                if s1: raw_signals.append(s1)

            s2 = self.strategy_suite.evaluate_vwap_fvg_reversion(snapshot, c15m)
            if s2 and self.regime_engine.is_strategy_allowed(s2.strategy, regime):
                raw_signals.append(s2)

            s3 = self.strategy_suite.evaluate_absorption(snapshot)
            if s3 and self.regime_engine.is_strategy_allowed(s3.strategy, regime):
                raw_signals.append(s3)

            s4 = self.strategy_suite.evaluate_trend_continuation(snapshot, c15m)
            if s4 and self.regime_engine.is_strategy_allowed(s4.strategy, regime):
                raw_signals.append(s4)

            # Score and filter candidate signals
            for sig in raw_signals:
                # BTC Macro Beta Context for altcoins
                btc_score_delta = 0.0
                if sym != "BTCUSDT":
                    btc_res = self.btc_filter.evaluate_alt_trade(
                        sig.side, btc_c15m, btc_c4h, btc_vwap
                    )
                    if len(btc_res) == 4:
                        btc_pass, btc_reason, btc_state, btc_score_delta = btc_res
                    else:
                        btc_pass, btc_reason, btc_state = btc_res
                    snapshot.btc_state = btc_state
                    if not btc_pass:
                        continue
                else:
                    snapshot.btc_state = "SELF"

                # Cross-Exchange Intelligence Enrichment
                cross_conf = 50.0
                deriv_score = 0.0
                if self.intelligence:
                    try:
                        cross = self.intelligence.get_cross_exchange_state(sym)
                        deriv = self.intelligence.get_derivatives_context(sym)
                        cross_conf = cross.confidence_score
                        deriv_score = deriv.derivatives_context_score
                        sig.features["cross_exchange_confidence"] = cross_conf
                        sig.features["derivatives_context_score"] = deriv_score
                        sig.features["cross_exchange_basis"] = cross.basis
                        sig.features["orderflow_confirmation"] = cross.orderflow_confirmation.value
                        sig.features["price_sync"] = cross.price_sync.value
                    except Exception:
                        pass

                # Score with modular evidence engine
                sig.alpha_score = self.scorer.score_signal(
                    sig,
                    snapshot,
                    btc_score_delta=btc_score_delta,
                    cross_confidence=cross_conf,
                    derivatives_score=deriv_score,
                )
                sig.expected_ev = self.scorer.calculate_ev(sig, snapshot)
                sig.features["execution_score"] = self.scorer.evaluate_execution_quality(sig, snapshot)

                # Filter against minimum gates (default MIN_ALPHA_SCORE = 70.0)
                if sig.alpha_score >= self.scorer.MIN_ALPHA_SCORE and sig.expected_ev >= self.scorer.MIN_EXPECTED_EV:
                    candidates.append(sig)

        # Opportunity Ranker (Section 103): Rank by (expected_ev * execution_score * alpha_score)
        candidates.sort(
            key=lambda s: s.expected_ev * s.features.get("execution_score", 50.0) * s.alpha_score,
            reverse=True
        )
        return candidates
