"""
Self-Tuning & Champion / Challenger Governance Engine.
Evaluates rolling trade batches (30, 60, 100 trades), enforces parameter bounds clamping,
and tests challengers in shadow mode without ever modifying hard safety limits.
"""

import logging
from typing import Dict, Any, List, Optional
from src.bybit.config import TunableParameterRegistry, HardSafetyLimits
from src.bybit.storage.database import BybitDatabase

logger = logging.getLogger(__name__)


class SelfTuningEngine:
    """Governance framework for bounded parameter optimization."""

    def __init__(self, registry: TunableParameterRegistry, db: BybitDatabase):
        self.registry = registry
        self.db = db
        self.champion_params: Dict[str, float] = {k: v.current_val for k, v in registry.bounds.items()}
        self.challenger_params: Dict[str, float] = dict(self.champion_params)
        self.challenger_active: bool = False

    async def evaluate_batch(self):
        """Analyze recent trade batches and tune bounded parameters."""
        trades = await self.db.get_recent_closed_trades(limit=50)
        if len(trades) < 20:
            return  # Require minimum statistical sample

        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) < 0]
        win_rate = len(wins) / len(trades) if trades else 0.5

        # Example tuning: If win rate is high (>65%) but EV is strong, slightly relax OBI to capture more trades
        if win_rate > 0.65:
            current_obi = self.registry.get("ob_imbalance_bullish")
            # Step down slightly within bounds (e.g. 1.30 -> 1.25)
            self.registry.update("ob_imbalance_bullish", current_obi - 0.05)
            logger.info(f"Self-Tuning: Relaxed bullish OBI to {self.registry.get('ob_imbalance_bullish'):.2f} based on strong win rate ({win_rate*100:.1f}%)")

        elif win_rate < 0.40:
            current_obi = self.registry.get("ob_imbalance_bullish")
            # Tighten threshold (e.g. 1.30 -> 1.35)
            self.registry.update("ob_imbalance_bullish", current_obi + 0.05)
            logger.info(f"Self-Tuning: Tightened bullish OBI to {self.registry.get('ob_imbalance_bullish'):.2f} due to weak win rate ({win_rate*100:.1f}%)")
