"""
notifier.py — Telegram alerts for TradeIQ
Replaces email notifications with Telegram messages.
"""
import os
import requests
from dotenv import load_dotenv
from logger import log
load_dotenv()

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def _send(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        log.error(f"Telegram alert error: {e}")

def send_email(subject, body):
    """Legacy compatibility — sends via Telegram instead."""
    _send(f"<b>{subject}</b>\n{body}")

def send_sms(message):
    _send(message)

def alert_trade_opened(symbol, side, entry, qty, sl, tp, confidence, market):
    emoji = "📈" if market == "stocks" else "🪙"
    _send(f"{emoji} <b>Trade Opened — {symbol}</b>\nSide: {side} | Entry: ${entry:,.2f}\nQty: {qty} | Conf: {confidence:.0%}\nSL: ${sl:,.2f} | TP: ${tp:,.2f}")

def alert_trade_closed(symbol, side, entry, exit_price, pnl, pnl_pct, status, market):
    emoji = "✅" if pnl > 0 else "❌"
    reason = "TP Hit" if status == "closed_tp" else "SL Hit" if status == "closed_sl" else "Closed"
    sign = "+" if pnl >= 0 else ""
    _send(f"{emoji} <b>{reason} — {symbol}</b>\nEntry: ${entry:,.2f} → Exit: ${exit_price:,.2f}\nP&L: <b>{sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%)</b>")

def alert_option_bought(symbol, contract, strike, expiry, cost, delta):
    _send(f"📈 <b>Option Bought — {symbol}</b>\nContract: {contract}\nStrike: ${strike} | Expiry: {expiry}\nCost: ${cost:.0f} | Delta: {delta:.2f}")

def alert_option_closed(symbol, contract, pnl, reason):
    emoji = "✅" if pnl > 0 else "❌"
    _send(f"{emoji} <b>Option Closed — {symbol}</b>\nContract: {contract}\nP&L: <b>${pnl:+.2f}</b> | {reason}")

def alert_kill_switch(reason, daily_pnl):
    _send(f"🚨 <b>KILL SWITCH ACTIVATED</b>\nReason: {reason}\nDaily P&L: ${daily_pnl:+.2f}\nAll new trades blocked.")

def alert_daily_summary(total, crypto, stocks, tpnl, wins, losses):
    total_trades = wins + losses
    wr = f"{wins/total_trades*100:.0f}%" if total_trades > 0 else "—"
    sign = "+" if tpnl >= 0 else ""
    emoji = "🟢" if tpnl >= 0 else "🔴"
    
    # Build individual trade list
    trades_text = ""
    try:
        from trade_history import get_daily_summary
        summary = get_daily_summary()
        trades = summary.get("trades", [])
        if trades:
            for t in trades:
                pnl = float(t.get("pnl_usd", 0))
                sym = t.get("product_id", "")
                status = t.get("status", "")
                t_sign = "+" if pnl >= 0 else ""
                t_emoji = "✅" if pnl >= 0 else "❌"
                status_str = "TP" if "tp" in status else "SL" if "sl" in status else "Manual"
                trades_text += "\n  " + t_emoji + " " + sym + ": " + t_sign + "$" + str(round(pnl,2)) + " (" + status_str + ")"
        else:
            trades_text = "\n  No closed trades today"
    except:
        trades_text = "\n  No trade data available"
    
    msg = f"{emoji} <b>Daily Summary — {__import__('datetime').datetime.now().strftime('%b %d')}</b>"
    msg += f"
Portfolio: ${total:,.2f}"
    msg += f"
P&L: <b>{sign}${tpnl:.2f}</b>"
    msg += f"
Trades: {wins}W / {losses}L | Win Rate: {wr}"
    msg += f"

📋 <b>Today's Trades:</b>{trades_text}"
    _send(msg)

def send_weekly_report():
    try:
        from datetime import datetime, timedelta
        import json
        with open("trade_history.json") as f:
            all_trades = json.load(f)
        today = datetime.now()
        monday = today - timedelta(days=today.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        week_trades = []
        for t in all_trades:
            try:
                closed = t.get("closed_at") or t.get("opened_at", "")
                if closed:
                    dt = datetime.fromisoformat(closed.replace("Z",""))
                    if dt >= monday:
                        week_trades.append(t)
            except: pass
        if not week_trades:
            _send("📊 <b>Weekly Report</b>\nNo closed trades this week.")
            return
        total_pnl = sum(t.get("pnl_usd", 0) for t in week_trades)
        wins = [t for t in week_trades if t.get("pnl_usd", 0) > 0]
        losses = [t for t in week_trades if t.get("pnl_usd", 0) <= 0]
        wr = len(wins)/len(week_trades)*100 if week_trades else 0
        sign = "+" if total_pnl >= 0 else ""
        emoji = "🟢" if total_pnl >= 0 else "🔴"
        _send(f"{emoji} <b>Weekly Report — {monday.strftime('%b %d')} to {today.strftime('%b %d')}</b>\nTotal P&L: <b>{sign}${total_pnl:.2f}</b>\nTrades: {len(week_trades)} | Win Rate: {wr:.0f}%\nWins: {len(wins)} | Losses: {len(losses)}")
        log.info(f"Weekly report sent")
    except Exception as e:
        log.error(f"send_weekly_report error: {e}")

if __name__ == "__main__":
    _send("✅ <b>TradeIQ Telegram Alerts Active!</b>\nAll notifications will be sent here.")
    print("Test message sent!")
