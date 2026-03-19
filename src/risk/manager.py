"""
Risk management — position sizing, exposure limits, stop-loss, kill switch.
"""

import logging
import time
from typing import List, Optional
from dataclasses import dataclass

from src.config import RiskConfig
from src.data.models import TradeSignal, Position, PortfolioSnapshot, Conviction

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Enforce all risk constraints:
    - Position sizing (Kelly criterion)
    - Per-position stop-loss / take-profit
    - Portfolio exposure limits
    - Daily loss limits
    - Cash buffer
    - Kill switch
    """

    def __init__(self, config: RiskConfig, starting_budget: float):
        self.config = config
        self.starting_budget = starting_budget
        self.cash = starting_budget
        self.daily_loss: float = 0.0
        self.daily_start_value: float = starting_budget
        self.killed: bool = False
        self._last_daily_reset: float = time.time()
        
        # Reinvestment Engine: locked profit reserves
        self.locked_reserves: float = 0.0
        self.peak_value: float = starting_budget
        
        # Performance Tracking
        self.current_win_rate: float = 0.5
        self._cooldown_until: float = 0.0
        self._symbol_cooldown_until: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Position Sizing
    # ------------------------------------------------------------------

    def size_position(self, signal: TradeSignal) -> float:
        """
        Calculate position size using Dynamic Kelly Criterion.
        Kelly fraction auto-scales DOWN as the portfolio grows:
          $10-50   → Kelly 0.50 (aggressive compounding)
          $50-500  → Kelly 0.35
          $500-5k  → Kelly 0.25
          $5k+     → Kelly 0.15 (capital preservation)
        """
        if self.killed:
            return 0.0

        # Dynamic Kelly Scaling based on current portfolio size
        available = self.available_cash
        total_value = available + self.locked_reserves
        
        if total_value < 50:
            dynamic_kelly = 0.50    # Aggressive: grow fast from nothing
        elif total_value < 500:
            dynamic_kelly = 0.35    # Foundation: still fairly aggressive
        elif total_value < 5000:
            dynamic_kelly = 0.25    # Acceleration: balanced
        else:
            dynamic_kelly = 0.15    # Preservation: protect the gains

        # Kelly fraction: f* = (bp - q) / b
        win_prob = signal.target_price + abs(signal.edge)
        win_prob = min(0.99, max(0.01, win_prob))
        lose_prob = 1.0 - win_prob

        if signal.target_price > 0 and signal.target_price < 1.0:
            odds = (1.0 - signal.target_price) / signal.target_price
        else:
            return 0.0

        if odds <= 0:
            return 0.0

        kelly = (odds * win_prob - lose_prob) / odds
        kelly = max(0, kelly)
        
        # Mathematical Win-Rate Auto-Tuner Multiplier
        win_rate_multiplier = 1.0
        if self.current_win_rate < 0.50:
            # Choppy / Cold streak -> Slash Kelly by 50% to preserve capital
            win_rate_multiplier = 0.5
            logger.debug(f"🥶 COLD STREAK (Win Rate: {self.current_win_rate*100:.1f}%) -> Slashing Kelly by 50%.")
        elif self.current_win_rate > 0.60:
            # Hot streak -> Boost Kelly by 25% to press the mathematical advantage
            win_rate_multiplier = 1.25
            logger.debug(f"🔥 HOT STREAK (Win Rate: {self.current_win_rate*100:.1f}%) -> Boosting Kelly by 25%.")

        kelly *= dynamic_kelly * win_rate_multiplier

        max_pct = self.config.max_position_pct / 100.0
        kelly = min(kelly, max_pct)

        size_usd = available * kelly

        if size_usd < 0.50:
            return 0.0

        return round(size_usd, 2)

    @property
    def available_cash(self) -> float:
        """Cash available for trading (excludes locked reserves)."""
        return max(0, self.cash - self.locked_reserves)

    # ------------------------------------------------------------------
    # Risk Checks
    # ------------------------------------------------------------------

    def check_trade(
        self,
        signal: TradeSignal,
        positions: List[Position],
    ) -> tuple[bool, str]:
        """
        Check if a trade passes all risk constraints.
        Returns (approved, reason).
        """
        if self.killed:
            return False, "Kill switch activated"
        if time.time() < self._cooldown_until:
            return False, "Risk cooldown active"
        sym = (getattr(signal, "underlying_symbol", "") or "").upper()
        if sym:
            until = self._symbol_cooldown_until.get(sym, 0.0)
            if time.time() < until:
                return False, f"Symbol cooldown active ({sym})"

        # Daily loss limit
        self._maybe_reset_daily()
        if self.daily_loss >= (self.starting_budget * self.config.daily_loss_limit_pct / 100):
            return False, f"Daily loss limit reached ({self.daily_loss:.2f})"

        # Cash buffer (use available_cash which excludes reserves)
        min_cash = self.starting_budget * (self.config.cash_buffer_pct / 100)
        if self.available_cash - signal.size_usd < min_cash:
            return False, f"Would breach cash buffer (need ${min_cash:.2f} reserve)"

        # Max position size
        max_pos = self.starting_budget * (self.config.max_position_pct / 100)
        if signal.size_usd > max_pos:
            signal.size_usd = max_pos  # Auto-downsize

        # Category exposure (simple: count positions in same market)
        same_market = [
            p for p in positions
            if p.condition_id == signal.market.condition_id
        ]
        if len(same_market) >= 1:
            return False, "Already hold an active position in this market"

        # Correlated exposure cap (BTC/ETH/SOL cluster)
        cluster = "MAJORS" if any(s in sym for s in ("BTC", "ETH", "SOL")) else ""
        if cluster == "MAJORS":
            cluster_cost = 0.0
            for p in positions:
                ps = (getattr(p, "underlying_symbol", "") or "").upper()
                if any(s in ps for s in ("BTC", "ETH", "SOL")):
                    cluster_cost += float(p.cost_usd or 0.0)
            cluster_cap = self.starting_budget * 0.60
            if cluster_cost + float(signal.size_usd or 0.0) > cluster_cap:
                return False, "Correlated exposure cap (majors cluster)"

        return True, "Approved"

    def record_loss(self, amount: float):
        """Record a loss for daily tracking."""
        self.daily_loss += abs(amount)

    def record_trade(self, cost: float):
        """Record cash spent on a trade."""
        self.cash -= cost

    def record_close(self, proceeds: float):
        """Record cash received from closing a position and lock profit reserves."""
        self.cash += proceeds
        
        # Reinvestment Engine: lock a percentage of new profits
        total_value = self.cash
        if total_value > self.peak_value:
            new_profit = total_value - self.peak_value
            lock_amount = new_profit * (self.config.reserve_lock_pct / 100.0)
            self.locked_reserves += lock_amount
            self.peak_value = total_value
            if lock_amount > 0.01:
                logger.info(
                    f"🔒 Locked ${lock_amount:.2f} profit into reserves "
                    f"(total reserves: ${self.locked_reserves:.2f})"
                )

    # ------------------------------------------------------------------
    # Stop-Loss / Take-Profit
    # ------------------------------------------------------------------

    def check_stops(self, positions: List[Position]) -> List[Position]:
        """Return positions that should be closed (hit stop-loss or take-profit)."""
        to_close = []
        for pos in positions:
            if pos.current_price <= pos.stop_loss_price and pos.stop_loss_price > 0:
                logger.warning(
                    f"STOP-LOSS hit: {pos.market_question[:40]} "
                    f"(current={pos.current_price:.3f}, stop={pos.stop_loss_price:.3f})"
                )
                to_close.append(pos)
            elif pos.current_price >= pos.take_profit_price and pos.take_profit_price > 0:
                logger.info(
                    f"TAKE-PROFIT hit: {pos.market_question[:40]} "
                    f"(current={pos.current_price:.3f}, target={pos.take_profit_price:.3f})"
                )
                to_close.append(pos)
        return to_close

    # ------------------------------------------------------------------
    # Kill Switch
    # ------------------------------------------------------------------

    def activate_kill_switch(self, reason: str = "Manual"):
        """Emergency shutdown — prevent all new trades."""
        self.killed = True
        logger.critical(f"🚨 KILL SWITCH ACTIVATED: {reason}")

    def activate_cooldown(self, seconds: float, reason: str = "Cooldown"):
        """Temporarily pause new trades without fully killing the bot."""
        until = time.time() + max(0.0, seconds)
        self._cooldown_until = max(self._cooldown_until, until)
        logger.warning(f"🧊 Risk cooldown for {seconds:.0f}s: {reason}")

    def activate_symbol_cooldown(self, symbol: str, seconds: float, reason: str = "Symbol cooldown"):
        sym = (symbol or "").upper()
        if not sym:
            return
        until = time.time() + max(0.0, seconds)
        self._symbol_cooldown_until[sym] = max(self._symbol_cooldown_until.get(sym, 0.0), until)
        logger.warning(f"🧊 Symbol cooldown for {seconds:.0f}s ({sym}): {reason}")

    def deactivate_kill_switch(self):
        """Re-enable trading."""
        self.killed = False
        logger.info("Kill switch deactivated")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self):
        """Reset daily counters at midnight."""
        now = time.time()
        if now - self._last_daily_reset > 86400:
            self.daily_loss = 0.0
            self.daily_start_value = self.cash
            self._last_daily_reset = now
            logger.info("Daily risk counters reset")
