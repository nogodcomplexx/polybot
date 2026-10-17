"""
Circuit Breakers & Drawdown Governor.
Maintains 5 independent kill switches, daily high-watermark drawdown tiers,
and persistent 4-hour cooldown after 2 consecutive losses.
"""

import time
import logging
from typing import Dict, Any, Tuple, Optional
from src.bybit.storage.database import BybitDatabase

logger = logging.getLogger(__name__)


class CircuitBreakers:
    """Independent kill switches and drawdown risk governors."""

    def __init__(
        self,
        db: BybitDatabase,
        high_watermark: float = 1000.0,
        mode: str = "live",
        cooldown_enabled: Optional[bool] = None,
        kill_switch_enabled: Optional[bool] = None,
    ):
        self.db = db
        self.high_watermark = high_watermark
        self.daily_drawdown_pct: float = 0.0
        self.consecutive_losses: int = 0
        self.cooldown_until: float = 0.0
        self.today_start: float = float((int(time.time()) // 86400) * 86400)
        self.mode = mode.lower() if isinstance(mode, str) else "live"
        if cooldown_enabled is not None:
            self.cooldown_enabled = cooldown_enabled
        else:
            self.cooldown_enabled = (self.mode == "live")

        if kill_switch_enabled is not None:
            self.kill_switch_enabled = kill_switch_enabled
        else:
            self.kill_switch_enabled = (self.mode == "live")

        # 5 Independent Kill Switches
        self.kill_switches: Dict[str, bool] = {
            "ACCOUNT_KILL": False,
            "DRAWDOWN_KILL": False,
            "MARKET_KILL": False,
            "EXECUTION_KILL": False,
            "SOFTWARE_KILL": False,
        }

    async def initialize(self):
        """Load persisted governor metrics from database across restarts."""
        state = await self.db.load_governor_state()
        self.consecutive_losses = state["consecutive_losses"]
        self.cooldown_until = state["cooldown_until"]
        self.high_watermark = max(self.high_watermark, state["high_watermark"])
        if state["kill_switch_active"]:
            if self.kill_switch_enabled and self.mode == "live":
                self.kill_switches["DRAWDOWN_KILL"] = True
                logger.warning("🚨 Restored active DRAWDOWN_KILL switch from database")
            else:
                self.kill_switches["DRAWDOWN_KILL"] = False
                logger.info("ℹ️ Persisted DRAWDOWN_KILL found in DB but ignored (kill switch disabled in paper/testing mode)")
                await self.db.save_governor_state(
                    consecutive_losses=self.consecutive_losses,
                    cooldown_until=self.cooldown_until,
                    high_watermark=self.high_watermark,
                    daily_loss=0.0,
                    kill_switch=False,
                )

        if self.is_cooldown_active():
            remaining_mins = (self.cooldown_until - time.time()) / 60.0
            logger.warning(f"⏳ Restored 4-hour cooldown from database ({remaining_mins:.1f} min remaining)")
        elif self.cooldown_until > time.time() and (not self.cooldown_enabled or self.mode in ("paper", "demo", "testnet", "shadow")):
            logger.info("ℹ️ Persisted cooldown timestamp found but ignored (cooldown disabled in paper/non-live mode)")

    def update_equity(self, current_equity: float):
        """Update high watermark and calculate current daily drawdown."""
        now = time.time()
        # Reset daily high watermark at UTC midnight
        current_day_start = float((int(now) // 86400) * 86400)
        if current_day_start > self.today_start:
            self.today_start = current_day_start
            self.high_watermark = current_equity
            self.daily_drawdown_pct = 0.0
            if self.kill_switches.get("DRAWDOWN_KILL", False):
                self.kill_switches["DRAWDOWN_KILL"] = False
                logger.info("🌅 UTC midnight rollover: Reset daily DRAWDOWN_KILL switch and high watermark")

        if current_equity > self.high_watermark:
            self.high_watermark = current_equity

        if self.high_watermark > 0:
            self.daily_drawdown_pct = max(0.0, (self.high_watermark - current_equity) / self.high_watermark * 100.0)

        # Trigger Drawdown Kill if DD > 5.0%
        if self.daily_drawdown_pct >= 5.0:
            if self.kill_switch_enabled and self.mode == "live":
                self.kill_switches["DRAWDOWN_KILL"] = True
                logger.critical(f"🛑 DRAWDOWN KILL ACTIVATED: Daily Drawdown is {self.daily_drawdown_pct:.2f}% (Limit 5.0%)")
            else:
                logger.warning(f"⚠️ High Drawdown observed: {self.daily_drawdown_pct:.2f}% (Limit 5.0%), but DRAWDOWN_KILL is bypassed in paper/testing mode")

    def get_drawdown_risk_tier(self) -> Tuple[float, bool]:
        """
        Returns (risk_scalar, a_plus_only_flag):
        DD < 2%: normal (1.0, False)
        2-3%: risk reduced (0.50, False)
        3-4%: risk heavily reduced (0.25, False)
        4-5%: A+ only (0.20, True)
        > 5%: Drawdown Kill (0.0, True)
        """
        if not self.kill_switch_enabled or self.mode in ("paper", "demo", "testnet", "shadow"):
            return 1.0, False
        dd = self.daily_drawdown_pct
        if dd < 2.0:
            return 1.0, False
        elif dd < 3.0:
            return 0.50, False
        elif dd < 4.0:
            return 0.25, False
        elif dd < 5.0:
            return 0.20, True
        else:
            return 0.0, True

    def is_cooldown_active(self) -> bool:
        if not self.cooldown_enabled or self.mode in ("paper", "demo", "testnet", "shadow"):
            return False
        return time.time() < self.cooldown_until

    async def record_trade_result(self, pnl: float, current_equity: float):
        """Record trade outcome and update consecutive losses & cooldown."""
        if pnl < 0:
            self.consecutive_losses += 1
            logger.warning(f"Trade Loss recorded. Consecutive losses: {self.consecutive_losses}")
            if self.consecutive_losses >= 2:
                if not self.cooldown_enabled or self.mode in ("paper", "demo", "testnet", "shadow"):
                    logger.info("ℹ️ Consecutive losses >= 2, but 4-hour cooldown is DISABLED in paper/non-live mode.")
                else:
                    self.cooldown_until = time.time() + (4.0 * 3600.0)  # 4-hour cooldown
                    logger.critical("🛑 2 CONSECUTIVE LOSSES: 4-HOUR MANDATORY COOLDOWN ACTIVATED")
        else:
            self.consecutive_losses = 0

        self.update_equity(current_equity)

        # Persist updated state to database
        await self.db.save_governor_state(
            consecutive_losses=self.consecutive_losses,
            cooldown_until=self.cooldown_until,
            high_watermark=self.high_watermark,
            daily_loss=self.daily_drawdown_pct,
            kill_switch=self.kill_switches["DRAWDOWN_KILL"],
        )

    def is_any_kill_switch_active(self) -> Tuple[bool, str]:
        if not self.kill_switch_enabled or self.mode in ("paper", "demo", "testnet", "shadow"):
            return False, ""
        for name, active in self.kill_switches.items():
            if active:
                return True, f"Active Kill Switch: {name}"
        return False, ""
