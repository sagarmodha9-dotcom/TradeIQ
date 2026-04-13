import sys
import time
import json
import signal
import argparse
from datetime import datetime
from tabulate import tabulate
import config
from logger import log
from coinbase_client import CoinbaseClient
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

def save_state(crypto_signals, stock_signals, tm, alpaca_positions, pt):
    try:
        # Load existing stock positions to preserve them
        try:
            import json as _json
            with open("bot_state.json") as _f:
                _existing = _json.load(_f)
            _existing_stocks = [p for p in _existing.get("positions", []) if p.get("market") == "stocks"]
        except Exception:
            _existing_stocks = []
        # Merge: new positions take priority, keep existing ones not in new list
        new_syms = [p["product_id"] for p in alpaca_positions]
        merged_stocks = alpaca_positions + [p for p in _existing_stocks if p["product_id"] not in new_syms]
        alpaca_positions = merged_stocks
        crypto_pos = [
            {
                "product_id":  pid, "side": pos.side,
                "entry_price": pos.entry_price, "quantity": pos.quantity,
                "usd_value":   pos.usd_value, "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit, "confidence": pos.confidence,
                "reasoning":   pos.reasoning, "opened_at": pos.opened_at,
                "market":      "crypto", "pnl_usd": 0.0,
            }
            for pid, pos in tm.positions.items()
        ]
        all_positions = crypto_pos + alpaca_positions
        closed = [
            {
                "product_id":  p.product_id, "side": p.side,
                "entry_price": p.entry_price, "exit_price": p.exit_price,
                "pnl_usd":     p.pnl_usd, "pnl_pct": p.pnl_pct,
                "status":      p.status, "closed_at": p.closed_at,
                "market":      "crypto",
            }
            for p in tm.closed[-20:]
        ]
        port = pt.to_dict()
        with open("bot_state.json", "w") as f:
            json.dump({
                "signals":          crypto_signals + stock_signals,
                "scanned_at":       datetime.now().isoformat(),
                "positions":        all_positions,
                "closed_trades":    closed,
                "stats":            tm.stats(),
                "portfolio_usd":    port["total_balance"],
                "crypto_portfolio": port["crypto_balance"],
                "stock_portfolio":  port["stock_balance"],
                "crypto_pnl":       port["crypto_pnl"],
                "stock_pnl":        port["stock_pnl"],
                "total_pnl":        port["total_pnl"],
                "crypto_open":      len(crypto_pos),
                "stock_open":       len(alpaca_positions),
                "crypto_win_rate":  port["crypto_win_rate"],
                "stock_win_rate":   port["stock_win_rate"],
                "crypto_trades":    port["crypto_trades"],
                "stock_trades":     port["stock_trades"],
            }, f)
        log.info(f"Portfolio — Crypto:${port['crypto_balance']:,.2f} | Stocks:${port['stock_balance']:,.2f} | Total:${port['total_balance']:,.2f}")
    except Exception as e:
        log.error(f"Could not save state: {e}")

def is_crypto_market_bullish(cb):
    """Returns True if BTC is above its 20-day EMA (bullish trend)."""
    try:
        candles = cb.get_candles("BTC-USD", granularity="ONE_DAY", limit=25)
        if len(candles) < 20:
            return True  # Default to allowing trades if not enough data
        closes = [float(c["close"]) for c in candles]
        ema20 = closes[0]
        k = 2 / 21
        for c in closes[1:]:
            ema20 = c * k + ema20 * (1 - k)
        current = closes[-1]
        bullish = current > ema20
        log.info(f"BTC trend: ${current:,.0f} vs EMA20 ${ema20:,.0f} — {'BULLISH ✅' if bullish else 'BEARISH ❌'}")
        return bullish
    except Exception as e:
        log.error(f"Trend check error: {e}")
        return True

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

def run_crypto_scan(cb, analyzer, tm, portfolio_usd, skip_bearish=True):
    if skip_bearish and not is_crypto_market_bullish(cb):
        log.info("── Crypto scan — SKIPPING (BTC bearish)")
        return []
    log.info(f"── Crypto scan — {len(config.CRYPTO_PAIRS)} pairs")
    signals = []
    for pair in config.CRYPTO_PAIRS:
        try:
            candles = cb.get_candles(pair)
            signal  = analyzer.analyze(pair, candles)
            if signal:
                signal["market"] = "crypto"
                signals.append(signal)
        except Exception as e:
            log.error(f"{pair}: {e}")
    crypto_cooldown = get_recent_losers(24)
    for s in signals:
        if s["action"] == "BUY" and s["confidence"] >= config.MIN_CONFIDENCE and s["product_id"] not in crypto_cooldown:
            tm.open_trade(s, portfolio_usd)
    return signals

