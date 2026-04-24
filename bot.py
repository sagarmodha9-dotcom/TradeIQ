import sys
import os
import time
import json

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
from alpaca_client import AlpacaClient
from analyzer import Analyzer
from stock_analyzer import StockAnalyzer
from trade_manager import TradeManager
from portfolio_tracker import PortfolioTracker

_running = True

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
    log.info(f"── Stock scan — {len(config.STOCK_SYMBOLS)} symbols")
    # No time-based cooldown — daily loss limit is the safety net
    signals, positions = [], []
    for symbol in config.STOCK_SYMBOLS:
        try:
            bars   = ibkr.get_bars(symbol, timeframe="1Hour", limit=100)
            signal = stock_analyzer.analyze(symbol, bars)
            if signal:
                signals.append({
                    "product_id":  symbol, "action": signal["action"],
                    "confidence":  signal["confidence"], "entry_price": signal["entry_price"],
                    "stop_loss":   signal["stop_loss"], "take_profit": signal["take_profit"],
                    "risk_reward": signal.get("risk_reward", 2.0),
                    "reasoning":   signal.get("reasoning", ""), "market": "stocks",
                })
                # 2 minute cooldown after TP/SL — prevents buying right at peak
                _cooldown_active = False
                if symbol in _trade_cooldown:
                    elapsed = (datetime.now() - _trade_cooldown[symbol]).total_seconds()
                    if elapsed < 120:  # 2 minutes
                        _cooldown_active = True
                        log.info(f"{symbol}: cooldown active ({120-elapsed:.0f}s remaining)")
                    else:
                        del _trade_cooldown[symbol]  # cooldown expired
                if signal["action"] == "BUY" and signal["confidence"] >= config.MIN_CONFIDENCE and not _cooldown_active:
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
                    # Always size based on TOTAL account balance, capped at available cash
                    base_size = config.LIVE_ACCOUNT_BALANCE * config.MAX_POSITION_PCT
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
            # Trailing stop: once up 3%, move SL to breakeven; up 4.5%, trail at 1.5% below current
            try:
                current = ibkr.get_latest_price(symbol)
                if not current or current <= 0:
                    continue
                pnl = (current - entry) * float(pos["quantity"])
                pnl_pct = (current - entry) / entry * 100 if entry > 0 else 0
                if current >= entry * 1.045:
                    trail_sl = round(current * 0.985, 4)
                    if trail_sl > sl:
                        pos["stop_loss"] = trail_sl
                        sl = trail_sl
                        log.info(f"🔒 TRAIL SL {symbol} → ${trail_sl:.2f} (price=${current:.2f} +{pnl_pct:.1f}%)")
                elif current >= entry * 1.03:
                    breakeven_sl = round(entry * 1.001, 4)
                    if breakeven_sl > sl:
                        pos["stop_loss"] = breakeven_sl
                        sl = breakeven_sl
                        log.info(f"🔒 BREAKEVEN SL {symbol} → ${breakeven_sl:.2f} (price=${current:.2f} +{pnl_pct:.1f}%)")
                # Time-based exit — if open 4+ hours and up 3%+, take profit
                try:
                    opened_at = pos.get("opened_at")
                    if opened_at:
                        from datetime import datetime as _dt
                        opened_time = _dt.fromisoformat(opened_at.replace("Z",""))
                        hours_open = (_dt.now() - opened_time).total_seconds() / 3600
                        if hours_open >= 4 and pnl_pct >= 3.0:
                            log.info(f"⏰ TIME EXIT {symbol} @ ${current:.2f} — open {hours_open:.1f}hrs +{pnl_pct:.1f}% — taking profit")
                            ibkr.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
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
                    _trade_cooldown[symbol] = datetime.now()
                    try:
                        import json as _cj, os as _os
                        _cf = "cooldown_state.json"
                        _cd = _cj.load(open(_cf)) if _os.path.exists(_cf) else {}
                        _cd[symbol] = datetime.now().isoformat()
                        _cj.dump(_cd, open(_cf, "w"))
                    except: pass
                    ibkr.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
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
                    _trade_cooldown[symbol] = datetime.now()
                    try:
                        import json as _cj, os as _os
                        _cf = "cooldown_state.json"
                        _cd = _cj.load(open(_cf)) if _os.path.exists(_cf) else {}
                        _cd[symbol] = datetime.now().isoformat()
                        _cj.dump(_cd, open(_cf, "w"))
                    except: pass
                    ibkr.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
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
                else:
                    log.info(f"📈 STOCK {symbol} @ ${current:.2f} PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)")
                    pos["pnl_usd"] = round(pnl, 4)
                    pos["pnl_pct"] = round(pnl_pct, 2)
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
                        tp_threshold = 50 if days_to_expiry <= 5 else 100
                        log.info(f"OPTIONS {p['product_id']} DTE={days_to_expiry} TP={tp_threshold}%")

                        def _close_option(reason):
                            try:
                                options_client.place_option_order(p["product_id"], qty=1, side="sell")
                            except Exception as ce:
                                log.error(f"Options close order failed: {ce}")
                            p["status"] = reason
                            from trade_history import save_trade
                            save_trade({**p, "exit_price": current, "pnl_usd": pnl, "pnl_pct": pnl_pct, "market": "options"})
                            state["positions"] = [x for x in state["positions"] if x.get("product_id") != p["product_id"]]
                            with open("bot_state.json", "w") as _f:
                                import json as _j
                                _j.dump(state, _f, indent=2, default=str)
                            # Telegram alert for option closed
                            from notifier import alert_option_closed
                            alert_option_closed(p.get("underlying", p["product_id"]), p["product_id"], pnl, reason)
                            log.info(f"OPTIONS AUTO-CLOSED: {p['product_id']} {pnl_pct:.1f}% ({reason})")

                        if pnl_pct >= tp_threshold and not p.get("alerted_tp"):
                            log.info(f"OPTIONS TP HIT {p['product_id']}: +{pnl_pct:.1f}% (threshold={tp_threshold}%)")
                            # Profit ladder: sell half at TP, let rest ride free
                            if p.get("quantity", 1) >= 2 and not p.get("ladder_triggered"):
                                try:
                                    options_client.place_option_order(p["product_id"], qty=1, side="sell")
                                    p["ladder_triggered"] = True
                                    p["quantity"] = p.get("quantity", 2) - 1
                                    p["cost_basis_recovered"] = True
                                    new_tp = tp_threshold * 2  # let rest run to 2x
                                    log.info(f"💰 LADDER: sold half {p['product_id']} at +{pnl_pct:.1f}% — letting rest ride to +{new_tp:.0f}%")
                                    from notifier import _send
                                    _send(f"💰 <b>LADDER — {p['product_id']}</b>\nSold half at +{pnl_pct:.1f}% (${pnl/2:.2f} profit)\nLetting rest ride to +{new_tp:.0f}%\nCost basis recovered!")
                                except Exception as le:
                                    log.error(f"Ladder sell error: {le}")
                                    alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_tp", "options")
                                    _close_option("closed_tp")
                            else:
                                alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_tp", "options")
                                _close_option("closed_tp")
                        elif pnl_pct <= -50 and not p.get("alerted_sl"):
                            alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_sl_alert", "options")
                            p["alerted_sl"] = True
                        if pnl_pct <= -30 and p.get("status") != "closed_sl":
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

