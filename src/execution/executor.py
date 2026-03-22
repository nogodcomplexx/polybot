"""
Execution engine — converts trade signals into CLOB orders with paper trading support.
"""

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from src.config import ExecutionConfig
from src.data.models import (
    TradeSignal, Trade, Position, TradeStatus, Side, OrderType,
)
from src.api.clob_client import PolymarketCLOB
from src.data.db import Database
from src.risk.manager import RiskManager

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Execute trade signals on Polymarket CLOB.
    Supports paper trading (simulated) and live trading.
    """

    def __init__(
        self,
        clob: Optional[PolymarketCLOB],
        db: Database,
        risk: RiskManager,
        config: ExecutionConfig,
        paper_trading: bool = True,
    ):
        self.clob = clob
        self.db = db
        self.risk = risk
        self.config = config
        self.paper_trading = paper_trading

    async def execute_signals(
        self,
        signals: List[TradeSignal],
        positions: List[Position],
    ) -> List[Trade]:
        """Execute a batch of trade signals."""
        executed: List[Trade] = []

        for signal in signals:
            # Validate market is actually tradeable
            if not self._is_market_live(signal):
                logger.info(
                    f"❌ Skipping dead/resolved market: "
                    f"{signal.market.question[:50] if signal.market else 'unknown'}"
                )
                continue

            # Risk check
            approved, reason = self.risk.check_trade(signal, positions)
            if not approved:
                logger.info(f"Trade rejected by risk manager: {reason}")
                continue

            # Size with Kelly
            kelly_size = self.risk.size_position(signal)
            if kelly_size > 0:
                signal.size_usd = min(signal.size_usd, kelly_size)

            if signal.size_usd < 0.50:
                logger.debug(f"Trade too small (${signal.size_usd:.2f}), skipping")
                continue

            # Execute
            trade = await self._execute_single(signal)
            if trade:
                executed.append(trade)
                await self.db.save_trade(trade)

                # Update position tracking
                # In paper mode we simulate maker orders:
                # - OPEN means "posted" but not filled yet (no cash deducted, no position opened).
                # - FILLED means we actually got filled (deduct cash + open position).
                if trade.status == TradeStatus.FILLED.value:
                    pos = Position(
                        condition_id=signal.market.condition_id,
                        token_id=signal.token_id,
                        market_question=signal.market.question,
                        side=signal.side.value,
                        entry_price=signal.target_price,
                        current_price=signal.target_price,
                        size=trade.size,
                        cost_usd=trade.cost_usd,
                        stop_loss_price=signal.stop_loss_price,
                        take_profit_price=signal.take_profit_price,
                        slug=signal.market.slug if signal.market else signal.slug,
                        underlying_symbol=getattr(signal, "underlying_symbol", "") or "",
                        direction=getattr(signal, "direction", "") or "",
                    )
                    await self.db.save_position(pos)
                    self.risk.record_trade(trade.cost_usd)

        logger.info(f"Executed {len(executed)}/{len(signals)} trades")
        return executed

    async def close_position(self, position: Position) -> Optional[Trade]:
        """Close a position by selling."""
        signal = TradeSignal(
            strategy_name=position.strategy_name,
            market=None,  # We don't need the full market for closing
            side=Side.SELL,
            token_id=position.token_id,
            target_price=position.current_price,
            size_usd=position.size * position.current_price,
            order_type=OrderType.GTC,
        )

        trade = await self._execute_single(signal, is_close=True)
        if trade:
            # Calculate P/L
            trade.pnl = (position.current_price - position.entry_price) * position.size
            trade.market_question = position.market_question
            await self.db.save_trade(trade)
            await self.db.delete_position(position.token_id)
            self.risk.record_close(trade.cost_usd)
            if trade.pnl < 0:
                self.risk.record_loss(abs(trade.pnl))
        return trade

    def _is_market_live(self, signal: TradeSignal) -> bool:
        """Validate that the market is actually open and accepting orders."""
        market = signal.market
        if not market:
            return False

        # Must be active and not closed
        if not market.active or market.closed:
            return False

        # End date must be in the future
        if market.end_date:
            try:
                end_dt = datetime.fromisoformat(
                    market.end_date.replace("Z", "+00:00")
                )
                if end_dt < datetime.now(timezone.utc):
                    return False
            except (ValueError, TypeError):
                pass  # If we can't parse the date, let it through

        # Price sanity: must have a real price (not 0 or 1)
        if market.yes_price <= 0.001 or market.yes_price >= 0.999:
            return False

        return True

    async def _execute_single(
        self, signal: TradeSignal, is_close: bool = False
    ) -> Optional[Trade]:
        """Execute a single trade (paper or live)."""
        trade_id = str(uuid.uuid4())[:8]

        if self.paper_trading:
            return self._paper_trade(signal, trade_id)

        return await self._live_trade(signal, trade_id)

    def _paper_trade(self, signal: TradeSignal, trade_id: str) -> Trade:
        """
        Simulate a maker order without placing real orders.

        We create an OPEN paper order (not instantly filled). A background poller will:
        - mark it FILLED if price touches within TTL
        - otherwise CANCELLED
        """

        trade = Trade(
            id=trade_id,
            strategy_name=signal.strategy_name,
            market_question=signal.market.question if signal.market else "",
            condition_id=signal.market.condition_id if signal.market else "",
            token_id=signal.token_id,
            side=signal.side.value,
            order_type=signal.order_type.value,
            price=signal.target_price,
            size=0.0,
            cost_usd=0.0,
            status=TradeStatus.OPEN.value,
            order_id=f"paper_{trade_id}",
            filed_at=0.0,
            is_paper=True,
            slug=signal.market.slug if signal.market else signal.slug,
            underlying_symbol=getattr(signal, "underlying_symbol", "") or "",
            session=getattr(signal, "session", "") or "",
            btc_dominance_pct=float(getattr(signal, "btc_dominance_pct", 0.0) or 0.0),
            btc_dominance_change_1h_pct=float(getattr(signal, "btc_dominance_change_1h_pct", 0.0) or 0.0),
            intended_cost_usd=float(getattr(signal, "size_usd", 0.0) or 0.0),
            model_confidence=float(getattr(signal, "confidence", 0.0) or 0.0),
            model_edge=float(getattr(signal, "edge", 0.0) or 0.0),
            direction=getattr(signal, "direction", "") or "",
        )

        logger.info(
            f"📝 PAPER ORDER: {signal.side.value} "
            f"limit=${signal.target_price:.4f} notional=${signal.size_usd:.2f} "
            f"[{signal.strategy_name}]"
        )
        return trade

    async def _live_trade(self, signal: TradeSignal, trade_id: str) -> Optional[Trade]:
        """Place a real order on the CLOB."""
        if not self.clob:
            logger.error("CLOB client not initialized for live trading")
            return None

        try:
            # Spread / market quality gate (protect against toxic liquidity)
            spread = self.clob.get_spread(signal.token_id)
            if spread and spread > 0:
                # Spread is absolute (e.g. 0.01 = 1c). Convert to % of price.
                denom = signal.target_price if signal.target_price > 0 else 0.0
                if denom > 0:
                    spread_pct = (spread / denom) * 100
                    if spread_pct > max(3.0, self.config.max_slippage_pct * 1.5):
                        logger.warning(
                            f"Spread too wide ({spread_pct:.1f}%), skipping live trade"
                        )
                        return None

            # Slippage check
            current_price = self.clob.get_price(signal.token_id)
            if current_price > 0:
                slippage = abs(current_price - signal.target_price) / signal.target_price * 100
                if slippage > self.config.max_slippage_pct:
                    logger.warning(
                        f"Slippage too high ({slippage:.1f}% > {self.config.max_slippage_pct}%), "
                        f"skipping trade"
                    )
                    return None

            shares = signal.size_usd / signal.target_price if signal.target_price > 0 else 0

            # Place order
            resp = self.clob.place_order(
                token_id=signal.token_id,
                side=signal.side,
                price=signal.target_price,
                size=shares,
                order_type=signal.order_type,
            )

            trade = Trade(
                id=trade_id,
                strategy_name=signal.strategy_name,
                market_question=signal.market.question if signal.market else "",
                condition_id=signal.market.condition_id if signal.market else "",
                token_id=signal.token_id,
                side=signal.side.value,
                order_type=signal.order_type.value,
                price=signal.target_price,
                size=shares,
                cost_usd=signal.size_usd,
                status=TradeStatus.OPEN.value,
                order_id=resp.get("orderID", resp.get("id", "")),
                slug=signal.market.slug if signal.market else signal.slug,
                is_paper=False,
                underlying_symbol=getattr(signal, "underlying_symbol", "") or "",
                session=getattr(signal, "session", "") or "",
                btc_dominance_pct=float(getattr(signal, "btc_dominance_pct", 0.0) or 0.0),
                btc_dominance_change_1h_pct=float(getattr(signal, "btc_dominance_change_1h_pct", 0.0) or 0.0),
                intended_cost_usd=float(getattr(signal, "size_usd", 0.0) or 0.0),
                model_confidence=float(getattr(signal, "confidence", 0.0) or 0.0),
                model_edge=float(getattr(signal, "edge", 0.0) or 0.0),
                direction=getattr(signal, "direction", "") or "",
            )

            logger.info(
                f"🔴 LIVE TRADE: {signal.side.value} {shares:.2f} shares @ "
                f"${signal.target_price:.4f} = ${signal.size_usd:.2f} "
                f"[{signal.strategy_name}]"
            )
            return trade

        except Exception as e:
            logger.error(f"Live trade failed: {e}")
            return None
