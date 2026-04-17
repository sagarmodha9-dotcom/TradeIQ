# TradeIQ Command Cheat Sheet

## 🔴 EMERGENCY
# Kill switch (stop all trading)
curl -X POST http://localhost:8081/kill

# Resume trading
curl -X POST http://localhost:8081/resume

---

## 🔧 FIX ISSUES
# Dashboard not loading
launchctl unload ~/Library/LaunchAgents/com.tradeiq.tunnel.plist && sleep 2 && launchctl load ~/Library/LaunchAgents/com.tradeiq.tunnel.plist

# Bot not running
launchctl unload ~/Library/LaunchAgents/com.tradeiq.bot.plist && sleep 2 && launchctl load ~/Library/LaunchAgents/com.tradeiq.bot.plist

# Restart everything
launchctl unload ~/Library/LaunchAgents/com.tradeiq.bot.plist && launchctl unload ~/Library/LaunchAgents/com.tradeiq.api.plist && launchctl unload ~/Library/LaunchAgents/com.tradeiq.web.plist && launchctl unload ~/Library/LaunchAgents/com.tradeiq.tunnel.plist && sleep 3 && launchctl load ~/Library/LaunchAgents/com.tradeiq.tunnel.plist && launchctl load ~/Library/LaunchAgents/com.tradeiq.web.plist && launchctl load ~/Library/LaunchAgents/com.tradeiq.api.plist && launchctl load ~/Library/LaunchAgents/com.tradeiq.bot.plist && echo "✅ All services restarted"

---

## 📊 CHECK STATUS
# Full system check
cd ~/tradeiq && source venv/bin/activate && launchctl list | grep tradeiq && curl -s http://localhost:8081/risk | python3 -m json.tool

# Watch live bot log
tail -f ~/tradeiq/bot.log

# Check errors
tail -20 ~/tradeiq/bot_error.log

---

## 💰 BALANCE UPDATE
# When 2nd $1k clears (auto but if needed)
sed -i '' 's/LIVE_ACCOUNT_BALANCE=1000/LIVE_ACCOUNT_BALANCE=2000/' ~/tradeiq/.env && launchctl unload ~/Library/LaunchAgents/com.tradeiq.bot.plist && sleep 2 && launchctl load ~/Library/LaunchAgents/com.tradeiq.bot.plist && echo "✅ Updated to $2,000"

---

## 💾 BACKUP
# Full backup (run after any changes)
cd ~/tradeiq && git add -A && git commit -m "backup $(date '+%Y-%m-%d %H:%M')" && git push origin main && cp *.py *.json .env tradeiq_app.html backups/$(date +%Y%m%d)/ 2>/dev/null && cp -r backups/$(date +%Y%m%d) ~/Library/Mobile\ Documents/com~apple~CloudDocs/TradeIQ_backup_$(date +%Y%m%d) 2>/dev/null && echo "✅ All backed up"

---

## 🔍 DIAGNOSTICS
# Check IBKR connection
curl -s http://localhost:8081/status | python3 -m json.tool | grep -E "ibkr|connected|account|portfolio"

# Check open positions
curl -s http://localhost:8081/status | python3 -m json.tool | grep -E "open_positions|stock_open|options_open"

# Check daily P&L
curl -s http://localhost:8081/risk | python3 -m json.tool | grep -E "daily_pnl|daily_limit"

