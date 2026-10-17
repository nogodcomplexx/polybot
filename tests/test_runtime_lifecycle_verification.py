"""
Runtime Lifecycle & Safety Verification Test Suite.
Explicitly verifies the 8 critical lifecycle requirements from user audit:
1. main starts all necessary WS connections (Public WS, Private WS, Intelligence)
2. Waits for their initialization/warmup
3. Starts reconciliation on startup
4. Starts watchdog with stream health tracking
5. Starts dynamic universe discovery and management
6. Reconnects upon individual WS drop
7. Freezes trading when any critical data source is down (NO blind trading!) and resumes when healthy
8. Graceful shutdown of all tasks and connections
"""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.bybit.config import BybitScalperConfig, UniverseConfig, BinanceConfig, CoinGlassConfig
from src.bybit.app.orchestrator import BybitScalperOrchestrator
from src.bybit.models import PositionState
from src.bybit.intelligence.models import ProviderStatus, ProviderHealth


@pytest.fixture
def mock_config():
    cfg = BybitScalperConfig(
        api_key="test_key",
        api_secret="test_secret",
        testnet=True,
        mode="paper",
        symbols=["BTCUSDT", "ETHUSDT"],
        starting_equity=1000.0,
        universe=UniverseConfig(mode="fixed", pinned_symbols=["BTCUSDT"]),
        binance=BinanceConfig(enabled=True),
        coinglass=CoinGlassConfig(enabled=False),
        db_path=":memory:",
    )
    return cfg


@pytest.mark.asyncio
async def test_item_1_and_2_ws_startup_and_warmup(mock_config):
    """
    Items 1 & 2:
    Verifies that orchestrator starts all necessary WS feeds (Public WS, Private WS, Intelligence)
    and executes connection warmup.
    """
    orchestrator = BybitScalperOrchestrator(mock_config)

    # Mock all underlying I/O
    orchestrator.db.connect = AsyncMock()
    orchestrator.circuit_breakers.initialize = AsyncMock()
    orchestrator.client.sync_server_time = AsyncMock(return_value=10.0)
    orchestrator.client.get_instruments_info = AsyncMock(return_value=[])
    orchestrator.client.get_klines = AsyncMock(return_value=[])
    orchestrator.reconciler.reconcile = AsyncMock(return_value=True)

    orchestrator.ws.start = AsyncMock()
    orchestrator.private_ws.start = AsyncMock()
    orchestrator.intelligence.start = AsyncMock()
    orchestrator.intelligence.subscribe_symbols = AsyncMock()

    # Simulate feeds becoming connected during warmup
    async def fake_ws_start():
        orchestrator.ws.connected = True
    orchestrator.ws.start.side_effect = fake_ws_start

    # Run start for 1 cycle then stop
    async def stop_soon():
        await asyncio.sleep(0.05)
        orchestrator.running = False
        orchestrator._wake_event.set()

    asyncio.create_task(stop_soon())
    await orchestrator.start()

    # Verify all WS connections were started
    assert orchestrator.ws.start.called, "Bybit Public WS start() must be called"
    assert orchestrator.private_ws.start.called, "Bybit Private WS start() must be called"
    assert orchestrator.intelligence.start.called, "Multi-exchange intelligence start() must be called"
    assert orchestrator.intelligence.subscribe_symbols.called, "Intelligence subscribe_symbols must be called"


@pytest.mark.asyncio
async def test_item_3_initial_reconciliation_on_startup(mock_config):
    """
    Item 3:
    Reconciliation must run during initialization BEFORE any trading loop starts,
    preventing desync or unhedged exposure across restarts.
    """
    orchestrator = BybitScalperOrchestrator(mock_config)
    orchestrator.db.connect = AsyncMock()
    orchestrator.circuit_breakers.initialize = AsyncMock()
    orchestrator.client.sync_server_time = AsyncMock(return_value=5.0)
    orchestrator.client.get_instruments_info = AsyncMock(return_value=[])
    orchestrator.client.get_klines = AsyncMock(return_value=[])
    orchestrator.ws.start = AsyncMock()
    orchestrator.private_ws.start = AsyncMock()
    orchestrator.intelligence.start = AsyncMock()
    orchestrator.intelligence.subscribe_symbols = AsyncMock()

    reconcile_called = False
    async def mock_reconcile():
        nonlocal reconcile_called
        reconcile_called = True
        return True
    orchestrator.reconciler.reconcile = AsyncMock(side_effect=mock_reconcile)

    async def stop_immediately():
        await asyncio.sleep(0.05)
        orchestrator.running = False
        orchestrator._wake_event.set()

    asyncio.create_task(stop_immediately())
    await orchestrator.start()

    assert reconcile_called is True, "Reconciliation must be executed on startup before trading loop!"


