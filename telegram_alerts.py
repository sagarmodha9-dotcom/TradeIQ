import os
import requests
from dotenv import load_dotenv
load_dotenv()

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send(msg):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except:
        pass

def alert_trade_closed(symbol, pnl, reason, entry, exit_price):
    emoji = "🟢" if pnl > 0 else "🔴"
    send(f"{emoji} <b>Stock Closed — {symbol}</b>\nEntry: ${entry:.2f} → Exit: ${exit_price:.2f}\nP&L: <b>${pnl:+.2f}</b>\nReason: {reason}")

def alert_option_bought(symbol, contract, strike, expiry, cost, delta):
    send(f"📈 <b>Option Bought — {symbol}</b>\nContract: {contract}\nStrike: ${strike} | Expiry: {expiry}\nCost: ${cost:.0f} | Delta: {delta:.2f}")

def alert_option_closed(symbol, contract, pnl, reason):
    emoji = "🟢" if pnl > 0 else "🔴"
    send(f"{emoji} <b>Option Closed — {symbol}</b>\nContract: {contract}\nP&L: <b>${pnl:+.2f}</b>\nReason: {reason}")

def alert_kill_switch(reason, daily_pnl):
    send(f"🚨 <b>KILL SWITCH ACTIVATED</b>\nReason: {reason}\nDaily P&L: ${daily_pnl:+.2f}")

def alert_error(message):
    send(f"⚠️ <b>Bot Alert</b>\n{message}")

if __name__ == "__main__":
    send("✅ Telegram alerts connected and working!")
    print("Test message sent to phone")
