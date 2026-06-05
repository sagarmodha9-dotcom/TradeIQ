# TradeIQ — Parameter History
(.env is gitignored; this file is the committed record. Update at every review.)

## Week 2 — set Fri Jun 5, 2026 (post-review)
| Param | Value | Was | Why |
|---|---|---|---|
| STOCK_SL_PCT / SHORT_SL_PCT | 0.0075 | 0.005 | 8 SLs in 2 days = stops under 5-min noise floor |
| STOCK_TP_PCT / SHORT_TP_PCT | 0.012 | 0.008 | keep 1.6 R:R, breakeven WR ~38.5% |
| POSITION_SIZE_PCT | 0.30 | 0.35 | offset wider stop; worst case ≈ −$46/day |
| MAX_TRADES_PER_DAY | 10 | 15 | same |
| COOLDOWN_WIN_MIN | 0 | 12 | press winners immediately |
| COOLDOWN_LOSS_MIN | 35 | 35 | unchanged |
| SHORTS_ENABLED | true | — | live since Thu PM; NVDA round trip verified |
| DATA_FEED | sip | iex | Algo Trader Plus free month; July 4 A/B decision |
| Watchlist | 30 (v2) | 26 | cut SNAP/DIS/BABA/SOFI/SHOP/SQ; added MU/AVGO/SMCI/CRWD/MRVL/AMAT/DELL/ORCL/LRCX/RDDT |

## Unchanged since launch (Jun 4)
DAILY_LOSS_LIMIT_USD=60 · EQUITY_FLOOR=2030 · entries 9:45+ · force-close 15:50 · MAX_OPEN_POSITIONS=4 · MAX_OPEN_OPTIONS=0 · SCAN_INTERVAL=30s · 5Min bars

## Week-1 baseline (for reference)
SL 0.5 / TP 0.8 · size 0.35 · 15 trades/day · win-cd 12m
Result: 12 trades, 1 TP / 8 SL / 3 force, −$28.60. Diagnosis: noise-stops.
