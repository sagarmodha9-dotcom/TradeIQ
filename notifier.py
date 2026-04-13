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
