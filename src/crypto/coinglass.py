"""
Coinglass API client — fetches live Open Interest & Long/Short Ratio data
to power the Liquidation Cascade Detection engine.

Endpoints used:
  - /public/v2/open_interest  → Aggregate Open Interest per symbol
  - /public/v2/long_short     → Global Long/Short Ratio per symbol
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://open-api.coinglass.com/public/v2"

# Map our internal Binance symbols → Coinglass symbol names
SYMBOL_MAP = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "SOLUSDT": "SOL",
}


@dataclass
class CoinglassMetrics:
    """Live derivatives metrics for a single symbol."""
    symbol: str
    open_interest_usd: float = 0.0       # Aggregate OI in USD
    oi_change_1h_pct: float = 0.0        # 1-hour OI change %
    long_short_ratio: float = 1.0        # Global L/S ratio (1.0 = balanced)
    long_pct: float = 50.0              # Long account %
    short_pct: float = 50.0             # Short account %
    last_update: float = 0.0


class CoinglassClient:
    """
    Async client that polls Coinglass every 60s for OI & L/S data.
    Designed to be started once and queried from CryptoDataFeed.
    """

    def __init__(self, api_key: str, poll_interval: int = 60):
        self.api_key = api_key
        self.poll_interval = poll_interval
        self.metrics: Dict[str, CoinglassMetrics] = {}
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self):
        """Initialize HTTP client and start background polling."""
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={"coinglassSecret": self.api_key},
        )
        # Initialize empty metrics for each tracked symbol
        for binance_sym, cg_sym in SYMBOL_MAP.items():
            self.metrics[binance_sym] = CoinglassMetrics(symbol=cg_sym)

        # Do one immediate fetch, then schedule background loop
        await self._poll_once()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("🔭 Coinglass client started — polling every %ds", self.poll_interval)

    async def close(self):
        """Shutdown."""
        if self._task:
            self._task.cancel()
        if self._client:
            await self._client.aclose()

    def get_metrics(self, binance_symbol: str) -> Optional[CoinglassMetrics]:
        """Return the latest metrics snapshot for a symbol."""
        return self.metrics.get(binance_symbol)

    # ------------------------------------------------------------------
    # Internal polling
    # ------------------------------------------------------------------

    async def _poll_loop(self):
        """Background loop that refreshes data every poll_interval seconds."""
        while True:
            await asyncio.sleep(self.poll_interval)
            try:
                await self._poll_once()
            except Exception as e:
                logger.error(f"Coinglass poll error: {e}")

    async def _poll_once(self):
        """Fetch OI + L/S ratio for all tracked symbols."""
        await asyncio.gather(
            self._fetch_open_interest(),
            self._fetch_long_short(),
            return_exceptions=True,
        )

    async def _fetch_open_interest(self):
        """GET /public/v2/open_interest for each tracked symbol."""
        if not self._client:
            return

        for binance_sym, cg_sym in SYMBOL_MAP.items():
            try:
                resp = await self._client.get(
                    f"{BASE_URL}/open_interest",
                    params={"symbol": cg_sym, "time_type": 0},
                )
                data = resp.json()

                if data.get("success") and data.get("data"):
                    items = data["data"]
                    # Aggregate total OI across all exchanges
                    total_oi = 0.0
                    for item in items:
                        total_oi += float(item.get("openInterest", 0))

                    metrics = self.metrics[binance_sym]
                    old_oi = metrics.open_interest_usd

                    metrics.open_interest_usd = total_oi
                    if old_oi > 0:
                        metrics.oi_change_1h_pct = ((total_oi - old_oi) / old_oi) * 100
                    metrics.last_update = time.time()

                    logger.debug(
                        f"📊 Coinglass OI [{cg_sym}]: ${total_oi:,.0f} "
                        f"(Δ{metrics.oi_change_1h_pct:+.2f}%)"
                    )
            except Exception as e:
                logger.debug(f"Coinglass OI fetch failed for {cg_sym}: {e}")

    async def _fetch_long_short(self):
        """GET /public/v2/long_short for each tracked symbol."""
        if not self._client:
            return

        for binance_sym, cg_sym in SYMBOL_MAP.items():
            try:
                resp = await self._client.get(
                    f"{BASE_URL}/long_short",
                    params={"symbol": cg_sym, "time_type": 1},
                )
                data = resp.json()

                if data.get("success") and data.get("data"):
                    items = data["data"]
                    if items:
                        # Use the first (most recent) entry
                        latest = items[0] if isinstance(items, list) else items
                        ls_ratio = float(latest.get("longRate", 50)) / max(
                            float(latest.get("shortRate", 50)), 0.01
                        )
                        long_pct = float(latest.get("longRate", 50))
                        short_pct = float(latest.get("shortRate", 50))

                        metrics = self.metrics[binance_sym]
                        metrics.long_short_ratio = round(ls_ratio, 3)
                        metrics.long_pct = long_pct
                        metrics.short_pct = short_pct
                        metrics.last_update = time.time()

                        logger.debug(
                            f"📊 Coinglass L/S [{cg_sym}]: "
                            f"ratio={ls_ratio:.2f} "
                            f"(L:{long_pct:.1f}% / S:{short_pct:.1f}%)"
                        )
            except Exception as e:
                logger.debug(f"Coinglass L/S fetch failed for {cg_sym}: {e}")
