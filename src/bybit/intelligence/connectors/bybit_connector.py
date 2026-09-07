"""
Authoritative Bybit V5 Perpetual Futures Connector.
Connects Bybit Public WS, Private WS, and REST into normalized MarketDataBus streams.
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Tuple

from src.bybit.intelligence.provider import MarketDataProvider
from src.bybit.intelligence.models import (
    CanonicalSymbol, SymbolMapper, default_symbol_mapper,
    EventType, TradeEvent, OrderBookSnapshot, LiquidationEvent,
    OpenInterestEvent, FundingEvent, ProviderStatus
)
from src.bybit.intelligence.bus import MarketDataBus
from src.bybit.exchange.bybit_client import BybitClient
from src.bybit.exchange.public_ws import BybitPublicWS

logger = logging.getLogger(__name__)


class BybitConnector(MarketDataProvider):
    """Authoritative Execution & Local Microstructure Connector for Bybit V5."""

    def __init__(
        self,
        client: BybitClient,
        ws: BybitPublicWS,
        bus: MarketDataBus,
        symbol_mapper: Optional[SymbolMapper] = None,
    ):
        super().__init__(provider_name="bybit", rate_limit_per_sec=20.0, burst_capacity=40.0)
        self.client = client
        self.ws = ws
        self.bus = bus
        self.mapper = symbol_mapper or default_symbol_mapper
        self.subscribed_symbols: List[CanonicalSymbol] = []

        # Hook WS callbacks
        self.ws.on_trade(self._handle_trade)
        self.ws.on_orderbook(self._handle_orderbook)

    async def connect(self):
        """Start underlying WebSocket feed and verify connection."""
        await self.ws.start()
        # Ping server time to check REST connection and latency
        try:
            start = time.time()
            drift = await self.client.sync_server_time()
            latency = (time.time() - start) * 1000
            self.record_message(latency_ms=latency)
            self.health_state.authenticated = bool(self.client.api_key)
            logger.info("✅ [BYBIT CONNECTOR] Connected and synchronized")
        except Exception as e:
            self.record_error(str(e))

    async def disconnect(self):
        """Stop feeds cleanly."""
        await self.ws.stop()
        self.health_state.connected = False
        self.health_state.status = ProviderStatus.OFFLINE
        logger.info("[BYBIT CONNECTOR] Disconnected")

    async def subscribe(self, symbols: List[CanonicalSymbol]):
        """Subscribe to live streams for canonical symbols."""
        bybit_symbols = [s.bybit for s in symbols if s not in self.subscribed_symbols]
        if not bybit_symbols:
            return
        self.subscribed_symbols.extend([s for s in symbols if s not in self.subscribed_symbols])
        await self.ws.subscribe_symbols(bybit_symbols)
        logger.info(f"[BYBIT CONNECTOR] Subscribed to {len(bybit_symbols)} symbols: {', '.join(bybit_symbols)}")

    async def unsubscribe(self, symbols: List[CanonicalSymbol]):
        """Unsubscribe from live streams."""
        bybit_symbols = [s.bybit for s in symbols if s in self.subscribed_symbols]
        if not bybit_symbols:
            return
        self.subscribed_symbols = [s for s in self.subscribed_symbols if s not in symbols]
        await self.ws.unsubscribe_symbols(bybit_symbols)
        logger.info(f"[BYBIT CONNECTOR] Unsubscribed from {len(bybit_symbols)} symbols: {', '.join(bybit_symbols)}")

    def _handle_trade(self, symbol: str, price: float, size: float, side: str, ts: float):
        """Normalize trade and publish onto MarketDataBus."""
        now = time.time()
        self.record_message(latency_ms=(now - ts) * 1000 if ts > 0 else 0.0)

        canonical = self.mapper.from_bybit(symbol).canonical
        trade_evt = TradeEvent(
            source="bybit",
            canonical_symbol=canonical,
            price=price,
            quantity=size,
            side=side.capitalize(),
            exchange_timestamp=ts,
            receive_timestamp=now,
        )
        self.bus.publish(EventType.TRADE_UPDATE, trade_evt, symbol=canonical)

    def _handle_orderbook(self, symbol: str):
        """Normalize orderbook summary and publish onto MarketDataBus."""
        now = time.time()
        self.record_message()

        canonical = self.mapper.from_bybit(symbol).canonical
        summary = self.ws.get_orderbook_summary(symbol)

        book_raw = self.ws.orderbooks.get(symbol, {})
        raw_bids = sorted(book_raw.get("bids", {}).items(), key=lambda x: x[0], reverse=True)[:50]
        raw_asks = sorted(book_raw.get("asks", {}).items(), key=lambda x: x[0])[:50]

        book_snap = OrderBookSnapshot(
            source="bybit",
            canonical_symbol=canonical,
            bids=raw_bids,
            asks=raw_asks,
            best_bid=summary["best_bid"],
            best_ask=summary["best_ask"],
            mid=summary["mid_price"],
            spread=summary["spread"],
            spread_pct=summary["spread_pct"],
            timestamp=now,
            sequence=int(book_raw.get("ts", 0)),
            bid_depth=summary["bid_depth"],
            ask_depth=summary["ask_depth"],
            obi=summary["orderbook_imbalance"],
            microprice=summary["microprice"],
        )
        self.bus.publish(EventType.ORDERBOOK_UPDATE, book_snap, symbol=canonical)
