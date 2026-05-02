# TradeIQ Vacation Runbook
**Trip:** Thu May 7 - Mon May 11, 2026
**Bot status:** Running with full Telegram alerts

---

## 🚨 EMERGENCY: KILL ALL TRADING

If something is seriously wrong and you need the bot to STOP NOW:

**From your phone via Tastytrade app:** Cancel all working orders, then close all positions manually.

**From your phone via Alpaca app:** Same — cancel orders, close positions.

**To stop the bot itself (requires Mac access via Tailscale or similar):**
```bash
launchctl stop com.tradeiq.bot
```

**The bot will NOT auto-restart if you stop it this way until you run:**
```bash
launchctl start com.tradeiq.bot
```

---

## Telegram Alerts — What Each Means

| Alert | Meaning | Action |
|---|---|---|
| 🟢 Position opened | Bot bought something | None — note the entry |
| 🟢 Position closed +$X | Profitable close | None — celebrate quietly |
| 🔴 Position closed -$X | Loss taken | None if reasonable, intervene if huge |
| ⚠️ DAILY LOSS LIMIT | Down 3%+ today | Bot already paused new entries. Verify. |
| ⚠️ BOT CRASH ALERT | Bot restarted 3+ times in 10 min | Critical — investigate or stop bot |
| 🛑 KILL SWITCH | Bot self-stopped | Critical — likely needs manual intervention |
| 📊 Daily Summary | End of day P&L | Read it, sanity check |

**No alerts for hours = bot is fine, market is calm, or market is closed.**

---

## Common Scenarios

### Scenario 1: NFLX-style stuck order (close fails repeatedly)
**Symptom:** Telegram shows repeated "sell rejected" or "cannot_close_against_more_than_existing_position" errors.

**Action:**
1. Open Tastytrade or Alpaca app on phone
2. Go to Order History / Working Orders
3. Cancel any duplicate orders for that symbol
4. Place ONE manual close at limit price (use the bid)
5. Bot will see position is gone on next scan

**Why this happens:** Bot retries close orders that didn't fill. Tastytrade rejects duplicates. Need to clear the queue.

### Scenario 2: Big loss alert (-$100+)
**Symptom:** Single trade closed at significant loss.

**Action:**
1. Check WHY in dashboard — what was the entry reason?
2. Verify no cascading losses on same symbol
3. If it's one bad trade, accept it, move on
4. If bot opens 3+ same-symbol losers in a day, consider /pause command

### Scenario 3: Earnings surprise
**Symptom:** Position holds through earnings unexpectedly.

**Action:**
1. Check earnings calendar in bot logs — pre-earnings rules should have force-exited day-of
2. If position is open during earnings, either:
   - Wait it out (catalyst could go either way)
   - Manually close in broker app to cap risk
3. Note for Wednesday review: which earnings rule failed?

### Scenario 4: Dashboard shows weird numbers
**Symptom:** Total P&L, win rate, or positions look wrong.

**Action:**
- Don't panic — dashboard pulls live from brokers, so if it shows -$1000, that's likely real
- Cross-check against Tastytrade and Alpaca apps directly
- If broker apps disagree with dashboard, broker truth wins — dashboard has a bug
- Note for Wednesday review

### Scenario 5: Bot stops sending Telegram alerts
**Symptom:** No alerts for many hours during market hours.

**Action:**
1. Send `/status` to bot — does it respond? (after Wednesday's update)
2. Open dashboard URL — does it load with current data?
3. If both work but no alerts: notifier issue, not critical
4. If neither works: bot may be down, requires Mac access to fix

### Scenario 6: Account goes negative or huge drawdown
**Symptom:** Daily P&L shows -10% or more in a single day.

**Action:**
1. The 3% daily loss limit should have already paused new entries
2. Open both broker apps and verify positions
3. Manually close any options positions if down >50%
4. Stop the bot from your phone (Tailscale → SSH → launchctl stop)

---

## Pre-Trip Checklist (do Wed May 6 evening)

- [ ] APP earnings (May 6) — verify bot exited that morning
- [ ] SNAP earnings (May 6) — verify bot exited that morning  
- [ ] NFLX option closed Monday (May 4) — verify in trade_history
- [ ] No options expiring during trip (check open positions)
- [ ] No earnings during trip on currently-held symbols
- [ ] Telegram test message — receive on phone ✓
- [ ] All launchd services running: `launchctl list | grep tradeiq`
  - bot, api, gitbackup, reconcile, crash_monitor, snapshot
- [ ] Latest snapshot zip exists in iCloud
- [ ] GitHub backup current
- [ ] Phone has: Tastytrade app, Alpaca app, Telegram, browser bookmarked dashboard URL

---

## Numbers to Know

- **Account total:** ~$2,900 (varies daily)
- **Daily loss limit:** 3% (~$87)
- **Max stock positions:** 6 (config)
- **Max options positions:** 6 (config)
- **Per-contract options cap:** 30% of Tastytrade balance
- **Bot scan interval:** 60 seconds
- **Reconciliation runs:** 4:15 PM ET weekdays
- **Crash monitor runs:** Every 10 min
- **Snapshot runs:** 5:00 PM ET daily

## Key Contacts/Links

- Dashboard: http://192.168.1.185:8081 (only on home network — use Tailscale from PR)
- GitHub: https://github.com/sagarmodha9-dotcom/TradeIQ
- Tastytrade account: 5WI60248
- Alpaca account: (live)

## What NOT to Do From PR

- Don't deploy code changes — too risky without full Mac access
- Don't manually edit JSON files — easy to corrupt
- Don't override bot decisions reactively — let the safety nets work
- Don't check dashboard obsessively — vacation is vacation

## Worst-Case Decision Tree

**Bot loses $200+ in one day:**
→ Send /pause from phone (after Wed update)
→ Check positions, manually close anything obviously bad
→ Wait until you're back to debug

**Bot opens 5+ trades you don't recognize:**
→ Send /pause
→ Don't close everything, but stop new entries
→ Investigate from phone

**Account drops below $2,500:**
→ Stop bot completely
→ Close all positions manually
→ Wait until back home to resume

**Anything that feels wrong:**
→ Stop bot
→ Better to miss trades than to bleed money you can't see
→ The bot will be there when you return

---

## After You Get Back (Mon May 11 night / Tue May 12)

1. Read all Telegram alerts you missed (scroll back)
2. Check dashboard total vs what you remember
3. Run reconciliation: `cd ~/tradeiq && venv/bin/python3 reconcile.py 7`
4. Compare bot trade_history to broker statements
5. Calculate real performance for the trip period
6. Note any bugs/weirdness for next Saturday's review session

---

**Last updated:** Sat May 2, 2026
**Next update:** Wed May 6 evening (after vacation hardening session)
