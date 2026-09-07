"""
Brain Decision Trace & Audit Recorder.
Records step-by-step reasoning during candidate evaluation, SMC auditing, and veto gates.
Enables instant debugging of why trades were approved or rejected.
"""

import time
from collections import deque
from typing import Dict, Any, List, Optional
from src.bybit.telemetry.events import DecisionTraceStep


class BrainDecisionTrace:
    """Bounded circular buffer recording atomic decision trace events."""

    def __init__(self, max_history: int = 150):
        self.max_history = max_history
        self._history: deque[DecisionTraceStep] = deque(maxlen=max_history)

    def record_step(
        self,
        symbol: str,
        stage: str,
        status: str,
        message: str,
        requirement: Optional[str] = None,
        actual: Optional[str] = None,
        timestamp: Optional[float] = None
    ) -> DecisionTraceStep:
        """Record an atomic step in the decision trace."""
        step = DecisionTraceStep(
            timestamp=timestamp or time.time(),
            symbol=symbol,
            stage=stage,
            status=status,
            message=message,
            requirement=requirement,
            actual=actual
        )
        self._history.append(step)
        return step

    def record_candidate_found(self, symbol: str, strategy: str, score: float):
        """Record candidate discovery."""
        self.record_step(
            symbol=symbol,
            stage="SCANNER",
            status="INFO",
            message=f"Scanner identified candidate {symbol} ({strategy}) with Alpha {score:.1f}"
        )

    def record_smc_audit(self, symbol: str, checks: Dict[str, str], passed: bool):
        """Record SMC Auditor check results."""
        status_str = "PASS" if passed else "VETO"
        summary = ", ".join(f"{k}: {v}" for k, v in checks.items())
        self.record_step(
            symbol=symbol,
            stage="SMC_AUDITOR",
            status=status_str,
            message=f"SMC Auditor evaluation: {summary}"
        )

    def record_veto(self, symbol: str, rule: str, requirement: str, actual: str):
        """Record specific rejection with requirement vs actual."""
        self.record_step(
            symbol=symbol,
            stage="VETO",
            status="VETO",
            message=f"VETO on {rule}: {actual} vs required {requirement}",
            requirement=requirement,
            actual=actual
        )

    def record_trade_approved(self, symbol: str, side: str, qty: float, entry: float, stop: float):
        """Record trade approval."""
        self.record_step(
            symbol=symbol,
            stage="POLICY",
            status="PASS",
            message=f"TRADE APPROVED: {side} {qty:.3f} {symbol} @ ${entry:.2f} (SL: ${stop:.2f})"
        )

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return most recent steps as dictionaries."""
        items = list(self._history)[-limit:]
        return [item.to_dict() for item in reversed(items)]
