"""
State Reconciliation & WebSocket Recovery Engine.
Continuously reconciles local position and order states against authoritative Bybit exchange state.
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
from src.bybit.exchange.bybit_client import BybitClient
from src.bybit.exchange.account import BybitAccountRepository
from src.bybit.state.state_machine import PositionStateMachine
from src.bybit.models import PositionState

logger = logging.getLogger(__name__)


class ReconciliationEngine:
    """Guarantees local state matches exchange reality at all times."""

    def __init__(
        self,
        client: BybitClient,
        account: BybitAccountRepository,
        state_machine: PositionStateMachine,
    ):
        self.client = client
        self.account = account
        self.state_machine = state_machine
        self.reconcile_frozen: bool = False

    async def reconcile(self) -> bool:
        """
        Poll authoritative exchange state and reconcile with local state.
        Returns True if state is consistent, False if discrepancy resolved/frozen.
        """
        try:
            # Query exchange positions and wallet balance
            wallet = await self.client.get_wallet_balance(coin="USDT")
            self.account.update_from_wallet_response(wallet)

            positions = await self.client.get_positions(category="linear")
            self.account.update_positions(positions)

            # Check if desk invariant (MAX_ACTIVE_POSITIONS = 1) is respected on exchange
            active_exchange_count = len(self.account.cached_positions)

            if active_exchange_count > 1:
                logger.critical(f"🛑 CRITICAL INVARIANT VIOLATION: Bybit has {active_exchange_count} active positions! Freezing desk.")
                await self.state_machine.transition_to(PositionState.RECONCILING, f"{active_exchange_count} active exchange positions")
                self.reconcile_frozen = True
                return False

            # Check if local state believes desk is FLAT but exchange has an open position
            if self.state_machine.state == PositionState.FLAT and active_exchange_count == 1:
                sym = list(self.account.cached_positions.keys())[0]
                logger.warning(f"⚠️ DISCREPANCY: Local is FLAT but Bybit has open position on {sym}. Freezing and reconciling.")
                await self.state_machine.transition_to(PositionState.RECONCILING, f"Discrepancy on {sym}")
                self.reconcile_frozen = True
                return False

            # Check if local state believes desk is ACTIVE but exchange position is zero
            active_states = {
                PositionState.ACTIVE, PositionState.PROTECTION_PENDING,
                PositionState.TP1_FILLED, PositionState.PROTECTED_RUNNER,
                PositionState.DAMAGE_CONTROL, PositionState.EXITING,
                PositionState.PARTIALLY_FILLED,
            }
            if self.state_machine.state in active_states and active_exchange_count == 0:
                logger.info("ℹ️ Reconciliation: Active position on exchange closed externally. Returning desk to FLAT.")
                await self.state_machine.transition_to(PositionState.CLOSED, "Exchange position closed")
                await self.state_machine.transition_to(PositionState.FLAT, "Desk reset to FLAT")

            self.reconcile_frozen = False
            return True

        except Exception as e:
            logger.error(f"Error during exchange reconciliation: {e}")
            return False
