import json
import time
from datetime import datetime, timezone

# Response cache to avoid hammering IBKR
_cache = {}
_cache_ttl = 10  # seconds

# Live balance cache — read from portfolio_state on startup
def _load_initial_balance():
    try:
        import json, os
        if os.path.exists("portfolio_state.json"):
            with open("portfolio_state.json") as f:
                s = json.load(f)
            return s.get("stock_balance", 1000.0)
    except:
        pass
    return 1000.0

_balance_cache = {"ibkr": _load_initial_balance(), "tt": 500.0, "updated": 0}

def _refresh_balance_background():
    import threading, time
    def _fetch():
        while True:
            try:
                import config
                if config.IS_LIVE:
                    from ibkr_client import IBKRClient
                    ibkr = IBKRClient()
                    if ibkr.ib.isConnected():
                        val = ibkr.get_portfolio_value()
                        if val > 0:
                            _balance_cache["ibkr"] = val
                        ibkr.disconnect()
                    from tastytrade_client import TastytradeClient
                    tt = TastytradeClient()
                    if tt.session_token:
                        _balance_cache["tt"] = tt.get_balance()
                    _balance_cache["updated"] = time.time()
            except:
                pass
            time.sleep(300)  # refresh every 5 minutes
    t = threading.Thread(target=_fetch, daemon=True)
    t.start()

_refresh_balance_background()

def _get_cached(key, fn):
    now = time.time()
    if key in _cache and now - _cache[key]['t'] < _cache_ttl:
        return _cache[key]['v']
    val = fn()
    _cache[key] = {'v': val, 't': now}
    return val
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import config
from trade_manager import TradeManager
from logger import log

config.validate_config()
try:
    from ibkr_client import IBKRClient
    ibkr = IBKRClient(client_id=2)
except Exception as e:
    log.warning(f"IBKR not available for API: {e}")
    ibkr = None
tm     = TradeManager(None)
log.info("Dashboard API ready")

def load_state():
    try:
        with open("bot_state.json") as f: return json.load(f)
    except Exception: return {}

def load_trade_history():
    try:
        with open("trade_history.json") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def get_status():
    state = load_state()
    closed = load_trade_history()
    return {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "mode":             "live" if config.IS_LIVE else "paper",
        "portfolio_usd":    _balance_cache["ibkr"] + _balance_cache["tt"],
        "total_pnl":        state.get("total_pnl", 0),
        "daily_pnl":        __import__('trade_history').get_daily_summary().get("total_pnl", 0) + sum(p.get("pnl_usd",0) for p in state.get("positions",[]) if p.get("market")=="options"),
        "win_rate":         state.get("stats", {}).get("win_rate", 0),
        "total_trades":     state.get("stats", {}).get("total_trades", 0),
        "open_positions":   state.get("positions", []),
        "closed_trades":    closed,
        "recent_trades":    closed[:20],
        "last_signals":     state.get("signals", []),
        "scanned_at":       state.get("scanned_at"),
        "options_portfolio": _balance_cache["tt"],
        "stock_portfolio":  _balance_cache["ibkr"],
        "options_pnl":       state.get("options_pnl", 0),
        "stock_pnl":        sum(p.get("pnl_usd",0) for p in state.get("positions",[]) if p.get("market")=="stocks"),
        "options_open":      state.get("options_open", 0),
        "stock_open":       state.get("stock_open", 0),
        "crypto_win_rate":  state.get("crypto_win_rate", 0),
        "stock_win_rate":   state.get("stock_win_rate", 0),
        "crypto_trades":    state.get("crypto_trades", 0),
        "stock_trades":     state.get("stock_trades", 0),
        "stats":            state.get("stats", {}),
    }