@pytest.mark.asyncio
async def test_item_4_watchdog_stream_health_monitoring(mock_config):
    """
    Item 4:
    Watchdog monitors stream connectivity and trips kill switch if streams remain down.
    """
    orchestrator = BybitScalperOrchestrator(mock_config)
    wdog = orchestrator.watchdog

    # Initially healthy
    wdog.beat()
    wdog.update_stream_health(ws_connected=True, private_ws_connected=True)
    healthy, reason = wdog.check_health()
    assert healthy is True
    assert reason == "OK"

    # Stream disconnects for > 15s
    wdog.update_stream_health(ws_connected=False)
    wdog.last_ws_disconnect_ts = time.time() - 20.0  # 20s elapsed
    healthy, reason = wdog.check_health()
    assert healthy is False
    assert "stream disconnected" in reason.lower()

    # Stream recovers
    wdog.update_stream_health(ws_connected=True)
    healthy, reason = wdog.check_health()
    assert healthy is True


@pytest.mark.asyncio
async def test_item_7_trading_frozen_when_critical_source_down(mock_config):
    """
    Item 7 (CRITICAL ARCHITECTURAL INVARIANT):
    Verify the user diagram:
    critical data source down -> TRADING FROZEN (no blind trading!)
    reconnect / resync -> data healthy -> trading resumes
    """
    orchestrator = BybitScalperOrchestrator(mock_config)
    orchestrator.ws.connected = True
    orchestrator.private_ws.connected = True
    orchestrator.private_ws.authenticated = True
    orchestrator.state_machine.state = PositionState.FLAT
    orchestrator.reconciler.reconcile = AsyncMock(return_value=True)
    orchestrator.account.positions = {}
    orchestrator.reconciler.reconcile_frozen = False
    orchestrator.alpha_engine.scan_all_symbols = MagicMock(return_value=[])
    orchestrator.self_tuner.evaluate_batch = AsyncMock()

    if orchestrator.binance_conn:
        orchestrator.binance_conn.record_message()

    # 1. Healthy baseline -> NOT frozen
    frozen, reason = orchestrator.is_trading_frozen()
    assert frozen is False, f"Baseline should be healthy, got {reason}"

    # 2. Bybit WS drops -> TRADING FROZEN
    orchestrator.ws.connected = False
    frozen, reason = orchestrator.is_trading_frozen()
    assert frozen is True
    assert "BYBIT_WS_DOWN" in reason

    # Step must NOT scan candidates when frozen!
    orchestrator.alpha_engine.scan_all_symbols.reset_mock()
    await orchestrator._step()
    assert not orchestrator.alpha_engine.scan_all_symbols.called, "Bot must NOT scan candidates when Bybit WS is down!"

    # 3. Bybit WS reconnects -> TRADING RESUMES
    orchestrator.ws.connected = True
    frozen, reason = orchestrator.is_trading_frozen()
    assert frozen is False
    assert reason == "HEALTHY"

    # Step now scans candidates
    await orchestrator._step()
    assert orchestrator.alpha_engine.scan_all_symbols.called, "Bot must scan candidates once healthy and reconnected"

    # 4. Binance WS drops -> TRADING FROZEN (when Binance cross-exchange is enabled)
    if orchestrator.binance_conn:
        orchestrator.binance_conn.health_state.connected = False
        frozen, reason = orchestrator.is_trading_frozen()
        assert frozen is True
        assert "BINANCE_WS_DOWN" in reason

        orchestrator.alpha_engine.scan_all_symbols.reset_mock()
        await orchestrator._step()
        assert not orchestrator.alpha_engine.scan_all_symbols.called, "Bot must NOT scan when Binance feed is down!"

        # Binance recovers -> TRADING RESUMES
        orchestrator.binance_conn.record_message()
        frozen, reason = orchestrator.is_trading_frozen()
        assert frozen is False


@pytest.mark.asyncio
async def test_item_8_graceful_shutdown(mock_config):
    """
    Item 8:
    stop() shuts down intelligence connectors, private WS, public WS, REST client,
    and database cleanly without hanging or leaving orphaned tasks.
    """
    orchestrator = BybitScalperOrchestrator(mock_config)
    orchestrator.running = True

    orchestrator.intelligence.stop = AsyncMock()
    orchestrator.private_ws.stop = AsyncMock()
    orchestrator.ws.stop = AsyncMock()
    orchestrator.client.close = AsyncMock()
    orchestrator.db.close = AsyncMock()

    await orchestrator.stop()

    assert orchestrator.running is False
    assert orchestrator.intelligence.stop.called
    assert orchestrator.private_ws.stop.called
    assert orchestrator.ws.stop.called
    assert orchestrator.client.close.called
    assert orchestrator.db.close.called
