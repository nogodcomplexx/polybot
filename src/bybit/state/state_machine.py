"""
Position State Machine & Atomic Lifecycle Controller.
Enforces the 15 explicit states, atomic state transitions, and strict stop immutability.
"""

import logging
import time
from typing import Optional, Dict, Any, Callable
from src.bybit.models import PositionState, ActivePosition
from src.bybit.storage.database import BybitDatabase

logger = logging.getLogger(__name__)


class PositionStateMachine:
    """Manages explicit position states with transition validation and audit logging."""

    VALID_TRANSITIONS = {
        PositionState.FLAT: {PositionState.SIGNAL_DETECTED, PositionState.POLICY_PENDING, PositionState.ENTRY_PENDING, PositionState.RECONCILING, PositionState.KILLED},
        PositionState.SIGNAL_DETECTED: {PositionState.POLICY_PENDING, PositionState.FLAT, PositionState.KILLED},
        PositionState.POLICY_PENDING: {PositionState.ENTRY_PENDING, PositionState.FLAT, PositionState.KILLED},
        PositionState.ENTRY_PENDING: {PositionState.PARTIALLY_FILLED, PositionState.PROTECTION_PENDING, PositionState.FLAT, PositionState.KILLED},
        PositionState.PARTIALLY_FILLED: {PositionState.PROTECTION_PENDING, PositionState.DAMAGE_CONTROL, PositionState.EXITING, PositionState.CLOSED},
        PositionState.PROTECTION_PENDING: {PositionState.ACTIVE, PositionState.DAMAGE_CONTROL, PositionState.EXITING, PositionState.ERROR},
        PositionState.ACTIVE: {PositionState.TP1_FILLED, PositionState.DAMAGE_CONTROL, PositionState.EXITING, PositionState.CLOSED, PositionState.RECONCILING},
        PositionState.TP1_FILLED: {PositionState.PROTECTED_RUNNER, PositionState.EXITING, PositionState.CLOSED},
        PositionState.PROTECTED_RUNNER: {PositionState.CLOSED, PositionState.DAMAGE_CONTROL, PositionState.EXITING, PositionState.RECONCILING},
        PositionState.DAMAGE_CONTROL: {PositionState.EXITING, PositionState.CLOSED, PositionState.ERROR, PositionState.FLAT},
        PositionState.EXITING: {PositionState.CLOSED, PositionState.ERROR},
        PositionState.CLOSED: {PositionState.FLAT},
        PositionState.RECONCILING: {PositionState.ACTIVE, PositionState.FLAT, PositionState.ERROR, PositionState.DAMAGE_CONTROL},
        PositionState.KILLED: {PositionState.FLAT},
        PositionState.ERROR: {PositionState.RECONCILING, PositionState.FLAT},
    }

    def __init__(self, db: BybitDatabase):
        self.db = db
        self.state: PositionState = PositionState.FLAT
        self.active_position: Optional[ActivePosition] = None

    async def transition_to(self, new_state: PositionState, reason: str = ""):
        """Atomically transition state and record audit log."""
        allowed = self.VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            logger.error(f"ILLEGAL STATE TRANSITION ATTEMPT: {self.state.value} -> {new_state.value}")
            return False

        old_state = self.state
        self.state = new_state
        symbol = self.active_position.symbol if self.active_position else "DESK"

        logger.info(f"🔄 [STATE TRANSITION] {symbol}: {old_state.value} -> {new_state.value} ({reason})")

        # Asynchronously log to SQLite database
        await self.db.save_state_transition(
            symbol=symbol,
            from_state=old_state.value,
            to_state=new_state.value,
            reason=reason,
        )
        return True

    def validate_stop_update(self, current_stop: float, proposed_stop: float, is_long: bool) -> bool:
        """
        STOP IMMUTABILITY (Section 9/35):
        Stop may: remain, move closer to entry, move into profit.
        Stop may NOT: move farther away or increase capital at risk.
        """
        if is_long:
            # For long, proposed stop must be >= current stop
            if proposed_stop < current_stop * 0.9999:
                logger.error(f"STOP WIDENING FORBIDDEN (LONG): {current_stop} -> {proposed_stop}")
                return False
        else:
            # For short, proposed stop must be <= current stop
            if proposed_stop > current_stop * 1.0001:
                logger.error(f"STOP WIDENING FORBIDDEN (SHORT): {current_stop} -> {proposed_stop}")
                return False
        return True
