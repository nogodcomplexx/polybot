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
from src.bybit.models import PositionState, OrderSide, ActivePosition

logger = logging.getLogger(__name__)


class ReconciliationEngine:
    """Guarantees local state matches exchange reality at all times."""

    def __init__(
        self,
        client: BybitClient,
        account: BybitAccountRepository,
        state_machine: PositionStateMachine,
        executor: Optional[Any] = None,
        auto_adopt: bool = True,
    ):
        self.client = client
        self.account = account
        self.state_machine = state_machine
        self.executor = executor
        self.auto_adopt = auto_adopt
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
                if self.state_machine.state != PositionState.RECONCILING:
                    await self.state_machine.transition_to(PositionState.RECONCILING, f"{active_exchange_count} active exchange positions")
                self.reconcile_frozen = True
                return False

            # Check if local state is FLAT or RECONCILING but exchange has an open position
            if (self.state_machine.state in (PositionState.FLAT, PositionState.RECONCILING)) and active_exchange_count == 1:
                sym = list(self.account.cached_positions.keys())[0]
                pos_data = self.account.cached_positions[sym]
                logger.warning(f"⚠️ DISCREPANCY: Local is {self.state_machine.state.value} but Bybit has open position on {sym}. Resolving.")

                if self.auto_adopt:
                    size = float(pos_data.get("size", 0.0) or 0.0)
                    entry_price = float(pos_data.get("entry_price", 0.0) or 0.0)
                    raw_side = pos_data.get("side", "Buy")
                    side = OrderSide.BUY if raw_side == "Buy" else OrderSide.SELL
                    stop_loss = float(pos_data.get("stop_loss", 0.0) or 0.0)
                    take_profit = float(pos_data.get("take_profit", 0.0) or 0.0)

                    # Ensure server-side emergency stop is in place
                    if stop_loss <= 0 and entry_price > 0:
                        stop_loss = entry_price * 0.99 if side == OrderSide.BUY else entry_price * 1.01
                        try:
                            await self.client.set_trading_stop(symbol=sym, stop_loss=stop_loss)
                            logger.warning(f"🛡️ [RECONCILIATION] Set emergency stop loss at ${stop_loss:.4f} for {sym}")
                        except Exception as err:
                            logger.error(f"Failed to set emergency stop loss on Bybit: {err}")

                    # Adopt into executor live_positions if available
                    if self.executor:
                        pos = self.executor.live_positions.get(sym)
                        if not pos:
                            pos = ActivePosition(
                                symbol=sym,
                                side=side,
                                size=size,
                                entry_price=entry_price,
                                stop_price=stop_loss,
                                tp1_price=take_profit if take_profit > 0 else (entry_price * 1.015 if side == OrderSide.BUY else entry_price * 0.985),
                                tp2_price=entry_price * 1.03 if side == OrderSide.BUY else entry_price * 0.97,
                                leverage=int(pos_data.get("leverage", 10)),
                                strategy="reconciled_external",
                                regime="UNKNOWN",
                                client_order_id=f"rec_{sym}_{int(time.time())}",
                                entry_time=time.time(),
                                state=PositionState.ACTIVE,
                                filled_quantity=size,
                                notional=size * entry_price,
                            )
                            self.executor.live_positions[sym] = pos
                        self.state_machine.active_position = pos

                    if self.state_machine.state == PositionState.FLAT:
                        await self.state_machine.transition_to(PositionState.RECONCILING, f"Adopting external position on {sym}")
                    await self.state_machine.transition_to(PositionState.ACTIVE, f"Position on {sym} adopted and secured")
                    self.reconcile_frozen = False
                    logger.info(f"✅ [RECONCILIATION] Successfully adopted {sym} into active desk")
                    return True
                else:
                    # Auto-adopt disabled: Emergency liquidate untracked position
                    logger.critical(f"🛑 [RECONCILIATION EMERGENCY CLOSE] Liquidating untracked position on {sym}")
                    close_side = "Sell" if pos_data.get("side") == "Buy" else "Buy"
                    try:
                        await self.client.cancel_all_orders(symbol=sym)
                        await self.client.place_order(
                            symbol=sym,
                            side=close_side,
                            qty=pos_data["size"],
                            order_type="Market",
                            reduce_only=True,
                        )
                    except Exception as e:
                        logger.error(f"Failed to emergency close untracked position on {sym}: {e}")

                    if self.state_machine.state == PositionState.RECONCILING:
                        await self.state_machine.transition_to(PositionState.FLAT, "Untracked position liquidated")
                    self.reconcile_frozen = False
                    return True

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
