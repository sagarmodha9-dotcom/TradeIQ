import sys
import os
import time
import json
# ===== Earnings cache (prevents rate limit) =====
_earnings_cache = {}
_earnings_cache_time = {}

def cached_days_to_earnings(symbol):
    # Earnings API temporarily disabled because provider is rate-limiting.
    # Return None so bot skips earnings-based blocks/exits instead of spamming requests.
    return None

def _safe_json(obj):
    """Recursively convert non-JSON-serializable types."""
    if isinstance(obj, bool): return int(obj)
    if isinstance(obj, dict): return {k: _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, list): return [_safe_json(i) for i in obj]
    try: json.dumps(obj); return obj
    except: return str(obj)
import signal
import argparse
from datetime import datetime
from tabulate import tabulate
import config
import risk_manager
import news_sentiment
import win_rate_tracker
from earnings_calendar import get_options_expiry_days, has_earnings_soon
from logger import log

# In-memory cooldown
_trade_cooldown = {}
_immediate_redeploy = False  # Flag to trigger instant rescan after TP close
from alpaca_client import AlpacaClient
from analyzer import Analyzer
from stock_analyzer import StockAnalyzer
from trade_manager import TradeManager
from portfolio_tracker import PortfolioTracker

_running = True
_sent_summaries = set()

# ===== GLOBAL SAFETY / KILL SWITCH =====
SAFETY_STATE_FILE = "safety_state.json"
MAX_ORDER_FAILURES = 3