def get_crypto_chart(symbol, granularity="ONE_HOUR", limit=24):
    try:
        # Coinbase max candles = 300, and requires start/end within range
        gran_seconds = {
            "ONE_MINUTE": 60, "FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900,
            "THIRTY_MINUTE": 1800, "ONE_HOUR": 3600, "TWO_HOUR": 7200,
            "SIX_HOUR": 21600, "ONE_DAY": 86400,
        }
        secs = gran_seconds.get(granularity, 3600)
        limit = min(int(limit), 300)
        end   = int(time.time())
        start = end - (secs * limit)








        endpoint = f"/products/{symbol}/candles?start={start}&end={end}&granularity={granularity}&limit={limit}"
        raw = cb._request("GET", endpoint).get("candles", [])
        candles = [{"time": int(c["start"]), "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]), "volume": float(c["volume"])}
                   for c in reversed(raw)]
        return {"candles": candles, "symbol": symbol}
    except Exception as e:
        log.error(f"Crypto chart {symbol}: {e}")
        return {"candles": [], "symbol": symbol}

def get_stock_chart(symbol, timeframe="1Hour", limit=24):
    try:
        bars = ibkr.get_bars(symbol, timeframe=timeframe, limit=int(limit))
        return {"bars": bars, "symbol": symbol}
    except Exception as e:
        log.error(f"Stock chart {symbol}: {e}")
        return {"bars": [], "symbol": symbol}

class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status=200):
        try:
            body = json.dumps(data, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception: pass
    def do_OPTIONS(self):
        try:
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
        except Exception: pass
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)
        path   = parsed.path
        if path == "/status":
            self._json(get_status())
        elif path == "/health":
            self._json({"ok": True})
        elif path == "/chart/crypto":
            symbol      = qs.get("symbol",     ["BTC-USD"])[0]
            granularity = qs.get("granularity", ["ONE_HOUR"])[0]
            limit       = qs.get("limit",       ["24"])[0]
            self._json(get_crypto_chart(symbol, granularity, limit))
        elif path == "/history":
            from trade_history import load_history, get_daily_summary
            from datetime import date, timedelta
            history = load_history()
            today = get_daily_summary()
            yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
            yesterday = get_daily_summary(yesterday_str)
            from datetime import date, timedelta
            def summary(d): return get_daily_summary(d)
            def date_range(days):
                trades = [t for t in history if t.get("date","") >= (date.today()-timedelta(days=days)).strftime("%Y-%m-%d")]
                wins=[t for t in trades if t.get("pnl_usd",0)>0]
                losses=[t for t in trades if t.get("pnl_usd",0)<=0]
                return {"trades":trades,"wins":len(wins),"losses":len(losses),"total_pnl":round(sum(t.get("pnl_usd",0) for t in trades),4),"win_rate":len(wins)/len(trades) if trades else 0}
            self._json({"all_trades":history,"today":summary(date.today().strftime("%Y-%m-%d")),"yesterday":summary((date.today()-timedelta(days=1)).strftime("%Y-%m-%d")),"week":date_range(7),"month":date_range(30),"year":date_range(365),"all_time":date_range(9999)})
        elif path == "/volume":
            try:
                import requests as _req
                symbols = ["SPY","QQQ","NVDA","MSFT","TSLA","AMZN","META","GOOGL","AMD","INTC","SOFI","NFLX","DIS","BABA","HOOD"]
                result = []
                for sym in symbols:
                    try:
                        bars = ibkr.get_bars(sym, timeframe="1Hour", limit=24)
                        if not bars: continue
                        vols = [b.get("v",0) for b in bars]
                        avg_vol = sum(vols[:-1])/len(vols[:-1]) if len(vols)>1 else 0
                        cur_vol = vols[-1] if vols else 0
                        close = bars[-1].get("c",0)
                        open_p = bars[0].get("o",0)
                        chg = (close-open_p)/open_p*100 if open_p else 0
                        result.append({
                            "symbol": sym,
                            "volume": int(cur_vol),
                            "avg_volume": int(avg_vol),
                            "vol_ratio": round(cur_vol/avg_vol,2) if avg_vol else 0,
                            "price": round(close,2),
                            "change_pct": round(chg,2)
                        })
                    except: pass
                result.sort(key=lambda x: x["vol_ratio"], reverse=True)
                self._json({"symbols": result})
            except Exception as e:
                self._json({"error": str(e), "symbols": []})
        elif path == "/forecast":
            try:
                import requests as _req
                r = _req.get(
                    "https://gamma-api.polymarket.com/markets?limit=200&active=true&closed=false&order=volume&ascending=false",
                    timeout=10
                )
                keywords = ['trump','fed','rate','tariff','inflation','gdp','bitcoin','btc','nasdaq','sp500','recession','economy','market','stock','crypto','election','powell','dollar','oil','trade']
                markets = []
                for m in r.json():
                    q = m.get("question","").lower()
                    if not any(k in q for k in keywords):
                        continue
                    q = m.get("question","")
                    prob = m.get("outcomePrices","[]")
                    try:
                        import json as _j
                        prices = _j.loads(prob) if isinstance(prob, str) else prob
                        yes_price = float(prices[0]) if prices else 0
                    except:
                        yes_price = 0
                    markets.append({
                        "id":       m.get("id",""),
                        "question": q,
                        "yes_price": round(yes_price * 100),
                        "no_price":  round((1 - yes_price) * 100),
                        "volume":   m.get("volume","0"),
                        "end_date": m.get("endDate",""),
                        "category": m.get("category",""),
                        "image":    m.get("image",""),
                    })
                self._json({"markets": markets})
            except Exception as e:
                self._json({"error": str(e), "markets": []})
        elif path == "/chart/stock":
            symbol    = qs.get("symbol",    ["AAPL"])[0]
            timeframe = qs.get("timeframe", ["1Hour"])[0]
            limit     = qs.get("limit",     ["24"])[0]
            self._json(get_stock_chart(symbol, timeframe, limit))
        elif path == "/winrate":
            import win_rate_tracker
            stats = win_rate_tracker.get_symbol_stats(days=30)
            weak  = win_rate_tracker.get_weak_symbols()
            self._json({"stats": stats, "weak_symbols": weak})
        elif path == "/earnings":
            from earnings_calendar import get_earnings_summary, days_to_earnings, get_earnings_date
            summary = get_earnings_summary(config.STOCK_SYMBOLS)
            # Add upcoming earnings sorted by date
            upcoming = []
            for sym, data in summary.items():
                if data.get("days_to_earnings") is not None and 0 <= data["days_to_earnings"] <= 14:
                    upcoming.append({
                        "symbol": sym,
                        "days": data["days_to_earnings"],
                        "date": data["earnings_date"],
                        "soon": data["has_earnings_soon"],
                    })
            upcoming.sort(key=lambda x: x["days"])
            self._json({"earnings": summary, "upcoming": upcoming})

        elif path == "/premarket":
            import os, json as _j
            if os.path.exists("premarket_watchlist.json"):
                try:
                    with open("premarket_watchlist.json") as f:
                        wl = _j.load(f)
                    self._json({"watchlist": wl, "count": len(wl)})
                except:
                    self._json({"watchlist": [], "count": 0})
            else:
                self._json({"watchlist": [], "count": 0})

        elif path == "/sector":
            import risk_manager
            spy_chg = risk_manager.get_spy_change()
            allowed, required, reason = risk_manager.get_sector_filter(0.72)
            if spy_chg <= -1.5:
                status = "bearish"
                color  = "red"
            elif spy_chg <= -0.75:
                status = "cautious"
                color  = "amber"
            elif spy_chg >= 1.0:
                status = "bullish"
                color  = "green"
            else:
                status = "neutral"
                color  = "text"
            self._json({
                "spy_change":  round(spy_chg, 2),
                "status":      status,
                "color":       color,
                "required_confidence": required,
                "reason":      reason,
                "filter_active": required > 0.72,
            })
        elif path == "/market-news":
            import time as _time
            # Return cached news immediately, refresh in background
            now = _time.time()
            cache = _get_cached.__globals__.get('_news_cache', {})
            if cache.get('news') and now - cache.get('t', 0) < 300:
                self._json({"news": cache['news']})
            else:
                # Fetch in background thread
                import threading, anthropic, os, json as _json
                def fetch_news():
                    try:
                        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
                        msg = client.messages.create(
                            model="claude-haiku-4-5-20251001",
                            max_tokens=1000,
                            messages=[{"role": "user", "content": "Search web for today top 5 market-moving news affecting US stocks. Return ONLY JSON array with fields: headline, summary, impact (bullish/bearish/neutral), sectors. No markdown."}],
                            tools=[{"type": "web_search_20250305", "name": "web_search"}]
                        )
                        text = "".join(b.text for b in msg.content if hasattr(b, "text"))
                        clean = text.replace("```json","").replace("```","").strip()
                        news = _json.loads(clean)
                        _get_cached.__globals__['_news_cache'] = {'news': news, 't': _time.time()}
                    except Exception as e:
                        pass
                t = threading.Thread(target=fetch_news, daemon=True)
                t.start()
                # Return cached or empty while fetching
                self._json({"news": cache.get('news', []), "loading": True})

        elif path == "/news":
            sym = qs.get("symbol", ["SPY"])[0]
            import news_sentiment
            result = news_sentiment.get_news_sentiment(sym)
            self._json(result)
        elif path == "/risk":
            import risk_manager
            self._json({
                "killed":          risk_manager.is_killed(),
                "market_open":     risk_manager.is_market_open(),
                "no_trade_window": risk_manager.is_in_no_trade_window(),
                "daily_pnl":       round(risk_manager.get_daily_pnl(), 2),
                "daily_limit":     round(-config.get_daily_loss_limit_usd(), 2),
                "open_positions":  risk_manager.get_open_position_count(),
                "open_stocks":     risk_manager.get_open_stock_count(),
                "open_options":    risk_manager.get_open_options_count(),
                "max_positions":   config.MAX_OPEN_POSITIONS,
                "max_options":     config.MAX_OPEN_OPTIONS,
                "vix":             round(risk_manager.get_vix(), 2),
                "vix_limit":       config.MAX_VIX,
                "mode":            config.TRADING_MODE,
                "is_live":         config.IS_LIVE,
            })
        else:
            self._json({"error": "not found"}, 404)
    def do_POST(self):
        import risk_manager
        parsed = urlparse(self.path)
        path   = parsed.path
        if path == "/kill":
            risk_manager.activate_kill_switch(reason="dashboard")
            self._json({"status": "killed"})
        elif path == "/resume":
            risk_manager.deactivate_kill_switch()
            self._json({"status": "active"})
        else:
            self._json({"status": "ok"})
    def log_message(self, fmt, *args): pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8081), Handler)
    log.info("Dashboard API running on http://localhost:5001")
    log.info("Chart endpoints: /chart/crypto?symbol=BTC-USD  /chart/stock?symbol=AAPL")
    try:    server.serve_forever()
    except KeyboardInterrupt: log.info("Stopped.")
