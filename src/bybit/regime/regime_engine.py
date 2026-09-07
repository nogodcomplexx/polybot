"""
Market Regime Engine.
Deterministically classifies symbols into 9 distinct market regimes and enforces
strategy eligibility matrices.
"""

from typing import Dict, Any, List, Set
from src.bybit.models import MarketRegime, StrategyName
from src.bybit.market.candles import Candle


class MarketRegimeEngine:
    """Classifies market regime based on multi-factor analysis."""

    # Strategy to allowed regimes mapping (Hard safety vetoes reserved for SHOCK, LIQUIDATION_CASCADE, LOW_LIQUIDITY)
    STRATEGY_REGIMES: Dict[StrategyName, Set[MarketRegime]] = {
        StrategyName.TURTLE_SOUP: {MarketRegime.RANGE, MarketRegime.LOW_VOL, MarketRegime.UNCERTAIN},
        StrategyName.VWAP_FVG_REVERSION: {MarketRegime.RANGE, MarketRegime.LOW_VOL, MarketRegime.TREND_UP, MarketRegime.TREND_DOWN, MarketRegime.HIGH_VOL, MarketRegime.UNCERTAIN},
        StrategyName.ABSORPTION: {MarketRegime.RANGE, MarketRegime.HIGH_VOL, MarketRegime.LOW_VOL, MarketRegime.UNCERTAIN},
        StrategyName.TREND_CONTINUATION: {MarketRegime.TREND_UP, MarketRegime.TREND_DOWN, MarketRegime.HIGH_VOL, MarketRegime.UNCERTAIN},
    }

    def classify(
        self,
        candles_15m: List[Candle],
        atr_15m: float,
        spread_pct: float,
        orderbook_imbalance: float,
        liquidation_flow: float = 0.0,
    ) -> MarketRegime:
        """Classify market regime into exactly one primary regime."""
        if len(candles_15m) < 25:
            return MarketRegime.UNCERTAIN

        current_price = candles_15m[-1].close
        if current_price <= 0:
            return MarketRegime.UNCERTAIN

        # 1. Check for extreme illiquidity
        if spread_pct > 0.12:
            return MarketRegime.LOW_LIQUIDITY

        # 2. Check for liquidation cascade or shock event
        atr_pct = (atr_15m / current_price) * 100 if current_price > 0 else 0.0
        last_candle_range_pct = ((candles_15m[-1].high - candles_15m[-1].low) / current_price) * 100

        if last_candle_range_pct > 3.0 or liquidation_flow > 500000.0:
            return MarketRegime.LIQUIDATION_CASCADE

        if atr_pct > 1.8:
            return MarketRegime.HIGH_VOL

        if atr_pct < 0.20:
            return MarketRegime.LOW_VOL

        # 3. Trend vs Range analysis via EMAs
        closes = [c.close for c in candles_15m]
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)
        ema50 = self._calculate_ema(closes, 50)

        # Clear Trend UP: EMA9 > EMA21 > EMA50
        if ema9 > ema21 * 1.001 and ema21 > ema50 * 1.001:
            return MarketRegime.TREND_UP

        # Clear Trend DOWN: EMA9 < EMA21 * 0.999 and ema21 < ema50 * 0.999
        if ema9 < ema21 * 0.999 and ema21 < ema50 * 0.999:
            return MarketRegime.TREND_DOWN

        # Range: EMAs intertwined / slope flat
        ema_spread = abs(ema9 - ema50) / ema50 * 100
        if ema_spread < 0.35:
            return MarketRegime.RANGE

        return MarketRegime.UNCERTAIN

    def is_strategy_allowed(self, strategy: StrategyName, regime: MarketRegime) -> bool:
        """
        Verify whether the strategy is permitted in the current regime.
        Hard vetoes are reserved strictly for chaotic states: SHOCK, LIQUIDATION_CASCADE, and LOW_LIQUIDITY.
        """
        if regime in (MarketRegime.SHOCK, MarketRegime.LIQUIDATION_CASCADE, MarketRegime.LOW_LIQUIDITY):
            return False
        allowed = self.STRATEGY_REGIMES.get(strategy, set())
        return regime in allowed

    def _calculate_ema(self, series: List[float], period: int) -> float:
        if len(series) < period:
            return series[-1] if series else 0.0
        mult = 2.0 / (period + 1.0)
        ema = sum(series[:period]) / period
        for val in series[period:]:
            ema = (val - ema) * mult + ema
        return ema
