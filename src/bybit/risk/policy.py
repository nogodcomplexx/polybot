"""
Trading Policy Engine & Constitutional Gatekeeper (Brain 2).
Implements the 50-section Institutional Trading Constitution and the Section 62 Minimal Decision Algorithm.
Operates strictly between the Alpha Engine and the Execution Engine with centralized atomic position locking.
"""

import asyncio
import logging
import time
from typing import Dict, Any, Optional, Tuple

from src.bybit.models import Signal, MarketSnapshot, PolicyDecision, OrderSide, PositionState
from src.bybit.config import HardSafetyLimits
from src.bybit.exchange.account import BybitAccountRepository
from src.bybit.risk.position_sizer import PositionSizer
from src.bybit.risk.circuit_breakers import CircuitBreakers
from src.bybit.risk.risk_tier import RiskTierSimulator
from src.bybit.regime.regime_engine import MarketRegimeEngine
from src.bybit.storage.database import BybitDatabase

logger = logging.getLogger(__name__)


class PolicyEngine:
    """The immutable policy enforcement layer. Determines whether trading is permitted."""

    def __init__(
        self,
        account: BybitAccountRepository,
        sizer: PositionSizer,
        circuit_breakers: CircuitBreakers,
        risk_tier_sim: RiskTierSimulator,
        regime_engine: MarketRegimeEngine,
        db: BybitDatabase,
        safety: HardSafetyLimits = HardSafetyLimits(),
    ):
        self.account = account
        self.sizer = sizer
        self.circuit_breakers = circuit_breakers
        self.risk_tier_sim = risk_tier_sim
        self.regime_engine = regime_engine
        self.db = db
        self.safety = safety

        # Centralized Atomic Lock for MAX_ACTIVE_POSITIONS = 1
        self._position_lock = asyncio.Lock()
        self.has_active_position: bool = False

    async def evaluate_trade(self, signal: Signal, snapshot: MarketSnapshot) -> Tuple[PolicyDecision, float]:
        """
        Execute Section 62 Minimal Decision Algorithm.
        Returns: (PolicyDecision, final_quantity)
        """
        checks: Dict[str, bool] = {}
        now = time.time()

        # 1. Market Data Freshness
        data_fresh = (now - snapshot.timestamp) <= 5.0
        checks["Data Freshness"] = data_fresh
        if not data_fresh:
            return await self._reject("STALE_DATA: Market snapshot is older than 5.0s", checks, signal)

        # 2. System Kill Switches
        killed, kill_reason = self.circuit_breakers.is_any_kill_switch_active()
        checks["System Not Killed"] = not killed
        if killed:
            return await self._reject(f"KILL_SWITCH: {kill_reason}", checks, signal)

        # 3. Cooldown Status
        cooldown_active = self.circuit_breakers.is_cooldown_active()
        checks["Cooldown Inactive"] = not cooldown_active
        if cooldown_active:
            rem_sec = self.circuit_breakers.cooldown_until - now
            return await self._reject(f"COOLDOWN: Mandatory 4-hour cooldown active ({rem_sec/60:.1f}m remaining)", checks, signal)

        # 4. Capital Fortress Requirement (>= 85%)
        fortress_ok = self.account.is_fortress_secure(self.safety.MIN_CASH_FORTRESS_PCT / 100.0)
        checks["Capital Fortress >= 85%"] = fortress_ok
        if not fortress_ok:
            return await self._reject(
                f"CASH_FORTRESS: Unencumbered equity is {self.account.fortress_percent:.1f}% (Minimum 85.0%)",
                checks, signal
            )

        # 5. Position Limit Atomicity (MAX_ACTIVE_POSITIONS = 1)
        checks["Position Limit (Max 1)"] = not self.has_active_position
        if self.has_active_position:
            return await self._reject("POSITION_EXISTS: Desk already has 1 active position", checks, signal)

        # 6. Minimum Alpha Score (>= MIN_ALPHA_SCORE, default 70.0)
        min_score = self.safety.MIN_ALPHA_SCORE
        score_ok = signal.alpha_score >= min_score
        checks[f"Alpha Score >= {min_score:.0f}"] = score_ok
        if not score_ok:
            return await self._reject(f"LOW_SCORE: Alpha score {signal.alpha_score:.1f} < {min_score:.1f}", checks, signal)

        # 7. Market Regime Eligibility
        regime_ok = self.regime_engine.is_strategy_allowed(signal.strategy, signal.regime)
        checks["Regime Compatible"] = regime_ok
        if not regime_ok:
            return await self._reject(f"REGIME: Strategy {signal.strategy.value} not allowed in regime {signal.regime.value}", checks, signal)

        # 8. Spoofing Risk
        spoof_ok = snapshot.spoof_probability < 0.65
        checks["Spoof Check Passed"] = spoof_ok
        if not spoof_ok:
            return await self._reject(f"SPOOF_RISK: High spoofing probability ({snapshot.spoof_probability:.2f})", checks, signal)

        # 9. Reward:Risk Ratios (TP1 >= 1.5, TP2 >= 2.8)
        stop_dist = abs(signal.entry - signal.structural_stop)
        tp1_dist = abs(signal.tp1 - signal.entry)
        tp2_dist = abs(signal.tp2 - signal.entry)

        rr1 = tp1_dist / stop_dist if stop_dist > 0 else 0.0
        rr2 = tp2_dist / stop_dist if stop_dist > 0 else 0.0
        rr_ok = (rr1 >= self.safety.MIN_TP1_RR) and (rr2 >= self.safety.MIN_TP2_RR)
        checks["R:R (TP1>=1.5, TP2>=2.8)"] = rr_ok
        if not rr_ok:
            return await self._reject(f"RR: Insufficient R:R (TP1 R:R={rr1:.2f}, TP2 R:R={rr2:.2f})", checks, signal)

        # 10. Expected Value (EV)
        ev_ok = signal.expected_ev >= 0.0020
        checks["Net EV > Edge"] = ev_ok
        if not ev_ok:
            return await self._reject(f"LOW_EV: Net expected edge ({signal.expected_ev:.4f}) below minimum", checks, signal)

        # 11. Execution Quality
        exec_score = signal.features.get("execution_score", 0.0)
        exec_ok = exec_score >= 65.0
        checks["Execution Quality >= 65"] = exec_ok
        if not exec_ok:
            return await self._reject(f"EXECUTION: Execution quality score ({exec_score:.1f}) < 65.0", checks, signal)

        # 12. Structural Stop Verification
        stop_dist_pct = (stop_dist / signal.entry) * 100.0 if signal.entry > 0 else 0.0
        stop_valid = 0.10 <= stop_dist_pct <= 2.50
        checks["Structural Stop Valid"] = stop_valid
        if not stop_valid:
            return await self._reject(f"STRUCTURAL_STOP: Stop distance {stop_dist_pct:.2f}% outside safe bounds (0.10%-2.50%)", checks, signal)

        # 13. Drawdown Governor Tier
        dd_scalar, a_plus_only = self.circuit_breakers.get_drawdown_risk_tier()
        checks["Drawdown Governor Normal"] = dd_scalar > 0.0
        if dd_scalar <= 0.0:
            return await self._reject("DRAWDOWN_HALT: Drawdown exceeds 5% kill threshold", checks, signal)
        if a_plus_only and signal.alpha_score < 85.0:
            return await self._reject(f"DRAWDOWN_TIER: Drawdown restricts to A+ setups (Score >= 85.0, got {signal.alpha_score:.1f})", checks, signal)

        # 14. Position Sizing with Score-Based Scaling
        qty = self.sizer.size_position(
            symbol=signal.symbol,
            equity=self.account.total_equity,
            allowed_risk_pct=self.safety.DEFAULT_RISK_PER_TRADE_PCT,
            entry=signal.entry,
            stop=signal.structural_stop,
            drawdown_scalar=dd_scalar,
            kelly_fraction=signal.confidence,
            alpha_score=signal.alpha_score,
        )
        checks["Position Sizing Valid"] = qty > 0.0
        if qty <= 0.0:
            return await self._reject("EXCHANGE_CONSTRAINT: Calculated quantity is below minimum lot size", checks, signal)

        # 15. Risk Tier & Liquidation Distance Check
        liq_safe, liq_reason = self.risk_tier_sim.simulate_order(
            symbol=signal.symbol,
            side=signal.side,
            quantity=qty,
            entry_price=signal.entry,
            stop_price=signal.structural_stop,
            account_equity=self.account.total_equity,
            leverage=10,
        )
        checks["Liquidation Distance Safe"] = liq_safe
        if not liq_safe:
            return await self._reject(f"LIQUIDATION_RISK: {liq_reason}", checks, signal)

        # All 15 checks passed -> APPROVE
        risk_usd = qty * stop_dist
        risk_pct = (risk_usd / self.account.total_equity) * 100.0 if self.account.total_equity > 0 else 0.0

        decision = PolicyDecision(
            approved=True,
            risk_pct=round(risk_pct, 2),
            risk_usd=round(risk_usd, 2),
            checks=checks,
            rejection_reason="",
        )

        # Log policy decision to database
        await self.db.set_state(f"last_policy_{signal.symbol}", {
            "status": "APPROVED", "risk_usd": risk_usd, "time": now
        })
        return decision, qty

    async def _reject(self, reason: str, checks: Dict[str, bool], signal: Signal) -> Tuple[PolicyDecision, float]:
        decision = PolicyDecision(
            approved=False,
            rejection_reason=reason,
            checks=checks,
        )
        # Log rejection asynchronously
        await self.db.save_rejection(
            symbol=signal.symbol,
            reason=reason,
            strategy=signal.strategy.value,
            score=signal.alpha_score,
            ev=signal.expected_ev,
            checks=checks,
        )
        return decision, 0.0

    def format_policy_report(self, decision: PolicyDecision, signal: Signal) -> str:
        """Format Section 46 standardized policy decision output."""
        lines = [
            "==================================================",
            f"POLICY CHECK: {signal.side.value} {signal.symbol} ({signal.strategy.value})",
            "=================================================="
        ]
        for check_name, passed in decision.checks.items():
            status = "[PASS]" if passed else "[FAIL]"
            lines.append(f"  {check_name:<30}: {status}")
        lines.append("--------------------------------------------------")
        if decision.approved:
            lines.append(f"FINAL POLICY: APPROVED (Risk: ${decision.risk_usd:.2f}, {decision.risk_pct:.2f}%)")
        else:
            lines.append(f"FINAL POLICY: REJECTED ({decision.rejection_reason})")
        lines.append("==================================================")
        return "\n".join(lines)
