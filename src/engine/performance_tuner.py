from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


DominanceBucket = str  # "RISE" | "FLAT" | "FALL"


def dominance_bucket(btc_dominance_change_1h_pp: float) -> DominanceBucket:
    """
    Bucket BTC dominance change (percentage points over 1h).
    Example: +0.25 means BTC.D +0.25pp in the last hour.
    """
    if btc_dominance_change_1h_pp >= 0.10:
        return "RISE"
    if btc_dominance_change_1h_pp <= -0.10:
        return "FALL"
    return "FLAT"


@dataclass(frozen=True)
class RegimeKey:
    underlying_symbol: str
    session: str
    dom_bucket: DominanceBucket


@dataclass
class RegimeStats:
    n: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    total_cost: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.n) if self.n else 0.0

    @property
    def roi(self) -> float:
        # Return on deployed cost.
        return (self.total_pnl / self.total_cost) if self.total_cost else 0.0


@dataclass(frozen=True)
class RegimeTuning:
    min_edge: float
    kelly_mult: float
    n: int
    win_rate: float
    roi: float


class PerformanceTuner:
    """
    Converts realized outcomes into regime-specific tuning knobs.

    The output is intentionally conservative: we only tune when we have
    enough resolved trades in a bucket.
    """

    def __init__(
        self,
        base_min_edge: float,
        min_samples: int = 20,
        max_min_edge: float = 0.04,
        min_min_edge: float = 0.005,
    ):
        self.base_min_edge = base_min_edge
        self.min_samples = min_samples
        self.max_min_edge = max_min_edge
        self.min_min_edge = min_min_edge

    def build(self, rows: Iterable[dict]) -> Dict[RegimeKey, RegimeTuning]:
        stats: Dict[RegimeKey, RegimeStats] = {}

        for r in rows:
            sym = (r.get("underlying_symbol") or "").upper()
            session = (r.get("session") or "").upper() or "UNKNOWN"
            dom_chg = float(r.get("btc_dominance_change_1h_pct") or 0.0)
            key = RegimeKey(sym, session, dominance_bucket(dom_chg))

            st = stats.get(key) or RegimeStats()
            st.n += 1
            pnl = float(r.get("pnl") or 0.0)
            cost = float(r.get("cost_usd") or 0.0)
            st.total_pnl += pnl
            st.total_cost += abs(cost)
            if (r.get("status") or "").upper() == "WIN":
                st.wins += 1
            stats[key] = st

        tuned: Dict[RegimeKey, RegimeTuning] = {}
        for key, st in stats.items():
            if st.n < self.min_samples:
                continue

            # Conservative policy:
            # - If bucket performs poorly -> raise edge threshold and cut sizing.
            # - If bucket performs well   -> slightly loosen threshold and modestly increase sizing.
            min_edge = self.base_min_edge
            k_mult = 1.0

            if st.roi < -0.01 or st.win_rate < 0.48:
                min_edge = min(self.max_min_edge, self.base_min_edge + 0.010)
                k_mult = 0.70
            elif st.roi > 0.01 and st.win_rate > 0.55:
                min_edge = max(self.min_min_edge, self.base_min_edge - 0.003)
                k_mult = 1.10

            tuned[key] = RegimeTuning(
                min_edge=min_edge,
                kelly_mult=k_mult,
                n=st.n,
                win_rate=st.win_rate,
                roi=st.roi,
            )

        return tuned

