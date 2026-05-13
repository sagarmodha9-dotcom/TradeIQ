#!/bin/bash
cd ~/tradeiq

# Only commit code changes — runtime state is in .gitignore
git add -A
git diff --cached --quiet || git commit -m "Auto backup $(date '+%Y-%m-%d %H:%M')"
git push origin main

# Backup runtime state to iCloud (NOT git)
ICLOUD=~/Library/Mobile\ Documents/com~apple~CloudDocs/TradeIQ
cp .env "$ICLOUD/" 2>/dev/null
cp trade_history.json "$ICLOUD/" 2>/dev/null
cp bot_state.json "$ICLOUD/" 2>/dev/null
cp trades.csv "$ICLOUD/" 2>/dev/null
