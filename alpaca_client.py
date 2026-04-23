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
                return resp.json()
            except requests.exceptions.RequestException as e:
                log.error(f"Alpaca request error ({attempt+1}/{retries}): {e}")
                if attempt == retries - 1:
                    raise
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
        end   = datetime.now(timezone.utc)
        start = end - timedelta(hours=limit * 2)
        endpoint = (
            f"/v2/stocks/{symbol}/bars"
            f"?timeframe={timeframe}"
            f"&start={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&end={end.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&limit={limit}"
            f"&feed=iex"
        )
        data = self._request("GET", self.data_url, endpoint)
        bars = []
        for b in data.get("bars", []):
            bars.append({
                "time":   b["t"],
                "open":   float(b["o"]),
                "high":   float(b["h"]),
                "low":    float(b["l"]),
                "close":  float(b["c"]),
                "volume": float(b["v"]),
            })
        # Fallback to sip feed if iex returns nothing
        if not bars:
            endpoint_sip = endpoint.replace("feed=iex", "feed=sip")
            data = self._request("GET", self.data_url, endpoint_sip)
            for b in data.get("bars", []):
                bars.append({
                    "time":   b["t"],
                    "open":   float(b["o"]),
                    "high":   float(b["h"]),
                    "low":    float(b["l"]),
                    "close":  float(b["c"]),
                    "volume": float(b["v"]),
                })
        return bars

    def get_latest_price(self, symbol):
        try:
            try:
                data = self._request(
                    "GET", self.data_url,
                    f"/v2/stocks/{symbol}/quotes/latest?feed=iex"
                )
            except:
                data = self._request(
                    "GET", self.data_url,
                    f"/v2/stocks/{symbol}/quotes/latest?feed=sip"
                )
            quote = data.get("quote", {})
            bid = float(quote.get("bp", 0))
            ask = float(quote.get("ap", 0))
            mid = (bid + ask) / 2 if bid and ask else 0
            return mid if mid > 0 else ask if ask > 0 else bid
        except Exception as e:
            log.error(f"Alpaca price error {symbol}: {e}")
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
        body = {
            "symbol":           symbol,
            "side":             side,
            "type":             "market",
            "time_in_force":    "day",
            "client_order_id":  client_order_id,
        }
        if notional:
            body["notional"] = str(round(notional, 2))
        elif qty:
            body["qty"] = str(qty)
        resp = self._request("POST", self.base_url, "/v2/orders", body=body)
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

    def close_position(self, symbol):
        try:
            return self._request("DELETE", self.base_url, f"/v2/positions/{symbol}")
        except Exception as e:
            log.error(f"Alpaca close position error {symbol}: {e}")
            return None
