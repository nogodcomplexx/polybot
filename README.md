# ⚡ Bybit V5 Linear Perpetual Scalper

Autonomous, institutional-grade 24/7 Bybit V5 Linear Perpetual Futures scalping system built with a **Three-Brain Architecture** and **Multi-Exchange Market Intelligence Layer** (Bybit + Binance + CoinGlass).

---

## Architecture

```text
MARKET DATA (Bybit L50 WS + Binance Futures WS + CoinGlass REST)
    ↓
BRAIN 1: ALPHA ENGINE (SMC, WaveTrend, VWAP/FVG, Absorption, Cross-Exchange Confirmation)
    ↓
BRAIN 2: POLICY ENGINE (18 Hard Risk Gates, Cash Fortress ≥85%, Drawdown Circuit Breakers)
    ↓
BRAIN 3: EXECUTION ENGINE (Post-Only Maker Limits, Private WS Stream, Hard Server Stops, TP1/TP2 Runners)
    ↓
SUPERVISION & TELEMETRY (Continuous State Reconciliation, Watchdog Dead-Man, Rich Live Dashboard)
```

---

## Features

- **Three-Brain Decoupled Execution**:
  - **Brain 1 (Alpha)**: Scans dynamic top 20 liquid perpetuals across 4 strategies (Turtle Soup, VWAP/FVG Reversion, Orderbook Absorption, Trend Continuation) with BTC Macro Beta filter.
  - **Brain 2 (Policy)**: Strict institutional capital preservation gatekeeper enforcing 18 sequential checks before authorizing any trade.
  - **Brain 3 (Execution)**: Maker-only Post-Only limit orders with server-side SL/TP brackets attached on order creation, 50% partial TP1 close with breakeven trailing stop, and time-based invalidation.
- **Multi-Exchange Market Intelligence**:
  - Cross-venue price basis and velocity (%/sec over 10s window).
  - Cross-exchange Cumulative Volume Delta (CVD) confirmation.
  - Dual-venue spoof detection and wall verification.
  - Derivatives context fusion (aggregate OI, funding spreads, liquidation cascades).
  - Sub-3-second data staleness veto.
- **Dynamic Universe Discovery & Rotation**:
  - Automatically queries Bybit linear tickers every 2 hours.
  - Filters by $50M turnover, 0.04% max spread, and 2.5% minimum volatility.
  - Always retains macro anchors (BTCUSDT, ETHUSDT) and protects active positions from eviction.
- **Capital Protection & Invariants**:
  - Maximum active positions = 1 (strict single-trade desk invariant).
  - Cash fortress floor: ≥85% unencumbered equity.
  - Maximum risk per trade: 7.5% ceiling (5.0% default).
  - 2 consecutive losses trigger mandatory 4-hour cooldown.
  - Daily drawdown kill switches (10% software pause, 15% hardware halt).
- **Restart-Safe SQLite Storage**:
  - Persists trades, orders, signals, policy decisions, state transitions, and governor metrics in SQLite WAL mode.

---

## Quick Start

### 1. Requirements
- Python 3.11+
- Virtual environment (`.venv`)

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Add your Bybit V5 API credentials:
```env
BYBIT_API_KEY=your_bybit_api_key
BYBIT_API_SECRET=your_bybit_api_secret
BYBIT_TESTNET=true
```

### 3. Configure Scalper Settings
Adjust `config.yaml`:
```yaml
bybit:
  mode: "paper"          # "paper" for simulation, "live" for real execution
  testnet: true
  default_leverage: 10
  max_position_usd: 100.0
```

### 4. Run the Scalper
```bash
python src/bybit_main.py
```

### 5. Run Tests
```bash
pytest tests/ -v
```
