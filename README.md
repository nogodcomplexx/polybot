# 🤖 PolyBot — Polymarket Prediction Trading Bot

Automated trading bot for [Polymarket](https://polymarket.com) that combines **real-time news sentiment analysis** with **market data signals** to find low-risk, high-reward prediction market trades.

## Features

- **4 Trading Strategies**: Arbitrage, news-driven, high-probability bonds, contrarian
- **News Sentiment Engine**: FinBERT (financial NLP) + VADER fallback
- **Multi-Source News**: NewsAPI, RSS feeds from Reuters/BBC/CNN
- **Bayesian Prediction**: Weighted ensemble combining market, news, volume, and historical signals
- **Risk Management**: Kelly criterion sizing, stop-loss, daily loss limits, kill switch
- **Paper Trading**: Full simulation mode with no real money
- **Rich CLI Dashboard**: Live portfolio, positions, and P/L tracking
- **Discord Alerts**: Real-time trade notifications via webhook

## Quick Start

### 1. Install Python 3.11+

### 2. Set up virtual environment
```bash
cd polybot
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Mac/Linux
```

### 3. Install dependencies
```bash
pip install -e ".[dev]"
```

### 4. Configure
Edit `.env` and add your wallet private key:
```
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
```

Optionally add your Discord webhook URL for alerts:
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

### 5. Run (Paper Trading)
```bash
python -m src.main --paper --once        # Single cycle
python -m src.main --paper               # Continuous
python -m src.main --paper --debug       # With debug logs
```

### 6. Run (Live Trading) ⚠️
```bash
python -m src.main --live
```

> **Warning**: Live trading uses real money. Start with paper trading first!

## Architecture

```
discover markets → ingest news → analyze sentiment → predict probabilities
→ detect edge → run strategies → risk check → execute trades → dashboard
```

## Configuration

All tunable parameters are in `config.yaml`. Key settings:
- `risk.max_position_pct`: Max % of budget per trade (default: 15%)
- `risk.kelly_fraction`: Kelly sizing safety factor (default: 0.25 = quarter-Kelly)
- `prediction.min_edge_pct`: Minimum edge to consider a trade (default: 5%)

## License

Private / Personal Use
