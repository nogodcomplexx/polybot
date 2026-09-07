"""
Base Market Data Provider Abstraction.
Encapsulates connection lifecycle, token-bucket rate limiting, backoff, and health metrics.
"""

from abc import ABC, abstractmethod
import asyncio
import logging
import random
import time
from typing import Dict, Any, List, Optional

from src.bybit.intelligence.models import (
    CanonicalSymbol, ProviderHealth, ProviderStatus
)

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """Thread-safe async token bucket rate limiter."""

    def __init__(self, rate: float = 10.0, capacity: float = 20.0):
        self.rate = rate                # Tokens replenished per second
        self.capacity = capacity        # Max burst capacity
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0):
        wait_sec = 0.0
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

            if self.tokens < cost:
                needed = cost - self.tokens
                wait_sec = needed / self.rate
                self.tokens = 0.0
            else:
                self.tokens -= cost

        if wait_sec > 0:
            await asyncio.sleep(wait_sec)


class MarketDataProvider(ABC):
    """Abstract base class for all exchange market data providers."""

    def __init__(
        self,
        provider_name: str,
        rate_limit_per_sec: float = 10.0,
        burst_capacity: float = 20.0,
    ):
        self.provider_name = provider_name
        self.rate_limiter = TokenBucketRateLimiter(rate=rate_limit_per_sec, capacity=burst_capacity)

        # Health & Telemetry State
        self.health_state = ProviderHealth(
            provider_name=provider_name,
            status=ProviderStatus.OFFLINE,
            connected=False,
            authenticated=False,
        )

        # Backoff parameters
        self._backoff_delay: float = 1.0
        self._min_backoff: float = 1.0
        self._max_backoff: float = 30.0
        self._backoff_factor: float = 1.5

    @abstractmethod
    async def connect(self):
        """Establish connections and start background listeners."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Gracefully disconnect and terminate streams."""
        pass

    @abstractmethod
    async def subscribe(self, symbols: List[CanonicalSymbol]):
        """Subscribe to live feeds for the specified canonical symbols."""
        pass

    @abstractmethod
    async def unsubscribe(self, symbols: List[CanonicalSymbol]):
        """Unsubscribe from live feeds for the specified canonical symbols."""
        pass

    def health(self) -> ProviderHealth:
        """Return current provider health status."""
        # Derive status based on last message age
        now = time.time()
        if not self.health_state.connected:
            self.health_state.status = ProviderStatus.OFFLINE
        elif self.health_state.error_count > 10:
            self.health_state.status = ProviderStatus.UNSAFE
        elif self.health_state.last_message_ts > 0 and (now - self.health_state.last_message_ts) > 10.0:
            self.health_state.status = ProviderStatus.DEGRADED
        else:
            self.health_state.status = ProviderStatus.HEALTHY

        return self.health_state

    def get_health(self) -> ProviderHealth:
        """Alias for health()."""
        return self.health()


    def record_message(self, latency_ms: float = 0.0):
        """Record a successful inbound message tick."""
        now = time.time()
        self.health_state.connected = True
        self.health_state.last_message_ts = now
        self.health_state.last_successful_request_ts = now
        if latency_ms > 0:
            self.health_state.latency_ms = round(latency_ms, 1)
        self._backoff_delay = self._min_backoff

    def record_error(self, err_msg: str):
        """Record a connection or query error."""
        self.health_state.error_count += 1
        logger.warning(f"[{self.provider_name}] Provider error: {err_msg}")

    def record_reconnect(self):
        """Record reconnect attempt."""
        self.health_state.reconnect_count += 1
        self.health_state.connected = False

    async def sleep_backoff(self):
        """Sleep with exponential backoff and jitter."""
        jitter = random.uniform(0.8, 1.2)
        sleep_time = min(self._backoff_delay * jitter, self._max_backoff)
        logger.info(f"[{self.provider_name}] Backing off for {sleep_time:.2f}s...")
        await asyncio.sleep(sleep_time)
        self._backoff_delay = min(self._backoff_delay * self._backoff_factor, self._max_backoff)