def run_options_scan(options_client, options_analyzer, stock_signals, pt):
    if not stock_signals:
        return []
    log.info(f"── Options scan — {len(stock_signals)} stock signals")
    positions = []
    # Only trade options on BUY signals with 72%+ confidence
    candidates = [s for s in stock_signals if s["action"] == "BUY" and s["confidence"] >= config.MIN_CONFIDENCE]
    account_balance = pt.stock_balance if pt else config.get_account_balance()
    if not candidates:
        log.info("No eligible stock signals for options")
        return []
    # Get existing options positions to avoid duplicates
    try:
        import json
        with open("bot_state.json") as f:
            existing_state = json.load(f)
        existing_opts = [p["underlying"] for p in existing_state.get("positions", []) if p.get("market") == "options"]
    except Exception:
        existing_opts = []

    for signal in candidates[:2]:  # Max 2 options trades per scan
        try:
            symbol = signal["product_id"]
            if symbol in existing_opts:
                log.info(f"Options {symbol}: already have position — skipping")
                continue
            price  = signal["entry_price"]
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
            budget = 1000 if config.IS_LIVE else 250
            # Earnings calendar: use weekly options if earnings within 7 days
            expiry_days, expiry_reason = get_options_expiry_days(symbol)
            if has_earnings_soon(symbol):
                log.info(f"Options {symbol}: earnings play — using weekly expiry ({expiry_reason})")
                budget = min(budget, 1000)  # earnings plays budget
            contract = options_client.find_best_option(symbol, direction, budget=budget)
            if not contract:
                log.info(f"Options {symbol}: No suitable contract found")
                continue
            contract_sym = contract.get("symbol")
            fill = options_client.place_option_order(contract_sym, qty=1, side="buy")
            if fill["success"]:
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
                _entry = float(_p["avg_entry_price"])
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
                    "opened_at": __import__("datetime").datetime.now().isoformat(),
                    "pnl_usd": 0.0,
                })
                _added += 1
                log.info(f"🔄 Auto-synced {_p['symbol']} from Alpaca")
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
    run_earnings_options_scan(options_client, ibkr)
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
    """Sync open Tastytrade options positions into bot_state.json on startup."""
    try:
        if not options_client or not options_client.tt:
            return
        tt_positions = options_client.tt.get_positions()
        if not tt_positions:
            return
        with open("bot_state.json") as f:
            state = json.load(f)
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
            msg = "🌅 <b>Pre-Market Picks</b>
