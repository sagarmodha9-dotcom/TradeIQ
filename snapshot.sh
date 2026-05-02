#!/bin/bash
# Daily date-stamped snapshot to iCloud
# Keeps last 14 days, auto-deletes older
set -e

DATE=$(date +%Y-%m-%d)
SNAPSHOT_DIR="/Users/sagarmodha/Library/Mobile Documents/com~apple~CloudDocs/TradeIQ/snapshots"
TRADEIQ_DIR="/Users/sagarmodha/tradeiq"
TMP_DIR="/tmp/tradeiq_snapshot_$$"

mkdir -p "$SNAPSHOT_DIR"
mkdir -p "$TMP_DIR"

# Copy all data files into temp dir
for f in .env trade_history.json bot_state.json cooldown_state.json earnings_plays.json portfolio_state.json last_signals.json trades.csv; do
  if [ -f "$TRADEIQ_DIR/$f" ]; then
    cp "$TRADEIQ_DIR/$f" "$TMP_DIR/$f" 2>/dev/null || true
  fi
done

# Also include last 7 days of bot.log if it exists (tail to keep size down)
if [ -f "$TRADEIQ_DIR/bot.log" ]; then
  tail -5000 "$TRADEIQ_DIR/bot.log" > "$TMP_DIR/bot.log.tail" 2>/dev/null || true
fi

# Create zip with date stamp
ZIP_NAME="tradeiq_${DATE}.zip"
cd "$TMP_DIR"
zip -q "$SNAPSHOT_DIR/$ZIP_NAME" * .env 2>/dev/null || zip -q "$SNAPSHOT_DIR/$ZIP_NAME" *

# Cleanup temp
rm -rf "$TMP_DIR"

# Delete snapshots older than 14 days
find "$SNAPSHOT_DIR" -name "tradeiq_*.zip" -type f -mtime +14 -delete 2>/dev/null || true

# Log result
echo "$(date '+%Y-%m-%d %H:%M:%S') - Snapshot created: $ZIP_NAME ($(ls -lh "$SNAPSHOT_DIR/$ZIP_NAME" | awk '{print $5}'))"
