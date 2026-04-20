import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from logger import log

def send_email(subject: str, body: str):
    try:
        user = os.getenv("GMAIL_USER", "")
        pw   = os.getenv("GMAIL_PASS", "")
        to   = os.getenv("GMAIL_TO", "")
        if not all([user, pw, to]):
            log.warning("Gmail not configured — skipping alert")
            return False
        msg = MIMEMultipart()
        msg["From"]    = user
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(user, pw)
            s.sendmail(user, to, msg.as_string())
        log.info(f"Email sent: {subject}")
        return True
    except Exception as e:
        log.error(f"Email error: {e}")
        return False

def send_sms(message: str):
    # Alias so existing code still works
    subject = message.split("\n")[0]
    return send_email(subject, message)

def alert_trade_opened(symbol, side, entry, qty, sl, tp, confidence, market):
    emoji = "🪙" if market == "crypto" else "📈"
    send_email(
        f"TradeIQ {emoji} OPENED {side} {symbol}",
        f"Symbol:     {symbol}\n"
        f"Side:       {side}\n"
        f"Entry:      ${entry:,.4f}\n"
        f"Quantity:   {qty:.4f}\n"
        f"Stop Loss:  ${sl:,.4f}\n"
        f"Take Profit:${tp:,.4f}\n"
        f"Confidence: {confidence:.0%}\n"
        f"Market:     {market.upper()}"
    )

def alert_trade_closed(symbol, side, entry, exit_price, pnl, pnl_pct, status, market):
    emoji = "🪙" if market == "crypto" else "📈"
    result = "✅ TP HIT" if status == "closed_tp" else "❌ SL HIT" if status == "closed_sl" else "◎ CLOSED"
    sign = "+" if pnl >= 0 else ""
    send_email(
        f"TradeIQ {emoji} {result} — {symbol} {sign}${pnl:.4f}",
        f"Symbol:     {symbol}\n"
        f"Side:       {side}\n"
        f"Entry:      ${entry:,.4f}\n"
        f"Exit:       ${exit_price:,.4f}\n"
        f"P&L:        {sign}${pnl:.4f} ({sign}{pnl_pct:.2f}%)\n"
        f"Result:     {result}\n"
        f"Market:     {market.upper()}"
    )

def alert_daily_summary(total, crypto, stocks, tpnl, wins, losses):
    sign = "+" if tpnl >= 0 else ""
    total_trades = wins + losses
    wr = f"{wins/total_trades*100:.0f}%" if total_trades > 0 else "—"
    send_email(
        f"TradeIQ 📊 Daily Summary — {sign}${tpnl:.2f}",
        f"Total Portfolio: ${total:,.2f}\n"
        f"Crypto:          ${crypto:,.2f}\n"
        f"Stocks:          ${stocks:,.2f}\n"
        f"Total P&L:       {sign}${tpnl:.2f}\n"
        f"Trades:          {wins}W / {losses}L\n"
        f"Win Rate:        {wr}"
    )

def send_weekly_report():
    """
    Weekly performance report — call every Friday at 4 PM.
    Summarizes the week: P&L, win rate, best/worst trade, top symbols.
    """
    try:
        from datetime import datetime, timedelta
        import json, os

        # Load trade history
        with open("trade_history.json") as f:
            all_trades = json.load(f)

        # Filter to this week (Monday - Friday)
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
            except:
                pass

        if not week_trades:
            send_email(
                "TradeIQ 📊 Weekly Report — No trades this week",
                "No closed trades this week.\nBot was running but no positions closed.\n\nCheck dashboard: https://tradeiqs.app"
            )
            return

        # Calculate stats
        total_pnl  = sum(t.get("pnl_usd", 0) for t in week_trades)
        wins       = [t for t in week_trades if t.get("pnl_usd", 0) > 0]
        losses     = [t for t in week_trades if t.get("pnl_usd", 0) <= 0]
        win_rate   = len(wins) / len(week_trades) * 100 if week_trades else 0
        best_trade = max(week_trades, key=lambda t: t.get("pnl_usd", 0))
        worst_trade= min(week_trades, key=lambda t: t.get("pnl_usd", 0))

        # Top symbols by P&L
        sym_pnl = {}
        for t in week_trades:
            sym = t.get("product_id", "?")
            sym_pnl[sym] = sym_pnl.get(sym, 0) + t.get("pnl_usd", 0)
        top_symbols = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:5]

        # Stock vs options breakdown
        stock_pnl = sum(t.get("pnl_usd",0) for t in week_trades if t.get("market") == "stocks")
        opts_pnl  = sum(t.get("pnl_usd",0) for t in week_trades if t.get("market") == "options")

        sign = "+" if total_pnl >= 0 else ""
        result_emoji = "🟢" if total_pnl >= 0 else "🔴"

        body = f"""TradeIQ Weekly Performance Report
Week of {monday.strftime('%b %d')} — {today.strftime('%b %d, %Y')}
{'='*45}

{result_emoji} TOTAL P&L:     {sign}${total_pnl:.2f}
   Stocks P&L:  {'+' if stock_pnl>=0 else ''}${stock_pnl:.2f}
   Options P&L: {'+' if opts_pnl>=0 else ''}${opts_pnl:.2f}

📊 TRADE STATS
   Total Trades: {len(week_trades)}
   Wins:         {len(wins)}
   Losses:       {len(losses)}
   Win Rate:     {win_rate:.0f}%
   Avg Win:      ${sum(t.get('pnl_usd',0) for t in wins)/len(wins):.2f if wins else 0:.2f}
   Avg Loss:     ${sum(t.get('pnl_usd',0) for t in losses)/len(losses):.2f if losses else 0:.2f}

🏆 BEST TRADE:  {best_trade.get('product_id')} +${best_trade.get('pnl_usd',0):.2f}
💀 WORST TRADE: {worst_trade.get('product_id')} ${worst_trade.get('pnl_usd',0):.2f}

📈 TOP SYMBOLS
"""
        for sym, pnl in top_symbols:
            sign2 = "+" if pnl >= 0 else ""
            body += f"   {sym:<12} {sign2}${pnl:.2f}\n"

        body += f"""
{'='*45}
Dashboard: https://tradeiqs.app
"""

        send_email(
            f"TradeIQ 📊 Weekly Report — {sign}${total_pnl:.2f} | {win_rate:.0f}% win rate",
            body
        )
        log.info(f"Weekly report sent: {len(week_trades)} trades, P&L={sign}${total_pnl:.2f}")

    except Exception as e:
        log.error(f"send_weekly_report error: {e}")
