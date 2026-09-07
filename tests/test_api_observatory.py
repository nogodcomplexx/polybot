"""
Tests for ApiObservatory: Ping tracking, latency calculation, and strict secret redaction.
"""

import time
from src.bybit.telemetry.api_observer import ApiObservatory


def test_api_observatory_latency_and_throughput():
    obs = ApiObservatory()
    
    # Record pings for Bybit
    obs.record_call("Bybit", "orderbook.SOLUSDT", 10.0, "WS", "WS")
    obs.record_call("Bybit", "orderbook.SOLUSDT", 20.0, "WS", "WS")

    stats = obs.get_stats()
    bybit_stats = stats["venues"]["bybit"]
    assert bybit_stats["messages_per_sec"] >= 1
    assert 8.0 <= bybit_stats["ws_latency_ms"] <= 20.0

    # Record Binance ping
    obs.record_call("Binance", "depth", 15.0, "WS", "WS")
    assert stats["venues"]["binance"]["last_seen_ts"] > 0


def test_api_observatory_sanitization_of_secrets():
    """
    SECURITY INVARIANT:
    No API keys, signatures, or secret query parameters may be leaked in observatory endpoints.
    """
    obs = ApiObservatory()
    
    dirty_endpoint = "/v5/order/create?api_key=SECRET12345&symbol=SOLUSDT&signature=abcd9876xyz"
    evt = obs.record_call("Bybit", dirty_endpoint, 32.0, "200", "REST")

    assert "SECRET12345" not in evt.endpoint
    assert "abcd9876xyz" not in evt.endpoint
    assert "api_key=***" in evt.endpoint
    assert "signature=***" in evt.endpoint
