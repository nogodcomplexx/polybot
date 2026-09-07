"""
CoinGlass Aggregated Derivatives Intelligence Connector.
Polls CoinGlass REST API for aggregate Open Interest, cross-exchange funding,
liquidations, and global Long/Short ratios.
Gracefully degrades without crashing if unavailable or unauthenticated.
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Tuple, Set
import httpx

from src.bybit.intelligence.provider import MarketDataProvider
from src.bybit.intelligence.models import (
    CanonicalSymbol, SymbolMapper, default_symbol_mapper,
    EventType, OpenInterestEvent, FundingEvent, ProviderStatus,
    DataQuality, DataPoint
)
from src.bybit.intelligence.bus import MarketDataBus

logger = logging.getLogger(__name__)


class CoinGlassConnector(MarketDataProvider):
    """
    Contextual Derivatives Intelligence Connector for CoinGlass.
    Tracks global market leverage, cross-exchange OI, and crowd positioning.
    """

    BASE_URL = "https://open-api.coinglass.com/public/v2"

    def __init__(
        self,
        bus: MarketDataBus,
        api_key: str = "",
        poll_interval_sec: float = 60.0,
        symbols: Optional[List[CanonicalSymbol]] = None,
        symbol_mapper: Optional[SymbolMapper] = None,
    ):
        super().__init__(provider_name="coinglass", rate_limit_per_sec=2.0, burst_capacity=5.0)
        self.bus = bus
        self.api_key = api_key
        self.poll_interval = poll_interval_sec
        self.mapper = symbol_mapper or default_symbol_mapper
        self.subscribed_symbols: List[CanonicalSymbol] = list(symbols or [])

        # Cached Derivatives Context
        self.aggregate_oi: Dict[str, float] = {}
        self.oi_change_1h: Dict[str, float] = {}
        self.long_short_ratio: Dict[str, float] = {}
        self.last_poll_time: float = 0.0

        self.running: bool = False
        self._poll_task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def connect(self):
        """Initialize HTTP client and start periodic polling task."""
        self.running = True
        headers = {}
        if self.api_key:
            headers["coinglassSecret"] = self.api_key
            headers["CG-API-KEY"] = self.api_key
            self.health_state.authenticated = True
        else:
            self.health_state.authenticated = False
            logger.info("ℹ️ [COINGLASS CONNECTOR] Running without API key — contextual derivatives will use fallback/neutral data.")

        self._client = httpx.AsyncClient(timeout=15.0, headers=headers)
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("✅ [COINGLASS CONNECTOR] Connector started")

    async def disconnect(self):
        """Cancel poller and close client."""
        self.running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()

        self.health_state.connected = False
        self.health_state.status = ProviderStatus.OFFLINE
        logger.info("[COINGLASS CONNECTOR] Disconnected")

    async def subscribe(self, symbols: List[CanonicalSymbol]):
        to_add = [s for s in symbols if s not in self.subscribed_symbols]
        self.subscribed_symbols.extend(to_add)

    async def unsubscribe(self, symbols: List[CanonicalSymbol]):
        self.subscribed_symbols = [s for s in self.subscribed_symbols if s not in symbols]

    async def _poll_loop(self):
        """Periodic polling cycle with rate limit compliance and error tolerance."""
        while self.running:
            if not self.api_key:
                # Without API key, remain online with degraded status without polling
                self.health_state.connected = True
                self.health_state.status = ProviderStatus.DEGRADED
                await asyncio.sleep(self.poll_interval)
                continue

            try:
                start = time.time()
                for sym_obj in list(self.subscribed_symbols):
                    if not self.running:
                        break
                    await self.rate_limiter.acquire(1.0)
                    await self._fetch_symbol_data(sym_obj)

                self.record_message(latency_ms=(time.time() - start) * 1000)
                self.last_poll_time = time.time()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.record_error(f"CoinGlass polling error: {e}")
                self.health_state.status = ProviderStatus.DEGRADED

            await asyncio.sleep(self.poll_interval)

    async def _fetch_symbol_data(self, sym_obj: CanonicalSymbol):
        """Fetch open interest and long/short ratio from CoinGlass API."""
        if not self._client or not self.api_key:
            return

        try:
            # 1. Fetch Open Interest
            oi_url = f"{self.BASE_URL}/open_interest"
            resp = await self._client.get(oi_url, params={"symbol": sym_obj.coinglass})
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data and isinstance(data, list):
                    total_oi = sum(float(item.get("openInterest", 0) or 0) for item in data)
                    self.aggregate_oi[sym_obj.canonical] = total_oi
                    now = time.time()

                    oi_evt = OpenInterestEvent(
                        source="coinglass",
                        canonical_symbol=sym_obj.canonical,
                        open_interest=total_oi,
                        notional_usd=total_oi,
                        exchange_timestamp=now,
                        receive_timestamp=now,
                    )
                    self.bus.publish(EventType.OI_UPDATE, oi_evt, symbol=sym_obj.canonical)

            # 2. Fetch Long/Short Ratio
            ls_url = f"{self.BASE_URL}/long_short"
            resp_ls = await self._client.get(ls_url, params={"symbol": sym_obj.coinglass})
            if resp_ls.status_code == 200:
                ls_data = resp_ls.json().get("data", [])
                if ls_data and isinstance(ls_data, list):
                    latest = ls_data[-1] if ls_data else {}
                    ratio = float(latest.get("longShortRatio", 1.0) or 1.0)
                    self.long_short_ratio[sym_obj.canonical] = ratio

        except Exception as err:
            self.record_error(f"Failed to fetch CoinGlass data for {sym_obj.canonical}: {err}")
