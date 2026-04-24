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

    # Balances
    tt_bal = tt.get_account_balance()
    alpaca_cash = ac.get_cash_balance()

    # Bot state
    with open("bot_state.json") as f:
        state = json.load(f)
    positions = state.get("positions", [])
    stocks = [p for p in positions if p.get("market") == "stocks"]
    options = [p for p in positions if p.get("market") == "options"]

    # Build positions text with P&L
    positions_text = ""
    for p in stocks:
        entry = float(p.get("entry_price", 0))
        current = ac.get_latest_price(p["product_id"]) or entry
        pnl = (current - entry) * float(p.get("quantity", 0))
        pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
        sign = "+" if pnl >= 0 else ""
        emoji = "🟢" if pnl >= 0 else "🔴"
        positions_text += "\n  " + emoji + " " + p["product_id"] + ": $" + str(round(current,2)) + " (" + sign + str(round(pnl_pct,1)) + "%) " + sign + "$" + str(round(pnl,2))
    for p in options:
        opt_pnl = float(p.get("pnl_usd", 0))
        sign = "+" if opt_pnl >= 0 else ""
        emoji = "🟢" if opt_pnl >= 0 else "🔴"
        positions_text += "\n  " + emoji + " " + p.get("underlying","OPT") + " CALL: " + sign + "$" + str(round(opt_pnl,2))
    if not positions_text:
        positions_text = "\n  No open positions"

    # Pre-market watchlist
    premarket_text = ""
    try:
        import os as _os
        if _os.path.exists("premarket_watchlist.json"):
            with open("premarket_watchlist.json") as _f:
                wl = json.load(_f)
            if wl:
                premarket_text = "\n\n🌅 <b>Pre-Market Picks</b>"
                for w in wl[:3]:
                    gap = w.get("gap_pct", 0)
                    sign = "+" if gap >= 0 else ""
                    reasons = ", ".join(w.get("reasons", []))
                    premarket_text += "\n  📊 " + w["symbol"] + ": " + sign + str(round(gap,1)) + "% | " + reasons
    except:
        pass

    # Earnings this week
    from earnings_calendar import get_earnings_date
    from datetime import date
    earnings_soon = []
    for sym in config.STOCK_SYMBOLS:
        try:
            ed = get_earnings_date(sym)
            if ed and (ed - date.today()).days <= 7:
                earnings_soon.append(sym + " (" + str((ed - date.today()).days) + "d)")
        except:
            pass

    market_emoji = "🟢 BULLISH" if spy_chg > 0 else "🔴 BEARISH"
    earnings_str = ", ".join(earnings_soon) if earnings_soon else "None"

    msg = "🌅 <b>TradeIQ Morning Briefing</b> — " + datetime.now().strftime('%b %d, %Y')
    msg += "\n\n📊 <b>Market</b>"
    msg += "\nSPY: $" + str(round(spy_price,2)) + " (" + ("+" if spy_chg >= 0 else "") + str(round(spy_chg,1)) + "% yesterday)"
    msg += "\nMarket: " + market_emoji
    msg += "\n\n💰 <b>Balances</b>"
    msg += "\nAlpaca Cash: $" + str(round(alpaca_cash,2))
    msg += "\nTastytrade: $" + str(round(tt_bal,2))
    msg += "\n\n📈 <b>Open Positions</b>" + positions_text
    msg += "\n\n⚡ <b>Earnings This Week</b>\n" + earnings_str
    msg += premarket_text
    # Overnight BUY signals
    overnight_text = ""
    try:
        import json as _j2, os as _os2
        if _os2.path.exists("bot_state.json"):
            with open("bot_state.json") as _f2:
                _state = _j2.load(_f2)
            signals = _state.get("signals", [])
            buys = [s for s in signals if s.get("action") == "BUY"]
            if buys:
                overnight_text = "\n\n🔍 <b>Top BUY Signals</b>"
                for s in sorted(buys, key=lambda x: x.get("confidence",0), reverse=True)[:5]:
                    conf = round(s.get("confidence",0)*100)
                    overnight_text += "\n  📈 " + s.get("product_id","") + ": " + str(conf) + "% conf"
    except:
        pass

    msg += overnight_text
    msg += "\n\n🎯 Bot is LIVE and scanning 26 symbols"

    send(msg)
    print("✅ Morning briefing sent!")

if __name__ == "__main__":
    run_briefing()

# Alias for compatibility
send_morning_briefing = run_briefing
