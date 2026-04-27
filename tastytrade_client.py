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
            # Auto re-login on 401 session expiry
            if resp.status_code == 401:
                log.warning("Tastytrade session expired — re-logging in")
                self._login()
                headers["Authorization"] = self.session_token
                resp = requests.request(
                    method, url, headers=headers,
                    json=body, params=params, timeout=15
                )
            if resp.status_code == 422:
                log.error(f"Tastytrade 422 error: {resp.text}")
                return resp.json()
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if "404" in str(e):
                log.debug(f"Tastytrade 404 (after-hours expected): {endpoint}")
            else:
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
        """Get current market price via DXFeed websocket — works during and after hours."""
        try:
            sym = contract_symbol.strip()
            # Auto-convert contract symbol to streamer format if needed
            # e.g. "SOFI  260522C00019000" -> ".SOFI260522C19"
            streamer_sym = sym
            if not sym.startswith("."):
                try:
                    import re
                    parts = sym.replace("  "," ").split()
                    underlying = parts[0] if parts else ""
                    m = re.search(r"(\d{6})([CP])(\d{8})", sym.replace(" ",""))
                    if m and underlying:
                        date_str = m.group(1)
                        cp = m.group(2)
                        strike_raw = int(m.group(3))
                        strike = strike_raw / 1000
                        strike_str = str(int(strike)) if strike == int(strike) else str(strike)
                        streamer_sym = f".{underlying}{date_str}{cp}{strike_str}"
                except:
                    streamer_sym = sym
            # Try DXFeed Quote first
            import websocket, threading, time as _time
            token_resp = self._request("GET", "/api-quote-tokens")
            token = token_resp.get("data", {}).get("token", "")
            if not token:
                return 0.0
            url = "wss://tasty-openapi-ws.dxfeed.com/realtime"
            result = {}
            done = threading.Event()
            # Convert contract symbol to streamer format BEFORE defining on_open
            streamer_sym = sym
            try:
                import re as _re2
                if not sym.startswith("."):
                    parts2 = sym.replace("  "," ").split()
                    und2 = parts2[0] if parts2 else ""
                    m2 = _re2.search(r"(\d{6})([CP])(\d{8})", sym.replace(" ",""))
                    if m2 and und2:
                        sr = int(m2.group(3)) / 1000
                        ss = str(int(sr)) if sr == int(sr) else str(sr)
                        streamer_sym = f".{und2}{m2.group(1)}{m2.group(2)}{ss}"
            except:
                pass

            def on_open(ws):
                ws.send(__import__('json').dumps({"type":"SETUP","channel":0,"version":"0.1","minVersion":"0.1","keepaliveTimeout":60,"acceptKeepaliveTimeout":60}))
                ws.send(__import__('json').dumps({"type":"AUTH","channel":0,"token":token}))
                ws.send(__import__('json').dumps({"type":"CHANNEL_REQUEST","channel":1,"service":"FEED","parameters":{"contract":"AUTO"}}))
                ws.send(__import__('json').dumps({"type":"FEED_SETUP","channel":1,"acceptAggregationPeriod":10,"acceptDataFormat":"COMPACT",
                    "acceptEventFields":{"Quote":["eventType","eventSymbol","bidPrice","askPrice"]}}))
                ws.send(__import__('json').dumps({"type":"FEED_SUBSCRIPTION","channel":1,"reset":True,
                    "add":[{"type":"Quote","symbol":streamer_sym}]}))

            def on_message(ws, msg):
                data = __import__('json').loads(msg)
                if data.get("type") == "FEED_DATA":
                    fields = data.get("data",[])
                    if fields and len(fields) >= 2:
                        values = fields[1]
                        if isinstance(values, list) and len(values) >= 4:
                            bid = float(values[2] or 0)
                            ask = float(values[3] or 0)
                            if bid > 0 and ask > 0:
                                result["price"] = round((bid + ask) / 2, 4)
                            elif ask > 0:
                                result["price"] = ask
                    ws.close()
                    done.set()

            def on_error(ws, err):
                done.set()

            ws_app = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_error=on_error)
            t = threading.Thread(target=ws_app.run_forever)
            t.daemon = True
            t.start()
            done.wait(timeout=8)
            if result.get("price", 0) > 0:
                return result["price"]
        except Exception as e:
            log.debug(f"get_option_price {contract_symbol}: {e}")
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
            # Get market price for limit order if not already fetched
            if not limit_price:
                try:
                    mkt = self.get_option_price(contract_symbol.strip())
                    if mkt and mkt > 0:
                        raw = mkt * 1.02
                        limit_price = round(round(raw / 0.05) * 0.05, 2)  # Round to $0.05 increment
                except:
                    pass

            if not limit_price or limit_price <= 0:
                log.error(f"Tastytrade: could not get price for {contract_symbol}")
                return {"success": False, "error": "no price available"}
            # Check buying power before attempting order
            try:
                buying_power = self.get_buying_power()
                order_cost = round(limit_price * 100, 2)
                if buying_power < order_cost:
                    log.warning(f"Tastytrade: insufficient buying power ${buying_power:.2f} for ${order_cost:.2f} — skipping")
                    return {"success": False, "error": "insufficient_buying_power"}
            except:
                pass
            # Check buying power before attempting order
            try:
                bal = self.get_account_balance()
                buying_power = float(bal) if bal else 0
                order_cost = round(limit_price * 100, 2)
                if buying_power < order_cost:
                    log.warning(f"Tastytrade: insufficient buying power ${buying_power:.2f} for ${order_cost:.2f} order — skipping")
                    return {"success": False, "error": "insufficient_buying_power"}
            except:
                pass

            price_effect = "Credit" if "Sell" in action else "Debit"
            order = {
                "time-in-force": tif,
                "order-type": "Limit",
                "price": str(round(limit_price, 2)),
                "price-effect": price_effect,
                "legs": [{
                    "instrument-type": "Equity Option",
                    "symbol": contract_symbol.strip(),
                    "quantity": str(qty),
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
                # Wait up to 10s for fill confirmation before logging as filled
                import time as _t
                for _ in range(5):
                    _t.sleep(2)
                    try:
                        _o = self._request("GET", f"/accounts/{self.account_number}/orders/{order_id}")
                        _status = _o.get("data", {}).get("status", "")
                        if _status == "Filled":
                            log.info(f"✅ Tastytrade FILLED: {contract_symbol} — ID {order_id}")
                            return {"success": True, "filled": True, "order_id": order_id, "symbol": contract_symbol}
                        elif _status in ("Cancelled", "Rejected"):
                            log.warning(f"Tastytrade order {_status}: {contract_symbol}")
                            return {"success": False, "filled": False, "status": _status}
                        log.info(f"Tastytrade order {_status}: {contract_symbol} — waiting...")
                    except:
                        pass
                # Still working after 10s — return pending, do not log as filled
                log.warning(f"⏳ Tastytrade order still pending after 10s: {contract_symbol} — not logging as filled")
                return {"success": True, "filled": False, "order_id": order_id, "symbol": contract_symbol}
            # Check for closing_only restriction
            errors = data.get("error", {}).get("errors", [])
            for e in errors:
                if e.get("code") == "closing_only":
                    log.warning(f"Tastytrade: {contract_symbol} is closing only — skipping")
                    return {"success": False, "error": "closing_only"}
            log.error(f"Tastytrade order failed: {data}")
            # Log detailed error for debugging
            errors = data.get("error", {})
            if errors:
                log.error(f"Tastytrade order error detail: {errors}")
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

    def get_buying_power(self):
        """Get available buying power for options."""
        try:
            data = self._request("GET", f"/accounts/{self.account_number}/balances")
            bal = data.get("data", {})
            # Use derivative-buying-power for options
            bp = float(bal.get("derivative-buying-power", 0) or 0)
            if bp <= 0:
                bp = float(bal.get("cash-available-to-withdraw", 0) or 0)
            if bp <= 0:
                bp = float(bal.get("net-liquidating-value", 0) or 0)
            return bp
        except Exception as e:
            log.error(f"Tastytrade get_buying_power: {e}")
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

    def get_real_greeks(self, streamer_symbol):
        """Get real Greeks via DXFeed websocket. Returns dict with delta, gamma, theta, vega, iv or None."""
        try:
            import websocket, threading, time
            token_resp = self._request("GET", "/api-quote-tokens")
            token = token_resp.get("data", {}).get("token", "")
            if not token:
                return None
            url = "wss://tasty-openapi-ws.dxfeed.com/realtime"
            result = {}
            done = threading.Event()

            def on_open(ws):
                ws.send(json.dumps({"type": "SETUP", "channel": 0, "version": "0.1", "minVersion": "0.1", "keepaliveTimeout": 60, "acceptKeepaliveTimeout": 60}))
                ws.send(json.dumps({"type": "AUTH", "channel": 0, "token": token}))
                ws.send(json.dumps({"type": "CHANNEL_REQUEST", "channel": 1, "service": "FEED", "parameters": {"contract": "AUTO"}}))
                ws.send(json.dumps({"type": "FEED_SETUP", "channel": 1, "acceptAggregationPeriod": 10, "acceptDataFormat": "COMPACT",
                    "acceptEventFields": {"Greeks": ["eventType","eventSymbol","delta","gamma","theta","vega","impliedVolatility","rho"]}
                }))
                ws.send(json.dumps({"type": "FEED_SUBSCRIPTION", "channel": 1, "reset": True,
                    "add": [{"type": "Greeks", "symbol": streamer_symbol}]
                }))

            def on_message(ws, msg):
                data = json.loads(msg)
                if data.get("type") == "FEED_DATA":
                    fields = data.get("data", [])
                    if fields and len(fields) >= 2:
                        values = fields[1]
                        if isinstance(values, list) and len(values) >= 7:
                            result["delta"] = float(values[2])
                            result["gamma"] = float(values[3])
                            result["theta"] = float(values[4])
                            result["vega"]  = float(values[5])
                            result["iv"]    = float(values[6]) if len(values) > 6 else 0.0
                    ws.close()
                    done.set()

            def on_error(ws, err):
                done.set()

            ws_app = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_error=on_error)
            t = threading.Thread(target=ws_app.run_forever)
            t.daemon = True
            t.start()
            done.wait(timeout=8)
            return result if result else None
        except Exception as e:
            log.debug(f"get_real_greeks {streamer_symbol}: {e}")
            return None

    def get_streamer_symbol(self, symbol, expiry, strike, option_type):
        """Get DXFeed streamer symbol from option chain."""
        try:
            data = self._request("GET", f"/option-chains/{symbol}/nested")
            items = data.get("data", {}).get("items", [])
            if not items:
                return None
            side_key = "call-streamer-symbol" if option_type == "call" else "put-streamer-symbol"
            for exp in items[0].get("expirations", []):
                if exp.get("expiration-date") != expiry:
                    continue
                for s in exp.get("strikes", []):
                    if float(s.get("strike-price", 0)) == float(strike):
                        return s.get(side_key, "")
            return None
        except Exception as e:
            log.debug(f"get_streamer_symbol {symbol}: {e}")
            return None

