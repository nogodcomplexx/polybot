"""
Data Quality & Freshness Engine (Sections 32, 33, 38).
Continuously monitors data point ages, timestamps, and provider health.
Computes a 0–100 Data Quality Score and raises critical trade vetoes if Bybit data stalls.
"""

import time
from typing import Dict, Any, List, Optional
from src.bybit.intelligence.models import DataQuality, DataPoint, ProviderHealth, ProviderStatus


class DataQualityEngine:
    """Evaluates data freshness and enforces strict critical venue safety vetoes."""

    def __init__(self, stale_threshold_sec: float = 3.0):
        self.stale_threshold_sec = stale_threshold_sec

    def assess_datapoint(self, value: Any, event_ts: float, source: str) -> DataPoint:
        """Create a typed DataPoint with exact latency and freshness classification."""
        now = time.time()
        age_ms = max(0.0, (now - event_ts) * 1000.0) if event_ts > 0 else 0.0
        age_sec = age_ms / 1000.0

        if age_sec < 1.0:
            quality = DataQuality.FRESH
        elif age_sec <= self.stale_threshold_sec:
            quality = DataQuality.WARNING
        elif age_sec <= 10.0:
            quality = DataQuality.STALE
        else:
            quality = DataQuality.INVALID

        return DataPoint(
            value=value,
            event_timestamp=event_ts,
            receive_timestamp=now,
            age_ms=round(age_ms, 1),
            source=source,
            quality=quality,
        )

    def evaluate_quality(
        self,
        symbol: str,
        bybit_last_ts: float,
        binance_last_ts: float,
        bybit_health: ProviderHealth,
        binance_health: ProviderHealth,
    ) -> Dict[str, Any]:
        """
        Compute 0-100 composite data quality score and determine if new entries must be disabled.
        """
        now = time.time()
        bybit_age = max(0.0, now - bybit_last_ts) if bybit_last_ts > 0 else 999.0
        binance_age = max(0.0, now - binance_last_ts) if binance_last_ts > 0 else 999.0

        score = 100.0

        # 1. Bybit Freshness Penalty (40 points max)
        if bybit_age > 10.0:
            score -= 40.0
        elif bybit_age > self.stale_threshold_sec:
            score -= 25.0
        elif bybit_age > 1.0:
            score -= 10.0

        # 2. Binance Freshness Penalty (20 points max)
        if binance_age > 10.0:
            score -= 20.0
        elif binance_age > self.stale_threshold_sec:
            score -= 10.0

        # 3. Provider Connection Health (20 points max)
        if bybit_health.status in (ProviderStatus.OFFLINE, ProviderStatus.UNSAFE):
            score -= 20.0
        elif bybit_health.status == ProviderStatus.DEGRADED:
            score -= 10.0

        if binance_health.status == ProviderStatus.OFFLINE:
            score -= 10.0

        # 4. Critical Venue Veto (Section 38: Bybit unavailable = NO NEW TRADES)
        disable_new_entries = False
        veto_reason = ""

        if bybit_age > self.stale_threshold_sec:
            disable_new_entries = True
            veto_reason = f"Bybit market data stale ({bybit_age:.1f}s > {self.stale_threshold_sec:.1f}s)"
        elif bybit_health.status in (ProviderStatus.OFFLINE, ProviderStatus.UNSAFE):
            disable_new_entries = True
            veto_reason = f"Bybit provider health {bybit_health.status.value}"

        final_score = max(0.0, min(100.0, score))

        return {
            "data_quality_score": round(final_score, 1),
            "bybit_age_sec": round(bybit_age, 2),
            "binance_age_sec": round(binance_age, 2),
            "disable_new_entries": disable_new_entries,
            "veto_reason": veto_reason,
        }
