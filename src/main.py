"""
PolyBot — Main entry point.
Async main loop: discover → ingest news → analyze → predict → trade → monitor.
"""

import asyncio
import argparse
import logging
import signal
import sys
import time
import random
import os
import httpx
from datetime import datetime, timezone
from typing import Optional
from dataclasses import asdict

# Ensure UTF-8 output for emojis on Windows
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    except Exception:
        pass

from rich.logging import RichHandler

from src.config import load_config, BotConfig
from src.api.gamma_client import GammaClient
from src.api.clob_client import PolymarketCLOB
from src.api.auth import build_clob_client
from src.data.db import Database
from src.news.ingest import NewsIngestor
from src.news.sentiment import SentimentAnalyzer
from src.news.mapper import NewsMapper
from src.engine.discovery import MarketDiscovery
from src.engine.predictor import Predictor
from src.engine.scorer import EdgeScorer
from src.strategies.orchestrator import StrategyOrchestrator
from src.risk.manager import RiskManager
from src.execution.executor import ExecutionEngine
from src.dashboard.cli import Dashboard
from src.dashboard.alerts import DiscordAlerter
from src.api.web import DashboardAPI
from src.data.models import PortfolioSnapshot, Position
from src.crypto.data_feed import CryptoDataFeed
from src.crypto.coinglass import CoinglassClient
from src.engine.performance_tuner import PerformanceTuner
from src.engine.calibration import ProbabilityCalibrator

logger = logging.getLogger("polybot")


