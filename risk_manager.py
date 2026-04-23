import os
import json
from datetime import datetime, timezone
import pytz
import config
from logger import log

_KILL_ACTIVE = False
EASTERN = pytz.timezone("US/Eastern")
_vix_cache = {"value": None, "fetched_at": None}
_pending_order_ids = set()
_spy_cache = {"value": None, "pct_change": None, "fetched_at": None}

def get_spy_change():
    """Get SPY % change today. Cached for 5 minutes."""
    from datetime import timezone
    now = datetime.now(timezone.utc)
    if _spy_cache["fetched_at"] and (now - _spy_cache["fetched_at"]).total_seconds() < 300:
        return _spy_cache["pct_change"]
    try:
        from alpaca_client import AlpacaClient
        ac = AlpacaClient()
        bars = ac.get_bars("SPY", timeframe="1Day", limit=2)
        if bars and len(bars) >= 2:
            prev_close = float(bars[-2]["close"])
            curr_close = float(bars[-1]["close"])
            pct = (curr_close - prev_close) / prev_close * 100
            _spy_cache["value"] = curr_close
            _spy_cache["pct_change"] = pct
            _spy_cache["fetched_at"] = now
            return pct
    except Exception:
        pass
    return _spy_cache["pct_change"] or 0.0

def get_sector_filter(confidence):
    """
    Sector filter based on SPY performance.
    Down >1.5%  → require 80%+ confidence (strong market headwind)
    Down >0.75% → require 76%+ confidence (mild headwind)
    Up           → normal 72% confidence
    Returns (allowed, required_confidence, reason)
    """
    spy_chg = get_spy_change()
    if spy_chg <= -1.5:
        required = 0.80
        if confidence < required:
            return False, required, f"SPY down {spy_chg:.1f}% — need {required:.0%} confidence"
        return True, required, f"SPY down {spy_chg:.1f}% — elevated threshold applied"
    elif spy_chg <= -0.75:
        required = 0.76
        if confidence < required:
            return False, required, f"SPY down {spy_chg:.1f}% — need {required:.0%} confidence"
        return True, required, f"SPY down {spy_chg:.1f}% — mild threshold applied"
    return True, 0.72, f"SPY {spy_chg:+.1f}% — normal threshold"

def activate_kill_switch(reason="manual"):
    global _KILL_ACTIVE
    _KILL_ACTIVE = True
    log.critical(f"KILL SWITCH ACTIVATED — reason: {reason}")

def deactivate_kill_switch():
    global _KILL_ACTIVE
    _KILL_ACTIVE = False
    log.warning("Kill switch deactivated")

def is_killed():
    return _KILL_ACTIVE or config.KILL_SWITCH

def get_daily_pnl():
    try:
        from trade_history import get_daily_summary
        closed_pnl = get_daily_summary().get("total_pnl", 0.0)
        # Add open unrealized P&L
        try:
            import json
            with open("bot_state.json") as f:
                state = json.load(f)
            open_pnl = sum(p.get("pnl_usd", 0) for p in state.get("positions", []))
        except:
            open_pnl = 0.0
        return round(closed_pnl + open_pnl, 2)
    except Exception as e:
        log.error(f"get_daily_pnl error: {e}")
        return 0.0

def get_open_position_count():
    try:
        with open("bot_state.json") as f:
            state = json.load(f)
        return len(state.get("positions", []))
    except Exception:
        return 0

def get_open_stock_count():
    try:
        with open("bot_state.json") as f:
            state = json.load(f)
        return len([p for p in state.get("positions", []) if p.get("market") == "stocks"])
    except Exception:
        return 0

def get_open_options_count():
    try:
        with open("bot_state.json") as f:
            state = json.load(f)
        return len([p for p in state.get("positions", []) if p.get("market") == "options"])
    except Exception:
        return 0

def is_market_open():
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return False
    o = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    c = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    return o <= now <= c

def is_in_no_trade_window():
    now = datetime.now(EASTERN)
    if now.weekday() >= 5:
        return True
    o = now.replace(hour=9,  minute=30, second=0, microsecond=0)
    c = now.replace(hour=16, minute=0,  second=0, microsecond=0)
    if (now - o).total_seconds() / 60 < config.NO_TRADE_OPEN_MINS:
        return True
    if (c - now).total_seconds() / 60 < config.NO_TRADE_CLOSE_MINS:
        return True
    return False

