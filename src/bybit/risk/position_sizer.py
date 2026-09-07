"""
Position Sizing & Kelly Framework Engine.
Sizes positions strictly from structural invalidation distance and risk budget.
Leverage is a derived execution artifact, never a risk parameter.
"""

import math
from typing import Dict, Any, Optional
from src.bybit.exchange.instruments import BybitInstrumentRepository


class PositionSizer:
    """Calculates risk-constrained order quantities."""

    def __init__(self, instrument_repo: BybitInstrumentRepository, max_risk_ceiling_pct: float = 7.5):
        self.instrument_repo = instrument_repo
        self.max_risk_ceiling_pct = max_risk_ceiling_pct

    def size_position(
        self,
        symbol: str,
        equity: float,
        allowed_risk_pct: float,
        entry: float,
        stop: float,
        drawdown_scalar: float = 1.0,
        kelly_fraction: Optional[float] = None,
    ) -> float:
        """
        Calculate final lot-rounded order quantity:
        Risk Budget USD = Equity * Effective Risk %
        Stop Distance = abs(Entry - Stop)
        Raw Quantity = Risk Budget USD / Stop Distance
        """
        symbol = symbol.upper()
        stop_dist = abs(entry - stop)
        if stop_dist <= 0 or equity <= 0:
            return 0.0

        # Cap risk percentage by configurable hard ceiling and drawdown scalar
        effective_risk_pct = min(self.max_risk_ceiling_pct, allowed_risk_pct * drawdown_scalar)

        # Kelly fraction can only REDUCE risk, never increase it
        if kelly_fraction is not None and kelly_fraction > 0:
            if kelly_fraction <= 1.0:
                effective_risk_pct = effective_risk_pct * kelly_fraction
            else:
                quarter_kelly_pct = (kelly_fraction * 0.25) * 100.0
                effective_risk_pct = min(effective_risk_pct, quarter_kelly_pct)

        risk_usd = equity * (effective_risk_pct / 100.0)
        raw_qty = risk_usd / stop_dist

        # Instrument lot sizing
        rounded_qty = self.instrument_repo.round_qty(symbol, raw_qty)

        # Strict Invariant: Rounding can NEVER increase risk above the dollar budget
        final_risk_usd = rounded_qty * stop_dist
        if final_risk_usd > risk_usd * 1.0001:
            spec = self.instrument_repo.get_spec(symbol)
            qty_step = spec.get("qty_step", 0.001) or 0.001
            rounded_qty = max(0.0, rounded_qty - qty_step)
            rounded_qty = self.instrument_repo.round_qty(symbol, rounded_qty)

        return rounded_qty