def _load_safety_state():
    try:
        import json, os
        if os.path.exists(SAFETY_STATE_FILE):
            with open(SAFETY_STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {"order_failures": 0, "options_trading_enabled": True}

def _save_safety_state(state):
    try:
        import json
        with open(SAFETY_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        try:
            log.error(f"Could not save safety state: {e}")
        except Exception:
            pass

def required_stock_confidence(open_stock_count=0):
    """
    Dynamic confidence threshold:
    - Normal minimum is config.MIN_CONFIDENCE
    - If already holding several stocks, require stronger signal
    - Prevents low-quality 72% entries when portfolio is already loaded
    """
    try:
        base = float(config.MIN_CONFIDENCE)
    except Exception:
        base = 0.72

    if open_stock_count >= 5:
        return max(base, 0.80)
    if open_stock_count >= 4:
        return max(base, 0.76)
    return base

def has_enough_bar_data(symbol, bars, min_bars=50):
    if not bars or len(bars) < min_bars:
        log.warning(f"[DATA BLOCK] {symbol}: only {len(bars) if bars else 0} bars — skipping")
        return False

    closes = []
    for b in bars:
        try:
            closes.append(float(b.get("close") or b.get("c") or 0))
        except Exception:
            closes.append(0)

    good = [c for c in closes if c > 0]
    if len(good) < min_bars:
        log.warning(f"[DATA BLOCK] {symbol}: insufficient valid close prices — skipping")
        return False

    return True

def dynamic_take_profit(pnl_pct):
    # scale profits instead of fixed 6%
    if pnl_pct >= 10:
        return "close"
    if pnl_pct >= 6:
        return "trail"
    return "hold"



def get_market_regime_from_signal(signal):
    """
    Market regime detection:
    TREND   = strong alignment + volume + momentum
    CHOP    = weak/sideways/low-volume market
    NEUTRAL = anything in between
    """
    try:
        indicators = signal.get("indicators", {}) or {}

        price_vs_ema20 = float(indicators.get("price_vs_ema20", 0) or 0)
        price_vs_vwap = float(indicators.get("price_vs_vwap", 0) or 0)
        volume_ratio = float(indicators.get("volume_ratio", 0) or 0)
        rsi = float(indicators.get("rsi_14", 50) or 50)
        ema_alignment = str(indicators.get("ema_alignment", "")).lower()

        macd = indicators.get("macd", {}) or {}
        macd_hist = float(macd.get("histogram", 0) or 0)

        if (
            ema_alignment == "bullish"
            and price_vs_ema20 >= 1.0
            and price_vs_vwap >= 0.75
            and volume_ratio >= 1.0
            and macd_hist > 0
            and 45 <= rsi <= 68
        ):
            return "TREND"

        if (
            abs(price_vs_ema20) < 0.40
            or volume_ratio < 0.55
            or ema_alignment in ("bearish", "mixed")
            or macd_hist <= 0
        ):
            return "CHOP"

        return "NEUTRAL"

    except Exception:
        return "NEUTRAL"


def get_market_regime_multiplier(signal):
    regime = get_market_regime_from_signal(signal)
    if regime == "TREND":
        return 1.15
    if regime == "CHOP":
        return 0.70
    return 1.0


def get_stock_streak_multiplier():
    """
    Conservative stock-only streak sizing.
    Options are NOT scaled by this.
    """
    try:
        import json, os

        if not os.path.exists("trade_history.json"):
            return 1.0

        with open("trade_history.json") as f:
            trades = json.load(f)

        stock_trades = [
            t for t in trades
            if t.get("market") == "stocks" and "pnl_usd" in t
        ][-10:]

        if not stock_trades:
            return 1.0

        streak = 0
        last_positive = None

        for t in reversed(stock_trades):
            pnl = float(t.get("pnl_usd", 0) or 0)
            positive = pnl > 0

            if last_positive is None:
                last_positive = positive

            if positive == last_positive:
                streak += 1
            else:
                break

        if last_positive is True:
            if streak >= 5:
                return 1.25
            if streak >= 3:
                return 1.15

        if last_positive is False:
            if streak >= 2:
                return 0.75

        return 1.0

    except Exception as e:
        log.warning(f"Streak sizing check failed: {e}")
        return 1.0


def dynamic_position_size(confidence, total_balance, signal=None):
    # Stock-only position sizing with conservative streak + regime scaling
    base = total_balance * 0.10
    streak_multiplier = get_stock_streak_multiplier()
    regime_multiplier = get_market_regime_multiplier(signal) if signal else 1.0

    if confidence >= 0.85:
        size = base * 1.30
    elif confidence >= 0.80:
        size = base * 1.15
    else:
        size = base

    final_size = size * streak_multiplier * regime_multiplier

    # Hard cap: never let one stock exceed 15% of account
    return min(final_size, total_balance * 0.15)

def valid_trade_price(symbol, price, min_price=1.0):
    try:
        price = float(price or 0)
        if price < min_price:
            log.warning(f"[DATA BLOCK] {symbol}: invalid price ${price:.2f} — skipping trade")
            return False
        return True
    except Exception:
        log.warning(f"[DATA BLOCK] {symbol}: invalid price value — skipping trade")
        return False

def good_options_time():
    try:
        now = datetime.now()
        hour = now.hour
        minute = now.minute

        # Convert to minutes since midnight
        t = hour * 60 + minute

        # 10:00–12:00 OR 13:30–15:30
        if (600 <= t <= 720) or (810 <= t <= 930):
            return True

        return False
    except:
        return True

def option_trade_allowed_today():
    try:
        import json, os
        f = "option_trade_limit.json"
        today = datetime.now().strftime("%Y-%m-%d")

        if os.path.exists(f):
            data = json.load(open(f))
        else:
            data = {}

        if data.get("date") == today and data.get("count", 0) >= 1:
            return False

        return True
    except:
        return True

def record_option_trade():
    try:
        import json, os
        f = "option_trade_limit.json"
        today = datetime.now().strftime("%Y-%m-%d")

        if os.path.exists(f):
            data = json.load(open(f))
        else:
            data = {}

        if data.get("date") == today:
            data["count"] = data.get("count", 0) + 1
        else:
            data = {"date": today, "count": 1}

        json.dump(data, open(f, "w"))
    except:
        pass

def options_trading_allowed():
    state = _load_safety_state()
    return bool(state.get("options_trading_enabled", True))

def record_safety_failure(reason):
    state = _load_safety_state()
    state["order_failures"] = int(state.get("order_failures", 0)) + 1
    try:
        log.error(f"SAFETY FAILURE {state['order_failures']}/{MAX_ORDER_FAILURES}: {reason}")
    except Exception:
        pass

    if state["order_failures"] >= MAX_ORDER_FAILURES:
        state["options_trading_enabled"] = False
        try:
            log.critical("🛑 KILL SWITCH: options trading disabled after repeated failures")
            from notifier import _send
            _send(f"🛑 <b>KILL SWITCH</b>\nOptions trading disabled after {state['order_failures']} failures.\nReason: {reason}")
        except Exception:
            pass

    _save_safety_state(state)

def clear_safety_failures():
    state = _load_safety_state()
    state["order_failures"] = 0
    _save_safety_state(state)

def _handle_exit(sig, frame):
    global _running
    log.warning("Shutdown signal — finishing cycle...")
    _running = False

signal.signal(signal.SIGINT,  _handle_exit)
signal.signal(signal.SIGTERM, _handle_exit)

def print_banner(is_live):
    mode = "LIVE TRADING" if is_live else "PAPER TRADING"
    print("\n" + "="*60)
    print(f"  TradeIQ AI Bot  |  {mode}")
    print(f"  Crypto: top {config.TOP_PAIRS_COUNT} pairs | Stocks: {len(config.STOCK_SYMBOLS)} symbols")
    print(f"  SL: {config.STOP_LOSS_PCT:.0%}  TP: {config.TAKE_PROFIT_PCT:.0%}  Min conf: {config.MIN_CONFIDENCE:.0%}")
    print("="*60 + "\n")
    # Telegram ping on every startup so user knows if launchd restarts the bot
    try:
        from notifier import _send
        from datetime import datetime as _dt
        _send(f"🟢 <b>BOT STARTED</b>\nMode: {mode}\nTime: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}\nIf you didn't restart it manually, launchd auto-restarted (possible crash).")
    except Exception as _be:
        pass  # don't crash bot if telegram unavailable

def save_state(stock_signals, positions, pt):
    try:
        # Load existing positions to preserve them
        try:
            import json as _json
            with open("bot_state.json") as _f:
                _existing = _json.load(_f)
            _existing_stocks = [p for p in _existing.get("positions", []) if p.get("market") == "stocks"]
            _existing_options = [p for p in _existing.get("positions", []) if p.get("market") == "options"]
        except Exception:
            _existing_stocks = []
            _existing_options = []
        # Merge: new positions take priority, keep existing ones not in new list
        new_syms = [p["product_id"] for p in positions]
        merged_stocks = positions + [p for p in _existing_stocks if p["product_id"] not in new_syms]
        new_opt_syms = [p["product_id"] for p in positions if p.get("market") == "options"]
        merged_options = [p for p in _existing_options if p["product_id"] not in new_opt_syms]
        all_positions = merged_stocks + merged_options
        port = pt.to_dict()
        # Calculate stock P&L
        try:
            import json as _j2
            with open("trade_history.json") as _tf:
                _all_trades = _j2.load(_tf)
            _closed_stock_pnl = sum(t.get("pnl_usd",0) for t in _all_trades if t.get("market")=="stocks")
        except Exception:
            _closed_stock_pnl = port["stock_pnl"]
        stock_positions = [p for p in all_positions if p.get("market") == "stocks"]
        unrealized_stock_pnl = sum(p.get("pnl_usd", 0) for p in stock_positions)
        stock_pnl = _closed_stock_pnl + unrealized_stock_pnl
        stock_balance = pt.stock_balance + stock_pnl
        opts_positions = [p for p in all_positions if p.get("market") == "options"]
        opts_pnl = sum(p.get("pnl_usd", 0) for p in opts_positions)
        opts_cost = sum(p.get("usd_value", 0) for p in opts_positions)
        total_pnl = stock_pnl + opts_pnl
        total_balance = stock_balance + opts_pnl
        # Safe JSON serializer — converts booleans and non-serializable types
        def _safe(obj):
            if isinstance(obj, bool): return int(obj)
            if isinstance(obj, dict): return {k: _safe(v) for k, v in obj.items()}
            if isinstance(obj, list): return [_safe(i) for i in obj]
            try: json.dumps(obj); return obj
            except: return str(obj)
        with open("bot_state.json", "w") as f:
            json.dump(_safe({
                "signals":          stock_signals,
                "scanned_at":       datetime.now().isoformat(),
                "positions":        all_positions,
                "closed_trades":    [],
                "stats":            tm.stats() if "tm" in dir() else {},
                "portfolio_usd":    total_balance,
                "stock_portfolio":  round(stock_balance, 2),
                "options_portfolio":opts_cost,
                "stock_pnl":        round(stock_pnl, 2),
                "options_pnl":      round(opts_pnl, 2),
                "total_pnl":        round(total_pnl, 2),
                "stock_open":       len([p for p in all_positions if p.get("market")=="stocks"]),
                "options_open":     len(opts_positions),
                "stock_win_rate":   port["stock_win_rate"],
                "stock_trades":     port["stock_trades"],
            }), f)
        log.info(f"Portfolio — Stocks:${stock_balance:,.2f} | Options P&L:${opts_pnl:+.2f} | Total:${total_balance:,.2f}")
    except Exception as e:
        log.error(f"Could not save state: {e}")

def get_recent_losers(hours=24):
    """Get symbols that hit SL in the last X hours - don't rebuy these."""
    try:
        from trade_history import load_history
        from datetime import datetime, timedelta
        cutoff = datetime.now() - timedelta(hours=hours)
        history = load_history()
        losers = set()
        for t in history:
            if t.get("status") == "closed_sl":
                closed_at = t.get("closed_at", "")
                if closed_at:
                    try:
                        dt = datetime.fromisoformat(closed_at.replace("Z",""))
                        if dt > cutoff:
                            losers.add(t["product_id"])
                    except Exception:
                        pass
        if losers:
            log.info(f"Cooldown symbols (hit SL recently): {', '.join(losers)}")
        return losers
    except Exception:
        return set()

def is_stock_market_bullish(ibkr):
    """Returns True if SPY is above its 20-day EMA (bullish trend)."""
    try:
        bars = ibkr.get_bars("SPY", timeframe="1Day", limit=25)
        if len(bars) < 20:
            return True
        closes = [float(b.get("c") or b.get("close") or 0) for b in bars]
        ema20 = closes[0]
        k = 2 / 21
        for c in closes[1:]:
            ema20 = c * k + ema20 * (1 - k)
        current = closes[-1]
        bullish = current > ema20
        log.info(f"SPY trend: ${current:,.2f} vs EMA20 ${ema20:,.2f} — {'BULLISH ✅' if bullish else 'BEARISH ❌'}")
        return bullish
    except Exception as e:
        log.error(f"SPY trend check error: {e}")
        return True

def get_position_size(symbol, base_size, ibkr):
    """Always return exactly base_size — fractional shares handle any price."""
    try:
        size = base_size  # Always $200 — fractional shares handle the rest
        log.info(f"{symbol}: position size → ${size:.0f}")
        return size
    except Exception:
        return base_size

_daily_loss_alerted = False
_daily_loss_check_date = None

def check_daily_loss_limit(ibkr):
    global _daily_loss_alerted, _daily_loss_check_date
    from datetime import datetime as _dt
    today = _dt.now().date()
    if _daily_loss_check_date != today:
        _daily_loss_alerted = False
        _daily_loss_check_date = today
    try:
        import requests, os
        headers = {"APCA-API-KEY-ID": os.getenv("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": os.getenv("ALPACA_SECRET_KEY")}
        r = requests.get("https://api.alpaca.markets/v2/account/portfolio/history?period=1D&timeframe=1H", headers=headers, timeout=10)
        data = r.json()
        equity = [e for e in data.get("equity", []) if e]
        if len(equity) >= 2:
            day_open = equity[0]
            current = equity[-1]
            day_change_pct = ((current - day_open) / day_open) * 100
            if day_change_pct <= -config.MAX_DAILY_LOSS_PCT:
                if not _daily_loss_alerted:
                    log.warning(f"DAILY LOSS LIMIT HIT: {day_change_pct:.2f}% - blocking new entries")
                    try:
                        from notifier import _send
                        _send(f"DAILY LOSS LIMIT\nDown {day_change_pct:.2f}% today\nNew entries paused")
                    except: pass
                    _daily_loss_alerted = True
                return True
        return False
    except Exception as e:
        log.error(f"Daily loss check error: {e}")
        return False

def run_stock_scan(ibkr, stock_analyzer, pt):
    global _trade_cooldown
    # Load existing stock positions from state
    try:
        import json
        with open("bot_state.json") as f:
            existing = json.load(f)
        existing_positions = [p for p in existing.get("positions", []) if p.get("market") == "stocks"]
    except Exception:
        existing_positions = []
    # Also check live Alpaca positions to prevent duplicate entries
    try:
        alpaca_positions = ibkr.get_positions()
        alpaca_syms = {p["symbol"] for p in alpaca_positions}
        for sym in alpaca_syms:
            if not any(p.get("product_id") == sym for p in existing_positions):
                existing_positions.append({"product_id": sym, "market": "stocks"})
    except Exception:
        alpaca_syms = set()
    # In-memory lock — track symbols ordered this scan cycle
    _ordered_this_scan = set()
    if not ibkr.is_market_open():
        log.info("── Stock market closed — skipping")
        return [], []
    if not is_stock_market_bullish(ibkr):
        log.info("── Stock scan — SKIPPING NEW BUYS (SPY bearish)")
        return [], []
    if check_daily_loss_limit(ibkr):
        log.info("── Stock scan — SKIPPING NEW BUYS (daily loss limit)")
        return [], []
    log.info(f"── Stock scan — {len(config.STOCK_SYMBOLS)} symbols")
    # No time-based cooldown — daily loss limit is the safety net
    signals, positions = [], []
    for symbol in config.STOCK_SYMBOLS:
        try:
            bars   = ibkr.get_bars(symbol, timeframe="1Hour", limit=100)
            if not has_enough_bar_data(symbol, bars, min_bars=50):
                continue
            signal = stock_analyzer.analyze(symbol, bars)
            if signal:
                if not valid_trade_price(symbol, signal.get("entry_price")):
                    continue
                signals.append({
                    "product_id":  symbol, "action": signal["action"],
                    "confidence":  signal["confidence"], "entry_price": signal["entry_price"],
                    "stop_loss":   signal["stop_loss"], "take_profit": signal["take_profit"],
                    "risk_reward": signal.get("risk_reward", 2.0),
                    "reasoning":   signal.get("reasoning", ""), "market": "stocks",
                })
                # Pre-earnings entry block: don't open new positions <= 1 day before earnings
                _entry_dte = cached_days_to_earnings(symbol)
                if _entry_dte is not None and _entry_dte <= 1 and signal["action"] == "BUY":
                    log.warning(f"[EARNINGS BLOCK] {symbol}: earnings in {_entry_dte} day(s) — skipping new entry")
                    continue
                
                # 2 minute cooldown after TP/SL — prevents buying right at peak
                _cooldown_active = False
                if symbol in _trade_cooldown:
                    elapsed = (datetime.now() - _trade_cooldown[symbol]).total_seconds()
                    if elapsed < 120:  # 2 minutes
                        _cooldown_active = True
                        log.info(f"{symbol}: cooldown active ({120-elapsed:.0f}s remaining)")
                    else:
                        del _trade_cooldown[symbol]  # cooldown expired
                open_stock_count = len(existing_positions)
                needed_conf = required_stock_confidence(open_stock_count)

                if signal["action"] == "BUY" and not _cooldown_active:
                    _regime = get_market_regime_from_signal(signal)
                    log.info(f"REGIME {symbol}: {_regime}")

                    open_stock_count = len(existing_positions)
                    needed_conf = required_stock_confidence(open_stock_count)

                    if signal["confidence"] < needed_conf:
                        log.info(f"{symbol}: BLOCKED — conf {signal['confidence']:.0%} < required {needed_conf:.0%}")
                        continue
                    if signal["confidence"] < needed_conf:
                        log.info(f"{symbol}: confidence {signal['confidence']:.0%} below dynamic threshold {needed_conf:.0%} — skipping")
                        continue
                    if not valid_trade_price(symbol, signal.get("entry_price")):
                        continue
                    # Skip if already have open position in this symbol
                    existing_syms = [p.get("product_id") for p in existing_positions]
                    if symbol in existing_syms:
                        log.info(f"{symbol}: already have open position — skipping")
                        continue
                    if symbol in _ordered_this_scan:
                        log.info(f"{symbol}: already ordered this scan — skipping")
                        continue
                    # News sentiment filter
                    signal = news_sentiment.adjust_signal_for_news(symbol, signal)
                    if not signal:
                        log.info(f"[NEWS BLOCK] {symbol}: skipping due to negative news")
                        continue
                    # Win rate filter
                    skip, wr_reason = win_rate_tracker.should_skip_symbol(symbol)
                    if skip:
                        log.info(f"[WINRATE BLOCK] {symbol}: {wr_reason}")
                        continue
                    ok, reason = risk_manager.check_all(symbol, signal["confidence"], pt.stock_balance)
                    if not ok:
                        log.info(f"[BLOCKED] {symbol}: {reason}")
                        continue
                    if len(existing_positions) >= config.MAX_OPEN_POSITIONS:
                        log.info(f"{symbol}: max open stock positions reached ({len(existing_positions)}/{config.MAX_OPEN_POSITIONS}) — skipping")
                        continue

                    # Always size based on TOTAL account balance, capped at available cash
                    # Use real live balance for dynamic position sizing
                    try:
                        _live_bal = float(ibkr.get_account().get("portfolio_value", config.LIVE_ACCOUNT_BALANCE))
                    except:
                        _live_bal = config.LIVE_ACCOUNT_BALANCE
                    base_size = _live_bal * config.MAX_POSITION_PCT
                    try:
                        avail_cash = ibkr.get_cash_balance()
                        if avail_cash < base_size:
                            log.warning(f"[BLOCKED] {symbol}: insufficient cash ${avail_cash:.0f} < ${base_size:.0f}")
                            continue
                    except:
                        avail_cash = base_size
                    size_usd = get_position_size(symbol, base_size, ibkr)
                    _ordered_this_scan.add(symbol)
                    fill = ibkr.place_market_order(symbol, "buy", notional=size_usd)
                    if fill and (fill.get("success") or fill.get("status") in ["filled", "partially_filled", "Filled", "accepted", "pending_new", "new"]):
                        # Wait for Alpaca to fill the order and get real price
                        import time; time.sleep(2)
                        # Fetch real fill price from Alpaca positions
                        try:
                            real_positions = ibkr.get_positions()
                            real_pos = next((p for p in real_positions if p["symbol"] == symbol), None)
                            if real_pos and float(real_pos.get("avg_entry_price", 0)) > 0:
                                entry_price = float(real_pos["avg_entry_price"])
                                qty = float(real_pos["qty"])
                            else:
                                entry_price = float(fill.get("fill_price") or ibkr.get_latest_price(symbol) or signal["entry_price"] or 0)
                                qty = float(size_usd / entry_price if entry_price > 0 else 1)
                        except:
                            entry_price = float(fill.get("fill_price") or signal["entry_price"] or 0)
                            qty = float(size_usd / entry_price if entry_price > 0 else 1)
                        # Telegram alert for trade opened
                        from notifier import alert_trade_opened
                        alert_trade_opened(symbol, "BUY", entry_price, round(qty, 4),
                            signal["stop_loss"], signal["take_profit"],
                            signal["confidence"], "stocks")
                        positions.append({
                            "product_id":  symbol, "side": "BUY",
                            "entry_price": entry_price,
                            "quantity":    qty,
                            "usd_value":   size_usd,
                            "stop_loss":   signal["stop_loss"],
                            "take_profit": signal["take_profit"],
                            "confidence":  signal["confidence"],
                            "reasoning":   signal.get("reasoning", ""),
                            "key_signals": signal.get("key_signals", []),
                            "indicators":  {k: int(v) if isinstance(v, bool) else v for k, v in signal.get("indicators", {}).items()},
                            "opened_at":   datetime.now().isoformat(),
                            "market":      "stocks", "pnl_usd": 0.0,
                        })
        except Exception as e:
            log.error(f"{symbol}: {e}")
    try:
        acct = ibkr.get_account()
        val = float(acct.get("portfolio_value", 0))
        if 0 < val < 50000:
            pt.update_stock_balance(val)
    except Exception:
        pass
    return signals, positions

def monitor_stock_positions(ibkr, pt):
    """Check open stock positions and close if SL or TP hit."""
    try:
        # Only monitor during market hours
        if not ibkr.is_market_open():
            return
        import json
        with open("bot_state.json") as f:
            state = json.load(f)
        stock_positions = [p for p in state.get("positions", []) if p.get("market") == "stocks"]
        if not stock_positions:
            return
        closed = []
        for pos in stock_positions:
            symbol = pos["product_id"]
            entry  = float(pos["entry_price"])
            sl     = float(pos["stop_loss"])
            tp     = float(pos["take_profit"])
            entry  = float(pos["entry_price"])
            # Trailing stop: once up 2%, move SL to breakeven; up 3.5%, trail at 1.5% below current
            try:
                current = ibkr.get_latest_price(symbol)
                if not current or current <= 0:
                    continue
                pnl = (current - entry) * float(pos["quantity"])
                pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
                # Progressive profit lock:
                # +2%  -> move SL to breakeven
                # +3%  -> lock small profit
                # +5%  -> trail tighter
                # +8%+ -> protect larger winner while letting it run
                new_sl = None
                sl_reason = None

                if current >= entry * 1.08:
                    new_sl = max(entry * 1.04, current * 0.97)
                    sl_reason = "RUNNER TRAIL"
                elif current >= entry * 1.05:
                    new_sl = max(entry * 1.02, current * 0.98)
                    sl_reason = "PROFIT TRAIL"
                elif current >= entry * 1.03:
                    new_sl = entry * 1.005
                    sl_reason = "PROFIT LOCK"
                elif current >= entry * 1.02:
                    new_sl = entry * 1.001
                    sl_reason = "BREAKEVEN"

                if new_sl:
                    new_sl = round(new_sl, 4)
                    if new_sl > sl:
                        pos["stop_loss"] = new_sl
                        sl = new_sl
                        log.info(f"🔒 {sl_reason} {symbol} → ${new_sl:.2f} (price=${current:.2f}, pnl={pnl_pct:+.1f}%)")
                # Pre-earnings risk management — tighten SL or exit before earnings
                try:
                    _dte = cached_days_to_earnings(symbol)
                    
                    # FORCE EXIT on earnings day (DTE == 0) — never hold through earnings
                    if _dte is not None and _dte == 0:
                        log.warning(f"⚠️ EARNINGS DAY EXIT {symbol} @ ${current:.2f} — earnings today, force closing")
                        _earnings_sell = ibkr.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
                        if _earnings_sell and isinstance(_earnings_sell, dict) and _earnings_sell.get("id"):
                            from trade_history import save_trade
                            save_trade({"product_id": symbol, "side": "BUY", "entry_price": entry,
                                "exit_price": current, "pnl_usd": round(pnl, 4),
                                "pnl_pct": round(pnl_pct, 2), "status": "closed_earnings_exit",
                                "market": "stocks", "confidence": pos.get("confidence", 0),
                                "opened_at": pos.get("opened_at"), "closed_at": datetime.now().isoformat()})
                            pt.record_stock_trade(round(pnl, 4), win=(pnl > 0))
                            from notifier import alert_trade_closed
                            alert_trade_closed(symbol, "BUY", entry, current, pnl, pnl_pct, "closed_earnings_exit", "stocks")
                            closed.append(symbol)
                            _trade_cooldown[symbol] = datetime.now()
                            continue
                        else:
                            log.warning(f"⚠️ EARNINGS DAY EXIT {symbol}: sell failed — will retry next scan")
                    
                    # Tighten stop loss as earnings approaches
                    if _dte is not None and _dte <= 4 and pos.get("entry_price"):
                        if _dte <= 2:
                            tight_sl_pct = 0.015  # 1.5% SL within 2 days of earnings
                        else:
                            tight_sl_pct = 0.020  # 2% SL within 3-4 days
                        tight_sl = round(float(pos["entry_price"]) * (1 - tight_sl_pct), 2)
                        if tight_sl > pos.get("stop_loss", 0):
                            log.info(f"🔒 PRE-EARNINGS SL {symbol} → ${tight_sl:.2f} (DTE={_dte}, was ${pos.get('stop_loss',0):.2f})")
                            pos["stop_loss"] = tight_sl
                            sl = tight_sl
                    
                    opened_at = pos.get("opened_at")
                    if opened_at:
                        from datetime import datetime as _dt
                        opened_time = _dt.fromisoformat(opened_at.replace("Z",""))
                        hours_open = (_dt.now() - opened_time).total_seconds() / 3600
                        # Time exit at +3% normally; FORCE time exit if earnings within 3 days
                        _force_exit = _dte is not None and _dte <= 3 and pnl_pct > 0
                        if (hours_open >= 4 and pnl_pct >= 3.0) or _force_exit:
                            log.info(f"⏰ TIME EXIT {symbol} @ ${current:.2f} — open {hours_open:.1f}hrs +{pnl_pct:.1f}% — taking profit")
                            _sell_result = ibkr.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
                            # Hard verify: check if Alpaca actually has the position gone, not just response shape
                            _sell_failed = not _sell_result or (isinstance(_sell_result, dict) and _sell_result.get("status") in ("rejected", "canceled")) or (isinstance(_sell_result, dict) and not _sell_result.get("id"))
                            if not _sell_failed:
                                # Verify with broker that position is actually gone (3 sec wait for fill)
                                import time as _t
                                _t.sleep(3)
                                try:
                                    _alp_pos = ibkr.get_positions() or []
                                    _still_open = any(str(_ap.get("symbol","")).upper() == symbol.upper() for _ap in _alp_pos)
                                    if _still_open:
                                        log.info(f"⏰ TIME EXIT {symbol}: order placed but position still at broker — checking again next scan")
                                        _trade_cooldown[symbol] = datetime.now()
                                        continue
                                except Exception as _ve:
                                    log.warning(f"TIME EXIT {symbol}: broker verify error ({_ve}) — proceeding cautiously")
                            if _sell_failed:
                                log.warning(f"⚠️ TIME EXIT {symbol}: sell order failed or rejected — NOT logging phantom close")
                                _trade_cooldown[symbol] = datetime.now()
                                continue
                            from trade_history import save_trade
                            save_trade({"product_id": symbol, "side": "BUY", "entry_price": entry,
                                "exit_price": current, "pnl_usd": round(pnl, 4),
                                "pnl_pct": round(pnl_pct, 2), "status": "closed_time_exit",
                                "market": "stocks", "confidence": pos.get("confidence", 0),
                                "opened_at": pos.get("opened_at"), "closed_at": datetime.now().isoformat()})
                            pt.record_stock_trade(round(pnl, 4), win=True)
                            from notifier import alert_trade_closed
                            alert_trade_closed(symbol, "BUY", entry, current, pnl, pnl_pct, "closed_time_exit", "stocks")
                            closed.append(symbol)
                            continue
                except Exception as _te:
                    log.debug(f"Time exit check error: {_te}")

                if current <= sl:
                    log.info(f"❌ STOCK SL HIT {symbol} @ ${current:.2f} PnL: ${pnl:.2f}")
                    # Verify sell actually executed before logging close
                    _sl_sell = ibkr.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
                    if not _sl_sell or (isinstance(_sl_sell, dict) and not _sl_sell.get("id")):
                        log.warning(f"⚠️ STOCK SL {symbol}: sell rejected/failed — NOT logging phantom close")
                        _trade_cooldown[symbol] = datetime.now()
                        continue
                    _trade_cooldown[symbol] = datetime.now()
                    try:
                        import json as _cj, os as _os
                        _cf = "cooldown_state.json"
                        _cd = _cj.load(open(_cf)) if _os.path.exists(_cf) else {}
                        _cd[symbol] = datetime.now().isoformat()
                        _cj.dump(_cd, open(_cf, "w"))
                    except: pass
                    from trade_history import save_trade
                    save_trade({"product_id": symbol, "side": "BUY", "entry_price": entry,
                                "exit_price": current, "pnl_usd": round(pnl, 4),
                                "pnl_pct": round(pnl_pct, 2), "status": "closed_sl",
                                "market": "stocks", "confidence": pos.get("confidence", 0),
                                "reasoning": pos.get("reasoning", ""),
                                "key_signals": pos.get("key_signals", []),
                                "indicators": pos.get("indicators", {}),
                                "opened_at": pos.get("opened_at"), "closed_at": datetime.now().isoformat()})
                    pt.record_stock_trade(round(pnl, 4), win=False)
                    from notifier import alert_trade_closed
                    alert_trade_closed(symbol, "BUY", entry, current, pnl, pnl_pct, "closed_sl", "stocks")
                    closed.append(symbol)
                elif current >= tp:
                    log.info(f"✅ STOCK TP HIT {symbol} @ ${current:.2f} PnL: ${pnl:.2f}")
                    # Verify sell actually executed before logging close
                    _tp_sell = ibkr.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
                    if not _tp_sell or (isinstance(_tp_sell, dict) and not _tp_sell.get("id")):
                        log.warning(f"⚠️ STOCK TP {symbol}: sell rejected/failed — NOT logging phantom close")
                        _trade_cooldown[symbol] = datetime.now()
                        continue
                    _trade_cooldown[symbol] = datetime.now()
                    try:
                        import json as _cj, os as _os
                        _cf = "cooldown_state.json"
                        _cd = _cj.load(open(_cf)) if _os.path.exists(_cf) else {}
                        _cd[symbol] = datetime.now().isoformat()
                        _cj.dump(_cd, open(_cf, "w"))
                    except: pass
                    from trade_history import save_trade
                    save_trade({"product_id": symbol, "side": "BUY", "entry_price": entry,
                                "exit_price": current, "pnl_usd": round(pnl, 4),
                                "pnl_pct": round(pnl_pct, 2), "status": "closed_tp",
                                "reasoning": pos.get("reasoning", ""),
                                "key_signals": pos.get("key_signals", []),
                                "indicators": pos.get("indicators", {}),
                                "market": "stocks", "confidence": pos.get("confidence", 0),
                                "opened_at": pos.get("opened_at"), "closed_at": datetime.now().isoformat()})
                    pt.record_stock_trade(round(pnl, 4), win=True)
                    from notifier import alert_trade_closed
                    alert_trade_closed(symbol, "BUY", entry, current, pnl, pnl_pct, "closed_tp", "stocks")
                    closed.append(symbol)
                    global _immediate_redeploy
                    _immediate_redeploy = True  # Trigger instant rescan
                    log.info(f"⚡ INSTANT REDEPLOY triggered — slot freed by {symbol} TP")
                else:
                    log.info(f"📈 STOCK {symbol} @ ${current:.2f} PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)")
                    pos['pnl_usd'] = round(pnl, 4)
                    pos['pnl_pct'] = round(pnl_pct, 2)
            except Exception as e:
                log.error(f"Stock monitor error {symbol}: {e}")
    except Exception as e:
        log.error(f"monitor_stock_positions error: {e}")

def get_option_price_yf(contract_symbol):
    """Get live option price via Tastytrade API."""
    try:
        from tastytrade_client import TastytradeClient
        tt = TastytradeClient()
        return tt.get_option_price(contract_symbol)
    except:
        return 0.0

def monitor_option_positions(options_client):
    """Update P&L on open options positions using Yahoo Finance."""
    try:
        import json
        with open("bot_state.json") as f:
            state = json.load(f)
        opts = [p for p in state.get("positions", []) if p.get("market") == "options"]
        if not opts:
            return
        updated = False
        # Sync exact P&L from Tastytrade balances
        try:
            if options_client and options_client.tt:
                bal_data = options_client.tt._request("GET", f"/accounts/{options_client.tt.account_number}/balances")
                bal = bal_data.get("data", {})
                long_deriv = float(bal.get("long-derivative-value", 0) or 0)
                total_cost = sum(float(p.get("entry_price", 0)) * 100 for p in opts)
                if long_deriv > 0 and total_cost > 0:
                    total_pnl_from_tt = round(long_deriv - total_cost, 2)
                    # Distribute P&L proportionally across positions
                    for p in state["positions"]:
                        if p.get("market") == "options":
                            weight = (float(p.get("entry_price", 0)) * 100) / total_cost if total_cost else 0
                            p["pnl_usd"] = round(total_pnl_from_tt * weight, 2)
                    updated = True
        except Exception as _e:
            pass
        for p in state["positions"]:
            if p.get("market") != "options":
                continue
            try:
                current = options_client.get_option_price(p["product_id"]) if options_client else get_option_price_yf(p["product_id"])
                if current > 0:
                    pnl = (current - p["entry_price"]) * 100
                    pnl_pct = (current - p["entry_price"]) / p["entry_price"] * 100
                    p["pnl_usd"] = round(pnl, 2)
                    p["current_price"] = current
                    updated = True
                    log.info(f"OPTIONS {p['product_id']} @ ${current:.2f} PnL: ${pnl:+.2f} ({pnl_pct:+.1f}%)")
                    # Smart TP: 7-14 days = 50% TP, 15+ days = 100% TP
                    try:
                        from notifier import alert_trade_closed
                        from datetime import datetime, date
                        import re as _re

                        # Parse days to expiry from symbol e.g. GOOGL 260427C00347500
                        days_to_expiry = 999
                        try:
                            m = _re.search(r'(\d{2})(\d{2})(\d{2})[CP]', p["product_id"].replace(" ",""))
                            if m:
                                exp = date(2000+int(m.group(1)), int(m.group(2)), int(m.group(3)))
                                days_to_expiry = (exp - date.today()).days
                        except:
                            pass

                        # Set TP threshold based on days to expiry
                        tp_threshold = 35 if days_to_expiry <= 30 else 50
                        log.info(f"OPTIONS {p['product_id']} DTE={days_to_expiry} TP={tp_threshold}%")

                        def _close_option(reason):
                            # Cooldown: don't retry close for 5 min after failed attempt
                            from datetime import datetime as _dt, timedelta as _td
                            
                            # Hard block: after 3 consecutive failures, block for 1 hour and alert
                            blocked_until = p.get("close_blocked_until")
                            if blocked_until:
                                try:
                                    bu_dt = _dt.fromisoformat(blocked_until.replace("Z",""))
                                    if _dt.now() < bu_dt:
                                        log.info(f"OPTIONS {p['product_id']}: close BLOCKED until {blocked_until[:19]} (3 consecutive fails)")
                                        return
                                    else:
                                        # Block expired, reset
                                        p["close_blocked_until"] = None
                                        p["close_failures"] = 0
                                except:
                                    pass
                            
                            # Soft cooldown: 5 min between attempts
                            last_attempt = p.get("last_close_attempt")
                            if last_attempt:
                                try:
                                    last_dt = _dt.fromisoformat(last_attempt.replace("Z",""))
                                    elapsed = (_dt.now() - last_dt).total_seconds()
                                    if elapsed < 300:
                                        log.info(f"OPTIONS {p['product_id']}: close cooldown active ({300-elapsed:.0f}s remaining)")
                                        return
                                except:
                                    pass
                            p["last_close_attempt"] = _dt.now().isoformat()
                            
                            def _record_failure(reason_text):
                                p["close_failures"] = p.get("close_failures", 0) + 1
                                log.warning(f"⚠️ OPTIONS CLOSE {p['product_id']}: {reason_text} (failure {p['close_failures']}/3)")
                                record_safety_failure(f"options close failed: {p['product_id']} - {reason_text}")
                                if p["close_failures"] >= 3:
                                    p["close_blocked_until"] = (_dt.now() + _td(hours=1)).isoformat()
                                    log.error(f"🛑 OPTIONS {p['product_id']}: 3 consecutive close failures — BLOCKING for 1 hour")
                                    try:
                                        from notifier import _send
                                        _send(f"🛑 <b>OPTIONS CLOSE BLOCKED — {p['product_id']}</b>\n3 consecutive close failures. Bot has STOPPED trying for 1 hour.\nReason: {reason_text}\nPnL: {pnl_pct:.1f}%\nManual action may be needed.")
                                    except:
                                        pass
                            
                            # HARD GUARD: verify Tastytrade position BEFORE selling
                            sell_result = None
                            try:
                                _broker_positions = options_client.tt.get_positions() if options_client and options_client.tt else []

                                _broker_pos = next(
                                    (
                                        bp for bp in (_broker_positions or [])
                                        if str(bp.get("symbol", "")).strip() == str(p["product_id"]).strip()
                                    ),
                                    None
                                )

                                if not _broker_pos:
                                    log.error(f"🧹 PHANTOM OPTION REMOVED: {p['product_id']} not found at Tastytrade — sell blocked")
                                    state["positions"] = [
                                        x for x in state["positions"]
                                        if x.get("product_id") != p["product_id"]
                                    ]
                                    with open("bot_state.json", "w") as _f:
                                        import json as _j
                                        _j.dump(_safe_json(state), _f, indent=2)
                                    return

                                actual_qty = int(float(_broker_pos.get("quantity", 0) or 0))
                                bot_qty = int(float(p.get("quantity", 1) or 1))
                                qty_to_sell = min(bot_qty, actual_qty)

                                if qty_to_sell <= 0:
                                    _record_failure("blocked: broker quantity is zero")
                                    return

                                sell_result = options_client.place_option_order(
                                    p["product_id"],
                                    qty=qty_to_sell,
                                    side="sell"
                                )

                            except Exception as ce:
                                log.error(f"Options close order failed: {ce}")
                                _record_failure(f"exception: {ce}")
                                return
                            
                            # Check if sell ACTUALLY FILLED
                            if not sell_result or not isinstance(sell_result, dict):
                                _record_failure("sell returned no result")
                                return
                            if not sell_result.get("success"):
                                _record_failure(f"sell rejected: {sell_result.get('error','unknown')}")
                                return
                            if sell_result.get("filled") is False:
                                _record_failure("order placed but NOT filled within timeout")
                                return
                            
                            # VERIFY position is actually gone from broker before logging close
                            try:
                                _broker_positions = options_client.tt.get_positions() if options_client.tt else []
                                _still_open = any(
                                    str(_bp.get("symbol","")).strip() == str(p["product_id"]).strip()
                                    for _bp in (_broker_positions or [])
                                )
                                if _still_open:
                                    _record_failure("broker still shows position open after sell — fill not confirmed")
                                    return
                            except Exception as _ve:
                                log.warning(f"OPTIONS CLOSE {p['product_id']}: broker verification skipped ({_ve}) — proceeding cautiously")
                            
                            # Sell verified gone from broker - log it properly
                            p["close_failures"] = 0
                            clear_safety_failures()
                            p["status"] = reason
                            from trade_history import save_trade
                            save_trade({**p, "exit_price": current, "pnl_usd": pnl, "pnl_pct": pnl_pct, 
                                        "market": "options", "closed_at": _dt.now().isoformat()})
                            state["positions"] = [x for x in state["positions"] if x.get("product_id") != p["product_id"]]
                            with open("bot_state.json", "w") as _f:
                                import json as _j
                                _j.dump(state, _f, indent=2, default=str)
                            from notifier import alert_option_closed
                            alert_option_closed(p.get("underlying", p["product_id"]), p["product_id"], pnl, reason)
                            log.info(f"OPTIONS AUTO-CLOSED (verified): {p['product_id']} {pnl_pct:.1f}% ({reason})")

                        if pnl_pct >= 20 and not p.get("alerted_tp"):
                            log.info(f"OPTIONS TP HIT {p['product_id']}: +{pnl_pct:.1f}%")

                            # Partial profit system:
                            # If holding 2+ contracts, sell 1 at +20%, keep the rest for +35%.
                            # If holding only 1 contract, close fully at +20%.
                            if p.get("quantity", 1) >= 2 and not p.get("partial_tp_done"):
                                try:
                                    _broker_positions = options_client.tt.get_positions() if options_client and options_client.tt else []
                                    _broker_pos = next(
                                        (
                                            bp for bp in (_broker_positions or [])
                                            if str(bp.get("symbol", "")).strip() == str(p["product_id"]).strip()
                                        ),
                                        None
                                    )

                                    if not _broker_pos:
                                        log.error(f"PARTIAL TP BLOCKED: {p['product_id']} not found at Tastytrade")
                                        _close_option("closed_tp")
                                    else:
                                        sell_one = options_client.place_option_order(
                                            p["product_id"],
                                            qty=1,
                                            side="sell"
                                        )

                                        if sell_one and sell_one.get("success") and sell_one.get("filled") is not False:
                                            p["quantity"] = int(p.get("quantity", 2)) - 1
                                            p["partial_tp_done"] = True
                                            p["alerted_tp"] = True
                                            p["next_tp_pct"] = 35

                                            with open("bot_state.json", "w") as _f:
                                                import json as _j
                                                _j.dump(_safe_json(state), _f, indent=2)

                                            log.info(f"💰 PARTIAL TP: sold 1 {p['product_id']} at +{pnl_pct:.1f}%, keeping {p['quantity']} runner")
                                            try:
                                                from notifier import _send
                                                _send(f"💰 <b>PARTIAL OPTIONS TP</b>\n{p['product_id']}\nSold 1 contract at +{pnl_pct:.1f}%\nRunner target: +35%")
                                            except Exception:
                                                pass
                                        else:
                                            log.warning(f"PARTIAL TP failed/not filled for {p['product_id']} — using guarded full close")
                                            _close_option("closed_tp")

                                except Exception as le:
                                    log.error(f"Partial TP error: {le}")
                                    _close_option("closed_tp")

                            else:
                                alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_tp", "options")
                                _close_option("closed_tp")

                        elif p.get("partial_tp_done") and pnl_pct >= 35:
                            log.info(f"OPTIONS RUNNER TP HIT {p['product_id']}: +{pnl_pct:.1f}%")
                            alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_runner_tp", "options")
                            _close_option("closed_runner_tp")
                        elif pnl_pct <= -50 and not p.get("alerted_sl"):
                            alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_sl_alert", "options")
                            p["alerted_sl"] = True
                        if pnl_pct <= -20 and p.get("status") != "closed_sl":
                            log.warning(f"OPTIONS AUTO-CLOSE {p['product_id']}: down {pnl_pct:.1f}% — cutting loss")
                            _close_option("closed_sl")
                    except Exception as ne:
                        log.error(f"Options notify error: {ne}")
                else:
                    log.info(f"OPTIONS {p['product_id']} price unavailable")
            except Exception as e:
                log.error(f"Options monitor error {p['product_id']}: {e}")
        if updated:
            with open("bot_state.json", "w") as f:
                json.dump(_safe_json(state), f, indent=2)
    except Exception as e:
        log.error(f"monitor_option_positions error: {e}")

def elite_option_score(symbol, signal):
    try:
        indicators = signal.get("indicators", {}) or {}

        conf = float(signal.get("confidence", 0) or 0)
        rsi = float(indicators.get("rsi_14", 50) or 50)
        volume_ratio = float(indicators.get("volume_ratio", 0) or 0)
        price_vs_ema20 = float(indicators.get("price_vs_ema20", 0) or 0)
        price_vs_vwap = float(indicators.get("price_vs_vwap", 0) or 0)
        macd = indicators.get("macd", {}) or {}
        macd_hist = float(macd.get("histogram", 0) or 0)
        ema_alignment = str(indicators.get("ema_alignment", "")).lower()

        score = 0
        reasons = []

        if conf >= 0.85:
            score += 25
            reasons.append("confidence")
        if 50 <= rsi <= 65:
            score += 15
            reasons.append("rsi_sweet_spot")
        elif 45 <= rsi < 50 or 65 < rsi <= 68:
            score += 8
            reasons.append("rsi_acceptable")
        if volume_ratio >= 1.0:
            score += 20
            reasons.append("volume_confirmed")
        elif volume_ratio >= 0.7:
            score += 10
            reasons.append("volume_ok")
        if price_vs_ema20 >= 1.0:
            score += 15
            reasons.append("above_ema20")
        if price_vs_vwap >= 0.75:
            score += 15
            reasons.append("above_vwap")
        if macd_hist > 0:
            score += 10
            reasons.append("macd_positive")
        if ema_alignment == "bullish":
            score += 10
            reasons.append("ema_bullish")

        log.info(f"Options ELITE SCORE {symbol}: {score}/110 | {', '.join(reasons)}")
        return score, reasons

    except Exception as e:
        log.warning(f"Options ELITE SCORE {symbol}: error {e}")
        return 0, []

def pro_option_setup_ok(symbol, signal, tastytrade_balance=0):
    """
    Pro options filter:
    only allow options on high-confidence, liquid, trending, momentum-backed setups.
    """
    try:
        conf = float(signal.get("confidence", 0) or 0)
        entry = float(signal.get("entry_price", 0) or 0)
        indicators = signal.get("indicators", {}) or {}

        rsi = float(indicators.get("rsi_14", 50) or 50)
        volume_ratio = float(indicators.get("volume_ratio", 0) or 0)
        price_vs_ema20 = float(indicators.get("price_vs_ema20", 0) or 0)
        price_vs_vwap = float(indicators.get("price_vs_vwap", 0) or 0)
        ema_alignment = str(indicators.get("ema_alignment", "")).lower()

        macd = indicators.get("macd", {}) or {}
        macd_hist = float(macd.get("histogram", 0) or 0)

        if tastytrade_balance and tastytrade_balance < 500:
            log.warning(f"Options SKIP {symbol}: Tastytrade balance under $500 protection floor")
            return False

        if conf < 0.85:
            log.info(f"Options SKIP {symbol}: confidence {conf:.0%} < 85%")
            return False

        if entry <= 0:
            log.info(f"Options SKIP {symbol}: invalid entry price")
            return False

        if volume_ratio < 0.70:
            log.info(f"Options SKIP {symbol}: low stock volume {volume_ratio:.2f}x")
            return False

        if not (45 <= rsi <= 68):
            log.info(f"Options SKIP {symbol}: RSI {rsi:.1f} outside 45-68")
            return False

        if ema_alignment != "bullish":
            log.info(f"Options SKIP {symbol}: EMA alignment not bullish ({ema_alignment})")
            return False

        if macd_hist <= 0:
            log.info(f"Options SKIP {symbol}: MACD histogram not positive ({macd_hist:.4f})")
            return False

        if price_vs_ema20 < 0.75:
            log.info(f"Options SKIP {symbol}: price not strong enough vs EMA20 ({price_vs_ema20:+.2f}%)")
            return False

        if price_vs_vwap < 0.50:
            log.info(f"Options SKIP {symbol}: price not strong enough vs VWAP ({price_vs_vwap:+.2f}%)")
            return False

        return True

    except Exception as e:
        log.warning(f"Options SKIP {symbol}: pro filter error {e}")
        return False




def daily_risk_shutdown_active():
    try:
        import trade_history as _th
        summary = _th.get_daily_summary()
        pnl = float(summary.get("total_pnl", 0) or 0)

        if pnl <= -100:
            log.critical(f"🚫 DAILY RISK SHUTDOWN: realized PnL ${pnl:.2f} <= -$100")
            return True

        return False
    except Exception as e:
        log.warning(f"Daily risk shutdown check failed: {e}")
        return False


def run_options_scan(options_client, options_analyzer, stock_signals, pt):
    if not stock_signals:
        return []
    # Daily loss limit also blocks options new entries (added 5/4)
    try:
        import requests as _r, os as _o
        _h = {"APCA-API-KEY-ID": _o.getenv("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": _o.getenv("ALPACA_SECRET_KEY")}
        _resp = _r.get("https://api.alpaca.markets/v2/account/portfolio/history?period=1D&timeframe=1H", headers=_h, timeout=10)
        _eq = [e for e in _resp.json().get("equity", []) if e]
        if len(_eq) >= 2:
            _pct = ((_eq[-1] - _eq[0]) / _eq[0]) * 100
            if _pct <= -config.MAX_DAILY_LOSS_PCT:
                log.info(f"── Options scan — SKIPPING NEW BUYS (daily loss {_pct:.2f}% <= -{config.MAX_DAILY_LOSS_PCT}%)")
                return []
    except Exception as _de:
        log.debug(f"Options daily loss check error: {_de}")
    log.info(f"── Options scan — {len(stock_signals)} stock signals")

    if daily_risk_shutdown_active():
        log.warning("DAILY RISK SHUTDOWN active — skipping options")
        return []
    positions = []
    try:
        with open("bot_state.json") as _sf:
            _state_for_risk = json.load(_sf)
        _open_stocks = [p for p in _state_for_risk.get("positions", []) if p.get("market") == "stocks"]
        if len(_open_stocks) >= config.MAX_OPEN_POSITIONS:
            log.info(f"Options scan — stock exposure maxed ({len(_open_stocks)}), allowing LIMITED options trade")
    except Exception:
        pass
    # Only trade options on BUY signals with 72%+ confidence
    # Options require higher conviction than stocks because losses move faster
    options_min_conf = max(config.MIN_CONFIDENCE, 0.85)
    # Cheap-underlying filter (added 5/21): only consider symbols whose ATM options fit our small TT budget
    _cheap_universe = set(s.strip().upper() for s in getattr(config, "CHEAP_OPTIONS_UNIVERSE", []))
    candidates = sorted(
        [s for s in stock_signals 
         if s["action"] == "BUY" 
         and s["confidence"] >= options_min_conf
         and s["product_id"].upper() in _cheap_universe],
        key=lambda x: x["confidence"],
        reverse=True
    )[:2]
    if not candidates:
        log.info(f"Options scan — no BUY signals in cheap universe ({len(_cheap_universe)} symbols)")
    for signal in candidates:
        if not option_trade_allowed_today():
            log.info("Options SKIP: daily limit reached")
            break

        if not good_options_time():
            log.info("Options SKIP: bad time window")
            break

        try:
            symbol = signal["product_id"]

            option_regime = get_market_regime_from_signal(signal)
            if option_regime == "CHOP":
                log.info(f"Options SKIP {symbol}: market regime CHOP")
                continue

            # Momentum filter
            try:
                entry = float(signal.get("entry_price") or 0)
                current = float(signal.get("current_price") or entry)
                if entry > 0:
                    move_pct = (current - entry) / entry
                    if abs(move_pct) < 0.01:  # require at least 1% move for real momentum
                        log.info(f"Options SKIP {symbol}: no momentum ({move_pct:.3%})")
                        continue

                    vol_ratio = float(signal.get("indicators", {}).get("volume_ratio", 0) or 0)
                    if vol_ratio < 0.7:
                        log.info(f"Options SKIP {symbol}: low volume ({vol_ratio:.2f}x)")
                        continue
            except Exception:
                pass

            if not valid_trade_price(symbol, signal.get("entry_price")):
                continue

            if symbol in existing_opts:
                log.info(f"Options {symbol}: already have position — skipping")
                continue

            price = signal["entry_price"]
            opt_signal = options_analyzer.analyze(symbol, signal, price)

            if not opt_signal or opt_signal["strategy"] == "pass":
                continue
            ok, reason = risk_manager.check_all(symbol + "_OPT", opt_signal["confidence"], account_balance)
            if not ok:
                log.info(f"[OPT BLOCKED] {symbol}: {reason}")
                continue
            if opt_signal["confidence"] < config.MIN_CONFIDENCE:
                continue
            direction = "call" if "call" in opt_signal["strategy"] else "put"
            # Position sizing: max 30% of TOTAL options account per contract (not 80% of BP)
            try:
                from tastytrade_client import TastytradeClient as _TT
                _tt = _TT()
                _bp = _tt.get_buying_power()
                _total = _tt.get_account_balance()
                # Cap at config.OPTIONS_BUDGET_PCT_OF_TT of total account, leave $50 BP buffer, hard ceiling config.OPTIONS_BUDGET_HARD_CAP
                per_contract_max = int(_total * min(config.OPTIONS_BUDGET_PCT_OF_TT, 0.10))
                budget = min(per_contract_max, max(0, int(_bp) - 50), config.OPTIONS_BUDGET_HARD_CAP) if _bp > 50 else 0
            except:
                budget = 250 if config.IS_LIVE else 100
                _bp = budget
                _total = budget
            log.info(f"Options budget: ${budget:.0f} | total=${_total:.0f} | BP=${_bp:.0f} | max_per_contract=30%")
            # Earnings calendar: use weekly options if earnings within 7 days
            expiry_days, expiry_reason = get_options_expiry_days(symbol)
            if has_earnings_soon(symbol):
                log.info(f"Options {symbol}: earnings play — using weekly expiry ({expiry_reason})")
                budget = min(budget, config.EARNINGS_PLAY_BUDGET_CAP)  # earnings plays budget
            contract = options_client.find_best_option(symbol, direction, budget=budget)
            if not contract:
                log.info(f"Options {symbol}: No suitable contract found")
                continue
            contract_sym = contract.get("symbol")
            fill = options_client.place_option_order(contract_sym, qty=1, side="buy")
            if fill and fill.get("success"):
                record_option_trade()
            if fill and fill.get("success") and fill.get("filled") is not False:
                # Verify Tastytrade actually shows the option before saving it to bot_state
                try:
                    import time as _t
                    _t.sleep(2)
                    _broker_positions = options_client.tt.get_positions() if options_client and options_client.tt else []
                    _exists = any(str(bp.get("symbol", "")).strip() == str(contract_sym).strip() for bp in (_broker_positions or []))
                    if not _exists:
                        log.warning(f"OPTIONS ENTRY NOT SAVED: {contract_sym} not found at Tastytrade after buy")
                        record_safety_failure(f"options buy not confirmed at broker: {contract_sym}")
                        continue
                    clear_safety_failures()
                except Exception as _ve:
                    log.warning(f"OPTIONS ENTRY VERIFY ERROR {contract_sym}: {_ve} — not saving")
                    record_safety_failure(f"options buy verify error: {contract_sym}")
                    continue

                cost = float(contract.get("close_price", 0) or 0) * 100
                log.info(f"OPTIONS {opt_signal['strategy'].upper()} {contract_sym} cost=${cost:.2f}")
                try:
                    from notifier import alert_option_bought
                    alert_option_bought(symbol, contract_sym, contract.get("strike_price",""), contract.get("expiration_date",""), cost, contract.get("delta",0))
                except: pass
                positions.append({
                    "product_id":  contract_sym,
                    "underlying":  symbol,
                    "strategy":    opt_signal["strategy"],
                    "side":        "BUY",
                    "entry_price": float(contract.get("close_price", 0) or 0),
                    "quantity":    1,
                    "usd_value":   cost,
                    "stop_loss":   0,
                    "take_profit": 0,
                    "confidence":  opt_signal["confidence"],
                    "reasoning":   opt_signal.get("reasoning", ""),
                    "opened_at":   fill.get("timestamp", datetime.now().isoformat()),
                    "market":      "options",
                    "pnl_usd":     0.0,
                })
        except Exception as e:
            log.error(f"Options scan error {signal.get('product_id')}: {e}")
    return positions

def run_scan(cb, ibkr, analyzer, stock_analyzer, tm, pt, options_client=None, options_analyzer=None):
    log.info(f"\nScan @ {datetime.now().strftime('%H:%M:%S')}")
    # Sync Alpaca positions every scan — keeps dashboard always up to date
    try:
        import json as _j
        with open("bot_state.json") as _f:
            _state = _j.load(_f)
        _alpaca_positions = ibkr.get_positions()
        _alpaca_syms = {p["symbol"] for p in _alpaca_positions}
        _bot_syms = {p["product_id"] for p in _state["positions"] if p.get("market") == "stocks"}
        _added = 0
        for _p in _alpaca_positions:
            if _p["symbol"] not in _bot_syms:
                import config as _cfg
                import requests as _req, os as _os
                _entry = float(_p["avg_entry_price"])
                # Get real fill time from Alpaca orders
                _opened_at = __import__("datetime").datetime.now().isoformat()
                try:
                    _hdrs = {"APCA-API-KEY-ID": _os.getenv("ALPACA_API_KEY"), "APCA-API-SECRET-KEY": _os.getenv("ALPACA_SECRET_KEY")}
                    _orders = _req.get("https://api.alpaca.markets/v2/orders?status=filled&limit=20", headers=_hdrs).json()
                    for _o in _orders:
                        if _o.get("symbol") == _p["symbol"] and _o.get("side") == "buy" and _o.get("filled_at"):
                            _opened_at = _o["filled_at"]
                            break
                except:
                    pass
                _state["positions"].append({
                    "product_id": _p["symbol"],
                    "market": "stocks",
                    "side": "BUY",
                    "entry_price": _entry,
                    "quantity": float(_p["qty"]),
                    "usd_value": round(_entry * float(_p["qty"]), 2),
                    "stop_loss": round(_entry * (1 - _cfg.get_sl_pct(_p["symbol"])), 4),
                    "take_profit": round(_entry * (1 + _cfg.get_tp_pct(_p["symbol"])), 4),
                    "confidence": 0.72,
                    "reasoning": "Auto-synced from Alpaca",
                    "opened_at": _opened_at,
                    "pnl_usd": 0.0,
                })
                _added += 1
                log.info(f"🔄 Auto-synced {_p['symbol']} from Alpaca (opened {_opened_at})")
        # Remove positions no longer in Alpaca
        _state["positions"] = [p for p in _state["positions"] 
                               if p.get("market") != "stocks" or p.get("product_id") in _alpaca_syms]
        with open("bot_state.json", "w") as _f:
            _j.dump(_state, _f, indent=2)
    except Exception as _e:
        log.debug(f"Auto-sync error: {_e}")
    tm.monitor_positions()
    monitor_stock_positions(ibkr, pt)
    monitor_option_positions(options_client)
    # # run_earnings_options_scan(options_client, ibkr)  # disabled due to earnings API rate limit
    stock_signals, stock_pos   = run_stock_scan(ibkr, stock_analyzer, pt)
    options_pos = run_options_scan(options_client, options_analyzer, stock_signals, pt) if options_client else []
    # Crypto disabled - focusing on stocks + options only

    all_signals = stock_signals
    if all_signals:
        rows = [
            [s.get("product_id","")[:20], s.get("market","").upper(),
             s["action"], f"{s['confidence']:.0%}", f"${float(s['entry_price'] or 0):,.4f}"]
            for s in all_signals
        ]
        print("\n" + tabulate(rows, headers=["Symbol","Market","Action","Conf","Entry"], tablefmt="rounded_outline") + "\n")
        save_state(stock_signals, stock_pos + options_pos, pt)

def sync_tastytrade_positions(options_client, pt):
    """Sync open Tastytrade options positions into bot_state.json on startup.
    Removes phantom options (in bot_state but not at broker) AND adds missing ones."""
    try:
        if not options_client or not options_client.tt:
            return
        tt_positions = options_client.tt.get_positions() or []
        with open("bot_state.json") as f:
            state = json.load(f)
        # PHANTOM REMOVAL: drop options in bot_state that aren't at TT
        broker_symbols = {str(bp.get("symbol","")).strip() for bp in tt_positions}
        before_opt = len([p for p in state.get("positions",[]) if p.get("market") == "options"])
        removed_phantoms = [p for p in state.get("positions",[]) 
                            if p.get("market") == "options" 
                            and str(p.get("product_id","")).strip() not in broker_symbols]
        for ph in removed_phantoms:
            log.warning(f"🧹 PHANTOM REMOVED on startup: {ph.get('product_id')} (was in bot_state, not at TT)")
        state["positions"] = [p for p in state.get("positions",[]) 
                              if p.get("market") != "options" 
                              or str(p.get("product_id","")).strip() in broker_symbols]
        if not tt_positions:
            # Save phantom removals even if no TT positions to add
            if removed_phantoms:
                with open("bot_state.json", "w") as f:
                    json.dump(_safe_json(state), f, indent=2)
                log.info(f"Tastytrade sync: 0 added, {len(removed_phantoms)} phantoms removed")
            return
        existing_ids = [p.get("product_id") for p in state.get("positions", [])]
        added = 0
        for p in tt_positions:
            symbol = p.get("symbol", "")
            if symbol in existing_ids:
                continue
            qty = float(p.get("quantity", 1))
            price = float(p.get("average-open-price") or p.get("close-price") or 0)
            cost = price * 100 * qty
            state["positions"].append({
                "product_id":  symbol,
                "underlying":  symbol[:4].strip(),
                "strategy":    "buy_call",
                "side":        "BUY",
                "entry_price": price,
                "quantity":    int(qty),
                "usd_value":   cost,
                "stop_loss":   0,
                "take_profit": 0,
                "confidence":  0.72,
                "reasoning":   "Synced from Tastytrade on startup",
                "opened_at":   datetime.now().isoformat(),
                "market":      "options",
                "pnl_usd":     0.0,
            })
            added += 1
            log.info(f"✅ Synced Tastytrade position: {symbol}")
        if added > 0:
            with open("bot_state.json", "w") as f:
                json.dump(_safe_json(state), f, indent=2)
            log.info(f"Tastytrade sync: {added} positions added")
    except Exception as e:
        log.error(f"sync_tastytrade_positions: {e}")

def run_premarket_scan(ibkr, analyzer, stock_analyzer):
    """
    Pre-market scanner: runs 8:00-9:25 AM ET.
    Scans for gap ups, volume spikes, news catalysts.
    Builds priority watchlist for market open.
    """
    from datetime import datetime
    import pytz
    EASTERN = pytz.timezone("US/Eastern")
    now = datetime.now(EASTERN)

    # Only run during pre-market window
    pre_start = now.replace(hour=8,  minute=0,  second=0, microsecond=0)
    pre_end   = now.replace(hour=9,  minute=25, second=0, microsecond=0)
    if not (pre_start <= now <= pre_end):
        return []

    log.info("🌅 PRE-MARKET SCAN starting...")
    watchlist = []

    from alpaca_client import AlpacaClient
    ac = AlpacaClient()
    for symbol in config.STOCK_SYMBOLS:
        try:
            hist = ac.get_bars(symbol, timeframe="1Day", limit=5)
            if len(hist) < 2:
                continue

            prev_close  = float(hist[-2]["close"])
            prev_volume = float(hist[-2]["volume"])
            avg_volume  = sum(float(b["volume"]) for b in hist[-5:]) / min(5, len(hist))

            # Get latest price
            pre_price = ac.get_latest_price(symbol) or prev_close

            gap_pct      = (pre_price - prev_close) / prev_close * 100
            volume_ratio = prev_volume / avg_volume if avg_volume > 0 else 1.0

            # Score the symbol
            score = 0
            reasons = []

            if gap_pct >= 2.0:
                score += 3
                reasons.append(f"gap up {gap_pct:.1f}%")
            elif gap_pct >= 1.0:
                score += 1
                reasons.append(f"gap up {gap_pct:.1f}%")
            elif gap_pct <= -2.0:
                score -= 2
                reasons.append(f"gap down {gap_pct:.1f}%")

            if volume_ratio >= 2.0:
                score += 2
                reasons.append(f"volume {volume_ratio:.1f}x avg")
            elif volume_ratio >= 1.5:
                score += 1
                reasons.append(f"volume {volume_ratio:.1f}x avg")

            # News sentiment boost
            try:
                sentiment = news_sentiment.get_sentiment(symbol)
                if sentiment and sentiment.get("safe") and sentiment.get("sentiment") == "positive":
                    score += 2
                    reasons.append("positive news")
                elif sentiment and not sentiment.get("safe"):
                    score -= 3
                    reasons.append("negative news")
            except:
                pass

            if score >= 2:
                watchlist.append({
                    "symbol":       symbol,
                    "score":        score,
                    "gap_pct":      round(gap_pct, 2),
                    "volume_ratio": round(volume_ratio, 2),
                    "pre_price":    round(pre_price, 2),
                    "reasons":      reasons,
                })
                log.info(f"🌅 PRE-MARKET {symbol}: score={score} gap={gap_pct:+.1f}% vol={volume_ratio:.1f}x | {', '.join(reasons)}")

        except Exception as e:
            log.warning(f"Pre-market scan {symbol}: {e}")

    # Sort by score descending
    watchlist.sort(key=lambda x: x["score"], reverse=True)

    if watchlist:
        log.info(f"🌅 PRE-MARKET top picks: {[w['symbol'] for w in watchlist[:5]]}")
        # Save to file so main scan can use it
        with open("premarket_watchlist.json", "w") as f:
            import json
            json.dump(_safe_json(watchlist), f, indent=2)
        # Send Telegram alert with top picks
        try:
            from notifier import _send
            top = watchlist[:3]
            msg = "🌅 <b>Pre-Market Picks</b>\n"
            for w in top:
                msg += f"\n📊 <b>{w['symbol']}</b>: {w.get('gap_pct',0):+.1f}% gap | score={w['score']}\n"
                msg += f"   {', '.join(w.get('reasons', []))}\n"
            msg += f"\n⏰ Market opens in {int((pre_end - now).total_seconds() / 60)} min"
            _send(msg)
            log.info("📱 Pre-market picks sent to Telegram")
        except Exception as _pe:
            log.debug(f"Pre-market Telegram error: {_pe}")
    else:
        log.info("🌅 PRE-MARKET: no high-score symbols found")

    return watchlist

def run_earnings_options_scan(options_client, ibkr):
    """
    Scan for upcoming earnings 5-10 days out.
    Automatically buy 30-day calls on strong stocks before earnings.
    Only runs once per symbol per earnings cycle.
    """
    import json, os
    from datetime import date
    from earnings_calendar import days_to_earnings, get_earnings_date
    from options_client import OptionsClient

    if not options_client or not options_client.tt:
        return

    # Only place orders during market hours
    if not ibkr.is_market_open():
        return

    # Load already-placed earnings plays
    ep_file = "earnings_plays.json"
    played = {}
    if os.path.exists(ep_file):
        try:
            with open(ep_file) as f:
                played = json.load(f)
        except:
            played = {}

    # Check buying power first
    try:
        bp = options_client.tt.get_balance()
        if bp < 150:
            log.info(f"Earnings scan: insufficient buying power (${bp:.2f})")
            return
    except:
        return

    for symbol in config.STOCK_SYMBOLS:
        try:
            dte = cached_days_to_earnings(symbol)
            if dte is None or not (5 <= dte <= 10):
                continue

            # Already played this earnings cycle?
            ed = str(get_earnings_date(symbol))
            key = f"{symbol}_{ed}"
            if key in played:
                continue

            # Check open options count
            with open("bot_state.json") as f:
                state = json.load(f)
            existing_opts = [p for p in state.get("positions", []) if p.get("market") == "options"]
            if len(existing_opts) >= 4:
                log.info(f"Earnings scan {symbol}: max options reached")
                continue

            # Already have this symbol in options?
            if any(symbol in p.get("underlying","") for p in existing_opts):
                log.info(f"Earnings scan {symbol}: already have options position")
                continue

            log.info(f"🎯 EARNINGS PLAY: {symbol} reports in {dte} days — looking for call...")

            # Use 30-day expiry for earnings plays (capture full move)
            # Position sizing: max 30% of TOTAL options account per earnings play
            try:
                tt_total = options_client.tt.get_account_balance() if options_client.tt else 0
                tt_bp_avail = options_client.tt.get_buying_power() if options_client.tt else 0
                # Cap at config.OPTIONS_BUDGET_PCT_OF_TT of total, must have BP, hard ceiling config.EARNINGS_PLAY_BUDGET_CAP
                per_contract_max = int(tt_total * config.OPTIONS_BUDGET_PCT_OF_TT)
                ep_budget = min(per_contract_max, max(0, int(tt_bp_avail) - 50), config.EARNINGS_PLAY_BUDGET_CAP)
            except:
                ep_budget = 200
            log.info(f"Earnings play {symbol}: budget=${ep_budget} (30% of total account)")
            if ep_budget < 50:
                log.warning(f"Earnings play {symbol}: budget too low (${ep_budget}), skipping")
                continue
            contract = options_client.find_best_option(symbol, "call", budget=ep_budget)
            if not contract:
                log.info(f"Earnings play {symbol}: no suitable contract found")
                continue

            # Quality filter using real Greeks
            delta = float(contract.get("delta", 0))
            theta = float(contract.get("theta", 0))
            iv    = float(contract.get("iv", 0))

            # Delta must be 0.35-0.50 — not too deep, not too far OTM
            if not (0.35 <= delta <= 0.50):
                log.info(f"Earnings play {symbol}: delta {delta:.3f} outside 0.35-0.50 range — skip")
                continue

            # Theta decay must not exceed $35/day for earnings plays (short hold period)
            if theta < -0.35:
                log.info(f"Earnings play {symbol}: theta {theta:.3f} too negative (>${abs(theta)*100:.1f}/day decay) — skip")
                continue

            # IV crush risk — skip if IV > 120% (too expensive pre-earnings)
            if iv > 1.20:
                log.info(f"Earnings play {symbol}: IV {iv:.1%} too high — skip")
                continue

            log.info(f"Earnings play {symbol}: Greeks OK — delta={delta:.3f} theta={theta:.3f} iv={iv:.1%}")

            fill = options_client.place_option_order(contract.get("symbol",""), qty=1, side="buy")
            if fill and fill.get("success") and not fill.get("paper") and fill.get("order_id"):
                # Verify order actually went through by checking it's not a failed response
                if isinstance(fill.get("order_id",""), str) and len(fill.get("order_id","")) < 5:
                    log.warning(f"Earnings play {symbol}: order response suspicious — skipping save")
                    continue
            # Only log position if order actually filled — not just submitted
            if fill and fill.get("success") and fill.get("filled") == False:
                log.warning(f"Earnings play {symbol}: order pending, not logging as filled yet")
                continue
            if fill and fill.get("success"):
                cost = float(contract.get("close_price", 0)) * 100
                contract_sym = contract.get("symbol", "")
                try:
                    from notifier import alert_option_bought
                    alert_option_bought(symbol, contract_sym, contract.get("strike_price",""), contract.get("expiration_date",""), cost, contract.get("delta",0))
                except: pass

                # Save to positions
                state["positions"].append({
                    "product_id":  contract_sym,
                    "underlying":  symbol,
                    "strategy":    "earnings_call",
                    "side":        "BUY",
                    "entry_price": float(contract.get("close_price", 0)),
                    "quantity":    1,
                    "usd_value":   cost,
                    "stop_loss":   0,
                    "take_profit": 0,
                    "confidence":  0.75,
                    "reasoning":   f"Earnings play — {symbol} reports in {dte} days",
                    "opened_at":   datetime.now().isoformat(),
                    "market":      "options",
                    "pnl_usd":     0.0,
                    "earnings_play": True,
                })
                with open("bot_state.json", "w") as f:
                    json.dump(_safe_json(state), f, indent=2)

                # Mark as played
                played[key] = datetime.now().isoformat()
                with open(ep_file, "w") as f:
                    json.dump(_safe_json(played), f, indent=2)

                from notifier import alert_option_bought, _send
                alert_option_bought(symbol, contract_sym, contract.get("strike_price",""), contract.get("expiration_date",""), cost, contract.get("delta",0))
                _send(f"🎯 <b>EARNINGS PLAY — {symbol}</b>\nContract: {contract_sym}\nCost: ${cost:.2f} | Earnings in {dte} days\nDelta: {contract.get('delta',0):.3f} | Strategy: Buy call before earnings")
                log.info(f"🎯 EARNINGS PLAY placed: {symbol} {contract_sym} cost=${cost:.2f}")
            else:
                log.warning(f"Earnings play {symbol}: order failed")

        except Exception as e:
            log.error(f"Earnings scan {symbol}: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--paper", action="store_true")
    parser.add_argument("--once",  action="store_true")
    args = parser.parse_args()
    if args.paper: config.IS_LIVE = False
    try:
        config.validate_config()
    except EnvironmentError as e:
        print(str(e)); sys.exit(1)
    print_banner(config.IS_LIVE)
    # Live mode confirmed via TRADING_MODE=live in .env
    cb = None  # Crypto disabled
    ibkr            = AlpacaClient()
    _ibkr_fail_count = 0
    from options_client import OptionsClient
    from options_analyzer import OptionsAnalyzer
    options_client   = OptionsClient(alpaca_client=ibkr)
    options_analyzer = OptionsAnalyzer()

    pt             = PortfolioTracker()
    sync_tastytrade_positions(options_client, pt)

    # Sync IBKR positions on startup to prevent duplicates
    def sync_ibkr_positions():
        try:
            with open("bot_state.json") as f:
                state = json.load(f)
            ibkr_positions = ibkr.get_positions()
            ibkr_symbols = {p["symbol"] for p in ibkr_positions}
            # Remove any stock positions from bot_state not in IBKR
            before = len([p for p in state["positions"] if p.get("market") == "stocks"])
            state["positions"] = [
                p for p in state["positions"]
                if p.get("market") != "stocks" or p.get("product_id") in ibkr_symbols
            ]
            # Add any IBKR positions missing from bot_state
            bot_symbols = {p["product_id"] for p in state["positions"] if p.get("market") == "stocks"}
            for p in ibkr_positions:
                if p["symbol"] not in bot_symbols:
                    state["positions"].append({
                        "product_id": p["symbol"],
                        "side": "BUY",
                        "entry_price": float(p["avg_entry_price"]),
                        "quantity": float(p["qty"]),
                        "usd_value": round(float(p["avg_entry_price"]) * float(p["qty"]), 2),
                        "stop_loss": round(float(p["avg_entry_price"]) * (1 - config.get_sl_pct(p.get("symbol",""))), 2),
                        "take_profit": round(float(p["avg_entry_price"]) * (1 + config.get_tp_pct(p.get("symbol",""))), 2),
                        "confidence": 0.72,
                        "reasoning": "Synced from Alpaca on startup",
                        "opened_at": __import__("datetime").datetime.now().isoformat(),
                        "market": "stocks",
                        "pnl_usd": 0.0,
                    })
            after = len([p for p in state["positions"] if p.get("market") == "stocks"])
            with open("bot_state.json", "w") as f:
                json.dump(state, f, indent=2)
            log.info(f"✅ Alpaca sync: {before} → {after} stock positions ({len(ibkr_symbols)} in Alpaca)")
            # Alert on Telegram for any newly synced positions
            new_positions = [p for p in state["positions"] if p.get("market") == "stocks" and p.get("reasoning") == "Synced from Alpaca on startup"]
            for p in new_positions:
                sym = p["product_id"]
                entry = p["entry_price"]
                tp = p["take_profit"]
                sl = p["stop_loss"]
                from notifier import _send
                _send(f"🔄 SYNCED POSITION: {sym}\nEntry: ${entry:.2f}\nTP: ${tp:.2f} | SL: ${sl:.2f}\nSource: Alpaca startup sync")
                log.info(f"📱 Telegram alert sent for synced position: {sym}")
        except Exception as e:
            log.error(f"Alpaca startup sync error: {e}")

    sync_ibkr_positions()
    analyzer       = Analyzer()
    stock_analyzer = StockAnalyzer()
    tm             = TradeManager(cb, portfolio_tracker=pt)
    log.info(f"Stocks: {len(config.STOCK_SYMBOLS)} symbols | Mode: {config.TRADING_MODE.upper()} | Alpaca")
    log.info(f"Portfolio — Stocks:${pt.stock_balance:,.2f} | Total:${pt.stock_balance:,.2f}\n")
    while _running:
        try:
            # Pre-market scanner (8:00-9:25 AM)
            run_premarket_scan(ibkr, analyzer, stock_analyzer)
            run_scan(cb, ibkr, analyzer, stock_analyzer, tm, pt, options_client, options_analyzer)
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
        if args.once or not _running: break
        # Daily summary + weekly report — fire once at 4 PM using a flag
        try:
            from datetime import datetime
            import pytz
            now_et = datetime.now(pytz.timezone("US/Eastern"))
            _summary_key = f"summary_{now_et.strftime('%Y%m%d')}"
            if now_et.hour == 16 and now_et.minute == 0 and now_et.weekday() < 5 and _summary_key not in _sent_summaries:
                _sent_summaries.add(_summary_key)
                # Daily summary — once at exactly 4:00 PM weekdays only
                from notifier import alert_daily_summary
                from trade_history import get_daily_summary
                summary = get_daily_summary()
                acct = ibkr.get_account()
                total = float(acct.get("portfolio_value", 0))
                alert_daily_summary(
                    total, 0, total,
                    summary.get("total_pnl", 0),
                    summary.get("wins", 0),
                    summary.get("losses", 0)
                )
                log.info("📊 Daily summary sent")
                # Weekly report — only on Fridays
                if now_et.weekday() == 4:
                    from notifier import send_weekly_report
                    send_weekly_report()
                    log.info("📊 Weekly report sent")
        except Exception as _we:
            log.error(f"Daily/weekly report error: {_we}")
        log.info(f"Next scan in {config.SCAN_INTERVAL}s. Ctrl+C to stop.\n")
        for _ in range(config.SCAN_INTERVAL):
            if not _running: break
            global _immediate_redeploy
            if _immediate_redeploy:
                _immediate_redeploy = False
                log.info("⚡ INSTANT REDEPLOY — skipping wait, scanning now")
                break
            time.sleep(1)
    log.info(f"Final — Stocks:${pt.stock_balance:,.2f} | Total:${pt.stock_balance:,.2f}")

if __name__ == "__main__":
    main()