class PolyBot:
    """Main bot class — orchestrates the full trading pipeline."""

    def __init__(self, config: BotConfig, show_dashboard: bool = True):
        self.config = config
        self.show_dashboard = show_dashboard
        self.running = False
        self.cycle_count = 0

        # Components (initialized in setup)
        self.gamma: Optional[GammaClient] = None
        self.clob: Optional[PolymarketCLOB] = None
        self.db: Optional[Database] = None
        self.news: Optional[NewsIngestor] = None
        self.sentiment: Optional[SentimentAnalyzer] = None
        self.mapper: Optional[NewsMapper] = None
        self.discovery: Optional[MarketDiscovery] = None
        self.predictor: Optional[Predictor] = None
        self.scorer: Optional[EdgeScorer] = None
        self.orchestrator: Optional[StrategyOrchestrator] = None
        self.risk: Optional[RiskManager] = None
        self.executor: Optional[ExecutionEngine] = None
        self.dashboard: Optional[Dashboard] = None
        self.alerts: Optional[DiscordAlerter] = None
        self.web_api: Optional[DashboardAPI] = None
        self.crypto_feed: Optional[CryptoDataFeed] = None
        self.coinglass: Optional[CoinglassClient] = None
        self.last_signals: list = []
        self._bg_tasks: list[asyncio.Task] = []
        self._session_start_ts: float = time.time()
        self._nofill_window: dict[str, list[float]] = {}

    async def setup(self):
        """Initialize all components."""
        # Mark start of this bot session (used to ignore legacy DB history in session-based rails)
        self._session_start_ts = time.time()
        logger.info("🤖 PolyBot starting up...")
        logger.info(f"Mode: {'📝 PAPER TRADING' if self.config.paper_trading else '🔴 LIVE TRADING'}")
        logger.info(f"Budget: ${self.config.starting_budget:.2f}")

        # Database
        self.db = Database()
        await self.db.connect()

        # Polymarket API
        self.gamma = GammaClient(self.config.polymarket.gamma_host)
        if not self.config.paper_trading and self.config.polymarket.private_key:
            try:
                raw_client = build_clob_client(self.config.polymarket)
                self.clob = PolymarketCLOB(raw_client)
                logger.info("CLOB client authenticated for live trading")
            except Exception as e:
                logger.error(f"CLOB auth failed: {e} — falling back to paper trading")
                self.config.paper_trading = True
        else:
            logger.info("Paper trading mode — CLOB client not required")

        # News & Sentiment
        self.news = NewsIngestor(self.config)
        self.sentiment = SentimentAnalyzer(self.config.sentiment.model)
        self.mapper = NewsMapper()

        # Engine
        self.discovery = MarketDiscovery(self.config.discovery)
        self.predictor = Predictor(self.config.prediction)
        self.scorer = EdgeScorer()

        # Crypto data feed
        self.crypto_feed = CryptoDataFeed()
        await self.crypto_feed.start()
        await self.crypto_feed.refresh()

        # Coinglass derivatives data
        import os
        cg_key = os.getenv("COINGLASS_API_KEY", "")
        if cg_key:
            self.coinglass = CoinglassClient(api_key=cg_key, poll_interval=60)
            await self.coinglass.start()
        else:
            logger.warning("No COINGLASS_API_KEY found — skipping derivatives data")

        # Strategies (with crypto data feed and CLOB)
        self.orchestrator = StrategyOrchestrator(
            self.config.strategies, self.config.risk,
            crypto_data_feed=self.crypto_feed,
            budget=self.config.starting_budget,
            clob=self.clob,
        )

        # Risk
        self.risk = RiskManager(self.config.risk, self.config.starting_budget)

        # Execution
        self.executor = ExecutionEngine(
            clob=self.clob,
            db=self.db,
            risk=self.risk,
            config=self.config.execution,
            paper_trading=self.config.paper_trading,
        )

        # Dashboard & Alerts
        self.dashboard = Dashboard()
        self.alerts = DiscordAlerter(
            self.config.alerts.discord_webhook_url,
            self.config.alerts.discord_enabled,
        )
        self.web_api = DashboardAPI(self)

        logger.info("✅ All components initialized")

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down PolyBot...")
        self.running = False
        # Stop background tasks first so they don't use closed clients.
        for t in list(self._bg_tasks):
            try:
                t.cancel()
            except Exception:
                pass
        if self._bg_tasks:
            try:
                await asyncio.gather(*self._bg_tasks, return_exceptions=True)
            except Exception:
                pass
        self._bg_tasks = []
        if self.gamma:
            await self.gamma.close()
        if self.crypto_feed:
            await self.crypto_feed.close()
        if self.news:
            await self.news.close()
        if self.alerts:
            await self.alerts.close()
        if self.db:
            await self.db.close()
        logger.info("👋 PolyBot stopped")

    async def _poll_resolutions(self):
        """Background task to poll Gamma API for market resolutions and settle paper trades."""
        while self.running:
            try:
                if not self.running or not self.db or not self.gamma:
                    await asyncio.sleep(1)
                    continue
                unsettled = await self.db.get_unsettled_trades()
                if unsettled:
                    logger.debug(f"🔍 Checking resolution for {len(unsettled)} open paper trades...")
                    for trade in unsettled:
                        try:
                            # 1. Fetch live market data
                            m_raw = await self.gamma.get_market(trade.condition_id)
                            if not m_raw or not m_raw.get("closed"):
                                continue

                            # 2. Fetch resolution status from CLOB API directly
                            # The Clob endpoint contains actual "winner" tags for closed markets
                            url = f"https://clob.polymarket.com/markets/{trade.condition_id}"
                            async with httpx.AsyncClient() as client:
                                resp = await client.get(url, timeout=10.0)
                                if resp.status_code != 200:
                                    logger.debug(f"Failed to fetch CLOB data for {trade.condition_id}")
                                    continue
                                clob_data = resp.json()

                            # 3. Determine if our specific token won
                            tokens = clob_data.get("tokens", [])
                            if not tokens:
                                continue
                                
                            won = False
                            lost = False
                            for t in tokens:
                                if t.get("token_id") == trade.token_id:
                                    if t.get("winner") is True:
                                        won = True
                                    elif t.get("winner") is False and clob_data.get("closed"):
                                        # Only explicitly mark lost if another token has winner=true
                                        # But wait, in Polymarket when a market is resolved, closed is true, 
                                        # and exactly one token has winner=true. 
                                        pass
                                
                            # Check if ANY token is winning to confirm the market is fully determined
                            any_winner = any(t.get("winner") is True for t in tokens)
                            if not any_winner:
                                # Market is closed but oracle hasn't settled the winner yet
                                continue
                            
                            if won:
                                # WIN
                                trade.pnl = (1.0 - trade.price) * trade.size
                                trade.status = "WIN"
                                await self.db.save_trade(trade)
                                self.risk.cash += (trade.cost_usd + trade.pnl)
                                logger.info(f"🏆 Won paper trade on {trade.market_question} (+${trade.pnl:.2f})")
                                # Close any open position for this token (keep UI consistent)
                                await self.db.delete_position(trade.token_id)
                            else:
                                # LOSS
                                trade.pnl = -trade.cost_usd
                                trade.status = "LOSS"
                                await self.db.save_trade(trade)
                                logger.info(f"💀 Lost paper trade on {trade.market_question} (-${trade.cost_usd:.2f})")
                                # Close any open position for this token (keep UI consistent)
                                await self.db.delete_position(trade.token_id)

                            # Brief sleep so we don't spam endpoints

                        except Exception as e:
                            logger.error(f"Error resolving trade {trade.id}: {e}")
                            
            except Exception as e:
                logger.error(f"Resolution poller error: {e}")
                
            await asyncio.sleep(60)

    async def _poll_paper_fills(self):
        """
        Background task to simulate maker fills for paper orders.

        - Paper orders are created with status=OPEN.
        - If the market price for the token touches our limit within TTL, mark FILLED and open a position.
        - Otherwise cancel after TTL.
        """
        # Give paper orders enough time to potentially "touch" the limit.
        ttl_seconds = 45
        check_interval = 2.0
        # Maker orders can fill slightly beyond the strict tick boundary.
        price_epsilon = 0.002
        max_replaces = 2
        replace_after_seconds = 8.0
        tick = 0.001

        while self.running:
            try:
                if not self.running or not self.db or not self.gamma or not self.executor or not self.risk:
                    await asyncio.sleep(check_interval)
                    continue

                open_orders = await self.db.get_open_paper_orders()
                now = time.time()
                for trade in open_orders:
                    try:
                        age = now - float(trade.created_at or now)
                        if age > ttl_seconds:
                            trade.status = "CANCELLED"
                            await self.db.save_trade(trade)
                            # Toxic no-fill blocker: repeated OPEN→CANCELLED (no fills) means
                            # we're not getting queue priority or the market is untradeable.
                            if float(trade.cost_usd or 0.0) <= 0.0:
                                sym = (getattr(trade, "underlying_symbol", "") or "").upper()
                                if sym:
                                    w = self._nofill_window.get(sym, [])
                                    w.append(now)
                                    cutoff = now - 10 * 60
                                    w = [t for t in w if t >= cutoff]
                                    self._nofill_window[sym] = w
                                    if len(w) >= 6:
                                        # Cool down only this symbol; keep other symbols trading.
                                        self.risk.activate_symbol_cooldown(
                                            sym,
                                            5 * 60,
                                            reason=f"Too many no-fills/cancels ({len(w)}/10m)",
                                        )
                            continue

                        m_raw = await self.gamma.get_market(trade.condition_id)
                        if not m_raw:
                            continue

                        tokens_raw = m_raw.get("clobTokenIds") or m_raw.get("clob_token_ids") or []
                        if isinstance(tokens_raw, str):
                            import json
                            try:
                                tokens_raw = json.loads(tokens_raw)
                            except Exception:
                                parts = [p.strip() for p in tokens_raw.replace("[", "").replace("]", "").split(",")]
                                tokens_raw = [p.strip('"').strip() for p in parts if p.strip()]
                        tokens = tokens_raw if isinstance(tokens_raw, list) else []
                        prices = m_raw.get("outcomePrices") or []
                        if isinstance(prices, str):
                            import json
                            prices = json.loads(prices)

                        if not tokens or not prices:
                            continue

                        trade_limit_guess = float(trade.price) if float(trade.price) > 0 else 0.0
                        try:
                            idx = tokens.index(trade.token_id)
                            current_px = float(prices[idx])
                        except (ValueError, IndexError):
                            # If Gamma returns a different token id list than what we stored
                            # at order-creation time, fall back to choosing the outcome price
                            # closest to our limit price.
                            try:
                                candidates = [float(p) for p in prices]
                            except Exception:
                                continue
                            if not candidates:
                                continue
                            current_px = min(candidates, key=lambda x: abs(x - trade_limit_guess))

                        limit_px = float(trade.price) if float(trade.price) > 0 else current_px
                        if limit_px <= 0:
                            continue
                        # Some Gamma responses can briefly return 0/0 outcomePrices
                        # for very short-lived markets. If that happens, fall back
                        # to our limit price so we still generate learning data.
                        if current_px <= 0:
                            current_px = limit_px

                        # Optional cancel/replace ladder (still maker-ish):
                        # if price moved away and we haven't been filled for a bit, nudge limit up by 1 tick.
                        if (
                            age >= replace_after_seconds
                            and int(getattr(trade, "replace_count", 0) or 0) < max_replaces
                            and current_px > limit_px + (2 * tick)
                        ):
                            trade.replace_count = int(getattr(trade, "replace_count", 0) or 0) + 1
                            trade.price = round(min(0.99, limit_px + tick), 3)
                            await self.db.save_trade(trade)
                            continue

                        # BUY limit touch: current price <= limit + eps.
                        # Additionally, if we're close to expiry and price is still
                        # reasonably near our limit, attempt a late fill (to avoid
                        # a pathological OPEN→CANCELLED with zero learning data).
                        late_fill_window = age >= (ttl_seconds * 0.85)
                        within_late_eps = current_px <= limit_px + (4 * price_epsilon)
                        if current_px <= limit_px + price_epsilon or (
                            late_fill_window and within_late_eps
                        ):
                            # Fill probability & partial fill simulation.
                            # Touch does NOT guarantee fill (queue position / maker competition).
                            liq = float(m_raw.get("liquidity") or 0.0)
                            vol24 = float(m_raw.get("volume24hr") or 0.0)

                            # Base fill probability when touched.
                            fill_p = 0.72
                            if liq >= 5000:
                                fill_p += 0.12
                            elif liq >= 1000:
                                fill_p += 0.06
                            if vol24 >= 10000:
                                fill_p += 0.12
                            elif vol24 >= 1000:
                                fill_p += 0.06

                            # Very old order -> less likely to be at the front.
                            fill_p -= min(0.07, age / ttl_seconds * 0.07)
                            fill_p = max(0.20, min(0.95, fill_p))

                            if random.random() > fill_p:
                                continue

                            intended = float(getattr(trade, "intended_cost_usd", 0.0) or 0.0)
                            already_filled = float(trade.cost_usd or 0.0)
                            remaining = max(0.0, intended - already_filled)
                            if remaining <= 0.0:
                                continue

                            # Partial fill fraction biased towards partials on low-liquidity.
                            frac = 1.0
                            if liq < 1500 or vol24 < 1500:
                                frac = random.uniform(0.25, 0.75)
                            else:
                                frac = random.uniform(0.50, 1.00)

                            fill_cost = min(remaining, remaining * frac)
                            fill_shares = fill_cost / limit_px if limit_px > 0 else 0.0

                            trade.cost_usd = already_filled + fill_cost
                            trade.size = float(trade.size or 0.0) + fill_shares
                            trade.filed_at = now
                            trade.status = "FILLED" if trade.cost_usd >= (intended * 0.99) else "PARTIALLY_FILLED"
                            await self.db.save_trade(trade)

                            # Open / add to position and deduct cash for the filled portion only.
                            stop_loss = max(0.01, limit_px * 0.5)
                            take_profit = min(0.99, limit_px * 1.5)

                            existing = await self.db.get_positions()
                            pos = next((p for p in existing if p.token_id == trade.token_id), None)
                            if pos:
                                new_cost = float(pos.cost_usd) + fill_cost
                                new_size = float(pos.size) + fill_shares
                                pos.cost_usd = new_cost
                                pos.size = new_size
                                pos.entry_price = new_cost / new_size if new_size > 0 else pos.entry_price
                                pos.current_price = limit_px
                                pos.stop_loss_price = stop_loss
                                pos.take_profit_price = take_profit
                                await self.db.save_position(pos)
                            else:
                                pos = Position(
                                    condition_id=trade.condition_id,
                                    token_id=trade.token_id,
                                    market_question=trade.market_question,
                                    side=trade.side,
                                    entry_price=limit_px,
                                    current_price=limit_px,
                                    size=fill_shares,
                                    cost_usd=fill_cost,
                                    stop_loss_price=stop_loss,
                                    take_profit_price=take_profit,
                                    strategy_name=trade.strategy_name,
                                    slug=trade.slug,
                                    underlying_symbol=getattr(trade, "underlying_symbol", "") or "",
                                )
                                await self.db.save_position(pos)

                            self.risk.record_trade(fill_cost)

                            # Successful fill -> reset no-fill window for this symbol
                            sym = (getattr(trade, "underlying_symbol", "") or "").upper()
                            if sym and sym in self._nofill_window:
                                self._nofill_window[sym] = []

                    except Exception as e:
                        logger.warning(f"Paper fill poller error on trade {trade.id}: {e}")

            except Exception as e:
                logger.debug(f"Paper fill poller loop error: {e}")

            await asyncio.sleep(check_interval)

    async def run(self, single_cycle: bool = False):
        """Main run loop."""
        await self.setup()
        self.running = True

        try:
            # Start the web API and resolution poller in the background
            self._bg_tasks.append(asyncio.create_task(self.web_api.run_server(port=8001)))
            self._bg_tasks.append(asyncio.create_task(self._poll_resolutions()))
            self._bg_tasks.append(asyncio.create_task(self._poll_paper_fills()))
            
            while self.running:
                self.cycle_count += 1
                logger.info(f"\n{'='*60}")
                logger.info(f"🔄 Cycle #{self.cycle_count}")
                logger.info(f"{'='*60}")

                try:
                    await self._run_cycle()
                except Exception as e:
                    logger.error(f"Cycle error: {e}", exc_info=True)
                    if "kill" in str(e).lower() or "critical" in str(e).lower():
                        self.risk.activate_kill_switch(str(e))
                        await self.alerts.send_kill_switch(str(e))

                if single_cycle:
                    break

                logger.info(
                    f"💤 Sleeping {self.config.cycle_interval_seconds}s until next cycle..."
                )
                await asyncio.sleep(self.config.cycle_interval_seconds)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            await self.shutdown()

    async def _run_cycle(self):
        """Execute one full trading cycle."""
        # 00. Update mathematical win rate for the Risk Manager auto-tuner
        self.risk.current_win_rate = await self.db.get_win_rate()
        # Additional safety rail: if today's recent resolved trades are cold, pause new entries briefly.
        try:
            daily = await self.db.get_daily_trades()
            # Only consider trades from THIS bot session to avoid legacy DB history
            # triggering cooldown immediately after restart.
            resolved = [
                t
                for t in daily
                if (t.created_at or 0) >= self._session_start_ts
                and t.status in ("WIN", "LOSS")
                and t.strategy_name == "crypto_scalper"
            ]
            recent = resolved[:20]
            if len(recent) >= 10:
                wr = sum(1 for t in recent if t.status == "WIN") / len(recent)
                if wr < 0.40:
                    self.risk.activate_cooldown(15 * 60, reason=f"Recent WR={wr*100:.0f}% (last {len(recent)})")
        except Exception as e:
            logger.debug(f"Daily risk rail failed: {e}")

        # 00b. Auto-tune crypto scalper by regime from resolved paper outcomes
        try:
            if self.orchestrator and getattr(self.orchestrator, "crypto_scalper", None):
                recent = await self.db.get_recent_settled_trades(limit=800, strategy_name="crypto_scalper")
                tuner = PerformanceTuner(base_min_edge=self.orchestrator.crypto_scalper.min_edge, min_samples=20)
                tuning = tuner.build(recent)
                self.orchestrator.crypto_scalper.set_tuning(tuning)

                # Probability calibration from resolved outcomes (free, online learning)
                calibrator = ProbabilityCalibrator(bin_size=0.05, min_samples_per_bin=12)
                calibrator.build(recent)
                self.orchestrator.crypto_scalper.set_calibrator(calibrator)
        except Exception as e:
            logger.debug(f"Auto-tuner update failed: {e}")

        # 0. Read current crypto data from memory (now populated by WSS)
        if self.crypto_feed:
            btc_price = self.crypto_feed.get_price("BTCUSDT")
            if btc_price > 0:
                logger.info(f"₿ BTC: ${btc_price:,.2f}")

        # 0b. Sync Coinglass metrics into snapshots
        if self.coinglass and self.crypto_feed:
            for sym, snap in self.crypto_feed.snapshots.items():
                cg = self.coinglass.get_metrics(sym)
                if cg:
                    snap.open_interest_usd = cg.open_interest_usd
                    snap.oi_change_pct = cg.oi_change_1h_pct
                    snap.long_short_ratio = cg.long_short_ratio

        # 1. Discover markets (prioritize same-day + crypto)
        logger.info("📡 Step 1: Discovering markets...")
        raw_markets = await self.gamma.fetch_active_markets(limit=200)
        markets = self.discovery.filter_markets(raw_markets)

        # Also get same-day markets for focused trading
        same_day = self.discovery.filter_same_day(raw_markets)
        crypto_markets = self.discovery.filter_crypto_5min(raw_markets)
        logger.info(f"🎯 Same-day: {len(same_day)} | Crypto 5-min: {len(crypto_markets)}")

        # Merge crypto markets into main list (deduped)
        seen_ids = {m.condition_id for m in markets}
        for m in crypto_markets + same_day:
            if m.condition_id not in seen_ids:
                markets.append(m)
                seen_ids.add(m.condition_id)

        if not markets:
            logger.warning("No tradable markets found")
            return

        # 2. Ingest news
        logger.info("📰 Step 2: Ingesting news...")
        queries = self.discovery.get_search_queries(markets)
        articles = await self.news.fetch_news(queries[:5])

        # 3. Analyze sentiment
        logger.info("🧠 Step 3: Analyzing sentiment...")
        sentiments = await self.sentiment.analyze_articles(articles)

        # 4. Map news to markets
        logger.info("🔗 Step 4: Mapping news → markets...")
        signals = self.mapper.match(articles, markets, sentiments)

        # Send Discord alerts for strong news signals
        for sig in signals[:3]:
            if sig.sentiment and abs(sig.sentiment.score) > 0.5:
                await self.alerts.send_news_signal(
                    sig.market.question,
                    sig.sentiment.label,
                    sig.sentiment.score,
                    len(sig.articles),
                )

        # 5. Predict & detect edge
        logger.info("📊 Step 5: Predicting probabilities & detecting edge...")
        predictions = self.predictor.predict_batch(markets, signals)

        # 6. Run strategies
        logger.info("🎯 Step 6: Running trading strategies...")
        trade_signals = self.orchestrator.evaluate_all(
            markets, predictions, signals, self.risk.cash,
        )
        # Store for the web API opportunities endpoint
        self.last_signals = trade_signals

        # 7. Execute trades
        if trade_signals:
            logger.info(f"⚡ Step 7: Executing {len(trade_signals)} trade(s)...")
            positions = await self.db.get_positions()
            trades = await self.executor.execute_signals(trade_signals, positions)

            for trade in trades:
                await self.alerts.send_trade(trade)
        else:
            logger.info("😴 No trade signals this cycle")

        # 8. Check stop-losses on open positions
        logger.info("🛡️ Step 8: Checking risk limits...")
        positions = await self.db.get_positions()
        # Update current prices
        for pos in positions:
            if self.clob:
                pos.current_price = self.clob.get_price(pos.token_id)
            # In paper mode, prices don't change dynamically (we'd need WebSocket for that)

        stops = self.risk.check_stops(positions)
        for pos in stops:
            logger.warning(f"Closing position: {pos.market_question[:40]}")
            await self.executor.close_position(pos)

        # 9. Render dashboard
        daily_trades = await self.db.get_daily_trades()
        snapshot = PortfolioSnapshot(
            total_value_usd=self.risk.cash + sum(p.cost_usd for p in positions),
            cash_usd=self.risk.cash,
            positions_value_usd=sum(p.cost_usd for p in positions),
            daily_pnl=await self.db.get_daily_pnl(),
            total_pnl=self.risk.cash - self.config.starting_budget,
            num_open_positions=len(positions),
            num_trades_today=len(daily_trades),
        )
        await self.db.save_snapshot(snapshot)

        if self.show_dashboard:
            self.dashboard.render(
                snapshot=snapshot,
                positions=positions,
                recent_trades=daily_trades,
                opportunities=trade_signals[:5],
                is_paper=self.config.paper_trading,
            )

        logger.info(
            f"✅ Cycle #{self.cycle_count} complete — "
            f"Portfolio: ${snapshot.total_value_usd:.2f} | "
            f"P/L: ${snapshot.total_pnl:+.2f}"
        )

        # Broadcast to Web UI
        if self.web_api:
            await self.web_api.broadcast({
                "type": "cycle_update",
                "snapshot": asdict(snapshot),
                "positions": [asdict(p) for p in positions],
                "signals": [asdict(s) for s in trade_signals[:10]],
                "recent_trades": [asdict(t) for t in daily_trades[:5]]
            })


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PolyBot — Polymarket Trading Bot")
    parser.add_argument("--paper", action="store_true", help="Force paper trading mode")
    parser.add_argument("--live", action="store_true", help="Enable live trading")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable CLI dashboard")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # Logging
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True)],
    )

    # Load config
    config = load_config()

    # CLI overrides
    if args.paper:
        config.paper_trading = True
    if args.live:
        config.paper_trading = False

    # Validate
    if not config.paper_trading and not config.polymarket.private_key:
        logger.error("❌ Live trading requires POLYMARKET_PRIVATE_KEY in .env")
        sys.exit(1)

    # Run
    bot = PolyBot(config, show_dashboard=not args.no_dashboard)

    # Graceful shutdown on SIGINT
    def handle_signal(sig, frame):
        logger.info("Received shutdown signal...")
        bot.running = False

    signal.signal(signal.SIGINT, handle_signal)

    asyncio.run(bot.run(single_cycle=args.once))


if __name__ == "__main__":
    main()
