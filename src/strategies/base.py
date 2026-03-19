"""
Base strategy interface — all trading strategies inherit from this.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from src.data.models import Market, MarketSignal, PredictionResult, TradeSignal


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        ...

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this strategy is active."""
        ...

    @abstractmethod
    def evaluate(
        self,
        markets: List[Market],
        predictions: List[PredictionResult],
        signals: List[MarketSignal],
        budget_usd: float,
    ) -> List[TradeSignal]:
        """
        Evaluate markets and return trade signals.
        
        Args:
            markets: Filtered active markets
            predictions: Prediction results with edge
            signals: News signals mapped to markets
            budget_usd: Available trading budget
            
        Returns:
            List of recommended trade signals
        """
        ...