def is_stock_market_bullish(alpaca):
    """Returns True if SPY is above its 20-day EMA (bullish trend)."""
    try:
        bars = alpaca.get_bars("SPY", timeframe="1Day", limit=25)
        if len(bars) < 20:
            return True
        closes = [float(b["close"]) for b in bars]
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

def get_position_size(symbol, base_size, alpaca):
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

def run_stock_scan(alpaca, stock_analyzer, pt):
    # Load existing stock positions from state
    try:
        import json
        with open("bot_state.json") as f:
            existing = json.load(f)
        existing_positions = [p for p in existing.get("positions", []) if p.get("market") == "stocks"]
    except Exception:
        existing_positions = []
    if not alpaca.is_market_open():
        log.info("── Stock market closed — skipping")
        return [], []
    if not is_stock_market_bullish(alpaca):
        log.info("── Stock scan — SKIPPING NEW BUYS (SPY bearish)")
        return [], []
    log.info(f"── Stock scan — {len(config.STOCK_SYMBOLS)} symbols")
    cooldown = get_recent_losers(24)
    signals, positions = [], []
    for symbol in config.STOCK_SYMBOLS:
        try:
            bars   = alpaca.get_bars(symbol)
            signal = stock_analyzer.analyze(symbol, bars)
            if signal:
                signals.append({
                    "product_id":  symbol, "action": signal["action"],
                    "confidence":  signal["confidence"], "entry_price": signal["entry_price"],
                    "stop_loss":   signal["stop_loss"], "take_profit": signal["take_profit"],
                    "risk_reward": signal.get("risk_reward", 2.0),
                    "reasoning":   signal.get("reasoning", ""), "market": "stocks",
                })
                if signal["action"] == "BUY" and signal["confidence"] >= config.MIN_CONFIDENCE and symbol not in cooldown:
                    size_usd = get_position_size(symbol, pt.stock_balance * config.MAX_POSITION_PCT, alpaca)
                    fill = alpaca.place_market_order(symbol, "buy", notional=size_usd)
                    if fill["success"]:
                        positions.append({
                            "product_id":  symbol, "side": "BUY",
                            "entry_price": fill["fill_price"], "quantity": fill["fill_quantity"],
                            "usd_value":   fill["fill_value_usd"], "stop_loss": signal["stop_loss"],
                            "take_profit": signal["take_profit"], "confidence": signal["confidence"],
                            "reasoning":   signal.get("reasoning", ""), "opened_at": fill["timestamp"],
                            "market":      "stocks", "pnl_usd": 0.0,
                        })
        except Exception as e:
            log.error(f"{symbol}: {e}")
    try:
        acct = alpaca.get_account()
        pt.update_stock_balance(float(acct.get("equity", 100000)))
    except Exception:
        pass
    return signals, positions

def monitor_stock_positions(alpaca, pt):
    """Check open stock positions and close if SL or TP hit."""
    try:
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
            try:
                bars = alpaca.get_bars(symbol, timeframe="1Min", limit=1)
                if not bars:
                    continue
                current = float(bars[-1]["close"])
                pnl = (current - entry) * float(pos["quantity"])
                pnl_pct = (current - entry) / entry * 100
                if current <= sl:
                    log.info(f"❌ STOCK SL HIT {symbol} @ ${current:.2f} PnL: ${pnl:.2f}")
                    alpaca.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
                    from trade_history import save_trade
                    save_trade({"product_id": symbol, "side": "BUY", "entry_price": entry,
                                "exit_price": current, "pnl_usd": round(pnl, 4),
                                "pnl_pct": round(pnl_pct, 2), "status": "closed_sl",
                                "market": "stocks", "confidence": pos.get("confidence", 0),
                                "opened_at": pos.get("opened_at"), "closed_at": datetime.now().isoformat()})
                    from notifier import alert_trade_closed
                    alert_trade_closed(symbol, "BUY", entry, current, pnl, pnl_pct, "closed_sl", "stocks")
                    closed.append(symbol)
                elif current >= tp:
                    log.info(f"✅ STOCK TP HIT {symbol} @ ${current:.2f} PnL: ${pnl:.2f}")
                    alpaca.place_market_order(symbol, "sell", qty=float(pos["quantity"]))
                    from trade_history import save_trade
                    save_trade({"product_id": symbol, "side": "BUY", "entry_price": entry,
                                "exit_price": current, "pnl_usd": round(pnl, 4),
                                "pnl_pct": round(pnl_pct, 2), "status": "closed_tp",
                                "market": "stocks", "confidence": pos.get("confidence", 0),
                                "opened_at": pos.get("opened_at"), "closed_at": datetime.now().isoformat()})
                    from notifier import alert_trade_closed
                    alert_trade_closed(symbol, "BUY", entry, current, pnl, pnl_pct, "closed_tp", "stocks")
                    closed.append(symbol)
                else:
                    log.info(f"📈 STOCK {symbol} @ ${current:.2f} PnL: ${pnl:.2f} ({pnl_pct:+.2f}%)")
            except Exception as e:
                log.error(f"Stock monitor error {symbol}: {e}")
    except Exception as e:
        log.error(f"monitor_stock_positions error: {e}")

