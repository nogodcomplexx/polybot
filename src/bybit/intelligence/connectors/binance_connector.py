"""
Binance Futures Cross-Exchange Intelligence Connector.
Streams real-time trades, depth, mark prices, funding, and liquidations from Binance USDT-M Futures.
Provides cross-venue confirmation without overriding Bybit execution truth.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any, List, Optional, Tuple, Set
import httpx
import websockets

from src.bybit.intelligence.provider import MarketDataProvider
from src.bybit.intelligence.models import (
    CanonicalSymbol, SymbolMapper, default_symbol_mapper,
    EventType, TradeEvent, OrderBookSnapshot, LiquidationEvent,
    OpenInterestEvent, FundingEvent, ProviderStatus
)
from src.bybit.intelligence.bus import MarketDataBus

logger = logging.getLogger(__name__)


class BinanceFuturesConnector(MarketDataProvider):
    """
    Asynchronous connector for Binance USDT-M Perpetual Futures.
    Provides external price discovery, cross-exchange CVD, and depth confirmation.
    """

    WS_BASE_URL = "wss://fstream.binance.com/stream"
    REST_BASE_URL = "https://fapi.binance.com"

    def __init__(
        self,
        bus: MarketDataBus,
        symbols: Optional[List[CanonicalSymbol]] = None,
        symbol_mapper: Optional[SymbolMapper] = None,
    ):
        super().__init__(provider_name="binance", rate_limit_per_sec=20.0, burst_capacity=40.0)
        self.bus = bus
        self.mapper = symbol_mapper or default_symbol_mapper
        self.subscribed_symbols: List[CanonicalSymbol] = list(symbols or [])

        # State Caches
        self.latest_prices: Dict[str, float] = {}
        self.orderbooks: Dict[str, Dict[str, Any]] = {}
        self.cvd: Dict[str, float] = {}
        self.recent_trades: Dict[str, List[Dict[str, Any]]] = {}
        self.open_interests: Dict[str, float] = {}
        self.funding_rates: Dict[str, float] = {}

        self.running: bool = False
        self._ws_task: Optional[asyncio.Task] = None
        self._current_ws = None
        self._rest_poll_task: Optional[asyncio.Task] = None
        self._http_client: Optional[httpx.AsyncClient] = None

    async def connect(self):
        """Establish Binance WebSocket connection and REST background poller."""
        self.running = True
        self._http_client = httpx.AsyncClient(timeout=10.0)
        self._ws_task = asyncio.create_task(self._run_ws_loop())
        self._rest_poll_task = asyncio.create_task(self._run_rest_poll_loop())
        logger.info("✅ [BINANCE CONNECTOR] Connector initialized and starting streams")

    async def disconnect(self):
        """Gracefully disconnect WebSocket and REST tasks."""
        self.running = False
        if self._current_ws is not None:
            try:
                await self._current_ws.close()
            except Exception:
                pass
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._rest_poll_task:
            self._rest_poll_task.cancel()
            try:
                await self._rest_poll_task
            except asyncio.CancelledError:
                pass
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

        self.health_state.connected = False
        self.health_state.status = ProviderStatus.OFFLINE
        logger.info("[BINANCE CONNECTOR] Disconnected")

    async def subscribe(self, symbols: List[CanonicalSymbol]):
        """Dynamically add symbols to subscription pool."""
        to_add = [s for s in symbols if s not in self.subscribed_symbols]
        if not to_add:
            return
        self.subscribed_symbols.extend(to_add)
        logger.info(f"[BINANCE CONNECTOR] Subscribed to {len(to_add)} symbols: {', '.join(s.binance for s in to_add)}")
        # Trigger quick reconnect with updated streams URL
        if self._current_ws is not None:
            try:
                await self._current_ws.close()
            except Exception:
                pass

    async def unsubscribe(self, symbols: List[CanonicalSymbol]):
        """Dynamically remove symbols."""
        self.subscribed_symbols = [s for s in self.subscribed_symbols if s not in symbols]
        for s in symbols:
            canonical = s.canonical
            self.latest_prices.pop(canonical, None)
            self.orderbooks.pop(canonical, None)
            self.cvd.pop(canonical, None)
            self.recent_trades.pop(canonical, None)
            self.open_interests.pop(canonical, None)
            self.funding_rates.pop(canonical, None)
        if self._current_ws is not None:
            try:
                await self._current_ws.close()
            except Exception:
                pass

    def _build_stream_url(self) -> str:
        """Construct combined stream query parameters."""
        streams = ["!forceOrder@arr"]  # Liquidations across all futures
        for s in self.subscribed_symbols:
            sym_lower = s.binance.lower()
            streams.append(f"{sym_lower}@aggTrade")
            streams.append(f"{sym_lower}@depth20@100ms")
            streams.append(f"{sym_lower}@markPrice@1s")

        return f"{self.WS_BASE_URL}?streams={'/'.join(streams)}"

    async def _run_ws_loop(self):
        """Continuous background WebSocket listener with exponential backoff."""
        while self.running:
            if not self.subscribed_symbols:
                await asyncio.sleep(1.0)
                continue

            url = self._build_stream_url()
            try:
                self.record_reconnect()
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self._current_ws = ws
                    self.record_message()
                    logger.info(f"Connected to Binance Futures WS ({len(self.subscribed_symbols)} symbols)")

                    try:
                        while self.running:
                            try:
                                msg_str = await ws.recv()
                                msg = json.loads(msg_str)
                                self._handle_message(msg)
                            except websockets.ConnectionClosed:
                                logger.warning("Binance Futures WS connection closed, reconnecting...")
                                break
                    finally:
                        self._current_ws = None
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.record_error(str(e))
                await self.sleep_backoff()


    def _handle_message(self, msg: Dict[str, Any]):
        """Route combined stream payloads."""
        stream = msg.get("stream", "")
        data = msg.get("data", {})
        if not data:
            return

        event_type = data.get("e", "")

        # 1. AggTrade Stream
        if event_type == "aggTrade":
            self._handle_agg_trade(data)
        # 2. Depth Stream
        elif event_type == "depthUpdate":
            self._handle_depth(data)
        # 3. Mark Price Stream
        elif event_type == "markPriceUpdate":
            self._handle_mark_price(data)
        # 4. Liquidation Stream
        elif event_type == "forceOrder":
            self._handle_force_order(data)

    def _handle_agg_trade(self, data: Dict[str, Any]):
        """Normalize Binance aggTrade and compute rolling CVD."""
        raw_sym = data.get("s", "").upper()
        canonical_obj = self.mapper.from_binance(raw_sym)
        canonical = canonical_obj.canonical

        price = float(data.get("p", 0))
        qty = float(data.get("q", 0))
        is_buyer_maker = bool(data.get("m", False))
        side = "Sell" if is_buyer_maker else "Buy"
        ts = int(data.get("T", 0)) / 1000.0
        now = time.time()

        self.latest_prices[canonical] = price
        self.record_message(latency_ms=(now - ts) * 1000 if ts > 0 else 0.0)

        # Update CVD
        delta = -qty if is_buyer_maker else qty
        self.cvd[canonical] = self.cvd.get(canonical, 0.0) + delta

        # Track recent trades for velocity
        trades = self.recent_trades.setdefault(canonical, [])
        trades.append({"price": price, "size": qty, "side": side, "time": ts})
        if len(trades) > 300:
            self.recent_trades[canonical] = trades[-300:]

        trade_evt = TradeEvent(
            source="binance",
            canonical_symbol=canonical,
            price=price,
            quantity=qty,
            side=side,
            exchange_timestamp=ts,
            receive_timestamp=now,
        )
        self.bus.publish(EventType.TRADE_UPDATE, trade_evt, symbol=canonical)

    def _handle_depth(self, data: Dict[str, Any]):
        """Normalize Binance L20 orderbook snapshot and publish."""
        raw_sym = data.get("s", "").upper()
        canonical = self.mapper.from_binance(raw_sym).canonical

        raw_bids = [(float(p), float(q)) for p, q in data.get("b", [])]
        raw_asks = [(float(p), float(q)) for p, q in data.get("a", [])]

        if not raw_bids or not raw_asks:
            return

        now = time.time()
        best_bid = raw_bids[0][0]
        best_ask = raw_asks[0][0]
        mid = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        spread_pct = (spread / mid) * 100 if mid > 0 else 0.0

        bid_depth = sum(q for _, q in raw_bids)
        ask_depth = sum(q for _, q in raw_asks)
        obi = (bid_depth / ask_depth) if ask_depth > 0 else 1.0

        # Microprice calculation
        microprice = ((best_ask * bid_depth) + (best_bid * ask_depth)) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else mid

        self.orderbooks[canonical] = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "spread_pct": spread_pct,
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "obi": obi,
            "microprice": microprice,
        }

        snap = OrderBookSnapshot(
            source="binance",
            canonical_symbol=canonical,
            bids=raw_bids,
            asks=raw_asks,
            best_bid=best_bid,
            best_ask=best_ask,
            mid=mid,
            spread=spread,
            spread_pct=spread_pct,
            timestamp=now,
            sequence=int(data.get("u", 0)),
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            obi=obi,
            microprice=microprice,
        )
        self.bus.publish(EventType.ORDERBOOK_UPDATE, snap, symbol=canonical)

    def _handle_mark_price(self, data: Dict[str, Any]):
        """Normalize Binance mark price and funding rate."""
        raw_sym = data.get("s", "").upper()
        canonical = self.mapper.from_binance(raw_sym).canonical
        funding_rate = float(data.get("r", 0) or 0)
        next_funding_time = int(data.get("T", 0)) / 1000.0

        self.funding_rates[canonical] = funding_rate
        now = time.time()

        fund_evt = FundingEvent(
            source="binance",
            canonical_symbol=canonical,
            funding_rate=funding_rate,
            next_funding_time=next_funding_time,
            timestamp=now,
        )
        self.bus.publish(EventType.FUNDING_UPDATE, fund_evt, symbol=canonical)

    def _handle_force_order(self, data: Dict[str, Any]):
        """Normalize Binance futures liquidations."""
        order_data = data.get("o", {})
        raw_sym = order_data.get("s", "").upper()
        canonical = self.mapper.from_binance(raw_sym).canonical

        side = "Buy" if order_data.get("S") == "BUY" else "Sell"
        price = float(order_data.get("p", 0) or order_data.get("ap", 0) or 0)
        qty = float(order_data.get("q", 0) or 0)
        notional = price * qty
        ts = int(order_data.get("T", 0)) / 1000.0

        liq_evt = LiquidationEvent(
            source="binance",
            canonical_symbol=canonical,
            side=side,
            price=price,
            quantity=qty,
            notional=notional,
            timestamp=ts or time.time(),
        )
        self.bus.publish(EventType.LIQUIDATION_UPDATE, liq_evt, symbol=canonical)

    async def _run_rest_poll_loop(self):
        """Poll Binance REST API for Open Interest and historical funding every 30 seconds."""
        while self.running:
            try:
                for sym_obj in list(self.subscribed_symbols):
                    if not self.running:
                        break
                    await self.rate_limiter.acquire(1.0)
                    await self._poll_open_interest(sym_obj)
                await asyncio.sleep(30.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.record_error(f"REST poll error: {e}")
                await asyncio.sleep(10.0)

    async def _poll_open_interest(self, sym_obj: CanonicalSymbol):
        """Fetch real-time Open Interest from Binance REST."""
        if not self._http_client:
            return
        try:
            url = f"{self.REST_BASE_URL}/fapi/v1/openInterest"
            resp = await self._http_client.get(url, params={"symbol": sym_obj.binance})
            if resp.status_code == 200:
                data = resp.json()
                oi = float(data.get("openInterest", 0) or 0)
                now = time.time()
                self.open_interests[sym_obj.canonical] = oi
                price = self.latest_prices.get(sym_obj.canonical, 0.0)

                oi_evt = OpenInterestEvent(
                    source="binance",
                    canonical_symbol=sym_obj.canonical,
                    open_interest=oi,
                    notional_usd=oi * price,
                    exchange_timestamp=int(data.get("time", 0)) / 1000.0,
                    receive_timestamp=now,
                )
                self.bus.publish(EventType.OI_UPDATE, oi_evt, symbol=sym_obj.canonical)
        except Exception as e:
            self.record_error(f"Failed to poll OI for {sym_obj.binance}: {e}")