def get_vix():
    now = datetime.now(timezone.utc)
    if _vix_cache["fetched_at"] and (now - _vix_cache["fetched_at"]).total_seconds() < 900:
        return _vix_cache["value"]
    try:
        # Real VIX from CBOE free feed
        try:
            import requests as _req
            r = _req.get(
                "https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json",
                timeout=5
            )
            val = float(r.json()["data"]["current_price"])
            if val > 0:
                _vix_cache["value"] = val
                _vix_cache["fetched_at"] = now
                return val
        except: pass
        return float(_vix_cache.get("value", 18.0) or 18.0)
    except Exception:
        pass
    return _vix_cache["value"] or 20.0

def is_high_volatility():
    vix = get_vix()
    if vix > config.MAX_VIX:
        log.warning(f"High volatility: VIX={vix:.1f} > {config.MAX_VIX}")
        return True
    return False

def calculate_position_size(symbol, account_balance):
    base = account_balance * config.MAX_POSITION_PCT
    if symbol.upper() in {"TSLA", "NVDA", "AMD", "SOFI", "HOOD", "BABA"}:
        base *= 0.6
    return round(min(base, config.get_max_position_usd()), 2)

def register_pending_order(symbol):
    _pending_order_ids.add(symbol)

def clear_pending_order(symbol):
    _pending_order_ids.discard(symbol)

def is_duplicate_order(symbol):
    return symbol.upper() in _pending_order_ids

def check_all(symbol, confidence, account_balance):
    if is_killed():
        return False, "kill switch active"
    if not is_market_open():
        return False, "market closed"
    if is_in_no_trade_window():
        return False, f"no-trade window (first/last {config.NO_TRADE_OPEN_MINS} min)"
    daily_pnl = get_daily_pnl()
    limit = -abs(config.get_daily_loss_limit_usd())
    if daily_pnl <= limit:
        activate_kill_switch(reason=f"daily loss limit: ${daily_pnl:.2f}")
        return False, f"daily loss limit reached (${daily_pnl:.2f})"
    # Separate limits for stocks and options
    # Detect options by symbol format (e.g. TSLA260422C00350000 or TSLA  260422C00350000)
    import re
    market = "options" if "_OPT" in symbol or re.search(r"[A-Z]{1,5}\s*\d{6}[CP]\d{8}", symbol) else "stocks"
    if market == "options":
        if get_open_options_count() >= config.MAX_OPEN_OPTIONS:
            return False, f"max options positions ({config.MAX_OPEN_OPTIONS}) reached"
    else:
        if get_open_stock_count() >= config.MAX_OPEN_POSITIONS:
            return False, f"max stock positions ({config.MAX_OPEN_POSITIONS}) reached"
    if confidence < config.MIN_CONFIDENCE:
        return False, f"confidence {confidence:.0%} < min {config.MIN_CONFIDENCE:.0%}"
    # Sector filter — raise confidence bar on down market days
    if "_OPT" not in symbol:
        allowed, required, reason = get_sector_filter(confidence)
        if not allowed:
            return False, f"sector filter: {reason}"
        if required > config.MIN_CONFIDENCE:
            log.info(f"[SECTOR] {reason}")
    if is_high_volatility():
        return False, f"high VIX ({get_vix():.1f})"
    if is_duplicate_order(symbol):
        return False, f"duplicate order pending for {symbol}"
    return True, "ok"

def log_risk_status(account_balance):
    log.info(
        f"[RISK] killed={is_killed()} | "
        f"daily_pnl=${get_daily_pnl():+.2f}/limit=-${config.get_daily_loss_limit_usd():.2f} | "
        f"stocks={get_open_stock_count()}/{config.MAX_OPEN_POSITIONS} opts={get_open_options_count()}/{config.MAX_OPEN_OPTIONS} | "
        f"VIX={get_vix():.1f} | no_trade_window={is_in_no_trade_window()}"
    )
