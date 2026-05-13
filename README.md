# TradeIQ

An automated multi-broker trading bot built in Python that executes algorithmic stock and options strategies across Alpaca and Tastytrade, with full production deployment, monitoring, and defensive trade execution.

## Overview

TradeIQ scans liquid US equities for momentum signals using technical indicators, news sentiment, and options Greeks. When high-confidence signals fire, it sizes positions using portfolio-aware risk controls and routes orders to the appropriate broker - stocks via Alpaca, options via Tastytrade.

Beyond signal generation, the project is built around the reality that brokerage APIs fail in non-obvious ways: orders silently reject, fill confirmations lie, and position state diverges from broker truth. The codebase is structured to detect, contain, and recover from these failures rather than assume success.

## Architecture

**Signal Layer** (technicals, news filter, Greeks) feeds into **Risk Engine** (position sizing, daily loss limits, pre-earnings rules) which routes through **Broker Layer** (Alpaca for stocks, Tastytrade for options). All three layers report into a **State and Monitor Layer** (state files, crash detection, retry storm guard, Telegram alerts).

## Key Features

### Trading Logic
- Multi-indicator signal generation (momentum, mean reversion, news catalysts)
- Confidence-weighted position sizing (configurable minimum threshold)
- Earnings-aware position management (pre-earnings stop-loss tightening, force-exit on earnings day)
- Options strategy with delta-targeted contract selection and Greeks-based filtering
- Profit ladder for partial profit-taking on winning options

### Risk Management
- Daily loss limit (configurable percentage) blocks new entries when breached - applies to both stocks and options
- Per-trade position size caps (percent of account, hard ceiling)
- No-trade window protection (first/last 15 min of market session)
- Stop-loss and take-profit on every position
- VIX-based regime filter to skip new entries in high-volatility environments
- Pre-earnings SL tightening (auto-tightens stops as earnings approach)

### Defensive Execution
- **Broker truth verification** - after every close attempt, queries the broker to confirm the position is actually gone before logging the trade. Prevents phantom fills.
- **3-strike close blocker** - if a close fails 3 times consecutively, the position is locked for 1 hour and a Telegram alert is sent. Prevents API retry storms from rejected orders.
- **Phantom position cleanup** - startup sync removes positions tracked in bot state but not present at broker.
- **Bi-directional state reconciliation** - both adds positions the broker has and removes ones it does not.

### Production Infrastructure
Deployed as launchd services on macOS:
- Main trading loop (60-second scan interval)
- REST API for dashboard
- Cloudflare tunnel for remote dashboard access
- Hourly auto-commits to GitHub plus iCloud sync
- Daily reconciliation audit (4:15 PM ET)
- Crash and retry-storm monitor (every 10 minutes)
- Daily state snapshots (5:00 PM ET, 14-day rolling history)

### Backup Strategy
- **GitHub**: hourly commits of code plus non-sensitive state
- **iCloud live**: hourly sync of latest state files
- **iCloud snapshots**: daily date-stamped zip archives with 14-day rolling retention

### Observability
- Real-time dashboard with broker-verified position list, P&L breakdown, strategy health metrics
- Telegram alerts for opens, closes, daily loss limit hits, crash detection, retry storms, daily summaries, bot restarts
- Sample-size warnings on performance metrics until statistical significance is reached

## Technology Stack
- **Python 3.11** - core runtime
- **Alpaca API** - US equities
- **Tastytrade API** - options
- **Pandas / NumPy** - signal calculations
- **Requests / WebSockets** - broker integration
- **launchd** - service orchestration on macOS
- **GitHub + iCloud** - multi-tier backups
- **Telegram Bot API** - alerts and notifications

## Repository Structure
- `bot.py` - Main trading loop, signal logic, position management
- `config.py` - All tunable parameters (sourced from .env)
- `tastytrade_client.py` - Options broker integration
- `options_client.py` - Broker-agnostic options wrapper
- `stock_analyzer.py` - Equity signal generation
- `options_analyzer.py` - Options strategy selection and Greeks
- `notifier.py` - Telegram alerts
- `dashboard_api.py` - Web dashboard backend
- `metrics.py` - P&L and statistics
- `reconcile.py` - Daily phantom-trade audit
- `crash_monitor.py` - Retry storm and crash detection
- `snapshot.sh` - Daily state backup

## Design Principles
1. **Broker truth is the only source of truth.** Bot state is reconciled to broker on every startup and verified after every close.
2. **Fail visibly, not silently.** Every rejection, retry, and unexpected state change is logged, counted, and surfaced to Telegram if it crosses a threshold.
3. **Boundary defense.** Each broker integration assumes failure modes the brokers do not document - partial fills, status field inconsistencies, network blips, rate limits.
4. **Stop trading before bleeding.** Daily loss limits and 3-strike blockers prioritize protecting capital over capturing every signal.

## Status
Actively deployed and trading live. The codebase prioritizes correctness and recoverability over feature breadth. Every state mutation is logged, every deployment is reversible from snapshot, and strategy refinements are ongoing.

---

**Author:** Sagar Modha
