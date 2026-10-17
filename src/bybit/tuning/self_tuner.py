"""
Self-Tuning & Champion / Challenger Governance Engine.
Evaluates rolling trade batches, enforces parameter bounds clamping,
and adapts OBI thresholds, Alpha Score gate, and ATR stop multipliers
without ever modifying hard safety limits.
"""

import logging
from typing import Dict, Any, List, Optional
from src.bybit.config import TunableParameterRegistry
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
        self._last_tuned_trade_count: int = 0

    async def evaluate_batch(self):
        """Analyze recent trade batches and tune bounded parameters when new trades arrive."""
        trades = await self.db.get_recent_closed_trades(limit=40)
        if len(trades) < 20:
            return  # Require minimum statistical sample of 20 trades

        # Prevent over-tuning on the exact same trade sample every 200 seconds
        perf = await self.db.get_performance_stats()
        total_closed = int(perf.get("total_trades", len(trades)))
        if self._last_tuned_trade_count > 0 and (total_closed - self._last_tuned_trade_count) < 5:
            return
        self._last_tuned_trade_count = total_closed

        wins = [t for t in trades if float(t.get("pnl", 0) or 0) > 0]
        losses = [t for t in trades if float(t.get("pnl", 0) or 0) <= 0]
        win_rate = len(wins) / len(trades) if trades else 0.5

        gross_profit = sum(float(t.get("pnl", 0) or 0) for t in wins)
        gross_loss = abs(sum(float(t.get("pnl", 0) or 0) for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (2.0 if gross_profit > 0 else 0.0)

        sl_deaths = sum(1 for t in losses if "Stop Loss" in str(t.get("exit_reason", "")))
        sl_death_ratio = sl_deaths / len(trades)

        # 1. Tune Orderbook Imbalance & Alpha Score Gate based on Win Rate + Profit Factor
        curr_obi_bull = self.registry.get("ob_imbalance_bullish")
        curr_obi_bear = self.registry.get("ob_imbalance_bearish")
        curr_min_score = self.registry.get("min_alpha_score")

        if win_rate > 0.62 and profit_factor >= 1.35:
            # Strong edge: slightly relax OBI and alpha score within bounds to capture more volume
            new_bull = self.registry.update("ob_imbalance_bullish", round(curr_obi_bull - 0.05, 2))
            new_bear = self.registry.update("ob_imbalance_bearish", round(curr_obi_bear + 0.03, 2))
            new_score = self.registry.update("min_alpha_score", round(curr_min_score - 1.0, 1))
            logger.info(
                f"🔧 [SELF-TUNER] Strong regime (WR={win_rate*100:.1f}%, PF={profit_factor:.2f}) -> "
                f"OBI Bull={new_bull:.2f}, OBI Bear={new_bear:.2f}, MinAlpha={new_score:.1f}"
            )
        elif win_rate < 0.42 or profit_factor < 0.90:
            # Weak edge / chop: tighten conviction filters to protect capital
            new_bull = self.registry.update("ob_imbalance_bullish", round(curr_obi_bull + 0.05, 2))
            new_bear = self.registry.update("ob_imbalance_bearish", round(curr_obi_bear - 0.03, 2))
            new_score = self.registry.update("min_alpha_score", round(curr_min_score + 1.5, 1))
            logger.info(
                f"🛡️ [SELF-TUNER] Defensive tightening (WR={win_rate*100:.1f}%, PF={profit_factor:.2f}) -> "
                f"OBI Bull={new_bull:.2f}, OBI Bear={new_bear:.2f}, MinAlpha={new_score:.1f}"
            )

        # 2. Tune ATR Stop Multiplier if excessive premature Stop-Loss sweeps occur
        curr_atr_mult = self.registry.get("atr_stop_multiplier")
        if sl_death_ratio > 0.45:
            new_atr = self.registry.update("atr_stop_multiplier", round(curr_atr_mult + 0.10, 2))
            logger.info(f"🔧 [SELF-TUNER] High SL hit rate ({sl_death_ratio*100:.1f}%) -> Widened atr_stop_multiplier to {new_atr:.2f}")
        elif sl_death_ratio < 0.15 and curr_atr_mult > 1.50:
            new_atr = self.registry.update("atr_stop_multiplier", round(curr_atr_mult - 0.05, 2))
            logger.info(f"🔧 [SELF-TUNER] Low SL hit rate ({sl_death_ratio*100:.1f}%) -> Normalized atr_stop_multiplier to {new_atr:.2f}")

        self.champion_params = {k: v.current_val for k, v in self.registry.bounds.items()}
