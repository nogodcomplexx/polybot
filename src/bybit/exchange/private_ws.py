"""
Bybit V5 Private WebSocket Client.
Provides authenticated streaming for positions, execution fills, orders, and wallet balance updates
with HMAC-SHA256 signature auth, automatic reconnect, and event dispatch.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Dict, Any, List, Optional, Callable

import websockets

from src.bybit.utils.api_logger import log_api_call

logger = logging.getLogger(__name__)

BYBIT_WS_PRIVATE_MAINNET = "wss://stream.bybit.com/v5/private"
BYBIT_WS_PRIVATE_TESTNET = "wss://stream-testnet.bybit.com/v5/private"
BYBIT_WS_PRIVATE_DEMO = "wss://stream-demo.bybit.com/v5/private"


class BybitPrivateWS:
    """Production authenticated streaming client for Bybit V5 user data."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        demo: bool = False,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.demo = demo
        if demo:
            self.ws_url = BYBIT_WS_PRIVATE_DEMO
        elif testnet:
            self.ws_url = BYBIT_WS_PRIVATE_TESTNET
        else:
            self.ws_url = BYBIT_WS_PRIVATE_MAINNET
        self.running = False
        self.connected = False
        self.authenticated = False
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._task: Optional[asyncio.Task] = None
        self._msg_counter = 0

        # Callbacks
        self._position_callbacks: List[Callable] = []
        self._order_callbacks: List[Callable] = []
        self._execution_callbacks: List[Callable] = []
        self._wallet_callbacks: List[Callable] = []

    def on_position(self, cb: Callable):
        self._position_callbacks.append(cb)

    def on_order(self, cb: Callable):
        self._order_callbacks.append(cb)

    def on_execution(self, cb: Callable):
        self._execution_callbacks.append(cb)

    def on_wallet(self, cb: Callable):
        self._wallet_callbacks.append(cb)

    async def start(self):
        """Start the private WebSocket client."""
        if self.running:
            return
        if not self.api_key or not self.api_secret:
            logger.info("Bybit Private WS not started: API credentials not provided (running in paper/public-only mode)")
            return

        self.running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Bybit Private WS starting...")

    async def stop(self):
        """Gracefully disconnect and terminate."""
        self.running = False
        self.connected = False
        self.authenticated = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Bybit Private WS stopped")

    def _generate_auth_message(self) -> Dict[str, Any]:
        """Generate Bybit V5 private WebSocket authentication payload."""
        expires = int((time.time() + 10) * 1000)
        val = f"GET/realtime{expires}"
        sig = hmac.new(
            self.api_secret.encode("utf-8"),
            val.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return {
            "op": "auth",
            "args": [self.api_key, expires, sig]
        }

    async def _run_loop(self):
        backoff = 1.0
        while self.running:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    self.connected = True
                    self.authenticated = False
                    backoff = 1.0
                    logger.info(f"Connected to Bybit V5 Private WS ({self.ws_url})")

                    # 1. Authenticate
                    auth_msg = self._generate_auth_message()
                    await ws.send(json.dumps(auth_msg))

                    while self.running:
                        try:
                            msg_str = await ws.recv()
                            msg = json.loads(msg_str)
                            await self._handle_message(msg, ws)
                        except websockets.ConnectionClosed:
                            self.connected = False
                            self.authenticated = False
                            logger.warning("Bybit Private WS connection closed, reconnecting...")
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.connected = False
                self.authenticated = False
                logger.error(f"Bybit Private WS connection error: {e}, retrying in {backoff:.1f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

    async def _handle_message(self, msg: Dict[str, Any], ws: Any):
        op = msg.get("op")
        if op == "auth":
            success = msg.get("success", False)
            if success:
                self.authenticated = True
                logger.info("✅ Bybit Private WS authenticated successfully")
                # Subscribe to private topics
                sub_msg = {
                    "op": "subscribe",
                    "args": ["position", "execution", "order", "wallet"]
                }
                await ws.send(json.dumps(sub_msg))
                logger.info("Subscribed to Bybit private streams: position, execution, order, wallet")
            else:
                ret_msg = msg.get("ret_msg", "Unknown auth failure")
                logger.critical(f"🛑 Bybit Private WS authentication failed: {ret_msg}")
            return

        topic = msg.get("topic", "")
        data = msg.get("data")
        if not topic or data is None:
            return

        self._msg_counter += 1
        if self._msg_counter % 25 == 0:
            log_api_call("BybitPrivateWS", f"stream({topic})", True, 1.0)

        # 1. Position stream
        if topic == "position":
            for cb in self._position_callbacks:
                try:
                    res = cb(data)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.error(f"Position callback error: {e}")

        # 2. Execution / fill stream
        elif topic == "execution":
            for cb in self._execution_callbacks:
                try:
                    res = cb(data)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.error(f"Execution callback error: {e}")

        # 3. Order stream
        elif topic == "order":
            for cb in self._order_callbacks:
                try:
                    res = cb(data)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.error(f"Order callback error: {e}")

        # 4. Wallet balance stream
        elif topic == "wallet":
            for cb in self._wallet_callbacks:
                try:
                    res = cb(data)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as e:
                    logger.error(f"Wallet callback error: {e}")
