import os
DATA_FEED = os.getenv("DATA_FEED", "iex")
import time
import requests
from datetime import datetime, timezone, timedelta
import config
from logger import log

class AlpacaClient:
    def __init__(self):
        self.api_key    = config.ALPACA_API_KEY
        self.secret_key = config.ALPACA_SECRET_KEY
        self.base_url   = config.ALPACA_BASE_URL
        self.data_url   = "https://data.alpaca.markets"
        self.headers    = {
            "APCA-API-KEY-ID":     self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type":        "application/json",
        }

    def _request(self, method, base, endpoint, body=None, retries=3):
        url = f"{base}{endpoint}"
        for attempt in range(retries):
            try:
                resp = requests.request(
                    method, url, headers=self.headers, json=body, timeout=8
                )
                if resp.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp.json() or {}
            except requests.exceptions.RequestException as e:
                log.error(f"Alpaca request error ({attempt+1}/{retries}): {e}")
                if attempt == retries - 1:
                    return {}
                time.sleep(2 ** attempt)

    def get_account(self):
        return self._request("GET", self.base_url, "/v2/account")

    def get_portfolio_value(self):
        try:
            acct = self.get_account()
            return float(acct.get("portfolio_value", 0))
        except Exception:
            return 0.0

    def get_cash_balance(self):
        try:
            acct = self.get_account()
            return float(acct.get("cash", 0))
        except Exception:
            return 0.0

    def is_market_open(self):
        try:
            clock = self._request("GET", self.base_url, "/v2/clock")
            return clock.get("is_open", False)
        except Exception:
            return False

    def get_bars(self, symbol, timeframe="1Hour", limit=100):
        def _parse_bars(data):
            result = []
            if not data or not isinstance(data, dict):
                return result
            for b in (data or {}).get("bars", []) or []:
                try:
                    result.append({
                        "time":   b["t"],
                        "open":   float(b["o"]),
                        "high":   float(b["h"]),
                        "low":    float(b["l"]),
                        "close":  float(b["c"]),
                        "volume": float(b["v"]),
                    })
                except: pass
            return result
        end   = datetime.now(timezone.utc)
        # Lookback widened for non-trading hours, scaled to bar timeframe
        if "Min" in timeframe:
            try: _mins = int(timeframe.replace("Min", ""))
            except Exception: _mins = 5
            start = end - timedelta(minutes=limit * _mins * 8)
        elif "Hour" in timeframe:
            start = end - timedelta(hours=limit * 8)
        else:
            start = end - timedelta(days=limit * 2)
        base_ep = (
            f"/v2/stocks/{symbol}/bars"
            f"?timeframe={timeframe}"
            f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&limit={limit}"
        )
        bars = _parse_bars(self._request("GET", self.data_url, base_ep + "&feed=" + DATA_FEED))
        if not bars:
            bars = _parse_bars(self._request("GET", self.data_url, base_ep + "&feed=" + DATA_FEED))
        return bars

    def get_bars_multi(self, symbols, timeframe="5Min", limit=100):
        """Batch bars for many symbols in one request. Returns {symbol: [bars]}.
        Symbols missing from the response are simply absent from the dict."""
        out = {}
        if not symbols:
            return out
        end = datetime.now(timezone.utc)
        if "Min" in timeframe:
            try: _mins = int(timeframe.replace("Min", ""))
            except Exception: _mins = 5
            start = end - timedelta(minutes=limit * _mins * 8)
        elif "Hour" in timeframe:
            start = end - timedelta(hours=limit * 8)
        else:
            start = end - timedelta(days=limit * 2)
        ep = (
            "/v2/stocks/bars"
            f"?symbols={','.join(symbols)}"
            f"&timeframe={timeframe}"
            f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&limit={limit * len(symbols)}"
            f"&feed={DATA_FEED}"
        )
        data = self._request("GET", self.data_url, ep) or {}
        # paginate if Alpaca returns a next_page_token
        pages = 0
        while True:
            for sym, blist in (data.get("bars") or {}).items():
                rows = out.setdefault(sym, [])
                for b in blist or []:
                    try:
                        rows.append({
                            "time":   b["t"],
                            "open":   float(b["o"]),
                            "high":   float(b["h"]),
                            "low":    float(b["l"]),
                            "close":  float(b["c"]),
                            "volume": float(b["v"]),
                        })
                    except: pass
            tok = data.get("next_page_token")
            pages += 1
            if not tok or pages >= 10:
                break
            data = self._request("GET", self.data_url, ep + f"&page_token={tok}") or {}
        # keep only the most recent `limit` bars per symbol
        for sym in list(out.keys()):
            out[sym] = out[sym][-limit:]
        return out

    def get_latest_price(self, symbol):
        # Try latest trade (most reliable)
        for feed in [DATA_FEED, "iex" if DATA_FEED=="sip" else "sip"]:
            try:
                data = self._request("GET", self.data_url,
                    f"/v2/stocks/{symbol}/trades/latest?feed={feed}") or {}
                price = float(data.get("trade", {}).get("p", 0))
                if price > 0:
                    return price
            except: pass
        # Try quote
        for feed in [DATA_FEED, "iex" if DATA_FEED=="sip" else "sip"]:
            try:
                data = self._request("GET", self.data_url,
                    f"/v2/stocks/{symbol}/quotes/latest?feed={feed}") or {}
                quote = data.get("quote", {})
                bid = float(quote.get("bp", 0))
                ask = float(quote.get("ap", 0))
                mid = (bid + ask) / 2 if bid and ask else 0
                if mid > 0:
                    return mid
            except: pass
        # Final fallback — last bar
        try:
            bars = self.get_bars(symbol, timeframe="1Hour", limit=1)
            if bars and bars[-1]["close"] > 0:
                return bars[-1]["close"]
        except: pass
        return 0.0

    def place_market_order(self, symbol, side, notional=None, qty=None):
        import uuid
        client_order_id = f"tradeiq-{uuid.uuid4().hex[:12]}"
        if not config.IS_LIVE:
            price = self.get_latest_price(symbol)
            fill_price = price["ask"] if side == "buy" else price["bid"]
            fill_qty   = (notional / fill_price) if notional else qty
            log.info(f"[PAPER] {side.upper()} {symbol} qty={fill_qty:.4f} @ ${fill_price:,.2f}")
            return {
                "success":        True,
                "paper":          True,
                "order_id":       client_order_id,
                "symbol":         symbol,
                "side":           side,
                "fill_price":     fill_price,
                "fill_quantity":  fill_qty,
                "fill_value_usd": notional or (qty * fill_price),
                "timestamp":      datetime.now(timezone.utc).isoformat(),
            }
        # Check if pre/post market
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        hour = now.hour
        is_extended = (hour < 13 or hour >= 20)  # before 9AM ET or after 4PM ET

        body = {
            "symbol":           symbol,
            "side":             side,
            "type":             "limit" if is_extended else "market",
            "time_in_force":    "day",
            "client_order_id":  client_order_id,
            "extended_hours":   is_extended,
        }
        # Extended hours requires limit order — use current price
        if is_extended:
            price = self.get_latest_price(symbol)
            if price > 0:
                # Buy slightly above, sell slightly below to ensure fill
                limit = round(price * 1.005 if side == "buy" else price * 0.995, 2)
                body["limit_price"] = str(limit)
            else:
                # Fall back to market during regular hours
                body["type"] = "market"
                body.pop("extended_hours", None)
        if notional:
            body["notional"] = str(round(notional, 2))
        elif qty:
            body["qty"] = str(qty)
        resp = self._request("POST", self.base_url, "/v2/orders", body=body)
        status = str((resp or {}).get("status", "")).lower()
        if not resp or status in ("rejected", "canceled", "expired") or not resp.get("id"):
            log.error(f"Alpaca order failed/rejected: {symbol} {side} resp={resp}")
            return {
                "success": False,
                "paper": False,
                "order_id": None,
                "symbol": symbol,
                "side": side,
                "error": resp,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        return {
            "success":        True,
            "paper":          False,
            "order_id":       resp.get("id", client_order_id),
            "symbol":         symbol,
            "side":           side,
            "fill_price":     float(resp.get("filled_avg_price") or 0),
            "fill_quantity":  float(resp.get("filled_qty") or 0),
            "fill_value_usd": notional or 0,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        }

    def get_positions(self):
        return self._request("GET", self.base_url, "/v2/positions")

    def get_asset(self, symbol):
        """Asset metadata — tradable/shortable/easy_to_borrow flags."""
        return self._request("GET", self.base_url, f"/v2/assets/{symbol}") or {}

    def get_prev_close(self, symbol):
        """Previous daily close (for SSR filter). Returns float or 0."""
        try:
            d = self._request("GET", self.data_url,
                f"/v2/stocks/{symbol}/snapshot?feed=" + DATA_FEED) or {}
            return float((d.get("prevDailyBar") or {}).get("c") or 0)
        except Exception:
            return 0.0

    def close_position(self, symbol):
        try:
            return self._request("DELETE", self.base_url, f"/v2/positions/{symbol}")
        except Exception as e:
            log.error(f"Alpaca close position error {symbol}: {e}")
            return None
