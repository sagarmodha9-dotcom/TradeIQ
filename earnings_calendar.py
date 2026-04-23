"""
earnings_calendar.py — Fetch upcoming earnings dates via yfinance.
yfinance returns calendar as a dict with 'Earnings Date' as a list.
"""
import yfinance as yf
from datetime import datetime, date, timedelta
from logger import log

_cache = {}

def get_earnings_date(symbol: str):
    """Return next earnings date for symbol, cached for 6 hours."""
    # Skip ETFs — they don't have earnings
    if symbol in ["SPY", "QQQ", "IWM", "DIA", "GLD", "SLV", "TLT", "SQ", "XLF", "XLK"]:
        return None
    now = datetime.now()
    if symbol in _cache:
        cached_val, cached_at = _cache[symbol]
        if (now - cached_at).total_seconds() < 21600:
            return cached_val
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal and isinstance(cal, dict):
            val = cal.get("Earnings Date")
            if val:
                if isinstance(val, list):
                    val = val[0]
                if hasattr(val, 'date'):
                    earnings_date = val.date()
                elif isinstance(val, date):
                    earnings_date = val
                else:
                    earnings_date = None
                if earnings_date and earnings_date >= date.today():
                    _cache[symbol] = (earnings_date, now)
                    return earnings_date
    except Exception as e:
        log.warning(f"earnings_calendar {symbol}: {e}")
    _cache[symbol] = (None, now)
    return None

def days_to_earnings(symbol: str):
    """Return number of days until next earnings, or None if unknown."""
    ed = get_earnings_date(symbol)
    if ed is None:
        return None
    return (ed - date.today()).days

def has_earnings_soon(symbol: str, within_days: int = 7) -> bool:
    """True if earnings are within N days."""
    d = days_to_earnings(symbol)
    return d is not None and 0 <= d <= within_days

def get_options_expiry_days(symbol: str):
    """Return (days_out_list, reason) for options expiry selection."""
    d = days_to_earnings(symbol)
    if d is not None and 1 <= d <= 7:
        log.info(f"{symbol}: earnings in {d} days — using weekly options")
        return [d + 1, 7], f"earnings_play_{d}d"
    return [30, 37, 45], "standard"

def get_earnings_summary(symbols: list) -> dict:
    """Get earnings dates for all symbols — for dashboard display."""
    result = {}
    for sym in symbols:
        d = days_to_earnings(sym)
        ed = get_earnings_date(sym)
        result[sym] = {
            "days_to_earnings": d,
            "earnings_date": str(ed) if ed else None,
            "has_earnings_soon": d is not None and 0 <= d <= 7,
        }
    return result
