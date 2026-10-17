"""
API call logger and telemetry tracker for Bybit exchange clients.
Records call latency, errors, and frequency for live monitoring.
"""

import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

api_call_history: List[Dict[str, Any]] = []
_global_observatory: Optional[Any] = None


def set_global_observatory(obs: Any):
    """Set global ApiObservatory instance to receive API and WS call metrics."""
    global _global_observatory
    _global_observatory = obs


def log_api_call(service: str, method: str, success: bool, duration_ms: float, detail: str = ""):
    """Record an outbound API or streaming call for telemetry and latency auditing."""
    now = time.time()
    event = {
        "service": service,
        "method": method,
        "success": success,
        "duration_ms": round(duration_ms, 1),
        "detail": detail,
        "timestamp": now,
    }
    api_call_history.append(event)
    if len(api_call_history) > 100:
        api_call_history.pop(0)

    if _global_observatory is not None:
        try:
            status = "200" if success else ("ERROR" if not detail else detail[:20])
            msg_type = "WS" if "WS" in service.upper() or "stream" in method.lower() else "REST"
            venue = "Bybit" if "bybit" in service.lower() else ("Binance" if "binance" in service.lower() else "CoinGlass")
            _global_observatory.record_call(
                venue=venue,
                endpoint=method,
                latency_ms=duration_ms,
                status=status,
                message_type=msg_type,
            )
        except Exception as e:
            logger.debug(f"Failed to record call in observatory: {e}")