"
            for w in top:
                msg += f"
📊 <b>{w['symbol']}</b>: {w.get('gap_pct',0):+.1f}% gap | score={w['score']}
"
                msg += f"   {', '.join(w.get('reasons', []))}
"
            msg += f"
⏰ Market opens in {int((pre_end - now).total_seconds() / 60)} min"
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
            dte = days_to_earnings(symbol)
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
            # Check available buying power before ordering
            try:
                tt_bp = options_client.tt.get_account_balance() if options_client.tt else 0
                available_bp = tt_bp * 0.85  # use max 85% of buying power
            except:
                available_bp = 500
            contract = options_client.find_best_option(symbol, "call", budget=min(1000, available_bp))
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

            # Theta decay must not exceed $15/day (theta is per share, x100)
            if theta < -0.15:
                log.info(f"Earnings play {symbol}: theta {theta:.3f} too negative (>${abs(theta)*100:.1f}/day decay) — skip")
                continue

            # IV crush risk — skip if IV > 120% (too expensive pre-earnings)
            if iv > 1.20:
                log.info(f"Earnings play {symbol}: IV {iv:.1%} too high — skip")
                continue

            log.info(f"Earnings play {symbol}: Greeks OK — delta={delta:.3f} theta={theta:.3f} iv={iv:.1%}")

            fill = options_client.place_option_order(contract.get("symbol",""), qty=1, side="buy")
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

                from notifier import send_email
                send_email(
                    f"TradeIQ 🎯 EARNINGS PLAY — {symbol} call bought",
                    f"Symbol:    {symbol}\n"
                    f"Contract:  {contract_sym}\n"
                    f"Cost:      ${cost:.2f}\n"
                    f"Earnings:  {dte} days away\n"
                    f"Strategy:  Buy call before earnings, close after announcement"
                )
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
                        "entry_price": p["avg_entry_price"],
                        "quantity": p["qty"],
                        "usd_value": round(p["avg_entry_price"] * p["qty"], 2),
                        "stop_loss": round(p["avg_entry_price"] * (1 - config.get_sl_pct(p.get("symbol",""))), 2),
                        "take_profit": round(p["avg_entry_price"] * (1 + config.get_tp_pct(p.get("symbol",""))), 2),
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
            if now_et.hour == 16 and now_et.minute == 0:
                # Daily summary — once at exactly 4:00 PM
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
            time.sleep(1)
    log.info(f"Final — Stocks:${pt.stock_balance:,.2f} | Total:${pt.stock_balance:,.2f}")

if __name__ == "__main__":
    main()

