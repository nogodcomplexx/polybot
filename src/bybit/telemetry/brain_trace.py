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

    def __init__(self, max_history: int = 150, bus: Optional[Any] = None):
        self.max_history = max_history
        self._history: deque[DecisionTraceStep] = deque(maxlen=max_history)
        self.bus = bus

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
        if self.bus:
            self.bus.publish(step)
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

    def record_trade_approved(self, symbol: str, side: str, qty: float, entry: float, stop: float, leverage: Optional[int] = None):
        """Record trade approval."""
        lev_str = f" | {leverage}x" if leverage else ""
        self.record_step(
            symbol=symbol,
            stage="POLICY",
            status="PASS",
            message=f"TRADE APPROVED: {side} {qty:.3f} {symbol} @ ${entry:.2f} (SL: ${stop:.2f}{lev_str})"
        )

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return most recent steps as dictionaries."""
        items = list(self._history)[-limit:]
        return [item.to_dict() for item in reversed(items)]

    async def seed_from_db(self, db: Any):
        """Seed recent state transitions and closed trades from SQLite into memory trace buffer."""
        try:
            if not hasattr(db, "_db") or db._db is None:
                return

            events = []

            # 1. Fetch recent state transitions
            cursor = await db._db.execute("""
                SELECT timestamp, symbol, from_state, to_state, reason
                FROM bybit_state_transitions
                ORDER BY id DESC LIMIT 20
            """)
            trans_rows = await cursor.fetchall()
            for r in trans_rows:
                ts, sym, f_st, t_st, reason = r
                events.append({
                    "timestamp": float(ts),
                    "symbol": str(sym),
                    "stage": "STATE",
                    "status": "PASS" if t_st != "ERROR" else "VETO",
                    "message": f"State Machine: {f_st} -> {t_st} ({reason})",
                })

            # 2. Fetch recent closed trades
            cursor = await db._db.execute("""
                SELECT timestamp, symbol, side, exit_reason, pnl, fees, exit_price
                FROM bybit_trades
                ORDER BY timestamp DESC LIMIT 10
            """)
            trade_rows = await cursor.fetchall()
            for r in trade_rows:
                ts, sym, side, reason, pnl, fees, exit_p = r
                events.append({
                    "timestamp": float(ts),
                    "symbol": str(sym),
                    "stage": "EXECUTION",
                    "status": "PASS" if float(pnl) >= 0 else "INFO",
                    "message": f"Historical closed trade: {side} {sym} via {reason} @ ${float(exit_p):.4f} (Net PnL: ${float(pnl):+.2f}, Fees: ${float(fees):.2f})",
                })

            # Sort chronologically and seed into _history without republishing to bus
            events.sort(key=lambda x: x["timestamp"])
            for e in events:
                step = DecisionTraceStep(
                    timestamp=e["timestamp"],
                    symbol=e["symbol"],
                    stage=e["stage"],
                    status=e["status"],
                    message=e["message"]
                )
                self._history.append(step)
        except Exception as err:
            pass
