from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class TradingSession:
    """
    Very simple 24/7 crypto regime buckets (UTC).

    - Asia: 00:00–07:59 UTC
    - EU:   08:00–13:59 UTC
    - NA:   14:00–21:59 UTC
    - Late: 22:00–23:59 UTC (thin liquidity / transition)
    """

    name: str
    start_hour_utc: int
    end_hour_utc_inclusive: int


_SESSIONS = (
    TradingSession("ASIA", 0, 7),
    TradingSession("EU", 8, 13),
    TradingSession("NA", 14, 21),
    TradingSession("LATE", 22, 23),
)


def get_session(now_utc: datetime | None = None) -> TradingSession:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    h = now_utc.hour
    for s in _SESSIONS:
        if s.start_hour_utc <= h <= s.end_hour_utc_inclusive:
            return s
    return _SESSIONS[-1]

