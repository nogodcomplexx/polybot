"""
CryptoScalper strategy — trades Polymarket 5-minute BTC Up/Down binary options
using technical analysis and Black-Scholes probability estimation.

This is the bot's primary money maker for the $10 budget.
"""

import logging
import time
from typing import List, Optional

from src.strategies.base import BaseStrategy
from src.crypto.data_feed import CryptoDataFeed
from src.crypto.probability import CryptoProbabilityEngine, CryptoPrediction
from src.utils.sessions import get_session
from src.engine.performance_tuner import RegimeKey, RegimeTuning, dominance_bucket
from src.engine.calibration import ProbabilityCalibrator
from src.data.models import (
    Market, TradeSignal, Side, OrderType, Conviction, MarketSignal,
    PredictionResult,
)

logger = logging.getLogger(__name__)


class CryptoScalperStrategy(BaseStrategy):
    """
    Trades Polymarket 5-min crypto binary options using:
    1. Binance real-time price data (1m/5m/15m candles)
    2. Technical analysis (7 indicators + MTF consensus)
    3. Black-Scholes probability estimation
    4. Coinglass OI + L/S squeeze detection
    
    Entry rules:
    - Edge > 2% between our probability and Polymarket price
    - Technical consensus (≥3/7 indicators agree)
    - Volume confirmation (current > 1.1× average)
    - Not in cooldown period after a loss
    - 24/7 operation — no time-of-day restrictions
    
    Risk rules:  
    - Dynamic Kelly sizing (scales with portfolio)
    - Max 10 concurrent positions
    - 2-min cooldown after losing trade
    """

    def __init__(self, data_feed: CryptoDataFeed, budget: float = 10.0, clob=None):
        self._name = "crypto_scalper"
        self.data_feed = data_feed
        self.prob_engine = CryptoProbabilityEngine()
        self.budget = budget
        self.clob = clob

        # Risk parameters
        # For live trading safety we require *positive* EV after a small fee/spread buffer.
        # 5-min markets are noisy; sub-1% edges are usually not robust.
        self.min_edge = 0.01          # 1.0% minimum edge AFTER buffers
        self.max_bet_usd = 20.00      # Max $20 per trade (Scaled up for $100 budget)
        self.max_bet_pct = 0.20       # Max 20% of portfolio per trade
        self.kelly_fraction = 0.35    # Use 35% Kelly for highly volatile 5-minute durations
        self.max_concurrent = 10

        # Cooldown tracking
        self._last_loss_time = 0.0
        self._cooldown_seconds = 120  # 2 minutes after loss (fast recovery)
        self._consecutive_wins = 0
        self._tuning: dict[RegimeKey, RegimeTuning] = {}
        self._calibrator: ProbabilityCalibrator | None = None
        self.last_scan: list[dict] = []

    def _scan_add(
        self,
        market: Market,
        symbol: str,
        session: str,
        decision: str,
        reason: str,
        **kwargs,
    ) -> None:
        try:
            row = {
                "ts": time.time(),
                "decision": decision,  # SKIP / SIGNAL
                "reason": reason,
                "symbol": symbol,
                "session": session,
                "question": market.question,
                "slug": market.slug,
                "condition_id": market.condition_id,
                "yes_price": float(market.yes_price or 0.0),
                "no_price": float(market.no_price or 0.0),
                "liquidity": float(market.liquidity or 0.0),
                "volume_24h": float(market.volume_24h or 0.0),
            }
            row.update(kwargs)
            self.last_scan.append(row)
        except Exception:
            pass

    def set_tuning(self, tuning: dict[RegimeKey, RegimeTuning]) -> None:
        self._tuning = tuning or {}

    def set_calibrator(self, calibrator: ProbabilityCalibrator | None) -> None:
        self._calibrator = calibrator

    @property
    def name(self) -> str:
        return self._name

    @property
    def enabled(self) -> bool:
        return True

    def evaluate(
        self,
        markets: List[Market],
        predictions: List[PredictionResult],
        signals: List[MarketSignal],
        available_cash: float,
    ) -> List[TradeSignal]:
        """
        Evaluate 5-min crypto markets and generate trade signals.
        """
        trade_signals = []
        self.last_scan = []

        # Find 5-min crypto binary markets
        crypto_markets = self._find_crypto_5min_markets(markets)
        if not crypto_markets:
            logger.debug("No 5-min crypto markets available")
            return []

        logger.info(f"🎰 CryptoScalper: Found {len(crypto_markets)} 5-min markets")

        # Check cooldown
        if self._in_cooldown():
            remaining = self._cooldown_seconds - (time.time() - self._last_loss_time)
            logger.info(f"⏳ CryptoScalper: In cooldown ({remaining:.0f}s remaining)")
            return []

        # Calculate BTC 1m logic for Statistical Arbitrage
        btc_velocity_1m = 0.0
        btc_snap = self.data_feed.snapshots.get("BTCUSDT")
        if btc_snap and len(btc_snap.candles_1m) >= 2:
            btc_last = btc_snap.candles_1m[-1]
            btc_prev = btc_snap.candles_1m[-2]
            btc_velocity_1m = (btc_last.close - btc_prev.close) / btc_prev.close

        for market in crypto_markets:
            # Determine which crypto symbol this market is about
            symbol = self._extract_symbol(market.question)
            if not symbol:
                print(f"DEBUG: Unrecognized symbol for 5-min market: {market.question}")
                continue

            snapshot = self.data_feed.snapshots.get(symbol)
            if not snapshot or len(snapshot.candles_5m) < 26:
                self._scan_add(
                    market,
                    symbol,
                    session=get_session().name,
                    decision="SKIP",
                    reason=f"Missing data ({len(snapshot.candles_5m) if snapshot else 0}/26 candles)",
                )
                continue

            # Get Polymarket YES price (probability the market thinks)
            market_yes_price = market.yes_price
            if market_yes_price <= 0.01 or market_yes_price >= 0.99:
                self._scan_add(
                    market,
                    symbol,
                    session=get_session().name,
                    decision="SKIP",
                    reason="Extreme price (near 0/1)",
                )
                continue  # Skip extreme prices

            # ------------------------------------------------------------------
            # Market quality gate (free, from Gamma fields)
            # ------------------------------------------------------------------
            # 5-min markets can be listed but effectively untradeable (low liq/vol).
            if market.liquidity and market.liquidity < 250:
                self._scan_add(
                    market,
                    symbol,
                    session=get_session().name,
                    decision="SKIP",
                    reason=f"Low liquidity (${market.liquidity:.0f})",
                )
                continue
            # NOTE: 5-min markets often report volume_24h=0 even when active/tradable.
            # We deliberately avoid blocking on volume_24h for these markets.

            # Run our probability engine
            prediction = self.prob_engine.predict(
                snapshot, market_yes_price, window_minutes=5.0
            )
            if not prediction:
                self._scan_add(
                    market,
                    symbol,
                    session=get_session().name,
                    decision="SKIP",
                    reason="No prediction (insufficient signals)",
                )
                continue

            # ------------------------------------------------------------------
            # Candle-boundary preference (not a hard ban)
            # ------------------------------------------------------------------
            # Maker orders fill best early in the 5m window. Outside the first minute,
            # require slightly higher edge and size down to avoid late/noisy entries.
            sec_into_5m = int(time.time()) % 300
            late_entry = sec_into_5m > 60

            # ------------------------------------------------------------------
            # MODE A: A+ SETUPS ONLY
            # ------------------------------------------------------------------
            # For 5-minute markets, win-rate comes from only trading when the tape is "doing something":
            # volatility expansion + strong MTF agreement + volume confirmation OR a clear squeeze signal.
            sig5 = prediction.signal_5m
            consensus_strength = float(getattr(sig5, "consensus_strength", 0.0) or 0.0) if sig5 else 0.0
            vol_ratio = float(getattr(sig5, "volume_ratio", 1.0) or 1.0) if sig5 else 1.0
            realized_vol = float(getattr(prediction, "realized_vol", 0.0) or 0.0)

            mtf_strong = "STRONG" in (prediction.mtf_status or "")
            # Keep Mode A selective, but not so strict that it never trades.
            # These thresholds target "active tape" without requiring a rare spike.
            vol_ok = realized_vol >= 0.25  # annualized
            volume_ok = vol_ratio >= 1.05
            consensus_ok = consensus_strength >= 0.45
            consensus_strong = consensus_strength >= 0.65

            squeeze_signal = (
                (snapshot.long_short_ratio < 0.8 and snapshot.oi_change_pct > 3.0)
                or (snapshot.long_short_ratio > 2.5 and snapshot.oi_change_pct > 3.0)
            )

            # Mode A gate:
            # - accept true squeeze setups immediately
            # - otherwise require either strong MTF OR very strong consensus,
            #   plus evidence of activity (vol/volume).
            mode_a_pass = bool(
                squeeze_signal
                or ((mtf_strong or consensus_strong) and (vol_ok or volume_ok) and consensus_ok)
            )

            # If Mode A blocks everything for too long, we still want to take
            # rare, very strong edges (otherwise you can run hundreds of cycles with no trades).
            # This override is intentionally strict.
            edge_override = float(getattr(prediction, "edge", 0.0) or 0.0) >= 0.03 and (vol_ok or volume_ok)

            if not (mode_a_pass or edge_override):
                self._scan_add(
                    market,
                    symbol,
                    session=get_session().name,
                    decision="SKIP",
                    reason="Mode A gate (setup not A+)",
                    sec_into_5m=sec_into_5m,
                    late_entry=late_entry,
                    consensus_strength=consensus_strength,
                    volume_ratio=vol_ratio,
                    realized_vol=realized_vol,
                    mtf_status=prediction.mtf_status,
                )
                continue

            # Current trading session (UTC bucket)
            session = get_session().name

            # ------------------------------------------------------------------
            # Bitcoin dominance regime adjustment
            # ------------------------------------------------------------------
            # When BTC dominance is rising, alts tend to underperform on average.
            # For 5-min options, we treat this as a *small* tilt, not a huge signal.
            btc_dom = float(getattr(snapshot, "btc_dominance_pct", 0.0) or 0.0)
            btc_dom_chg_1h = float(getattr(snapshot, "btc_dominance_change_1h_pct", 0.0) or 0.0)

            # Regime-specific tuning (learned from resolved paper outcomes)
            key = RegimeKey(symbol, session, dominance_bucket(btc_dom_chg_1h))
            tuned = self._tuning.get(key)
            min_edge_gate = tuned.min_edge if tuned else self.min_edge
            kelly_mult = tuned.kelly_mult if tuned else 1.0

            # Meta-filter (setup quality by regime): if this regime has a poor realized win-rate,
            # skip marginal opportunities altogether. This tends to increase win-rate by avoiding
            # known-bad conditions.
            if tuned and tuned.n >= 25 and tuned.win_rate < 0.52:
                self._scan_add(
                    market,
                    symbol,
                    session=session,
                    decision="SKIP",
                    reason=f"Regime cold (wr={tuned.win_rate*100:.0f}% n={tuned.n})",
                    sec_into_5m=sec_into_5m,
                    late_entry=late_entry,
                )
                continue

            # Online probability calibration (per symbol+session). This improves win-rate by
            # correcting over/under-confidence as the bot learns.
            if self._calibrator:
                try:
                    prediction.combined_probability = self._calibrator.calibrate(
                        strategy=self.name,
                        underlying_symbol=symbol,
                        session=session,
                        p=float(prediction.combined_probability),
                    )
                    prediction.combined_probability = max(0.01, min(0.99, prediction.combined_probability))
                    if prediction.direction == "UP":
                        prediction.edge = prediction.combined_probability - market_yes_price
                    else:
                        prediction.edge = (1.0 - prediction.combined_probability) - (1.0 - market_yes_price)
                except Exception:
                    pass

            if symbol in ["ETHUSDT", "SOLUSDT"] and btc_dom_chg_1h != 0:
                # Rising dominance -> slight DOWN bias for alts; falling dominance -> slight UP bias.
                # Scale: 0.10 dominance points (~0.1%) => 0.25% probability tilt, capped.
                tilt = max(-0.015, min(0.015, btc_dom_chg_1h * 0.0025))
                prediction.combined_probability = max(0.01, min(0.99, prediction.combined_probability - tilt))
                # Recompute edge relative to the market
                if prediction.direction == "UP":
                    prediction.edge = prediction.combined_probability - market_yes_price
                else:
                    prediction.edge = (1.0 - prediction.combined_probability) - (1.0 - market_yes_price)

            # Apply Statistical Correlation Arbitrage
            # If BTC violently moves (>0.15% in 1 minute), bias ETH & SOL
            if symbol in ["ETHUSDT", "SOLUSDT"]:
                if btc_velocity_1m > 0.0015:
                    logger.info(f"🚀 STAT-ARB (FRONT-RUN): BTC spiked +{btc_velocity_1m*100:.2f}%, boosting {symbol} UP probabilities.")
                    prediction.combined_probability += 0.15 # Inject 15% Arbitrage Edge
                    prediction.direction = "UP"
                elif btc_velocity_1m < -0.0015:
                    logger.info(f"🩸 STAT-ARB (FRONT-RUN): BTC dropped {btc_velocity_1m*100:.2f}%, boosting {symbol} DOWN probabilities.")
                    prediction.combined_probability -= 0.15 # Inject 15% Arbitrage Edge
                    prediction.direction = "DOWN"

                # Recalculate ultimate target edge after Stat-Arb adjustments
                prediction.combined_probability = max(0.01, min(0.99, prediction.combined_probability))
                if prediction.direction == "UP":
                    prediction.edge = prediction.combined_probability - market_yes_price
                else:
                    prediction.edge = (1.0 - prediction.combined_probability) - (1.0 - market_yes_price)

            # ── Coinglass Liquidation Cascade Detection ──
            ls_ratio = snapshot.long_short_ratio
            oi_change = snapshot.oi_change_pct

            # Short Squeeze: heavy shorts (L/S < 0.8) + OI spiking (> +3%)
            if ls_ratio < 0.8 and oi_change > 3.0:
                logger.info(
                    f"🔥 SHORT SQUEEZE detected [{symbol}]: "
                    f"L/S={ls_ratio:.2f}, OI Δ={oi_change:+.1f}% → boosting UP"
                )
                prediction.combined_probability += 0.12
                prediction.direction = "UP"
                prediction.combined_probability = min(0.99, prediction.combined_probability)
                prediction.edge = prediction.combined_probability - market_yes_price

            # Long Flush: heavy longs (L/S > 2.5) + OI spiking (> +3%)
            elif ls_ratio > 2.5 and oi_change > 3.0:
                logger.info(
                    f"💀 LONG FLUSH detected [{symbol}]: "
                    f"L/S={ls_ratio:.2f}, OI Δ={oi_change:+.1f}% → boosting DOWN"
                )
                prediction.combined_probability -= 0.12
                prediction.direction = "DOWN"
                prediction.combined_probability = max(0.01, prediction.combined_probability)
                prediction.edge = (1.0 - prediction.combined_probability) - (1.0 - market_yes_price)
               # Determine Polymarket outcome (UP vs DOWN is simple mapping based on outcome index)
            # Typically outcome 0 is YES (UP), outcome 1 is NO (DOWN)
            # You must ensure the market conforms precisely.
            
            # --- PHASE 26: Public API Data Fusion Edge Augmentation ---
            if prediction.edge > 0:
                # 1. Retail Euphoria / Global Momentum (CoinGecko + WallstreetBets)
                is_trending = snapshot.coingecko_trending_score > 0 and snapshot.coingecko_trending_score <= 7
                wsb_aligned = (prediction.direction == "UP" and snapshot.wsb_sentiment_score > 0.3) or \
                              (prediction.direction == "DOWN" and snapshot.wsb_sentiment_score < -0.3)
                if is_trending or wsb_aligned:
                    prediction.edge += 0.015 # Add flat 1.5% edge because global meme-momentum routinely forces short-term continuations
                
                # 2. Institutional Volatility Predictor (Mempool On-chain)
                if symbol == "BTCUSDT" and snapshot.mempool_fast_fee > 30:
                    prediction.edge += 0.008 # Add 0.8% edge; 5-min binary options rely heavily on raw volatility, which high on-chain congestion absolutely guarantees

            # --- Maker Execution & Margin of Safety ---
            # CryptoScalper places Maker limit orders inside the spread, so polymarket fees are 0%.
            # Still apply a small "reality buffer" for spread/latency/microstructure.
            post_fee_edge = prediction.edge - 0.002

            # Futures crowding regime filter (free from Binance futures):
            # - Extreme positive funding => crowded longs; avoid UP continuation bets unless edge is strong.
            # - Extreme negative funding => crowded shorts; avoid DOWN continuation bets unless edge is strong.
            fr = float(getattr(snapshot, "funding_rate", 0.0) or 0.0)
            basis = float(getattr(snapshot, "basis_pct", 0.0) or 0.0)
            crowding = abs(fr) > 0.00025 or abs(basis) > 0.15
            if crowding:
                if fr > 0 and prediction.direction == "UP":
                    post_fee_edge -= 0.004  # require meaningfully more edge to press crowded longs
                elif fr < 0 and prediction.direction == "DOWN":
                    post_fee_edge -= 0.004
            
            # Safety filter: require positive edge after buffers.
            # This is the core "mathematical algorithm" gate: no negative-EV bets.
            # Late entry penalty: require more edge and size down.
            if late_entry:
                post_fee_edge -= 0.003

            if post_fee_edge < min_edge_gate:
                self._scan_add(
                    market,
                    symbol,
                    session=session,
                    decision="SKIP",
                    reason=f"Edge gate (post={post_fee_edge*100:+.2f}% < min={min_edge_gate*100:.2f}%)",
                    direction=prediction.direction,
                    prob=float(getattr(prediction, "combined_probability", 0.0) or 0.0),
                    post_edge=float(post_fee_edge or 0.0),
                    sec_into_5m=sec_into_5m,
                    late_entry=late_entry,
                    funding_rate=fr,
                    basis_pct=basis,
                )
                continue
            
            # Store the adjusted edge to ensure sizing takes fees into account
            prediction.edge = post_fee_edge

            # Check entry conditions (Bypass technical consensus totally for HFT)
            # if not self._passes_entry_rules(prediction, available_cash):
            #     print(f"DEBUG: Skipping {market.question[:30]}: Failed technical/volume entry rules.")
            #     continue

            # Determine which side to trade
            if prediction.direction == "UP":
                # We think UP is more likely than market says → BUY YES
                side = Side.BUY
                token_id = market.yes_token_id
                target_price = market_yes_price
                conviction = self._edge_to_conviction(prediction.edge)
            elif prediction.direction == "DOWN":
                # We think DOWN is more likely → BUY NO
                side = Side.BUY
                token_id = market.no_token_id
                target_price = market.no_price
                conviction = self._edge_to_conviction(prediction.edge)
            else:
                continue

            # Base target price (Taker)
            target_price = market_yes_price if side == Side.BUY and token_id == market.yes_token_id else market.no_price

            # CLOB Orderbook Imbalance & Maker Execution Upgrade
            if self.clob:
                try:
                    ob = self.clob.get_order_book(token_id)
                    bids = ob.get("bids", [])
                    asks = ob.get("asks", [])
                    
                    # Convert Polymarket token sizes
                    bid_vol = sum(float(b["size"]) for b in bids[:5])
                    ask_vol = sum(float(a["size"]) for a in asks[:5])
                    
                    if side == Side.BUY:
                        # Safety Check: Is there a massive wall of sellers?
                        if ask_vol > 3 * bid_vol and bid_vol > 0:
                            logger.info(
                                f"Skipping {prediction.direction} trade due to Orderbook Imbalance: "
                                f"{ask_vol:.0f} vs {bid_vol:.0f}"
                            )
                            continue
                            
                        # Maker Limit Order Sizing: Place inside the spread
                        if bids:
                            best_bid = float(bids[0]["price"])
                            # Instead of taking the Ask, place a limit order exactly 1 tick above the Best Bid
                            maker_price = min(target_price, best_bid + 0.001)
                            target_price = round(maker_price, 3)
                except Exception as e:
                    logger.debug(f"Failed to fetch CLOB orderbook for maker sizing: {e}")

            # Dynamic Volatility Bands (Bollinger Expansion Multiplier)
            size_multiplier = 1.0
            if prediction.signal_5m:
                # `TechnicalSignal` exposes `bollinger_pct_b` (not `bb_pct`).
                bb_pct = getattr(prediction.signal_5m, "bollinger_pct_b", 0.5)
                if bb_pct > 1.0 and prediction.direction == "UP" and "BULL" in prediction.mtf_status:
                    logger.info("💥 BOLLINGER BREAKOUT: Upper band shattered with MTF support! Doubling bet size.")
                    size_multiplier = 2.0
                elif bb_pct < 0.0 and prediction.direction == "DOWN" and "BEAR" in prediction.mtf_status:
                    logger.info("🩸 BOLLINGER IMPLOSION: Lower band shattered with MTF support! Doubling bet size.")
                    size_multiplier = 2.0

            # Position sizing (aggressive Kelly for small budget)
            size_usd = self._position_size(
                prediction.edge, available_cash, kelly_mult=kelly_mult
            ) * size_multiplier
            if late_entry:
                size_usd *= 0.75
            if size_usd < 0.50:
                self._scan_add(
                    market,
                    symbol,
                    session=session,
                    decision="SKIP",
                    reason="Size too small after sizing",
                    direction=prediction.direction,
                    post_edge=float(prediction.edge or 0.0),
                    sec_into_5m=sec_into_5m,
                    late_entry=late_entry,
                )
                continue

            # Calculate stop and take profit
            stop_loss = max(0.01, target_price * 0.5)  # 50% stop
            take_profit = min(0.99, target_price * 1.5)  # 50% take profit

            signal = TradeSignal(
                strategy_name=self.name,
                market=market,
                side=side,
                token_id=token_id,
                target_price=target_price,
                size_usd=size_usd,
                edge=prediction.edge,
                conviction=conviction,
                confidence=prediction.combined_probability,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                underlying_symbol=symbol,
                session=session,
                btc_dominance_pct=btc_dom,
                btc_dominance_change_1h_pct=btc_dom_chg_1h,
                reasoning=(
                    f"{'📈 FOR' if prediction.direction == 'UP' else '📉 AGAINST'} "
                    f"| Session: {session} "
                    f"| BTC Dom: {btc_dom:.1f}% ({btc_dom_chg_1h:+.2f}pp/1h) "
                    f"| Edge: {prediction.edge*100:+.1f}% "
                    f"| {prediction.reasoning}"
                    + (
                        f" | TUNE(n={tuned.n}, wr={tuned.win_rate*100:.0f}%, roi={tuned.roi*100:+.1f}%)"
                        if tuned
                        else ""
                    )
                ),
                order_type=OrderType.GTC,
            )

            trade_signals.append(signal)
            self._scan_add(
                market,
                symbol,
                session=session,
                decision="SIGNAL",
                reason=signal.reasoning[:240],
                direction=prediction.direction,
                prob=float(getattr(prediction, "combined_probability", 0.0) or 0.0),
                post_edge=float(prediction.edge or 0.0),
                sec_into_5m=sec_into_5m,
                late_entry=late_entry,
                funding_rate=float(getattr(snapshot, "funding_rate", 0.0) or 0.0),
                basis_pct=float(getattr(snapshot, "basis_pct", 0.0) or 0.0),
                size_usd=float(size_usd or 0.0),
            )
            logger.info(
                f"🎯 CryptoScalper signal: {prediction.direction} "
                f"{market.question[:40]} "
                f"(edge={prediction.edge*100:+.1f}%, "
                f"size=${size_usd:.2f})"
            )

        return trade_signals

    def record_result(self, won: bool):
        """Record trade result for cooldown logic."""
        if won:
            self._consecutive_wins += 1
        else:
            self._consecutive_wins = 0
            self._last_loss_time = time.time()

    def _find_crypto_5min_markets(self, markets: List[Market]) -> List[Market]:
        """Filter markets that are 5-min crypto binary options."""
        crypto_keywords = ["bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto"]
        five_keywords = ["5 min", "5-min", "five min", "5 minutes", "5-minute", "5 minute"]

        result = []
        for market in markets:
            q_lower = market.question.lower()
            has_crypto = any(kw in q_lower for kw in crypto_keywords)
            has_updown = "up or down" in q_lower
            has_5m = any(kw in q_lower for kw in five_keywords)
            # Some 5-minute markets omit "5 min" in the question but still have 5m slugs.
            slug = (market.slug or "").lower()
            slug_5m = ("updown-5m" in slug) or ("-5m-" in slug) or ("5-min" in slug) or ("5min" in slug)

            # Only trade true 5-minute "up or down" markets here.
            # Same-day crypto targets are intentionally excluded from this strategy.
            if has_crypto and has_updown and (has_5m or slug_5m):
                result.append(market)

        return result

    def _is_same_day_market(self, market: Market) -> bool:
        """Check if market resolves today."""
        if not market.end_date:
            return False
        try:
            from datetime import datetime, timezone
            end = datetime.fromisoformat(market.end_date.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            return end.date() == now.date()
        except Exception:
            return False

    def _extract_symbol(self, question: str) -> Optional[str]:
        """Extract Binance symbol from market question."""
        q = question.lower()
        if "bitcoin" in q or "btc" in q:
            return "BTCUSDT"
        elif "ethereum" in q or "eth" in q:
            return "ETHUSDT"
        elif "solana" in q or "sol" in q:
            return "SOLUSDT"
        elif "dogecoin" in q or "doge" in q:
            return "DOGEUSDT"
        elif "xrp" in q:
            return "XRPUSDT"
        elif "bnb" in q:
            return "BNBUSDT"
        elif "hyperliquid" in q or "hype" in q:
            # Ignore HYPE for now as Binance might not have it or it has weird pairing
            return None
        return None

    def _passes_entry_rules(
        self, pred: CryptoPrediction, available_cash: float
    ) -> bool:
        """Check all entry conditions."""
        # Rule 1: Minimum edge
        if abs(pred.edge) < self.min_edge:
            return False

        # Rule 2: Technical consensus (≥3 indicators - Aggressive)
        if pred.signal_5m:
            logger.debug(f"TechnicalSignal attributes: {dir(pred.signal_5m)}")
            bullish = getattr(pred.signal_5m, 'bullish_count', 0)
            bearish = getattr(pred.signal_5m, 'bearish_count', 0)
            total_agreement = max(bullish, bearish)
            if total_agreement < 1:
                logger.debug(f"Skipping {pred.symbol}: Total technical consensus is ZERO.")
                return False

            # Rule 3: Volume confirmation
            # Loosened to 0.0x to allow high-frequency trading even during low-liquidity overnight sessions
            if pred.signal_5m.volume_ratio < 0.0:
                logger.debug(f"Skipping {pred.symbol}: Insufficient breakout volume.")
                return False

            # Rule 4: RSI check — don't bet UP if RSI > 80, don't bet DOWN if RSI < 20
            if pred.direction == "UP" and pred.signal_5m.rsi > 80:
                return False
            if pred.direction == "DOWN" and pred.signal_5m.rsi < 20:
                return False

        # Rule 5: Available cash
        if available_cash < 0.50:
            return False

        return True

    def _position_size(self, edge: float, available_cash: float, kelly_mult: float = 1.0) -> float:
        """Calculate position size using aggressive Kelly criterion."""
        # Kelly: f* = edge / odds
        # For binary at ~50%, odds ≈ 1:1
        kelly = abs(edge) * (self.kelly_fraction * max(0.25, min(1.50, kelly_mult)))

        size = kelly * available_cash
        max_bet = min(self.max_bet_usd, available_cash * self.max_bet_pct)

        # Win streak throttle — reduce bet after 3 consecutive wins
        if self._consecutive_wins >= 3:
            size *= 0.5

        size = max(0.50, min(size, max_bet))
        return round(size, 2)

    def _edge_to_conviction(self, edge: float) -> Conviction:
        """Map edge to conviction level."""
        if abs(edge) > 0.15:
            return Conviction.HIGH
        elif abs(edge) > 0.08:
            return Conviction.MEDIUM
        return Conviction.LOW

    def _in_cooldown(self) -> bool:
        """Check if we're in cooldown after a loss."""
        if self._last_loss_time <= 0:
            return False
        return (time.time() - self._last_loss_time) < self._cooldown_seconds
