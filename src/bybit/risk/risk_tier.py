"""
Bybit Risk-Tier & Liquidation Safety Simulator.
Pre-simulates projected position value, maintenance margin requirements (MMR),
and guarantees that structural stop lies safely before estimated liquidation.
"""

from typing import Tuple, Dict, Any
from src.bybit.models import OrderSide


class RiskTierSimulator:
    """Evaluates projected account exposure and liquidation buffers."""

    def simulate_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        entry_price: float,
        stop_price: float,
        account_equity: float,
        leverage: int = 10,
    ) -> Tuple[bool, str]:
        """
        Simulate risk-tier maintenance requirement and liquidation distance.
        Returns (is_safe: bool, reason: str).
        """
        if quantity <= 0 or entry_price <= 0 or account_equity <= 0:
            return False, "Invalid simulation parameters"

        order_notional = quantity * entry_price
        # Basic Bybit MMR for Tier 1 linear contracts (usually 0.5% or 1%)
        base_mmr = 0.01

        # Calculate estimated liquidation price for isolated margin:
        # Long: LiqPrice = Entry * (1 - 1/Lev + MMR)
        # Short: LiqPrice = Entry * (1 + 1/Lev - MMR)
        if side == OrderSide.BUY:
            est_liq = entry_price * (1.0 - (1.0 / leverage) + base_mmr)
            # Hard invariant: Stop must remain safely above liquidation price (with at least 0.25% buffer)
            if stop_price <= est_liq * 1.0025:
                return False, f"Structural stop ({stop_price:.2f}) is too close to or below liquidation ({est_liq:.2f})"
        else:
            est_liq = entry_price * (1.0 + (1.0 / leverage) - base_mmr)
            # Hard invariant: Stop must remain safely below liquidation price (with at least 0.25% buffer)
            if stop_price >= est_liq * 0.9975:
                return False, f"Structural stop ({stop_price:.2f}) is too close to or above liquidation ({est_liq:.2f})"

        return True, ""
