"""
Prediction engine — Bayesian probability estimator combining multiple signals.
"""

import logging
from typing import List, Optional

from src.config import PredictionConfig
from src.data.models import (
    Market, MarketSignal, PredictionResult, Conviction
)

logger = logging.getLogger(__name__)


class Predictor:
    """
    Estimate the true probability of market outcomes by combining:
    - Market price (baseline)
    - News sentiment signal
    - Volume/momentum signal
    - Historical resolution patterns
    """

    def __init__(self, config: PredictionConfig):
        self.config = config

    def predict(
        self,
        market: Market,
        signal: Optional[MarketSignal] = None,
    ) -> PredictionResult:
        """
        Generate a probability prediction for a market.
        """
        weights = self.config.weights

        # 1. Market price baseline (the "crowd wisdom")
        market_price = market.yes_price

        # 2. News sentiment signal
        news_signal = self._compute_news_signal(signal)

        # 3. Volume/momentum signal
        volume_signal = self._compute_volume_signal(market)

        # 4. Historical pattern signal
        historical_signal = self._compute_historical_signal(market)

        # Weighted ensemble
        our_prob = (
            weights["market_price"] * market_price
            + weights["news_sentiment"] * news_signal
            + weights["volume_momentum"] * volume_signal
            + weights["historical_patterns"] * historical_signal
        )

        # Clamp to [0.01, 0.99]
        our_prob = max(0.01, min(0.99, our_prob))

        # Edge
        edge = our_prob - market_price

        # Conviction level
        abs_edge = abs(edge) * 100  # Convert to percentage
        if abs_edge >= self.config.high_conviction_edge_pct:
            conviction = Conviction.HIGH
        elif abs_edge >= self.config.medium_conviction_edge_pct:
            conviction = Conviction.MEDIUM
        else:
            conviction = Conviction.LOW

        # Overall confidence
        confidence = self._compute_confidence(signal, market, abs_edge)

        result = PredictionResult(
            market=market,
            our_yes_prob=our_prob,
            market_yes_price=market_price,
            edge=edge,
            conviction=conviction,
            confidence=confidence,
            news_signal=news_signal,
            volume_signal=volume_signal,
            historical_signal=historical_signal,
            reasoning=self._build_reasoning(market, edge, signal),
        )
        return result

    def predict_batch(
        self,
        markets: List[Market],
        signals: List[MarketSignal],
    ) -> List[PredictionResult]:
        """Generate predictions for all markets."""
        signal_map = {s.market.condition_id: s for s in signals}
        results = []
        for market in markets:
            signal = signal_map.get(market.condition_id)
            pred = self.predict(market, signal)

            # Only include if edge exceeds minimum threshold
            if abs(pred.edge) * 100 >= self.config.min_edge_pct:
                results.append(pred)

        # Sort by absolute edge descending
        results.sort(key=lambda p: abs(p.edge), reverse=True)
        logger.info(f"Predictions: {len(results)} markets with edge >= {self.config.min_edge_pct}%")
        return results

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------

    def _compute_news_signal(self, signal: Optional[MarketSignal]) -> float:
        """Convert news sentiment into a probability signal."""
        if not signal or not signal.sentiment:
            return 0.5  # No news = neutral (market price is best guess)

        sentiment = signal.sentiment
        # sentiment.score is -1 to +1
        # Convert to probability space: -1 → 0.0, 0 → 0.5, +1 → 1.0
        news_prob = 0.5 + (sentiment.score * 0.5)

        # Scale by relevance and confidence
        relevance = signal.relevance_score
        confidence = sentiment.confidence

        # Blend with neutral baseline, weighted by how confident we are
        blend_weight = relevance * confidence
        blended = 0.5 * (1 - blend_weight) + news_prob * blend_weight

        return max(0.01, min(0.99, blended))

    def _compute_volume_signal(self, market: Market) -> float:
        """
        Volume-based momentum signal.
        High volume = more conviction in current price direction.
        """
        # Simple heuristic: high volume markets are more efficient
        # For now, return market price (volume confirms crowd wisdom)
        if market.volume_24h > 10000:
            return market.yes_price  # High volume = trust market
        elif market.volume_24h > 1000:
            return 0.5 * market.yes_price + 0.5 * 0.5  # Moderate → blend
        else:
            return 0.5  # Low volume → we can't trust the price

    def _compute_historical_signal(self, market: Market) -> float:
        """
        Historical resolution pattern signal.
        Simple heuristic: slight bias towards "NO" resolutions.
        """
        # Empirically, ~60% of prediction markets resolve NO
        # This is a minor correction factor
        base = market.yes_price
        # Mild regression to mean with NO bias
        return base * 0.95 + 0.5 * 0.05

    def _compute_confidence(
        self,
        signal: Optional[MarketSignal],
        market: Market,
        abs_edge_pct: float,
    ) -> float:
        """Compute overall confidence in our prediction."""
        factors = []

        # News confidence
        if signal and signal.sentiment:
            factors.append(signal.sentiment.confidence)
            factors.append(min(signal.relevance_score, 1.0))
            factors.append(min(len(signal.articles) / 5.0, 1.0))  # More sources = more confident

        # Liquidity confidence (more liquid = more confident in edge)
        if market.liquidity > 5000:
            factors.append(0.8)
        elif market.liquidity > 1000:
            factors.append(0.5)
        else:
            factors.append(0.3)

        # Edge magnitude
        factors.append(min(abs_edge_pct / 20.0, 1.0))

        return sum(factors) / max(len(factors), 1)

    def _build_reasoning(
        self,
        market: Market,
        edge: float,
        signal: Optional[MarketSignal],
    ) -> str:
        """Build a human-readable reasoning string."""
        direction = "YES" if edge > 0 else "NO"
        parts = [f"Edge: {edge*100:+.1f}% → favor {direction}"]

        if signal and signal.sentiment:
            parts.append(
                f"News: {signal.sentiment.label} "
                f"(score={signal.sentiment.score:.2f}, "
                f"{len(signal.articles)} articles)"
            )
        else:
            parts.append("No relevant news found")

        parts.append(f"Liquidity: ${market.liquidity:,.0f}")
        return " | ".join(parts)
