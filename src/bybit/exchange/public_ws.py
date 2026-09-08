"""
Bybit V5 Public Linear WebSocket Client.
Maintains real-time L50 orderbook state, trade stream, ticker updates (mark/index/OI/funding),
and liquidation flows with automatic reconnect and state reset semantics.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any, List, Optional, Callable

import websockets

from src.bybit.utils.api_logger import log_api_call

logger = logging.getLogger(__name__)

BYBIT_WS_PUBLIC_MAINNET = "wss://stream.bybit.com/v5/public/linear"
BYBIT_WS_PUBLIC_TESTNET = "wss://stream-testnet.bybit.com/v5/public/linear"
# Note: Bybit Demo Trading does NOT have public WS streams — use mainnet for public data


class BybitPublicWS:
    """Production streaming client for Bybit V5 Linear Perpetuals."""

    def __init__(self, symbols: List[str], testnet: bool = True, demo: bool = False):
        self.symbols = [s.upper() for s in symbols]
        self.testnet = testnet
        self.demo = demo
        if demo:
            # Demo uses mainnet public streams (demo only has private WS)
            self.ws_url = BYBIT_WS_PUBLIC_MAINNET
        elif testnet:
            self.ws_url = BYBIT_WS_PUBLIC_TESTNET
        else:
            self.ws_url = BYBIT_WS_PUBLIC_MAINNET
        self.running = False
        self.connected = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._task: Optional[asyncio.Task] = None
        self._msg_counter = 0

        # In-memory streaming state
        self.orderbooks: Dict[str, Dict[str, Any]] = {}
        self._orderbook_seq: Dict[str, Optional[int]] = {}
        self._orderbook_valid: Dict[str, bool] = {}
        self._orderbook_resubscribe_in_flight: set = set()
        self.latest_prices: Dict[str, float] = {}
        self.mark_prices: Dict[str, float] = {}
        self.index_prices: Dict[str, float] = {}
        self.open_interests: Dict[str, float] = {}
        self.funding_rates: Dict[str, float] = {}
        self.next_funding_times: Dict[str, float] = {}
        self.tickers: Dict[str, Dict[str, Any]] = {}
        self.recent_trades: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.symbols}
        self.recent_liquidations: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.symbols}

        # Callbacks
        self._trade_callbacks: List[Callable] = []
        self._book_callbacks: List[Callable] = []

    def on_trade(self, cb: Callable):
        self._trade_callbacks.append(cb)

    def on_orderbook(self, cb: Callable):
        self._book_callbacks.append(cb)

    async def start(self):
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info(f"Bybit Public WS starting for {self.symbols}...")

    async def stop(self):
        self.running = False
        self.connected = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Bybit Public WS stopped")

    async def _run_loop(self):
        backoff = 1.0
        while self.running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    self.connected = True
                    backoff = 1.0
                    logger.info(f"Connected to Bybit V5 Public WS ({self.ws_url})")

                    # Subscribe to topics
                    await self._subscribe()

                    while self.running:
                        try:
                            msg_str = await ws.recv()
                            msg = json.loads(msg_str)
                            self._handle_message(msg)
                        except websockets.ConnectionClosed:
                            self.connected = False
                            self._orderbook_valid.clear()
                            self._orderbook_seq.clear()
                            self.orderbooks.clear()
                            logger.warning("Bybit WS connection closed, reconnecting...")
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                logger.error(f"Bybit WS connection error: {e}, retrying in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

    async def _subscribe(self):
        if not self._ws:
            return
        topics = []
        for s in self.symbols:
            topics.append(f"orderbook.50.{s}")
            topics.append(f"publicTrade.{s}")
            topics.append(f"tickers.{s}")
            topics.append(f"liquidation.{s}")

        for i in range(0, len(topics), 10):
            batch = topics[i:i+10]
            await self._ws.send(json.dumps({"op": "subscribe", "args": batch}))
        logger.info(f"Subscribed to {len(topics)} Bybit linear streams across {len(self.symbols)} symbols")

    async def subscribe_symbols(self, symbols: List[str]):
        """Dynamically subscribe to new symbols."""
        to_add = [s.upper() for s in symbols if s.upper() not in self.symbols]
        if not to_add:
            return
        self.symbols.extend(to_add)
        if self._ws and self.connected:
            topics = []
            for s in to_add:
                topics.append(f"orderbook.50.{s}")
                topics.append(f"publicTrade.{s}")
                topics.append(f"tickers.{s}")
                topics.append(f"liquidation.{s}")
            for i in range(0, len(topics), 10):
                batch = topics[i:i+10]
                await self._ws.send(json.dumps({"op": "subscribe", "args": batch}))
            logger.info(f"Dynamically subscribed to {len(to_add)} new symbols: {', '.join(to_add)}")

    async def unsubscribe_symbols(self, symbols: List[str]):
        """Dynamically unsubscribe from removed symbols and clean up caches."""
        to_remove = [s.upper() for s in symbols if s.upper() in self.symbols]
        if not to_remove:
            return
        for s in to_remove:
            self.symbols.remove(s)
            self.orderbooks.pop(s, None)
            self.latest_prices.pop(s, None)
            self.mark_prices.pop(s, None)
            self.index_prices.pop(s, None)
        if self._ws and self.connected:
            topics = []
            for s in to_remove:
                topics.append(f"orderbook.50.{s}")
                topics.append(f"publicTrade.{s}")
                topics.append(f"tickers.{s}")
                topics.append(f"liquidation.{s}")
            for i in range(0, len(topics), 10):
                batch = topics[i:i+10]
                await self._ws.send(json.dumps({"op": "unsubscribe", "args": batch}))
            logger.info(f"Dynamically unsubscribed from {len(to_remove)} symbols: {', '.join(to_remove)}")


    def _handle_message(self, msg: Dict[str, Any]):
        topic = msg.get("topic", "")
        data = msg.get("data")
        if not topic or not data:
            return

        self._msg_counter += 1
        if self._msg_counter % 50 == 0:
            log_api_call("BybitWS", f"stream({topic.split('.')[0]})", True, 1.0)

        # 1. Orderbook stream (L50 snapshot & deltas)
        if topic.startswith("orderbook."):
            symbol = topic.split(".")[-1]
            book_type = msg.get("type", "snapshot")
            self._update_orderbook(symbol, data, is_snapshot=(book_type == "snapshot"))

        # 2. Public Trades stream
        elif topic.startswith("publicTrade."):
            symbol = topic.split(".")[-1]
            if isinstance(data, list):
                for t in data:
                    price = float(t.get("p", 0))
                    size = float(t.get("v", 0))
                    side = t.get("S", "")  # "Buy" or "Sell"
                    ts = int(t.get("T", 0)) / 1000
                    if price > 0:
                        self.latest_prices[symbol] = price
                        trades = self.recent_trades.setdefault(symbol, [])
                        trades.append({"price": price, "size": size, "side": side, "time": ts})
                        if len(trades) > 200:
                            self.recent_trades[symbol] = trades[-200:]

                        for cb in self._trade_callbacks:
                            try:
                                cb(symbol, price, size, side, ts)
                            except Exception as err:
                                logger.error(f"Trade callback error: {err}")

        # 3. Tickers stream
        elif topic.startswith("tickers."):
            symbol = topic.split(".")[-1]
            if isinstance(data, dict):
                self.tickers[symbol] = data
                if "lastPrice" in data and data["lastPrice"]:
                    self.latest_prices[symbol] = float(data["lastPrice"])
                if "markPrice" in data and data["markPrice"]:
                    self.mark_prices[symbol] = float(data["markPrice"])
                if "indexPrice" in data and data["indexPrice"]:
                    self.index_prices[symbol] = float(data["indexPrice"])
                if "openInterest" in data and data["openInterest"]:
                    self.open_interests[symbol] = float(data["openInterest"])
                if "fundingRate" in data and data["fundingRate"]:
                    self.funding_rates[symbol] = float(data["fundingRate"])
                if "nextFundingTime" in data and data["nextFundingTime"]:
                    self.next_funding_times[symbol] = int(data["nextFundingTime"]) / 1000

        # 4. Liquidation stream
        elif topic.startswith("liquidation."):
            symbol = topic.split(".")[-1]
            if isinstance(data, dict):
                liqs = self.recent_liquidations.setdefault(symbol, [])
                liqs.append(data)
                if len(liqs) > 50:
                    self.recent_liquidations[symbol] = liqs[-50:]

    def is_orderbook_valid(self, symbol: str) -> bool:
        """Check if local orderbook has verified sequence continuity and fresh non-crossed state."""
        symbol = symbol.upper()
        if not self._orderbook_valid.get(symbol, False):
            return False
        book = self.orderbooks.get(symbol)
        if not book or not book.get("bids") or not book.get("asks"):
            return False
        if time.time() - book.get("ts", 0) > 5.0:
            return False
        return True

    def _schedule_resubscribe(self, symbol: str):
        """Schedule an asynchronous resubscribe task to fetch a fresh snapshot from Bybit."""
        symbol = symbol.upper()
        if symbol in self._orderbook_resubscribe_in_flight:
            return
        self._orderbook_resubscribe_in_flight.add(symbol)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.resubscribe_orderbook(symbol))
        except RuntimeError:
            pass

    async def resubscribe_orderbook(self, symbol: str):
        """Unsubscribe and re-subscribe to orderbook.50 to force Bybit to emit a fresh snapshot."""
        symbol = symbol.upper()
        try:
            if self._ws and self.connected:
                topic = f"orderbook.50.{symbol}"
                await self._ws.send(json.dumps({"op": "unsubscribe", "args": [topic]}))
                await asyncio.sleep(0.05)
                await self._ws.send(json.dumps({"op": "subscribe", "args": [topic]}))
                logger.info(f"🔄 Resubscribed to {topic} to receive fresh snapshot")
        except Exception as e:
            logger.error(f"Failed to resubscribe to {symbol} orderbook: {e}")
        finally:
            self._orderbook_resubscribe_in_flight.discard(symbol)

    def _update_orderbook(self, symbol: str, data: Dict[str, Any], is_snapshot: bool):
        """Maintain L50 orderbook with strict Bybit V5 sequence/update ID continuity."""
        symbol = symbol.upper()
        curr_u = data.get("u")
        if curr_u is None:
            curr_u = data.get("seq")

        if is_snapshot or symbol not in self.orderbooks:
            if not is_snapshot and symbol not in self.orderbooks:
                # Delta received without prior snapshot! Cannot apply.
                logger.warning(f"⚠️ Delta received for {symbol} without prior snapshot. Invaliding book.")
                self._orderbook_valid[symbol] = False
                self._orderbook_seq[symbol] = None
                self._schedule_resubscribe(symbol)
                return

            # Full snapshot reset
            bids = {float(p): float(s) for p, s in data.get("b", [])}
            asks = {float(p): float(s) for p, s in data.get("a", [])}
            self.orderbooks[symbol] = {"bids": bids, "asks": asks, "ts": data.get("t", time.time())}
            self._orderbook_seq[symbol] = curr_u
            self._orderbook_valid[symbol] = True
            logger.info(f"📚 [ORDERBOOK SNAPSHOT] Established valid book for {symbol} (u={curr_u}, bids={len(bids)}, asks={len(asks)})")
        else:
            prev_u = self._orderbook_seq.get(symbol)
            if curr_u is not None and prev_u is not None:
                if curr_u == prev_u + 1:
                    # Valid sequential continuity
                    self._orderbook_seq[symbol] = curr_u
                elif curr_u <= prev_u:
                    # Duplicate or out-of-order old delta -> drop without corrupting state
                    logger.debug(f"Ignoring duplicate/old delta for {symbol} (curr_u={curr_u} <= prev_u={prev_u})")
                    return
                elif curr_u > prev_u + 1:
                    # SEQUENCE GAP! Missing delta N+1
                    logger.critical(f"🛑 [ORDERBOOK GAP] Sequence gap on {symbol}: expected {prev_u + 1}, got {curr_u}. Invalidating book.")
                    self._orderbook_valid[symbol] = False
                    self.orderbooks.pop(symbol, None)
                    self._orderbook_seq[symbol] = None
                    self._schedule_resubscribe(symbol)
                    return
            elif curr_u is not None:
                self._orderbook_seq[symbol] = curr_u

            book = self.orderbooks[symbol]
            for p, s in data.get("b", []):
                p_flt, s_flt = float(p), float(s)
                if s_flt == 0:
                    book["bids"].pop(p_flt, None)
                else:
                    book["bids"][p_flt] = s_flt

            for p, s in data.get("a", []):
                p_flt, s_flt = float(p), float(s)
                if s_flt == 0:
                    book["asks"].pop(p_flt, None)
                else:
                    book["asks"][p_flt] = s_flt
            book["ts"] = data.get("t", time.time())

            # Prune levels outside top 50 to prevent phantom level accumulation and memory bloat
            if len(book["bids"]) > 75:
                top_bids = sorted(book["bids"].items(), key=lambda x: x[0], reverse=True)[:50]
                book["bids"] = dict(top_bids)
            if len(book["asks"]) > 75:
                top_asks = sorted(book["asks"].items(), key=lambda x: x[0])[:50]
                book["asks"] = dict(top_asks)

            self._orderbook_valid[symbol] = True

        for cb in self._book_callbacks:
            try:
                cb(symbol)
            except Exception as err:
                logger.error(f"Book callback error: {err}")

    def get_orderbook_summary(self, symbol: str) -> Dict[str, Any]:
        """Compute top-of-book, spread, and OBI from current L50 cache."""
        symbol = symbol.upper()
        book = self.orderbooks.get(symbol)
        if not book or not book.get("bids") or not book.get("asks"):
            return {
                "best_bid": 0.0, "best_ask": 0.0, "mid_price": 0.0,
                "spread": 0.0, "spread_pct": 0.0, "orderbook_imbalance": 1.0,
                "bid_depth": 0.0, "ask_depth": 0.0, "microprice": 0.0,
                "is_valid": False,
            }

        sorted_bids = sorted(book["bids"].items(), key=lambda x: x[0], reverse=True)
        sorted_asks = sorted(book["asks"].items(), key=lambda x: x[0])

        best_bid = sorted_bids[0][0]
        best_ask = sorted_asks[0][0]

        # Crossed orderbook check: sequence gap or corruption
        if best_bid >= best_ask:
            logger.warning(f"Crossed orderbook detected on {symbol}: Bid ${best_bid} >= Ask ${best_ask}. Invalidating cache.")
            self._orderbook_valid[symbol] = False
            self.orderbooks.pop(symbol, None)
            self._orderbook_seq[symbol] = None
            self._schedule_resubscribe(symbol)
            return {
                "best_bid": 0.0, "best_ask": 0.0, "mid_price": 0.0,
                "spread": 0.0, "spread_pct": 0.0, "orderbook_imbalance": 1.0,
                "bid_depth": 0.0, "ask_depth": 0.0, "microprice": 0.0,
                "is_valid": False,
            }

        if not self._orderbook_valid.get(symbol, False):
            return {
                "best_bid": 0.0, "best_ask": 0.0, "mid_price": 0.0,
                "spread": 0.0, "spread_pct": 0.0, "orderbook_imbalance": 1.0,
                "bid_depth": 0.0, "ask_depth": 0.0, "microprice": 0.0,
                "is_valid": False,
            }

        mid_price = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        spread_pct = (spread / mid_price) * 100 if mid_price > 0 else 0.0

        bid_vol = sum(v for _, v in sorted_bids[:50])
        ask_vol = sum(v for _, v in sorted_asks[:50])
        obi = (bid_vol / ask_vol) if ask_vol > 0 else 1.0

        # Microprice calculation: (Ask * BidVol + Bid * AskVol) / (BidVol + AskVol)
        total_vol = bid_vol + ask_vol
        microprice = ((best_ask * bid_vol + best_bid * ask_vol) / total_vol) if total_vol > 0 else mid_price

        return {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": mid_price,
            "spread": spread,
            "spread_pct": spread_pct,
            "orderbook_imbalance": round(obi, 3),
            "bid_depth": bid_vol,
            "ask_depth": ask_vol,
            "microprice": round(microprice, 4),
            "is_valid": True,
        }
