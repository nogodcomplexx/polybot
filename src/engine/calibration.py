from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


@dataclass(frozen=True)
class CalibKey:
    strategy: str
    underlying_symbol: str
    session: str


@dataclass
class BinStats:
    n: int = 0
    wins: int = 0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.n) if self.n > 0 else 0.5


class ProbabilityCalibrator:
    """
    Very lightweight online calibration:
    - Bin predictions by confidence
    - Replace raw probability with empirical win-rate in that bin (per regime key)
    """

    def __init__(self, bin_size: float = 0.05, min_samples_per_bin: int = 12):
        self.bin_size = max(0.01, min(0.25, bin_size))
        self.min_samples_per_bin = max(5, min_samples_per_bin)
        self._table: Dict[Tuple[CalibKey, int], BinStats] = {}

    def _bin(self, p: float) -> int:
        p = max(0.01, min(0.99, float(p)))
        return int(p / self.bin_size)

    def build(self, rows: Iterable[dict]) -> None:
        """
        rows expected keys:
          - status: WIN/LOSS
          - underlying_symbol
          - session
          - model_confidence
          - strategy_name
        """
        self._table.clear()
        for r in rows:
            status = (r.get("status") or "").upper()
            if status not in ("WIN", "LOSS"):
                continue
            key = CalibKey(
                strategy=str(r.get("strategy_name") or ""),
                underlying_symbol=str(r.get("underlying_symbol") or ""),
                session=str(r.get("session") or ""),
            )
            b = self._bin(float(r.get("model_confidence") or 0.5))
            stats = self._table.get((key, b))
            if not stats:
                stats = BinStats()
                self._table[(key, b)] = stats
            stats.n += 1
            if status == "WIN":
                stats.wins += 1

    def calibrate(self, strategy: str, underlying_symbol: str, session: str, p: float) -> float:
        key = CalibKey(strategy=strategy, underlying_symbol=underlying_symbol, session=session)
        b = self._bin(p)
        stats = self._table.get((key, b))
        if not stats or stats.n < self.min_samples_per_bin:
            return float(p)
        return max(0.01, min(0.99, stats.win_rate))

