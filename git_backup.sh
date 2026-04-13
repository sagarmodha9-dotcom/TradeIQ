#!/bin/bash
cd ~/tradeiq
git add -A
git diff --cached --quiet || git commit -m "Auto backup $(date '+%Y-%m-%d %H:%M')"
git push origin main

# Also backup critical files to iCloud
cp ~/tradeiq/.env ~/tradeiq/trade_history.json ~/tradeiq/bot_state.json ~/tradeiq/trades.csv ~/Library/Mobile\ Documents/com~apple~CloudDocs/TradeIQ/