def run_options_scan(options_client, options_analyzer, stock_signals, pt):
    if not stock_signals:
        return []
    log.info(f"── Options scan — {len(stock_signals)} stock signals")
    positions = []
    # Only trade options on BUY signals with 72%+ confidence
    candidates = [s for s in stock_signals if s["action"] == "BUY" and s["confidence"] >= config.MIN_CONFIDENCE]
    if not candidates:
        log.info("No eligible stock signals for options")
        return []
    for signal in candidates[:2]:  # Max 2 options trades per scan
        try:
            symbol = signal["product_id"]
            price  = signal["entry_price"]
            opt_signal = options_analyzer.analyze(symbol, signal, price)
            if not opt_signal or opt_signal["strategy"] == "pass":
                continue
            if opt_signal["confidence"] < config.MIN_CONFIDENCE:
                continue
            direction = "BUY" if "call" in opt_signal["strategy"] else "SELL"
            contract = options_client.find_best_option(symbol, direction, budget=250)
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
                    "opened_at":   fill["timestamp"],
                    "market":      "options",
                    "pnl_usd":     0.0,
                })
        except Exception as e:
            log.error(f"Options scan error {signal.get('product_id')}: {e}")
    return positions






def run_scan(cb, alpaca, analyzer, stock_analyzer, tm, pt, options_client=None, options_analyzer=None):
    log.info(f"\nScan @ {datetime.now().strftime('%H:%M:%S')}")
    tm.monitor_positions()
    monitor_stock_positions(alpaca, pt)
    crypto_signals              = run_crypto_scan(cb, analyzer, tm, pt.crypto_balance)
    stock_signals, alpaca_pos   = run_stock_scan(alpaca, stock_analyzer, pt)
    options_pos = run_options_scan(options_client, options_analyzer, stock_signals, pt) if options_client else []

    all_signals = crypto_signals + stock_signals
    if all_signals:
        rows = [
            [s.get("product_id","")[:20], s.get("market","").upper(),
             s["action"], f"{s['confidence']:.0%}", f"${float(s['entry_price'] or 0):,.4f}"]
            for s in all_signals
        ]
        print("\n" + tabulate(rows, headers=["Symbol","Market","Action","Conf","Entry"], tablefmt="rounded_outline") + "\n")
    clean_crypto = [{
        "product_id": s["product_id"], "action": s["action"],
        "confidence": s["confidence"], "entry_price": s["entry_price"],
        "stop_loss":  s["stop_loss"],  "take_profit": s["take_profit"],
        "risk_reward": s.get("risk_reward", 2.0),
        "reasoning":  s.get("reasoning", ""), "market": "crypto",
    } for s in crypto_signals]

    save_state(clean_crypto, stock_signals, tm, alpaca_pos + options_pos, pt)

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
    if config.IS_LIVE:
        if input("LIVE MODE - real funds. Type yes to confirm: ").strip().lower() != "yes":
            config.IS_LIVE = False
    cb              = CoinbaseClient()
    alpaca          = AlpacaClient()
    from options_client import OptionsClient
    from options_analyzer import OptionsAnalyzer
    options_client   = OptionsClient()
    options_analyzer = OptionsAnalyzer()




    pt             = PortfolioTracker()
    analyzer       = Analyzer()
    stock_analyzer = StockAnalyzer()
    tm             = TradeManager(cb, portfolio_tracker=pt)
    log.info(f"Fetching top {config.TOP_PAIRS_COUNT} crypto pairs...")
    cb.fetch_top_pairs(config.TOP_PAIRS_COUNT)
    log.info(f"Crypto: {len(config.CRYPTO_PAIRS)} pairs | Stocks: {len(config.STOCK_SYMBOLS)} symbols")
    log.info(f"Portfolio — Crypto:${pt.crypto_balance:,.2f} | Stocks:${pt.stock_balance:,.2f} | Total:${pt.total_balance:,.2f}\n")
    while _running:
        try:
            run_scan(cb, alpaca, analyzer, stock_analyzer, tm, pt, options_client, options_analyzer)
        except Exception as e:
            log.error(f"Scan error: {e}", exc_info=True)
        if args.once or not _running: break
        log.info(f"Next scan in {config.SCAN_INTERVAL}s. Ctrl+C to stop.\n")
        for _ in range(config.SCAN_INTERVAL):
            if not _running: break
            time.sleep(1)
    log.info(f"Final — Crypto:${pt.crypto_balance:,.2f} | Stocks:${pt.stock_balance:,.2f} | Total:${pt.total_balance:,.2f}")

if __name__ == "__main__":
    main()

