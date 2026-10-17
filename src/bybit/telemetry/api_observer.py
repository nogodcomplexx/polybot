"""
API Observatory & Venue Ping Tracker.
Measures real-time latency, message rates, and connection status across Bybit, Binance, and CoinGlass.
SECURITY INVARIANT: Never stores or exposes API keys, secrets, or sensitive request payloads.
"""

import time
import re
from collections import deque
from typing import Dict, Any, List, Optional
from src.bybit.telemetry.events import ApiPingEvent


class ApiObservatory:
    """Aggregates real-time API call latencies, message throughput, and connection status."""

    def __init__(self, max_recent_pings: int = 100, bus: Optional[Any] = None):
        self.bus = bus
        self.max_recent_pings = max_recent_pings
        self._recent_pings: deque[ApiPingEvent] = deque(maxlen=max_recent_pings)

        # Per-venue aggregated stats
        self._stats: Dict[str, Dict[str, Any]] = {
            "bybit": {
                "ws_latency_ms": 8.0,
                "rest_latency_ms": 35.0,
                "messages_per_sec": 0,
                "reconnects": 0,
                "rate_limit_pct": 10.0,
                "errors": 0,
                "last_seen_ts": time.time(),
            },
            "binance": {
                "ws_latency_ms": 11.0,
                "rest_latency_ms": 42.0,
                "messages_per_sec": 0,
                "reconnects": 0,
                "rate_limit_pct": 5.0,
                "errors": 0,
                "last_seen_ts": time.time(),
            },
            "coinglass": {
                "ws_latency_ms": 0.0,
                "rest_latency_ms": 120.0,
                "messages_per_sec": 0,
                "reconnects": 0,
                "rate_limit_pct": 15.0,
                "errors": 0,
                "last_seen_ts": time.time(),
            },
        }

        # Rolling message counters for msgs/sec calculation
        self._msg_window: Dict[str, deque] = {
            "bybit": deque(),
            "binance": deque(),
            "coinglass": deque(),
        }

    def _sanitize_endpoint(self, endpoint: str) -> str:
        """Strip sensitive tokens, signatures, or keys from endpoint URLs."""
        if not endpoint:
            return ""
        # Remove api_key, apiKey, secret, signature query params
        sanitized = re.sub(r"(api_?key|signature|secret|token)=[^&]+", r"\1=***", endpoint, flags=re.IGNORECASE)
        return sanitized

    def record_call(
        self,
        venue: str,
        endpoint: str,
        latency_ms: float,
        status: str = "200",
        message_type: str = "REST"
    ) -> ApiPingEvent:
        """Record an API or WebSocket request/ping event."""
        now = time.time()
        venue_key = venue.lower()
        clean_endpoint = self._sanitize_endpoint(endpoint)

        event = ApiPingEvent(
            timestamp=now,
            venue=venue.capitalize(),
            endpoint=clean_endpoint,
            latency_ms=round(latency_ms, 1),
            status=str(status),
            message_type=message_type.upper()
        )
        self._recent_pings.append(event)
        if self.bus:
            try:
                self.bus.publish(event)
            except Exception:
                pass

        # Update stats
        if venue_key in self._stats:
            st = self._stats[venue_key]
            st["last_seen_ts"] = now
            if message_type.upper() == "WS":
                # Exponential moving average for WS latency
                st["ws_latency_ms"] = round(0.85 * st["ws_latency_ms"] + 0.15 * latency_ms, 1)
            else:
                st["rest_latency_ms"] = round(0.85 * st["rest_latency_ms"] + 0.15 * latency_ms, 1)

            if str(status).startswith(("4", "5")) or status == "ERROR":
                st["errors"] += 1

            # Append to message window
            w = self._msg_window[venue_key]
            w.append(now)

        return event

    def record_reconnect(self, venue: str):
        """Record a venue reconnect event."""
        venue_key = venue.lower()
        if venue_key in self._stats:
            self._stats[venue_key]["reconnects"] += 1

    def update_rate_limit(self, venue: str, percent: float):
        """Update current rate limit utilization percent."""
        venue_key = venue.lower()
        if venue_key in self._stats:
            self._stats[venue_key]["rate_limit_pct"] = round(max(0.0, min(100.0, percent)), 1)

    def get_stats(self) -> Dict[str, Any]:
        """Compute live messages/sec and return full venue telemetry."""
        now = time.time()
        cutoff = now - 1.0  # 1-second rolling window

        for venue_key, w in self._msg_window.items():
            while w and w[0] < cutoff:
                w.popleft()
            self._stats[venue_key]["messages_per_sec"] = len(w)

        return {
            "venues": dict(self._stats),
            "recent_pings": [p.to_dict() for p in reversed(list(self._recent_pings)[-30:])]
        }
