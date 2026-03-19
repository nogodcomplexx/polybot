"""
Binance real-time crypto data feed — fetches 5-minute OHLCV candles.
No API key required (public endpoint).
"""

import asyncio
import logging
import time
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional

import httpx
import websockets

logger = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.com/api/v3"
BINANCE_FUTURES_BASE = "https://fapi.binance.com/fapi/v1"


@dataclass
class Candle:
    """A single OHLCV candlestick."""
    timestamp: float       # Open time (unix seconds)
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: float      # Close time (unix seconds)
    quote_volume: float    # Quote asset volume
    trades: int            # Number of trades


@dataclass
class CryptoSnapshot:
    """Current state of a crypto pair across multiple timeframes."""
    symbol: str
    price: float
    candles_1m: List[Candle] = field(default_factory=list)
    candles_5m: List[Candle] = field(default_factory=list)
    candles_15m: List[Candle] = field(default_factory=list)
    # Coinglass derivatives data
    open_interest_usd: float = 0.0
    oi_change_pct: float = 0.0
    long_short_ratio: float = 1.0

    # Phase 26: Public API Data Fusion
    mempool_vbytes: int = 0               # Mempool.space pending vBytes (Network congestion)
    mempool_fast_fee: int = 0             # Mempool.space next block fee rate
    coingecko_trending_score: int = 0     # CoinGecko Rank (0 if not trending)
    wsb_sentiment_score: float = 0.0      # Reddit WallstreetBets momentum (-1.0 to 1.0)

    # Macro: market structure (CoinGecko global market-cap %)
    btc_dominance_pct: float = 0.0
    eth_dominance_pct: float = 0.0
    btc_dominance_change_1h_pct: float = 0.0

    # Futures microstructure (Binance public futures endpoints)
    funding_rate: float = 0.0           # lastFundingRate (e.g. 0.0001 = 0.01%)
    basis_pct: float = 0.0              # (mark - index) / index * 100

    last_refresh: float = 0.0


