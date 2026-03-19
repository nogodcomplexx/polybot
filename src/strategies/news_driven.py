"""
News-driven strategy — directional bets based on strong sentiment signals.
"""

import logging
from typing import List, Dict

from src.config import StrategyNewsDrivenConfig
from src.data.models import (
    Market, MarketSignal, PredictionResult, TradeSignal,
    Side, OrderType, Conviction,
)
from src.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class NewsDrivenStrategy(BaseStrategy):
    """
    Place directional bets when:
    - Strong news sentiment (confidence > threshold)
    - Edge detected by prediction engine
    - Multiple corroborating news sources
    """

    def __init__(self, config: StrategyNewsDrivenConfig):
        self.config = config

    @property
    def name(self) -> str:
        return "news_driven"

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def evaluate(
        self,
        markets: List[Market],
        predictions: List[PredictionResult],
        signals: List[MarketSignal],
        budget_usd: float,
    ) -> List[TradeSignal]:
        """Find opportunities where news creates a strong directional signal."""
        signal_map: Dict[str, MarketSignal] = {
            s.market.condition_id: s for s in signals
        }
        pred_map: Dict[str, PredictionResult] = {
            p.market.condition_id: p for p in predictions
        }

        trades = []

        for market in markets:
            cid = market.condition_id
            signal = signal_map.get(cid)
            pred = pred_map.get(cid)

            if not signal or not signal.sentiment:
                continue
            if not pred:
                continue

            sentiment = signal.sentiment

            # Check minimum conditions
            if sentiment.confidence < self.config.min_sentiment_confidence:
                continue
            if len(signal.articles) < self.config.min_sources:
                continue
            if abs(pred.edge) * 100 < self.config.min_edge_pct:
                continue

            # Direction
            if pred.edge > 0:
                # Undervalued YES
                side = Side.BUY
                token_id = market.yes_token_id
                price = market.yes_price
            else:
                # Undervalued NO
                side = Side.BUY
                token_id = market.no_token_id
                price = market.no_price

            # Position size: scale with conviction
            if pred.conviction == Conviction.HIGH:
                size_pct = 0.12
            elif pred.conviction == Conviction.MEDIUM:
                size_pct = 0.08
            else:
                size_pct = 0.05
            size_usd = budget_usd * size_pct

            # Stop-loss / take-profit
            stop_loss = price * 0.80  # 20% stop
            take_profit = min(price * 1.25, 0.98)  # 25% target

            trades.append(TradeSignal(
                strategy_name=self.name,
                market=market,
                side=side,
                token_id=token_id,
                target_price=price,
                size_usd=size_usd,
                edge=pred.edge,
                conviction=pred.conviction,
                confidence=pred.confidence,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                reasoning=(
                    f"News-driven: sentiment={sentiment.label} "
                    f"(score={sentiment.score:.2f}, conf={sentiment.confidence:.2f}), "
                    f"{len(signal.articles)} sources, edge={pred.edge*100:+.1f}%"
                ),
                order_type=OrderType.GTC,
            ))

            logger.info(
                f"News signal: {market.question[:50]} — "
                f"{sentiment.label} ({len(signal.articles)} sources), "
                f"edge={pred.edge*100:+.1f}%"
            )

        return trades
