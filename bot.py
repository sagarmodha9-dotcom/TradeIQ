import sys
import os
import time
import json
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
from ibkr_client import IBKRClient
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
        with open("bot_state.json", "w") as f:
            json.dump({
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
            }, f)
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
    """Adjust position size based on stock volatility."""
    try:
        # High volatility stocks — reduce position size
        high_vol = ["COIN", "MSTR", "PLTR", "HOOD", "SOFI", "TSLA", "NVDA", "AMD"]
        med_vol  = ["GOOGL", "AMZN", "META", "NFLX", "UBER", "BABA", "INTC", "DIS"]
        low_vol  = ["AAPL", "MSFT", "SPY", "QQQ"]
        if symbol in high_vol:
            size = base_size * 0.5   # 50% — $125 instead of $250
            log.info(f"{symbol}: HIGH volatility → reduced to ${size:.0f}")
        elif symbol in med_vol:
            size = base_size * 0.75  # 75% — $187 instead of $250
            log.info(f"{symbol}: MED volatility → reduced to ${size:.0f}")
        else:
            size = base_size          # 100% — $250 full size
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
            bars   = ibkr.get_bars(symbol)
            signal = stock_analyzer.analyze(symbol, bars)
            if signal:
                signals.append({
                    "product_id":  symbol, "action": signal["action"],
                    "confidence":  signal["confidence"], "entry_price": signal["entry_price"],
                    "stop_loss":   signal["stop_loss"], "take_profit": signal["take_profit"],
                    "risk_reward": signal.get("risk_reward", 2.0),
                    "reasoning":   signal.get("reasoning", ""), "market": "stocks",
                })
                if signal["action"] == "BUY" and signal["confidence"] >= config.MIN_CONFIDENCE and symbol not in _trade_cooldown:
                    # Skip if already have open position in this symbol
                    existing_syms = [p.get("product_id") for p in existing_positions]
                    if symbol in existing_syms:
                        log.info(f"{symbol}: already have open position — skipping")
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
                    size_usd = get_position_size(symbol, pt.stock_balance * config.MAX_POSITION_PCT, ibkr)
                    fill = ibkr.place_market_order(symbol, "buy", notional=size_usd)
                    if fill and fill.get("status") not in [None, "Cancelled", "Inactive"]:
                        entry_price = float(signal["entry_price"] or 0)
                        qty = fill.get("qty", 1)
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
                            "indicators":  signal.get("indicators", {}),
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
                bars = ibkr.get_bars(symbol, timeframe="1Min", limit=1)
                if not bars:
                    continue
                current = float(bars[-1].get("c") or bars[-1].get("close") or 0)
                if current >= entry * 1.045:
                    trail_sl = round(current * 0.985, 4)
                    if trail_sl > sl:
                        pos["stop_loss"] = trail_sl
                        sl = trail_sl
                elif current >= entry * 1.03:
                    breakeven_sl = round(entry * 1.001, 4)
                    if breakeven_sl > sl:
                        pos["stop_loss"] = breakeven_sl
                        sl = breakeven_sl
                pnl = (current - entry) * float(pos["quantity"])
                pnl_pct = (current - entry) / entry * 100
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
    """Get live option price from Yahoo Finance."""
    try:
        import yfinance as yf
        import re
        # Parse contract: NVDA260413C00180000
        m = re.match(r'^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d+)$', contract_symbol)
        if not m:
            return 0
        sym, y, mo, d, cp, strike_raw = m.groups()
        expiry = f"20{y}-{mo}-{d}"
        strike = int(strike_raw) / 1000
        option_type = "calls" if cp == "C" else "puts"
        ticker = yf.Ticker(sym)
        chain = ticker.option_chain(expiry)
        df = getattr(chain, option_type)
        row = df[df["strike"] == strike]
        if row.empty:
            # Try closest strike
            row = df.iloc[(df["strike"] - strike).abs().argsort()[:1]]
        if not row.empty:
            price = float(row["lastPrice"].iloc[0])
            if price == 0:
                bid = float(row["bid"].iloc[0])
                ask = float(row["ask"].iloc[0])
                price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0
            return price
        return 0
    except Exception as e:
        log.error(f"YF option price error {contract_symbol}: {e}")
        return 0

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
                    # Email alerts for big moves
                    try:
                        from notifier import alert_trade_closed
                        if pnl_pct >= 50 and not p.get("alerted_tp"):
                            alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_tp_alert", "options")
                            p["alerted_tp"] = True
                            log.info(f"OPTIONS ALERT SENT: {p['product_id']} +{pnl_pct:.1f}%")
                        elif pnl_pct <= -50 and not p.get("alerted_sl"):
                            alert_trade_closed(p["product_id"], "BUY", p["entry_price"], current, pnl, pnl_pct, "options_sl_alert", "options")
                        if pnl_pct <= -30:
                            log.warning(f"OPTIONS AUTO-CLOSE {p['product_id']}: down {pnl_pct:.1f}% — cutting loss")
                            try:
                                from options_client import OptionsClient
                                oc = options_client
                                oc.place_option_order(p["product_id"], qty=1, side="sell")
                            except Exception as ce:
                                log.error(f"Options close order failed: {ce}")
                            p["status"] = "closed_sl"
                            from trade_history import save_trade
                            save_trade({**p, "exit_price": current, "pnl_usd": pnl, "pnl_pct": pnl_pct, "market": "options"})
                            state["positions"] = [x for x in state["positions"] if x.get("product_id") != p["product_id"]]
                            with open("bot_state.json", "w") as _f:
                                import json as _j
                                _j.dump(state, _f, indent=2, default=str)
                            p["alerted_sl"] = True
                            log.info(f"OPTIONS AUTO-CLOSED: {p['product_id']} {pnl_pct:.1f}%")
                    except Exception as ne:
                        log.error(f"Options notify error: {ne}")
                else:
                    log.info(f"OPTIONS {p['product_id']} price unavailable")
            except Exception as e:
                log.error(f"Options monitor error {p['product_id']}: {e}")
        if updated:
            with open("bot_state.json", "w") as f:
                json.dump(state, f, indent=2)
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
            budget = 200 if config.IS_LIVE else 250
            # Earnings calendar: use weekly options if earnings within 7 days
            expiry_days, expiry_reason = get_options_expiry_days(symbol)
            if has_earnings_soon(symbol):
                log.info(f"Options {symbol}: earnings play — using weekly expiry ({expiry_reason})")
                budget = min(budget, 100)  # smaller budget for earnings plays
            contract = options_client.find_best_option(symbol, direction, budget=budget)
            if not contract:
                log.info(f"Options {symbol}: No suitable contract found")
                continue
            contract_sym = contract.get("symbol")
            fill = options_client.place_option_order(contract_sym, qty=1, side="buy")
            if fill["success"]:
                cost = float(contract.get("close_price", 0) or 0) * 100
                log.info(f"OPTIONS {opt_signal['strategy'].upper()} {contract_sym} cost=${cost:.2f}")
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
    tm.monitor_positions()
    monitor_stock_positions(ibkr, pt)
    monitor_option_positions(options_client)
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
            price = float(p.get("average-open-price", 0))
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
                json.dump(state, f, indent=2)
            log.info(f"Tastytrade sync: {added} positions added")
    except Exception as e:
        log.error(f"sync_tastytrade_positions: {e}")

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
    ibkr            = IBKRClient()
    _ibkr_fail_count = 0
    from options_client import OptionsClient
    from options_analyzer import OptionsAnalyzer
    options_client   = OptionsClient(ibkr_client=ibkr)
    options_analyzer = OptionsAnalyzer()




    pt             = PortfolioTracker()
    sync_tastytrade_positions(options_client, pt)
    analyzer       = Analyzer()
    stock_analyzer = StockAnalyzer()
    tm             = TradeManager(cb, portfolio_tracker=pt)
    log.info(f"Stocks: {len(config.STOCK_SYMBOLS)} symbols | Mode: {config.TRADING_MODE.upper()} | IBKR port {config.IBKR_PORT}")
    log.info(f"Portfolio — Stocks:${pt.stock_balance:,.2f} | Total:${pt.stock_balance:,.2f}\n")
    while _running:
        try:
            run_scan(cb, ibkr, analyzer, stock_analyzer, tm, pt, options_client, options_analyzer)
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
        if args.once or not _running: break
        log.info(f"Next scan in {config.SCAN_INTERVAL}s. Ctrl+C to stop.\n")
        for _ in range(config.SCAN_INTERVAL):
            if not _running: break
            time.sleep(1)
    log.info(f"Final — Stocks:${pt.stock_balance:,.2f} | Total:${pt.stock_balance:,.2f}")

if __name__ == "__main__":
    main()

