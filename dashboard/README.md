# Polybot Neural Telemetry Observatory & Dashboard

Modern, futuristic cybernetic Web HUD & Neural Network Observatory for the Bybit V5 Quantitative Scalping Operating System.

```
                    ┌─────────────────────────┐
                    │       DASHBOARD         │
                    │   Web UI / WebSocket    │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │     TELEMETRY BUS       │
                    └────────────┬────────────┘
                                 │
       ┌─────────────────────────┼──────────────────────────┐
       │                         │                          │
┌──────▼──────┐           ┌──────▼──────┐            ┌──────▼──────┐
│  MARKET     │           │    BRAIN    │            │ EXECUTION   │
│  TELEMETRY  │           │  TELEMETRY  │            │  TELEMETRY  │
└─────────────┘           └─────────────┘            └─────────────┘
       │                         │                          │
 Bybit WS                 Alpha Scanner               Orders
 Binance WS               SMC Auditor                 Fills
 CoinGlass                Veto Gate                    TP/SL
 Orderbook                Policy Engine                Slippage
 CVD / OI                  Kelly                        Latency
 Funding                  Risk                         Reconciliation
```

---

## Key Features

1. **Read-Only Observer Architecture**:
   - The dashboard is strictly decoupled from the core trading engine (`BOT -> DASHBOARD (READ ONLY)`).
   - All events flow through an asynchronous non-blocking telemetry queue with drop-oldest overflow protection.
   - If the dashboard server crashes or the browser disconnects, **trading continues 100% uninterrupted with zero latency impact**.

2. **Neural Network Synapses Visualizer**:
   - Interactive HTML5 Canvas visualizer rendering the 8-node decision pipeline:
     `MARKET DATA -> MICROSTRUCTURE -> ALPHA SCANNER -> SMC AUDITOR -> RISK -> EXECUTION GATE -> ORDER -> RECONCILIATION`.
   - Real-time particle electrical pulses travelling along synaptic connections.
   - Click any node to open an **Interactive Node Inspector** modal detailing all live internal parameters (e.g. 6-point SMC check matrix, Kelly sizing calculation, L2 orderbook wall defense).

3. **Real-Time API Observatory & Pings**:
   - Live streaming table of API requests and WebSocket ticks with latency color-coding (`8.2ms WS`, `84ms 200`, `143ms 200`).
   - Venue statistics for Bybit, Binance, and CoinGlass (messages/sec, average ping, rate limit %).
   - Optional Web Audio API audio ping feedback.
   - Strict security: API keys, secrets, and authorization headers are never exposed or logged.

4. **Quantitative Brain Panel & Live Decision Trace**:
   - 0–100 Alpha score conviction meter with breakdown progress bars (+24 Sweep, +18 SFP, +12 VWAP, +16 Orderbook, +11 WT, +8 BTC).
   - SMC Auditor Gate Checklist (Structure, Liquidity, Invalidation, RR, Execution, Macro).
   - Risk & Kelly Criterion parameters.
   - Real-time Decision Trace terminal logging atomic evaluations and exact rejection reasons (`RR TP2 = 2.41 < REQUIREMENT = 2.80 -> ❌ VETO`).

5. **Market Brain & L2 Depth Bars**:
   - Visual price anchors: 24h VWAP, 15m FVG zone, Liquidity sweep level, Entry price.
   - Live Bid / Ask depth volume bars with detected wall markers ($M).

6. **System & Account HUD**:
   - 85% Cash Fortress shield perimeter meter.
   - Active position monitor with live hold duration timer, mark price, PnL, ROE %, and MAE / MFE.
   - Accurate states (displays `🟡 UNKNOWN` when unconfirmed, never fake green).

---

## Running the Dashboard

### 1. Embedded with Live Bot
The dashboard automatically starts in the background when running the main bot:
```bash
python -m src.bybit_main
```
Open your browser at:
```
http://127.0.0.1:8080
```

### 2. Standalone Demo / Replay Mode
To test the visualizer and real-time streaming without running live market connections:
```bash
python -m dashboard.backend.server
```
Open:
```
http://127.0.0.1:8080
```
