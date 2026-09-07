"""
Integration Tests for DashboardServer: Static delivery, REST endpoints, and WebSocket broadcasting.
"""

import pytest
import json
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from dashboard.backend.server import DashboardServer
from src.bybit.telemetry.dashboard_bus import TelemetryBus
from src.bybit.telemetry.brain_trace import BrainDecisionTrace
from src.bybit.telemetry.api_observer import ApiObservatory
from src.bybit.telemetry.events import SystemStatusEvent, MarketTelemetryEvent


@pytest.mark.asyncio
async def test_dashboard_server_http_and_websocket():
    bus = TelemetryBus()
    brain_trace = BrainDecisionTrace()
    api_observer = ApiObservatory()

    # Pre-seed state
    brain_trace.record_candidate_found("SOLUSDT", "TURTLE_SOUP", 82.0)
    api_observer.record_call("Bybit", "orderbook", 8.5, "WS", "WS")
    bus.publish(SystemStatusEvent(
        timestamp=1000.0,
        bot_status="ONLINE",
        account_equity=10000.0,
        available_balance=9500.0,
        cash_fortress_pct=95.0,
    ))

    server = DashboardServer(
        host="127.0.0.1",
        port=8080,
        bus=bus,
        brain_trace=brain_trace,
        api_observer=api_observer,
    )
    app = server.build_app()

    test_server = TestServer(app)
    client = TestClient(test_server)
    await client.start_server()

    try:
        # 1. Test Index HTML
        resp = await client.get("/")
        assert resp.status == 200
        text = await resp.text()
        assert "POLYBOT V5" in text
        assert "neuralCanvas" in text

        # 2. Test Styles CSS
        resp_css = await client.get("/styles.css")
        assert resp_css.status == 200
        assert "header-hud" in await resp_css.text()

        # 3. Test REST /api/status
        resp_status = await client.get("/api/status")
        assert resp_status.status == 200
        data_status = await resp_status.json()
        assert "SYSTEM_STATUS" in data_status
        assert data_status["SYSTEM_STATUS"]["bot_status"] == "ONLINE"

        # 4. Test REST /api/trace
        resp_trace = await client.get("/api/trace")
        assert resp_trace.status == 200
        data_trace = await resp_trace.json()
        assert len(data_trace["traces"]) >= 1

        # 5. Test REST /api/api-stats
        resp_api = await client.get("/api/api-stats")
        assert resp_api.status == 200
        data_api = await resp_api.json()
        assert "venues" in data_api
        assert "bybit" in data_api["venues"]

        # 6. Test WebSocket Handshake and Streaming
        ws = await client.ws_connect("/ws")
        
        # First message is INITIAL_HYDRATION
        msg1 = await ws.receive_str()
        payload1 = json.loads(msg1)
        assert payload1["type"] == "INITIAL_HYDRATION"
        assert payload1["state"]["SYSTEM_STATUS"]["bot_status"] == "ONLINE"
        assert len(payload1["traces"]) >= 1

        # Broadcast a new event on TelemetryBus and verify WebSocket receives it
        mkt_event = MarketTelemetryEvent(
            symbol="AVAXUSDT",
            timestamp=2000.0,
            last_price=28.50,
            mark_price=28.50,
            spread_pct=0.02,
            best_bid=28.49,
            best_ask=28.51,
            bid_depth=600000.0,
            ask_depth=450000.0,
            orderbook_imbalance=1.33,
        )
        bus.publish(mkt_event)

        msg2 = await ws.receive_str()
        payload2 = json.loads(msg2)
        assert payload2["type"] == "MARKET_TELEMETRY"
        assert payload2["symbol"] == "AVAXUSDT"
        assert payload2["last_price"] == 28.50

        # Send ping to WebSocket
        await ws.send_str("ping")
        msg_pong = await ws.receive_str()
        payload_pong = json.loads(msg_pong)
        assert payload_pong["type"] == "PONG"

        await ws.close()

    finally:
        await client.close()
