import os
import requests
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send(msg):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                  json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"})

def run_briefing():
    from alpaca_client import AlpacaClient
    from tastytrade_client import TastytradeClient
    import json, config

    ac = AlpacaClient()
    tt = TastytradeClient()

    # Market status
    spy_bars = ac.get_bars("SPY", timeframe="1Day", limit=2) or []
    spy_price = ac.get_latest_price("SPY") or 0
    spy_chg = ((spy_price - spy_bars[-2]["close"]) / spy_bars[-2]["close"] * 100) if len(spy_bars) >= 2 else 0

    # Tastytrade balance
    tt_bal = tt.get_account_balance()
    tt_positions = tt.get_positions()

    # Bot state
    with open("bot_state.json") as f:
        state = json.load(f)
    positions = state.get("positions", [])
    stocks = [p for p in positions if p.get("market") == "stocks"]
    options = [p for p in positions if p.get("market") == "options"]

    # Alpaca balance
    alpaca_cash = ac.get_cash_balance()

    # Earnings this week
    from earnings_calendar import get_earnings_date
    from datetime import date, timedelta
    earnings_soon = []
    for sym in config.STOCK_SYMBOLS:
        try:
            ed = get_earnings_date(sym)
            if ed and (ed - date.today()).days <= 7:
                earnings_soon.append(f"{sym} ({(ed - date.today()).days}d)")
        except: pass

    msg = f"""🌅 <b>TradeIQ Morning Briefing</b> — {datetime.now().strftime('%b %d, %Y')}

📊 <b>Market</b>
SPY: ${spy_price:.2f} ({spy_chg:+.1f}% yesterday)
Market: {"🟢 BULLISH" if spy_chg > 0 else "🔴 BEARISH"}

💰 <b>Balances</b>
Alpaca Cash: ${alpaca_cash:,.2f}
Tastytrade: ${tt_bal:,.2f}

📈 <b>Open Positions</b>
Stocks: {len(stocks)}/4
Options: {len(options)}/4

⚡ <b>Earnings This Week</b>
{", ".join(earnings_soon) if earnings_soon else "None"}

🎯 Bot is LIVE and scanning 26 symbols
"""
    send(msg)
    print("✅ Morning briefing sent!")

if __name__ == "__main__":
    run_briefing()
