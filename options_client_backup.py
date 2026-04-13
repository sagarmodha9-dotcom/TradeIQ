import requests
from datetime import datetime, date, timedelta
from logger import log
import config

class OptionsClient:
    def __init__(self):
        self.api_key    = config.ALPACA_API_KEY
        self.secret_key = config.ALPACA_SECRET_KEY
        self.base_url   = config.ALPACA_BASE_URL
        self.headers    = {
            "APCA-API-KEY-ID":     self.api_key,
            "APCA-API-SECRET-KEY": self.secret_key,
            "Content-Type":        "application/json",
        }

    def _request(self, method, endpoint, body=None, params=None):
        url = f"{self.base_url}{endpoint}"
        try:
            r = requests.request(method, url, headers=self.headers, json=body, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error(f"Options API error {endpoint}: {e}")
            return {}

    def get_option_chain(self, symbol, expiry_date=None, option_type=None):
        """Get option chain for a symbol."""
        if not expiry_date:
            # Default to nearest Friday (weekly options)
            today = date.today()
            days_to_friday = (4 - today.weekday()) % 7
            if days_to_friday == 0:
                days_to_friday = 7
            expiry_date = (today + timedelta(days=days_to_friday)).strftime("%Y-%m-%d")
        params = {
            "feed":            "indicative",
            "expiration_date": expiry_date,
            "limit":           100,
        }
        if option_type:
            params["type"] = option_type
        data = self._request("GET", f"/v2/options/contracts", params={
            "underlying_symbols": symbol,
            "expiration_date":    expiry_date,
            "limit":              100,
        })
        return data.get("option_contracts", [])

    def get_option_quote(self, symbol):
        """Get latest quote for an option contract."""
        try:
            data = self._request("GET", f"/v2/options/snapshots/{symbol}")
            return data.get("snapshots", {}).get(symbol, {})
        except Exception as e:
            log.error(f"Option quote error {symbol}: {e}")
            return {}

    def find_best_option(self, symbol, direction, budget=250):
        """Find best call or put contract for a given direction and budget."""
        try:
            option_type = "call" if direction == "BUY" else "put"
            # Try 0DTE first, then weekly
            for days_out in [0, 7, 14]:
                target = date.today() + timedelta(days=days_out)
                # Skip weekends
                if target.weekday() == 5: target += timedelta(days=2)
                if target.weekday() == 6: target += timedelta(days=1)
                expiry = target.strftime("%Y-%m-%d")
                contracts = self.get_option_chain(symbol, expiry, option_type)
                if contracts:
                    # Filter by price range
                    affordable = [c for c in contracts if c.get("close_price", 999) and
                                  float(c.get("close_price", 0) or 0) * 100 <= budget]
                    if affordable:
                        # Pick ATM (closest to current price)
                        return sorted(affordable, key=lambda c: abs(float(c.get("strike_price", 0))))[0]
            return None
        except Exception as e:
            log.error(f"find_best_option error: {e}")
            return None

    def place_option_order(self, contract_symbol, qty, side="buy"):
        """Place an options order."""
        if not config.IS_LIVE:
            log.info(f"[PAPER] OPTIONS {side.upper()} {contract_symbol} x{qty}")
            return {
                "success":         True,
                "paper":           True,
                "symbol":          contract_symbol,
                "qty":             qty,
                "side":            side,
                "timestamp":       datetime.utcnow().isoformat(),
            }
        body = {
            "symbol":        contract_symbol,
            "qty":           str(qty),
            "side":          side,
            "type":          "market",
            "time_in_force": "day",
        }
        data = self._request("POST", "/v2/orders", body=body)
        if data.get("id"):
            log.info(f"OPTIONS {side.upper()} {contract_symbol} x{qty} — order {data['id']}")
            return {"success": True, "paper": False, "order_id": data["id"],
                    "symbol": contract_symbol, "qty": qty}
        return {"success": False}

    def get_option_positions(self):
        """Get all open option positions."""
        try:
            data = self._request("GET", "/v2/positions")
            if isinstance(data, list):
                return [p for p in data if p.get("asset_class") == "us_option"]
            return []
        except Exception as e:
            log.error(f"get_option_positions error: {e}")
            return []

    def close_option_position(self, contract_symbol):
        """Close an option position."""
        if not config.IS_LIVE:
            log.info(f"[PAPER] CLOSE OPTION {contract_symbol}")
            return {"success": True}
        data = self._request("DELETE", f"/v2/positions/{contract_symbol}")
        return {"success": True} if data else {"success": False}
