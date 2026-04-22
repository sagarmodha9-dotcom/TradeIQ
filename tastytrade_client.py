"""
tastytrade_client.py — Tastytrade API client for options trading.
Uses Tastytrade's REST API with session token authentication.
Paper trading uses sandbox.tastytrade.com, live uses api.tastytrade.com.
"""
import requests
import json
from datetime import datetime, date, timedelta
from logger import log
import config

PAPER_URL = "https://api.tastytrade.com"
LIVE_URL  = "https://api.tastytrade.com"

class TastytradeClient:
    def __init__(self):
        self.base_url = LIVE_URL if config.IS_LIVE else PAPER_URL
        self.session_token = None
        self.account_number = None
        self._login()

    def _login(self):
        """Authenticate and get session token using remember-token if available."""
        try:
            username = config.TASTYTRADE_USERNAME
            password = config.TASTYTRADE_PASSWORD
            remember_token = getattr(config, 'TASTYTRADE_REMEMBER_TOKEN', '')
            if not username or not password:
                log.error("Tastytrade credentials not set in .env")
                return
            headers = {"Content-Type": "application/json"}
            body = {"login": username, "password": password, "remember-me": True}
            if remember_token:
                headers["Authorization"] = remember_token
            resp = requests.post(
                f"{self.base_url}/sessions",
                json=body,
                headers=headers,
                timeout=15
            )
            data = resp.json()
            self.session_token = data.get("data", {}).get("session-token")
            # Save new remember token if provided
            new_token = data.get("data", {}).get("remember-token")
            if new_token:
                self.remember_token = new_token
            if self.session_token:
                log.info("✅ Tastytrade connected")
                self._get_account()
            else:
                log.error(f"Tastytrade login failed: {data}")
        except Exception as e:
            log.error(f"Tastytrade login error: {e}")

    def _get_account(self):
        """Get first account number."""
        try:
            data = self._request("GET", "/customers/me/accounts")
            accounts = data.get("data", {}).get("items", [])
            if accounts:
                self.account_number = accounts[0].get("account", {}).get("account-number")
                log.info(f"Tastytrade account: {self.account_number}")
        except Exception as e:
            log.error(f"Tastytrade get_account error: {e}")

    def _request(self, method, endpoint, body=None, params=None):
        """Make authenticated API request."""
        if not self.session_token:
            self._login()
        headers = {
            "Authorization": self.session_token,
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}{endpoint}"
        try:
            resp = requests.request(
                method, url, headers=headers,
                json=body, params=params, timeout=15
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.error(f"Tastytrade API error {endpoint}: {e}")
            return {}

    def get_option_chain(self, symbol, expiry, option_type="call"):
        """Get option chain for a symbol and expiry date."""
        try:
            data = self._request("GET", f"/option-chains/{symbol}/nested")
            contracts = []
            items = data.get("data", {}).get("items", [])
            if not items:
                return []
            expirations = items[0].get("expirations", [])
            side = "call" if option_type == "call" else "put"
            for exp in expirations:
                if exp.get("expiration-date") != expiry:
                    continue
                strikes = exp.get("strikes", [])
                for strike in strikes:
                    contract_symbol = strike.get(side, "").strip()
                    if not contract_symbol:
                        continue
                    contracts.append({
                        "symbol": contract_symbol,
                        "strike_price": float(strike.get("strike-price", 0)),
                        "expiration_date": expiry,
                        "type": option_type,
                        "close_price": 0,
                    })
            return contracts
        except Exception as e:
            log.error(f"Tastytrade get_option_chain {symbol}: {e}")
            return []

    def get_option_price(self, contract_symbol):
        """Get current market price of an option contract via yfinance."""
        try:
            import yfinance as yf
            # Convert Tastytrade symbol to yfinance format
            # NVDA  260515C00190000 -> NVDA260515C00190000
            clean = contract_symbol.strip().replace(" ", "")
            ticker = yf.Ticker(clean)
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
            # Try info
            info = ticker.info
            price = info.get("regularMarketPrice") or info.get("ask") or 0
            return float(price)
        except Exception as e:
            log.warning(f"get_option_price {contract_symbol}: {e}")
            return 0.0

    def place_option_order(self, contract_symbol, qty=1, side="buy"):
        """Place an options order on Tastytrade."""
        if not config.IS_LIVE:
            log.info(f"[PAPER-TT] OPTIONS {side.upper()} {contract_symbol} x{qty}")
            return {
                "success":   True,
                "paper":     True,
                "symbol":    contract_symbol,
                "qty":       qty,
                "side":      side,
                "timestamp": datetime.utcnow().isoformat(),
            }
        if not self.account_number:
            log.error("Tastytrade: no account number")
            return {"success": False}
        try:
            action = "Buy to Open" if side.lower() == "buy" else "Sell to Close"
            tif = "Day"
            # Tastytrade requires limit orders for options
            # Get current mid price for limit order
            limit_price = None
            try:
                pos_data = self._request("GET", f"/option-chains/{contract_symbol.strip()}")
                if pos_data and "data" in pos_data:
                    bid = float(pos_data["data"].get("bid", 0) or 0)
                    ask = float(pos_data["data"].get("ask", 0) or 0)
                    if bid > 0 and ask > 0:
                        limit_price = round((bid + ask) / 2, 2)
            except:
                pass
            order = {
                "time-in-force": tif,
                "order-type": "Limit" if limit_price else "Market",
                "price": str(limit_price) if limit_price else None,
                "legs": [{
                    "instrument-type": "Equity Option",
                    "symbol": contract_symbol,
                    "quantity": qty,
                    "action": action,
                }]
            }
            if not limit_price:
                order.pop("price", None)
            data = self._request(
                "POST",
                f"/accounts/{self.account_number}/orders",
                body=order
            )
            order_id = data.get("data", {}).get("order", {}).get("id")
            if order_id:
                log.info(f"Tastytrade order: {action} {contract_symbol} x{qty} — ID {order_id}")
                return {"success": True, "order_id": order_id, "symbol": contract_symbol}
            log.error(f"Tastytrade order failed: {data}")
            return {"success": False}
        except Exception as e:
            log.error(f"Tastytrade place_option_order {contract_symbol}: {e}")
            return {"success": False, "error": str(e)}

    def get_account_balance(self):
        """Get live account net liquidation value."""
        try:
            data = self._request("GET", f"/accounts/{self.account_number}/balances")
            bal = data.get("data", {})
            net_liq = float(bal.get("net-liquidating-value", 0))
            return net_liq
        except Exception as e:
            log.error(f"Tastytrade get_account_balance: {e}")
            return 0.0

    def get_option_market_price(self, symbol):
        """Get live market price for an option contract."""
        try:
            # Clean symbol for API
            clean = symbol.strip().replace(" ", "+")
            data = self._request("GET", f"/option-chains/{clean}/nested")
            if data and "data" in data:
                items = data["data"].get("items", [])
                if items:
                    return float(items[0].get("mid-price", 0) or 0)
        except Exception as e:
            pass
        try:
            # Try market data endpoint
            encoded = symbol.strip().replace("  ", " ")
            data = self._request("GET", f"/market-data/options?symbols[]={encoded}")
            if data and "data" in data:
                items = data["data"].get("items", [])
                for item in items:
                    mid = (float(item.get("bid", 0)) + float(item.get("ask", 0))) / 2
                    if mid > 0:
                        return mid
        except Exception as e:
            pass
        return 0.0

    def get_positions(self):
        """Get all open option positions."""
        try:
            data = self._request("GET", f"/accounts/{self.account_number}/positions")
            positions = data.get("data", {}).get("items", [])
            return [p for p in positions if p.get("instrument-type") == "Equity Option"]
        except Exception as e:
            log.error(f"Tastytrade get_positions: {e}")
            return []

    def get_balance(self):
        """Get account balance."""
        try:
            data = self._request("GET", f"/accounts/{self.account_number}/balances")
            return float(data.get("data", {}).get("cash-balance", 0))
        except Exception as e:
            log.error(f"Tastytrade get_balance: {e}")
            return 0.0
