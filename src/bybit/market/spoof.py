"""
Spoofing and Fake Liquidity Detector.
Tracks large orderbook walls, persistence, cancellation behavior, and calculates spoof probability.
"""

from typing import Dict, Any, List, Optional
import time


class SpoofDetector:
    """Evaluates authenticity of large displayed orderbook liquidity."""

    def __init__(self, wall_multiple: float = 3.0, max_spoof_prob: float = 0.65):
        self.wall_multiple = wall_multiple
        self.max_spoof_prob = max_spoof_prob
        # Tracked walls: {symbol: {price: {"size": float, "first_seen": float, "cancelled": bool, "hit_count": int}}}
        self.tracked_walls: Dict[str, Dict[float, Dict[str, Any]]] = {}

    def evaluate_walls(self, symbol: str, book: Dict[str, Any], current_price: float) -> float:
        """
        Scan top orderbook levels for walls and calculate highest spoof probability.
        Returns highest spoof probability among active large walls.
        """
        symbol = symbol.upper()
        if not book or not book.get("bids") or not book.get("asks"):
            return 0.0

        bids = book["bids"]
        asks = book["asks"]
        now = time.time()

        all_sizes = list(bids.values()) + list(asks.values())
        if not all_sizes:
            return 0.0

        avg_size = sum(all_sizes) / len(all_sizes)
        threshold_size = avg_size * self.wall_multiple

        max_spoof_prob = 0.0
        symbol_walls = self.tracked_walls.setdefault(symbol, {})

        # Evaluate large bid and ask levels
        for p, s in list(bids.items()) + list(asks.items()):
            if s >= threshold_size:
                if p not in symbol_walls:
                    symbol_walls[p] = {
                        "size": s,
                        "first_seen": now,
                        "hit_count": 0,
                        "cancelled": False,
                    }
                wall = symbol_walls[p]
                lifetime = now - wall["first_seen"]
                distance_pct = abs(p - current_price) / current_price * 100 if current_price > 0 else 1.0

                # Resting wall evaluation:
                # Genuine liquidity sits in the book. A newly arrived wall has modest uncertainty,
                # but should NEVER be vetoed (> 0.65) merely for being placed!
                prob = 0.15
                if lifetime < 2.0:
                    prob = 0.25  # Brand new wall, awaiting persistence check
                elif lifetime >= 5.0:
                    prob = 0.05  # Persistent wall proves real resting depth

                # Far away phantom wall (> 2.5%) that never gets touched has mild suspicion
                if distance_pct > 2.5:
                    prob += 0.10

                max_spoof_prob = max(max_spoof_prob, min(1.0, prob))

        # Cleanup old wall records and evaluate cancellations (pulls)
        for p in list(symbol_walls.keys()):
            wall = symbol_walls[p]
            # TTL cleanup: remove any wall tracking older than 60 seconds
            if now - wall["first_seen"] > 60.0:
                symbol_walls.pop(p)
                continue
            if p not in bids and p not in asks:
                # Wall is no longer resting in the orderbook
                lifetime = now - wall["first_seen"]
                symbol_walls.pop(p)
                # True spoofing signature:
                # A large wall was placed, but was PULLED within < 2.5s when price got close,
                # WITHOUT price actually crossing or penetrating the level (avoiding fill).
                dist_at_exit = abs(p - current_price) / current_price * 100 if current_price > 0 else 1.0
                if lifetime < 2.5 and 0.025 <= dist_at_exit < 0.15:
                    # Fleeting wall pulled just before fill
                    max_spoof_prob = max(max_spoof_prob, 0.70)

        return round(max_spoof_prob, 2)

    def cleanup_symbol(self, symbol: str):
        """Remove all tracked walls for a rotated-out symbol."""
        symbol = symbol.upper()
        self.tracked_walls.pop(symbol, None)
