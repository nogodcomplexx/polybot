"""
Derivatives Fusion & Market Move Classification Engine (Sections 24–28, 40).
Combines Open Interest, Funding Rates, Liquidations, and Cross-Exchange dynamics
into a unified Derivatives Context Score (-100 to +100) and Cross-Exchange Confidence (0–100).
"""

import time
from typing import Dict, Any, Optional, Tuple, List
from src.bybit.intelligence.models import (
    OIRegime, FundingRegime, LiquidationRegime, MarketMoveClassification,
    PriceSyncState, OrderflowConfirmation, LiquidityClassification
)


class DerivativesFusionEngine:
    """Fuses multi-venue derivatives intelligence and detects macro market state."""

    def evaluate_derivatives(
        self,
        bybit_oi: float,
        binance_oi: float,
        coinglass_aggregate_oi: float,
        bybit_funding: float,
        binance_funding: float,
        recent_long_liqs: float,
        recent_short_liqs: float,
        price_sync: PriceSyncState,
        orderflow_confirmation: OrderflowConfirmation,
        basis_pct: float,
    ) -> Dict[str, Any]:
        """
        Produce unified derivatives classification, confidence score, and context score.
        """
        # 1. Section 24: OI Fusion
        aggregate_oi = coinglass_aggregate_oi if coinglass_aggregate_oi > 0 else (bybit_oi + binance_oi)
        oi_regime = self._classify_oi(bybit_oi, binance_oi)

        # 2. Section 25: Funding Fusion
        funding_spread = bybit_funding - binance_funding
        funding_regime = self._classify_funding(bybit_funding, binance_funding, funding_spread)

        # 3. Section 26: Liquidation Fusion
        liq_regime, liq_intensity = self._classify_liquidations(recent_long_liqs, recent_short_liqs)

        # 4. Section 40: Local vs Global Move Classification
        move_classification = self._classify_move(
            price_sync=price_sync,
            flow=orderflow_confirmation,
            basis_pct=basis_pct,
            liq_regime=liq_regime,
        )

        # 5. Section 27: Cross-Exchange Confidence (0 to 100)
        cross_confidence = self._calculate_cross_confidence(
            price_sync=price_sync,
            flow=orderflow_confirmation,
            funding_regime=funding_regime,
            basis_pct=basis_pct,
        )

        # 6. Section 28: Derivatives Context Score (-100 to +100)
        derivatives_score = self._calculate_derivatives_score(
            bybit_funding=bybit_funding,
            binance_funding=binance_funding,
            oi_regime=oi_regime,
            liq_regime=liq_regime,
        )

        return {
            "aggregate_oi": round(aggregate_oi, 2),
            "oi_regime": oi_regime,
            "funding_spread": round(funding_spread, 6),
            "funding_regime": funding_regime,
            "liquidation_intensity": round(liq_intensity, 2),
            "liquidation_regime": liq_regime,
            "move_classification": move_classification,
            "cross_exchange_confidence": round(cross_confidence, 1),
            "derivatives_context_score": round(derivatives_score, 1),
        }

    def _classify_oi(self, bybit_oi: float, binance_oi: float) -> OIRegime:
        """Classify Open Interest participation."""
        if bybit_oi > 0 and binance_oi > 0:
            return OIRegime.BROAD_POSITION_BUILD
        elif bybit_oi > 0 and binance_oi == 0:
            return OIRegime.LOCAL_POSITION_BUILD
        return OIRegime.UNKNOWN

    def _classify_funding(self, bybit_f: float, binance_f: float, spread: float) -> FundingRegime:
        """Classify leveraged crowding from funding rates."""
        avg_funding = (bybit_f + binance_f) / 2.0 if (bybit_f != 0 and binance_f != 0) else bybit_f

        if abs(spread) > 0.0008:
            return FundingRegime.DISLOCATED
        elif avg_funding >= 0.0003:
            return FundingRegime.LONG_CROWDED
        elif avg_funding <= -0.0003:
            return FundingRegime.SHORT_CROWDED
        return FundingRegime.NEUTRAL

    def _classify_liquidations(self, long_liqs: float, short_liqs: float) -> Tuple[LiquidationRegime, float]:
        """Classify liquidation flow and intensity."""
        total_liq = long_liqs + short_liqs
        if total_liq <= 0:
            return LiquidationRegime.NORMAL, 0.0

        intensity = min(100.0, total_liq / 50000.0)

        if long_liqs > short_liqs * 3.0 and total_liq > 100000.0:
            return LiquidationRegime.CASCADE if long_liqs > 300000.0 else LiquidationRegime.LONG_LIQUIDATION, intensity
        elif short_liqs > long_liqs * 3.0 and total_liq > 100000.0:
            return LiquidationRegime.SHORT_SQUEEZE if short_liqs > 300000.0 else LiquidationRegime.SHORT_LIQUIDATION, intensity

        return LiquidationRegime.NORMAL, intensity

    def _classify_move(
        self,
        price_sync: PriceSyncState,
        flow: OrderflowConfirmation,
        basis_pct: float,
        liq_regime: LiquidationRegime,
    ) -> MarketMoveClassification:
        """Section 40: Local vs Global Move Detection."""
        if liq_regime == LiquidationRegime.CASCADE:
            return MarketMoveClassification.GLOBAL_MOVE
        if abs(basis_pct) > 0.25:
            return MarketMoveClassification.CROSS_EXCHANGE_DISLOCATION
        if price_sync == PriceSyncState.BINANCE_LEADING or flow == OrderflowConfirmation.BINANCE_LEADING:
            return MarketMoveClassification.BINANCE_LED_MOVE
        if price_sync == PriceSyncState.BYBIT_LEADING and flow == OrderflowConfirmation.BYBIT_LOCAL:
            return MarketMoveClassification.LOCAL_BYBIT_MOVE
        if price_sync == PriceSyncState.SYNCED and flow in (OrderflowConfirmation.CONFIRMED_BULLISH, OrderflowConfirmation.CONFIRMED_BEARISH):
            return MarketMoveClassification.GLOBAL_MOVE

        return MarketMoveClassification.UNKNOWN

    def _calculate_cross_confidence(
        self,
        price_sync: PriceSyncState,
        flow: OrderflowConfirmation,
        funding_regime: FundingRegime,
        basis_pct: float,
    ) -> float:
        """Section 27: Cross-Exchange Confidence (0 to 100)."""
        score = 50.0

        # Price alignment (+/- 25)
        if price_sync == PriceSyncState.SYNCED:
            score += 20.0
        elif price_sync in (PriceSyncState.BYBIT_LEADING, PriceSyncState.BINANCE_LEADING):
            score += 10.0
        elif price_sync == PriceSyncState.DIVERGING:
            score -= 25.0
        elif price_sync == PriceSyncState.UNSTABLE:
            score -= 20.0

        # Orderflow alignment (+/- 20)
        if flow in (OrderflowConfirmation.CONFIRMED_BULLISH, OrderflowConfirmation.CONFIRMED_BEARISH):
            score += 20.0
        elif flow == OrderflowConfirmation.MIXED:
            score -= 15.0

        # Funding alignment (+/- 10)
        if funding_regime == FundingRegime.DISLOCATED:
            score -= 10.0
        elif funding_regime == FundingRegime.NEUTRAL:
            score += 5.0

        # Basis magnitude penalty
        if abs(basis_pct) > 0.15:
            score -= 10.0

        return max(0.0, min(100.0, score))

    def _calculate_derivatives_score(
        self,
        bybit_funding: float,
        binance_funding: float,
        oi_regime: OIRegime,
        liq_regime: LiquidationRegime,
    ) -> float:
        """Section 28: Derivatives Context Score (-100 to +100)."""
        score = 0.0

        # Funding crowd bias (negative funding is bullish squeeze bias; positive is bearish exhaustion bias)
        avg_f = (bybit_funding + binance_funding) / 2.0 if (bybit_funding != 0 and binance_funding != 0) else bybit_funding
        if avg_f < -0.0002:
            score += 35.0   # Heavily short crowded -> bullish squeeze fuel
        elif avg_f > 0.0003:
            score -= 35.0   # Heavily long crowded -> long liquidation risk

        # Liquidation squeezes
        if liq_regime == LiquidationRegime.SHORT_SQUEEZE:
            score += 40.0
        elif liq_regime == LiquidationRegime.LONG_LIQUIDATION:
            score -= 40.0

        # OI build confirmation
        if oi_regime == OIRegime.BROAD_POSITION_BUILD:
            if score > 0:
                score += 10.0
            elif score < 0:
                score -= 10.0
            else:
                score += 5.0

        return max(-100.0, min(100.0, score))
