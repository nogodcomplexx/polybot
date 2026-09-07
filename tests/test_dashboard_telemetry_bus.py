"""
Tests for TelemetryBus: Non-blocking Guarantee, Overflows, and Decoupled Distribution.
"""

import asyncio
import pytest
from src.bybit.telemetry.dashboard_bus import TelemetryBus
from src.bybit.telemetry.events import SystemStatusEvent, MarketTelemetryEvent


@pytest.mark.asyncio
async def test_telemetry_bus_publish_and_subscribe():
    bus = TelemetryBus()
    q = bus.subscribe()

    event = MarketTelemetryEvent(
        symbol="SOLUSDT",
        timestamp=1000.0,
        last_price=100.0,
        mark_price=100.0,
        spread_pct=0.02,
        best_bid=99.99,
        best_ask=100.01,
        bid_depth=500000.0,
        ask_depth=400000.0,
        orderbook_imbalance=1.25,
    )

    bus.publish(event)
    received = await asyncio.wait_for(q.get(), timeout=1.0)
    assert received["type"] == "MARKET_TELEMETRY"
    assert received["symbol"] == "SOLUSDT"
    assert received["last_price"] == 100.0

    bus.unsubscribe(q)
    assert len(bus._subscribers) == 0


@pytest.mark.asyncio
async def test_telemetry_bus_non_blocking_on_full_queue():
    """
    CRITICAL INVARIANT:
    A full consumer queue must NEVER block the publisher.
    Oldest event must be dropped automatically to prevent latency degradation.
    """
    bus = TelemetryBus(max_queue_size=3)
    q = bus.subscribe(queue_size=3)

    # Publish 5 events into a queue of size 3
    for i in range(5):
        bus.publish({"type": "EVENT", "seq": i})

    assert bus.total_published == 5
    assert bus.total_dropped >= 2

    # Verify queue contains the freshest events (2, 3, 4) rather than stale events (0, 1)
    ev1 = q.get_nowait()
    ev2 = q.get_nowait()
    ev3 = q.get_nowait()
    assert ev1["seq"] == 2
    assert ev2["seq"] == 3
    assert ev3["seq"] == 4
    assert q.empty()


@pytest.mark.asyncio
async def test_telemetry_bus_state_cache():
    bus = TelemetryBus()
    sys_event = SystemStatusEvent(
        timestamp=1000.0,
        bot_status="ONLINE",
        account_equity=10000.0,
        available_balance=9500.0,
        cash_fortress_pct=95.0,
    )
    bus.publish(sys_event)
    state = bus.get_latest_state()
    assert "SYSTEM_STATUS" in state
    assert state["SYSTEM_STATUS"]["bot_status"] == "ONLINE"
    assert state["SYSTEM_STATUS"]["account_equity"] == 10000.0
