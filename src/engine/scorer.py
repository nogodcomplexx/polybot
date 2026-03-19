"""
Edge detection and scoring — ranks trade opportunities.
"""

import logging
from typing import List

from src.data.models import PredictionResult, TradeSignal, Side, OrderType, Conviction

logger = logging.getLogger(__name__)


class EdgeScorer:
    """Score and rank trade opportunities based on edge, confidence, and risk-reward."""

    def score_and_rank(
        self,
        predictions: List[PredictionResult],
        max_results: int = 10,
    ) -> List[PredictionResult]:
        """
        Score predictions by composite metric and return top N.
        
        Composite score = edge_magnitude * confidence * liquidity_factor
        """
        scored = []
        for pred in predictions:
            score = self._composite_score(pred)
            pred.confidence = score  # Repurpose confidence as composite score
            scored.append(pred)

        scored.sort(key=lambda p: p.confidence, reverse=True)
        return scored[:max_results]

    def prediction_to_signal(
        self,
        pred: PredictionResult,
        budget_usd: float,
        max_position_pct: float,
    ) -> TradeSignal:
        """Convert a prediction into a concrete trade signal."""
        market = pred.market

        # Determine direction
        if pred.edge > 0:
            # We think YES is underpriced → buy YES
            side = Side.BUY
            token_id = market.yes_token_id
            target_price = market.yes_price
        else:
            # We think NO is underpriced → buy NO
            side = Side.BUY
            token_id = market.no_token_id
            target_price = market.no_price

        # Position size (capped by budget)
        max_size_usd = budget_usd * (max_position_pct / 100.0)
        size_usd = min(max_size_usd, budget_usd * 0.5)  # Never more than 50% on one trade

        # Stop-loss and take-profit
        stop_loss_price = target_price * 0.75  # 25% stop-loss
        take_profit_price = min(target_price * 1.3, 0.99)  # 30% target or max

        return TradeSignal(
            strategy_name="prediction_engine",
            market=market,
            side=side,
            token_id=token_id,
            target_price=target_price,
            size_usd=size_usd,
            edge=pred.edge,
            conviction=pred.conviction,
            confidence=pred.confidence,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            reasoning=pred.reasoning,
            order_type=OrderType.GTC,
        )

    def _composite_score(self, pred: PredictionResult) -> float:
        """Calculate composite opportunity score."""
        edge_magnitude = abs(pred.edge) * 100  # 0 to ~50
        confidence = pred.confidence  # 0 to 1
        
        # Liquidity factor (normalized)
        liquidity = pred.market.liquidity
        if liquidity > 10000:
            liq_factor = 1.0
        elif liquidity > 5000:
            liq_factor = 0.8
        elif liquidity > 1000:
            liq_factor = 0.6
        else:
            liq_factor = 0.3

        # Risk-reward: higher edge + higher liquidity = better
        # Normalize edge to 0-1 range (20% edge = score 1.0)
        edge_norm = min(edge_magnitude / 20.0, 1.0)

        return edge_norm * 0.5 + confidence * 0.3 + liq_factor * 0.2
