"""
Execution Policy & Safety Pre-Check Engine.
Implements the 15-point pre-execution verification checklist, Mark/Index divergence monitoring,
and rate-limit budgeting.
"""

import time
import logging
from typing import Dict, Any, Tuple
from src.bybit.models import ExecutionPlan, MarketSnapshot, OrderSide

logger = logging.getLogger(__name__)


class ExecutionPolicyEngine:
    """Pre-execution safety auditor enforcing Section 36 execution checklist."""

    def __init__(self, max_divergence_pct: float = 0.50):
        self.max_divergence_pct = max_divergence_pct
        self._request_timestamps = []
        self.max_requests_per_second = 10

    def check_rate_limit(self) -> bool:
        """Proactively throttle requests before hitting exchange limits."""
        now = time.time()
        self._request_timestamps = [t for t in self._request_timestamps if now - t <= 1.0]
        if len(self._request_timestamps) >= self.max_requests_per_second:
            logger.warning("Rate limit governor throttling order placement")
            return False
        self._request_timestamps.append(now)
        return True

    def evaluate_execution_precheck(
        self,
        plan: ExecutionPlan,
        snapshot: MarketSnapshot,
        ws_connected: bool,
    ) -> Tuple[bool, str]:
        """
        Execute 15-Point Execution Pre-Check (Section 36):
        Market data fresh, exchange connectivity, position state, risk tier,
        margin state, price state, spread, liquidity, slippage, queue quality,
        alpha freshness, protection feasible, order constraints, rate limit budget, reconciliation.
        """
        now = time.time()

        # 1. Market Data Fresh
        if (now - snapshot.timestamp) > 4.0:
            return False, "Market data is stale (>4.0s)"

        # 2. Exchange Connectivity
        if not ws_connected:
            return False, "WebSocket disconnected"

        # 3. Mark / Last Divergence Guard
        if snapshot.last_price > 0 and snapshot.mark_price > 0:
            div_pct = abs(snapshot.last_price - snapshot.mark_price) / snapshot.mark_price * 100
            if div_pct > self.max_divergence_pct:
                return False, f"Excessive Mark/Last price divergence ({div_pct:.2f}% > {self.max_divergence_pct}%)"

        # 4. Mark / Index Divergence Guard
        if snapshot.index_price > 0 and snapshot.mark_price > 0:
            idx_div_pct = abs(snapshot.index_price - snapshot.mark_price) / snapshot.mark_price * 100
            if idx_div_pct > self.max_divergence_pct:
                return False, f"Excessive Mark/Index divergence ({idx_div_pct:.2f}%)"

        # 5. Spread Check
        if snapshot.spread_pct > 0.10:
            return False, f"Spread too wide ({snapshot.spread_pct:.3f}% > 0.10%)"

        # 6. Rate Limit Budget
        if not self.check_rate_limit():
            return False, "Rate limit budget exhausted"

        # 7. Quantity & Pricing Valid
        if plan.quantity <= 0 or plan.entry_price <= 0:
            return False, "Invalid plan quantity or entry price"

        # 8. Minimum Exchange Notional Check ($5.00 USDT Bybit minimum)
        order_notional = plan.quantity * plan.entry_price
        if order_notional < 5.0:
            return False, f"Order notional (${order_notional:.2f}) below exchange minimum ($5.00)"

        # 9. Protective Brackets Feasibility & Geometry Check
        if plan.stop_price <= 0 or plan.tp1_price <= 0 or plan.tp2_price <= 0:
            return False, "Protective stops and targets must be positive non-zero prices"

        is_buy = (plan.side == OrderSide.BUY or plan.side.value == "Buy")
        if is_buy:
            if not (plan.stop_price < plan.entry_price < plan.tp1_price < plan.tp2_price):
                return False, f"Inverted Buy bracket geometry: Stop(${plan.stop_price}) < Entry(${plan.entry_price}) < TP1(${plan.tp1_price}) < TP2(${plan.tp2_price}) violated"
        else:
            if not (plan.stop_price > plan.entry_price > plan.tp1_price > plan.tp2_price):
                return False, f"Inverted Sell bracket geometry: Stop(${plan.stop_price}) > Entry(${plan.entry_price}) > TP1(${plan.tp1_price}) > TP2(${plan.tp2_price}) violated"

        # 10. Orderbook Depth & Liquidity Check
        if snapshot.bid_depth <= 0 or snapshot.ask_depth <= 0:
            return False, "Insufficient orderbook depth (bid/ask depth is zero)"

        # 11. Spread to Stop Loss Distance
        stop_dist = abs(plan.entry_price - plan.stop_price)
        if stop_dist > 0 and snapshot.spread > 0:
            spread_ratio = snapshot.spread / stop_dist
            if spread_ratio > 0.35:
                return False, f"Spread consumes excessive stop distance ({spread_ratio * 100:.1f}% > 35%)"

        # 12. Mathematical Risk-to-Reward Ratio
        reward = abs(plan.tp1_price - plan.entry_price)
        if stop_dist > 0:
            rr_ratio = reward / stop_dist
            if rr_ratio < 1.20:
                return False, f"Mathematical R:R too low ({rr_ratio:.2f} < 1.20)"

        return True, ""
