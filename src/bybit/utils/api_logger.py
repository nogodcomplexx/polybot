"""
API call logger and telemetry tracker for Bybit exchange clients.
Records call latency, errors, and frequency for live monitoring.
"""

import time
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

api_call_history: List[Dict[str, Any]] = []


def log_api_call(service: str, method: str, success: bool, duration_ms: float, detail: str = ""):
    """Record an outbound API or streaming call for telemetry and latency auditing."""
    event = {
        "service": service,
        "method": method,
        "success": success,
        "duration_ms": round(duration_ms, 1),
        "detail": detail,
        "timestamp": time.time(),
    }
    api_call_history.append(event)
    if len(api_call_history) > 100:
        api_call_history.pop(0)
