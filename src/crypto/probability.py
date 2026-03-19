"""
Probability engine for crypto binary options.
Uses modified Black-Scholes N(d2) combined with technical indicator consensus
to estimate the probability of BTC being up/down within a time window.
"""

import math
import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

from src.crypto.data_feed import Candle, CryptoSnapshot
from src.crypto.indicators import TechnicalSignal, compute_indicators

logger = logging.getLogger(__name__)

# Cumulative normal distribution (no scipy needed)
def _norm_cdf(x: float) -> float:
    """Approximation of the cumulative normal distribution N(x)."""
    # Abramowitz & Stegun approximation 26.2.17
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2)

    return 0.5 * (1.0 + sign * y)


@dataclass
class CryptoPrediction:
    """Result of the crypto probability engine."""
    symbol: str
    current_price: float
    direction: str           # "UP" or "DOWN"
    bs_probability: float    # Black-Scholes raw probability
    tech_probability: float  # Technical analysis probability
    combined_probability: float  # Weighted combination
    edge: float              # combined_prob - market_price
    realized_vol: float      # Annualized realized volatility
    momentum: float          # Current momentum (drift)
    signal_5m: Optional[TechnicalSignal] = None
    mtf_status: str = "NEUTRAL"
    reasoning: str = ""


class CryptoProbabilityEngine:
    """
    Estimate the probability that BTC will go UP or DOWN in the next 5 minutes.
    
    Two-pronged approach:
    1. Black-Scholes N(d2) — mathematical probability from volatility + drift
    2. Technical consensus — RSI, MACD, EMA, Volume confirmation
    
    Final probability = 50% BS + 50% Technicals (weighted blend)
    """

    # Minutes in a year for annualization
    MINUTES_PER_YEAR = 365.25 * 24 * 60  # ~525,960

    def __init__(self):
        # Weights for combining BS and technicals
        self.bs_weight = 0.5
        self.tech_weight = 0.5
        # Minimum edge to suggest a trade
        self.min_edge = 0.05  # 5%

    def predict(
        self,
        snapshot: CryptoSnapshot,
        market_yes_price: float = 0.5,
        window_minutes: float = 5.0,
    ) -> Optional[CryptoPrediction]:
        """
        Predict the probability of price going UP in the next window.
        
        Args:
            snapshot: Current crypto data with candles
            market_yes_price: Polymarket YES price (probability implied by market)
            window_minutes: Time window in minutes (default 5)
        
        Returns:
            CryptoPrediction with direction, probabilities, and edge
        """
        candles = snapshot.candles_5m
        if len(candles) < 26:
            logger.warning("Not enough candles for prediction")
            return None

        current_price = snapshot.price
        if current_price <= 0:
            return None

        # 1. Calculate realized volatility from recent candles
        realized_vol = self._realized_volatility(candles, lookback=24)  # 2 hours

        # 2. Calculate momentum (drift) from recent candles
        momentum = self._momentum_drift(candles, lookback=12)  # 1 hour

        # 3. Black-Scholes probability of price being higher after window
        T = window_minutes / self.MINUTES_PER_YEAR
        bs_prob_up = self._black_scholes_prob_up(
            current_price, realized_vol, momentum, T
        )

        # 4. Multi-Timeframe Technical Analysis (MTF)
        tech_5m = compute_indicators(snapshot.candles_5m) if len(snapshot.candles_5m) > 26 else None
        tech_1m = compute_indicators(snapshot.candles_1m) if len(snapshot.candles_1m) > 26 else None
        tech_15m = compute_indicators(snapshot.candles_15m) if len(snapshot.candles_15m) > 26 else None
        
        mtf_status = "NEUTRAL (Conflicting Timeframes)"
        tech_prob_up = 0.5
        
        if tech_5m and tech_1m and tech_15m:
            if tech_1m.consensus_direction == "UP" and tech_5m.consensus_direction == "UP" and tech_15m.consensus_direction == "UP":
                mtf_status = "MTF STRONG BULLISH (1m/5m/15m aligned)"
                tech_prob_up = 0.8  # Strong consensus
            elif tech_1m.consensus_direction == "DOWN" and tech_5m.consensus_direction == "DOWN" and tech_15m.consensus_direction == "DOWN":
                mtf_status = "MTF STRONG BEARISH (1m/5m/15m aligned)"
                tech_prob_up = 0.2  # Strong consensus

        # 5. Combine probabilities
        combined_up = (
            self.bs_weight * bs_prob_up +
            self.tech_weight * tech_prob_up
        )

        # Clamp to [0.05, 0.95] — never 100% certain
        combined_up = max(0.05, min(0.95, combined_up))

        # 6. Determine direction and edge
        # For "UP" market: our_prob vs market YES price
        edge_up = combined_up - market_yes_price
        edge_down = (1 - combined_up) - (1 - market_yes_price)

        if abs(edge_up) > abs(edge_down):
            direction = "UP"
            edge = edge_up
            final_prob = combined_up
        else:
            direction = "DOWN"
            edge = edge_down
            final_prob = 1 - combined_up

        # Build reasoning
        reasons = []
        if tech_5m:
            reasons.append(f"MTF: {mtf_status}")
            reasons.append(f"5m RSI={tech_5m.rsi:.1f}")
            reasons.append(f"5m Vol_ratio={tech_5m.volume_ratio:.2f}")
        reasons.append(f"σ={realized_vol*100:.1f}%/yr")
        reasons.append(f"μ={momentum*100:.4f}%/yr")

        return CryptoPrediction(
            symbol=snapshot.symbol,
            current_price=current_price,
            direction=direction,
            bs_probability=bs_prob_up if direction == "UP" else (1 - bs_prob_up),
            tech_probability=tech_prob_up if direction == "UP" else (1 - tech_prob_up),
            combined_probability=final_prob,
            edge=edge,
            realized_vol=realized_vol,
            momentum=momentum,
            signal_5m=tech_5m,
            mtf_status=mtf_status,
            reasoning=" | ".join(reasons),
        )

    def _black_scholes_prob_up(
        self,
        price: float,
        vol: float,
        drift: float,
        T: float,
    ) -> float:
        """
        Calculate probability that price will be higher at time T.
        
        Uses N(d2) from Black-Scholes:
          d2 = [ln(S/K) + (μ - σ²/2) × T] / (σ × √T)
        
        For "will price go UP?", K = S (strike = current price)
        So ln(S/K) = 0, simplifying to:
          d2 = (μ - σ²/2) × √T / σ
        """
        if vol <= 0 or T <= 0:
            return 0.5  # No information

        sqrt_T = math.sqrt(T)
        d2 = (drift - 0.5 * vol * vol) * sqrt_T / vol

        prob_up = _norm_cdf(d2)

        # Sanity bounds
        return max(0.05, min(0.95, prob_up))

    def _tech_probability(self, signal: TechnicalSignal) -> float:
        """
        Convert technical signal consensus into a probability of UP movement.
        
        Strong bullish consensus   → 0.70-0.80
        Moderate bullish            → 0.55-0.65
        Neutral                     → 0.45-0.55
        Moderate bearish            → 0.35-0.45
        Strong bearish              → 0.20-0.30
        """
        net_score = signal.bullish_count - signal.bearish_count
        total = max(signal.bullish_count + signal.bearish_count, 1)

        # Normalize to [-1, 1] range
        normalized = net_score / 7.0  # 7 total indicators

        # Map to probability: 0.5 + normalized * 0.3
        # This gives range [0.20, 0.80]
        prob = 0.5 + normalized * 0.3

        # Add volume confirmation boost
        if signal.volume_ratio > 1.5 and abs(normalized) > 0.2:
            # High volume confirms the signal
            prob += 0.05 * (1 if normalized > 0 else -1)

        # 🐋 Whale Alert (Massive volume block trade front-running)
        if signal.volume_ratio > 3.0 and abs(normalized) > 0.4:
            logger.info("🐋 WHALE ALERT: Massive volume block detected! Injecting high conviction multiplier.")
            prob += 0.10 * (1 if normalized > 0 else -1)

        return max(0.15, min(0.85, prob))

    def _realized_volatility(
        self, candles: List[Candle], lookback: int = 24
    ) -> float:
        """
        Calculate annualized realized volatility from log returns.
        Uses the last `lookback` candles (e.g., 24 × 5min = 2 hours).
        """
        if len(candles) < lookback + 1:
            lookback = len(candles) - 1

        closes = [c.close for c in candles[-(lookback + 1):]]

        log_returns = []
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                log_returns.append(math.log(closes[i] / closes[i - 1]))

        if len(log_returns) < 2:
            return 0.5  # Default moderate volatility

        mean_return = sum(log_returns) / len(log_returns)
        variance = sum((r - mean_return) ** 2 for r in log_returns) / (len(log_returns) - 1)
        std = math.sqrt(variance)

        # Annualize: multiply by sqrt(periods_per_year)
        # 5-min candles → 365.25 * 24 * 12 = 105,120 periods/year
        periods_per_year = 365.25 * 24 * 12
        annualized_vol = std * math.sqrt(periods_per_year)

        return max(0.01, annualized_vol)  # Floor at 1%

    def _momentum_drift(self, candles: List[Candle], lookback: int = 12) -> float:
        """
        Estimate momentum (annualized drift) from recent candles.
        Positive drift = trending up, negative = trending down.
        """
        if len(candles) < lookback + 1:
            lookback = len(candles) - 1

        closes = [c.close for c in candles[-(lookback + 1):]]

        if len(closes) < 2 or closes[0] <= 0:
            return 0.0

        # Simple log return over the lookback period
        total_return = math.log(closes[-1] / closes[0])

        # Annualize
        periods_per_year = 365.25 * 24 * 12
        annualized_drift = total_return * (periods_per_year / lookback)

        # Clamp to reasonable range
        return max(-5.0, min(5.0, annualized_drift))
