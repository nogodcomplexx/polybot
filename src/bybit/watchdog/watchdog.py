"""
Watchdog & Dead-Man Safety System.
Monitors main execution loop heartbeats, stream health, clock drift, and activates safe mode upon failure.
"""

import time
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)


class Watchdog:
    """Independent supervisor tracking loop liveness, stream connectivity, and clock synchronization."""

    def __init__(self, max_heartbeat_delay_sec: float = 15.0, max_clock_drift_ms: float = 1500.0):
        self.max_heartbeat_delay = max_heartbeat_delay_sec
        self.max_clock_drift_ms = max_clock_drift_ms
        self.last_heartbeat: float = time.time()
        self.clock_drift_ms: float = 0.0
        self.last_ws_disconnect_ts: Optional[float] = None
        self.is_healthy: bool = True

    def beat(self):
        """Record loop heartbeat."""
        self.last_heartbeat = time.time()

    def update_clock_drift(self, drift_ms: float):
        self.clock_drift_ms = drift_ms

    def update_stream_health(self, ws_connected: bool, private_ws_connected: bool = True):
        """Track WebSocket connectivity state for dead-man switch."""
        now = time.time()
        if not ws_connected or not private_ws_connected:
            if self.last_ws_disconnect_ts is None:
                self.last_ws_disconnect_ts = now
        else:
            self.last_ws_disconnect_ts = None

    def check_health(self) -> Tuple[bool, str]:
        """Verify process and stream are responsive within acceptable tolerances."""
        now = time.time()
        delay = now - self.last_heartbeat

        if delay > self.max_heartbeat_delay:
            self.is_healthy = False
            return False, f"Loop heartbeat delay ({delay:.1f}s > {self.max_heartbeat_delay}s)"

        if abs(self.clock_drift_ms) > self.max_clock_drift_ms:
            self.is_healthy = False
            return False, f"Abnormal clock drift ({self.clock_drift_ms:.1f}ms > {self.max_clock_drift_ms}ms)"

        if self.last_ws_disconnect_ts is not None:
            down_duration = now - self.last_ws_disconnect_ts
            if down_duration > self.max_heartbeat_delay:
                self.is_healthy = False
                return False, f"Critical stream disconnected for {down_duration:.1f}s (> {self.max_heartbeat_delay}s)"

        self.is_healthy = True
        return True, "OK"