class CryptoDataFeed:
    """
    Fetches real-time crypto market data from Binance public API.
    Supports multiple symbols and caches candles for indicator calculation.
    """

    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]

    def __init__(self):
        self.client: Optional[httpx.AsyncClient] = None
        self.snapshots: Dict[str, CryptoSnapshot] = {}
        self._wss_tasks: List[asyncio.Task] = []
        self._refresh_interval = 30  # seconds
        self._dominance_history: List[tuple[float, float]] = []  # [(timestamp, btc_dom_pct)]

    async def start(self):
        """Initialize the HTTP client and start WebSocket streams."""
        self.client = httpx.AsyncClient(timeout=15.0)
        logger.info("📡 CryptoDataFeed started (Binance public API)")
        
        # Initial bootstrap via REST
        await self.refresh()
        await self._poll_public_apis()
        
        # Start background WSS listeners
        for symbol in self.SYMBOLS:
            t = asyncio.create_task(self._listen_wss(symbol))
            self._wss_tasks.append(t)
            
        # Start background REST pollers
        self._wss_tasks.append(asyncio.create_task(self._public_api_loop()))

    async def _listen_wss(self, symbol: str):
        """Continuous background loop for Binance WSS using combined streams."""
        sym = symbol.lower()
        streams = f"{sym}@kline_1m/{sym}@kline_5m/{sym}@kline_15m"
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"
        
        while True:
            try:
                logger.info(f"🔗 Connecting MTF WSS stream for {symbol}...")
                async with websockets.connect(url) as ws:
                    logger.info(f"✅ Connected to MTF WSS: {symbol}")
                    async for msg in ws:
                        self._handle_wss_msg(symbol, json.loads(msg))
            except Exception as e:
                logger.error(f"MTF WSS disconnected for {symbol}: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    def _handle_wss_msg(self, symbol: str, msg: dict):
        """Parse strict live WSS combined klines into memory."""
        # Handle combined stream format
        if "data" not in msg:
            return
        data = msg["data"]
            
        if "k" not in data:
            return
            
        k = data["k"]
        interval = k["i"]
        
        snap = self.snapshots.get(symbol)
        if not snap:
            return
            
        # Select the correct list tracking based on interval
        if interval == "1m":
            candle_list = snap.candles_1m
        elif interval == "5m":
            candle_list = snap.candles_5m
        elif interval == "15m":
            candle_list = snap.candles_15m
        else:
            return

        if not candle_list:
            return

        t = k["t"] / 1000
        
        # Parse live candle slice
        live_candle = Candle(
            timestamp=t,
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            close_time=k["T"] / 1000,
            quote_volume=float(k["q"]),
            trades=int(k["n"])
        )

        last_candle = candle_list[-1]
        if t == last_candle.timestamp:
            candle_list[-1] = live_candle
        elif t > last_candle.timestamp:
            candle_list.append(live_candle)
            
        # Clamp length
        if interval == "1m": snap.candles_1m = snap.candles_1m[-500:]
        elif interval == "5m": snap.candles_5m = snap.candles_5m[-500:]
        elif interval == "15m": snap.candles_15m = snap.candles_15m[-500:]
            
        snap.price = live_candle.close
        snap.last_refresh = time.time()

    async def close(self):
        """Close clients and cancel tasks."""
        if self.client:
            await self.client.aclose()
        for t in self._wss_tasks:
            t.cancel()

    async def refresh(self):
        """Fetch latest MTF candles for all tracked symbols via REST (only for bootstrap)."""
        if not self.client:
            return

        intervals = ["1m", "5m", "15m"]
        for symbol in self.SYMBOLS:
            for interval in intervals:
                try:
                    await self._fetch_candles(symbol, interval)
                except Exception as e:
                    logger.error(f"Failed to fetch {symbol} {interval} candles: {e}")

    async def _public_api_loop(self):
        """Continuously pulls orthogonal data vectors from external Public APIs."""
        while True:
            await asyncio.sleep(60) # Poll once a minute (respects rate limits)
            await self._poll_public_apis()

    async def _poll_public_apis(self):
        """Fetches Mempool, CoinGecko, and WallstreetBets sentiment APIs."""
        try:
            # 1. Mempool.space API (Bitcoin On-chain Congestion)
            mempool_r = await self.client.get("https://mempool.space/api/mempool", timeout=5.0)
            mempool_fee_r = await self.client.get("https://mempool.space/api/v1/fees/recommended", timeout=5.0)
            vbytes = mempool_r.json().get('vsize', 0) if mempool_r.status_code == 200 else 0
            fast_fee = mempool_fee_r.json().get('fastestFee', 0) if mempool_fee_r.status_code == 200 else 0
            
            # 2. CoinGecko API (Global Trending Coins)
            cg_r = await self.client.get("https://api.coingecko.com/api/v3/search/trending", timeout=5.0)
            trending_symbols = []
            if cg_r.status_code == 200:
                coins = cg_r.json().get('coins', [])
                for i, coin in enumerate(coins):
                    sym = coin.get('item', {}).get('symbol', '').upper()
                    if sym:
                        trending_symbols.append((sym, i + 1)) # Symbol, Rank

            # 2b. CoinGecko Global (BTC/ETH dominance)
            btc_dom = 0.0
            eth_dom = 0.0
            dom_change_1h = 0.0
            global_r = await self.client.get("https://api.coingecko.com/api/v3/global", timeout=5.0)
            if global_r.status_code == 200:
                pct = (global_r.json().get("data", {}) or {}).get("market_cap_percentage", {}) or {}
                try:
                    btc_dom = float(pct.get("btc", 0.0))
                    eth_dom = float(pct.get("eth", 0.0))
                except Exception:
                    btc_dom = 0.0
                    eth_dom = 0.0

                now = time.time()
                if btc_dom > 0:
                    self._dominance_history.append((now, btc_dom))
                    # keep ~2h history
                    cutoff = now - 2 * 3600
                    self._dominance_history = [(t, d) for (t, d) in self._dominance_history if t >= cutoff]

                    # nearest point >= 1h ago
                    one_h_ago = now - 3600
                    past = None
                    for t, d in self._dominance_history:
                        if t <= one_h_ago:
                            past = (t, d)
                    if past:
                        dom_change_1h = btc_dom - past[1]

            # 3. WallstreetBets Reddit API 
            wsb_r = await self.client.get("https://dashboard.nbshare.io/api/v1/apps/reddit", timeout=5.0)
            wsb_data = wsb_r.json() if wsb_r.status_code == 200 else []
            wsb_map = {}
            for item in wsb_data:
                tick = item.get('ticker', '').upper()
                sent = str(item.get('sentiment', 'Neutral'))
                score = 1.0 if sent == 'Bullish' else (-1.0 if sent == 'Bearish' else 0.0)
                wsb_map[tick] = score

            # Apply to snapshots mapped to Binance pairs
            for sym, snap in self.snapshots.items():
                base_coin = sym.replace("USDT", "")

                snap.mempool_vbytes = vbytes
                snap.mempool_fast_fee = fast_fee

                # CoinGecko Rank
                cg_rank = next((rank for c_sym, rank in trending_symbols if c_sym == base_coin), 0)
                snap.coingecko_trending_score = cg_rank

                # WSB Sentiment
                snap.wsb_sentiment_score = wsb_map.get(base_coin, 0.0)

                # Dominance macro
                snap.btc_dominance_pct = btc_dom
                snap.eth_dominance_pct = eth_dom
                snap.btc_dominance_change_1h_pct = dom_change_1h

                # Futures microstructure (funding + basis) for majors only
                if sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
                    try:
                        prem_r = await self.client.get(
                            f"{BINANCE_FUTURES_BASE}/premiumIndex",
                            params={"symbol": sym},
                            timeout=5.0,
                        )
                        if prem_r.status_code == 200:
                            j = prem_r.json()
                            fr = float(j.get("lastFundingRate", 0.0) or 0.0)
                            mark = float(j.get("markPrice", 0.0) or 0.0)
                            idx = float(j.get("indexPrice", 0.0) or 0.0)
                            snap.funding_rate = fr
                            snap.basis_pct = ((mark - idx) / idx * 100.0) if (idx and idx > 0) else 0.0
                    except Exception:
                        # Keep last known values; failures are common during rate-limits.
                        pass

        except Exception as e:
            logger.warning(f"Error fetching public APIs: {e}")

    async def _fetch_candles(self, symbol: str, interval: str = "5m", limit: int = 100):
        """Fetch OHLCV candles from Binance."""
        url = f"{BINANCE_BASE}/klines"
        params = {"symbol": symbol, "interval": interval, "limit": limit}

        resp = await self.client.get(url, params=params)
        resp.raise_for_status()
        raw = resp.json()

        candles = []
        for k in raw:
            candles.append(Candle(
                timestamp=k[0] / 1000,    # ms → s
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[5]),
                close_time=k[6] / 1000,
                quote_volume=float(k[7]),
                trades=int(k[8]),
            ))

        current_price = candles[-1].close if candles else 0.0

        if symbol not in self.snapshots:
            self.snapshots[symbol] = CryptoSnapshot(symbol=symbol, price=current_price)
        
        snap = self.snapshots[symbol]
        snap.price = current_price
        snap.last_refresh = time.time()
        
        if interval == "1m":
            snap.candles_1m = candles
        elif interval == "5m":
            snap.candles_5m = candles
        elif interval == "15m":
            snap.candles_15m = candles

        logger.info(
            f"📊 {symbol} {interval}: ${current_price:,.2f} "
            f"({len(candles)} candles fetched)"
        )

    def get_price(self, symbol: str = "BTCUSDT") -> float:
        """Get the latest price for a symbol."""
        snap = self.snapshots.get(symbol)
        return snap.price if snap else 0.0

    def get_candles(self, symbol: str = "BTCUSDT") -> List[Candle]:
        """Get cached candles for a symbol."""
        snap = self.snapshots.get(symbol)
        return snap.candles_5m if snap else []

    def get_snapshot(self, symbol: str = "BTCUSDT") -> Optional[CryptoSnapshot]:
        """Get the full snapshot for a symbol."""
        return self.snapshots.get(symbol)
