"""
CLOB API wrapper — order management, prices, positions.
Wraps py-clob-client with error handling, retries, and logging.
"""

import logging
import time
from typing import Optional, Dict, Any, List

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType as ClobOrderType

from src.data.models import OrderBook, Side, OrderType

logger = logging.getLogger(__name__)


class PolymarketCLOB:
    """High-level wrapper around the py-clob-client ClobClient."""

    def __init__(self, client: ClobClient):
        self.client = client

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch the order book for a given token."""
        try:
            book = self.client.get_order_book(token_id)
            bids = [{"price": float(b.price), "size": float(b.size)} for b in (book.bids or [])]
            asks = [{"price": float(a.price), "size": float(a.size)} for a in (book.asks or [])]
            midpoint = float(self.client.get_midpoint(token_id)) if token_id else 0.0
            spread = float(self.client.get_spread(token_id)) if token_id else 0.0

            return OrderBook(
                token_id=token_id,
                bids=bids,
                asks=asks,
                midpoint=midpoint,
                spread=spread,
            )
        except Exception as e:
            logger.error(f"Error fetching order book for {token_id}: {e}")
            return OrderBook(token_id=token_id)

    def get_price(self, token_id: str) -> float:
        """Get the midpoint price for a token."""
        try:
            return float(self.client.get_midpoint(token_id))
        except Exception as e:
            logger.error(f"Error fetching price for {token_id}: {e}")
            return 0.0

    def get_spread(self, token_id: str) -> float:
        """Get the spread for a token."""
        try:
            return float(self.client.get_spread(token_id))
        except Exception as e:
            logger.error(f"Error fetching spread for {token_id}: {e}")
            return 0.0

    # ------------------------------------------------------------------
    # Order Placement
    # ------------------------------------------------------------------

    def _map_order_type(self, order_type: OrderType) -> ClobOrderType:
        """Map our OrderType enum to py-clob-client's."""
        mapping = {
            OrderType.GTC: ClobOrderType.GTC,
            OrderType.GTD: ClobOrderType.GTD,
            OrderType.FOK: ClobOrderType.FOK,
            OrderType.FAK: ClobOrderType.FAK,
        }
        return mapping.get(order_type, ClobOrderType.GTC)

    def place_order(
        self,
        token_id: str,
        side: Side,
        price: float,
        size: float,
        order_type: OrderType = OrderType.GTC,
    ) -> Dict[str, Any]:
        """
        Place an order on the CLOB.
        
        Args:
            token_id: The token to trade
            side: BUY or SELL
            price: Limit price (0.01 to 0.99)
            size: Number of shares
            order_type: GTC, FOK, FAK, GTD
            
        Returns:
            Order response from CLOB API
        """
        try:
            order_args = OrderArgs(
                price=price,
                size=size,
                side=side.value,
                token_id=token_id,
            )

            signed_order = self.client.create_order(order_args)
            resp = self.client.post_order(
                signed_order,
                orderType=self._map_order_type(order_type),
            )
            logger.info(
                f"Order placed: {side.value} {size} shares @ ${price:.4f} "
                f"(token={token_id[:8]}..., type={order_type.value})"
            )
            return resp
        except Exception as e:
            logger.error(f"Order placement failed: {e}")
            raise

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """Cancel an open order."""
        try:
            resp = self.client.cancel(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return resp
        except Exception as e:
            logger.error(f"Cancel failed for {order_id}: {e}")
            raise

    def cancel_all_orders(self) -> Dict[str, Any]:
        """Cancel all open orders."""
        try:
            resp = self.client.cancel_all()
            logger.info("All orders cancelled")
            return resp
        except Exception as e:
            logger.error(f"Cancel all failed: {e}")
            raise

    # ------------------------------------------------------------------
    # Positions & Balance
    # ------------------------------------------------------------------

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """Get all open orders."""
        try:
            orders = self.client.get_orders()
            return orders if isinstance(orders, list) else []
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []

    def get_trades(self) -> List[Dict[str, Any]]:
        """Get trade history."""
        try:
            trades = self.client.get_trades()
            return trades if isinstance(trades, list) else []
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            return []
