"""
options_client.py — IBKR-based options trading (paper and live)
All contract lookup, pricing, and order placement goes through IBKR.
"""
from datetime import datetime, date, timedelta
from logger import log
import config

class OptionsClient:
    def __init__(self, alpaca_client=None, ibkr_client=None):
        self.alpaca = alpaca_client
        self.ibkr = ibkr_client
        # Use Tastytrade for options
        try:
            from tastytrade_client import TastytradeClient
            self.tt = TastytradeClient()
        except Exception as e:
            from logger import log
            log.warning(f"Tastytrade not available: {e}")
            self.tt = None

    def get_option_delta(self, symbol, expiry, strike, option_type):
        """Estimate option delta based on moneyness."""
        try:
            current = self.alpaca.get_latest_price(symbol) if hasattr(self, "alpaca") and self.alpaca else (self.ibkr.get_latest_price(symbol) if self.ibkr else 0)
            if current <= 0:
                return 0.35
            K = float(strike)
            moneyness = current / K
            if option_type == "call":
                if moneyness >= 1.05: return 0.70
                elif moneyness >= 1.02: return 0.55
                elif moneyness >= 0.99: return 0.45
                elif moneyness >= 0.96: return 0.32
                elif moneyness >= 0.93: return 0.18
                else: return 0.08
            else:
                if moneyness <= 0.95: return 0.70
                elif moneyness <= 0.98: return 0.55
                elif moneyness <= 1.01: return 0.45
                elif moneyness <= 1.04: return 0.32
                elif moneyness <= 1.07: return 0.18
                else: return 0.08
        except:
            return 0.35

    def find_best_option(self, symbol, direction, budget=250):
        """Find best option contract via Tastytrade — 30-45 day expiry, 1-6% OTM."""
        try:
            option_type = direction
            # Use Alpaca for price lookup, fallback to ibkr
            if self.alpaca:
                current_price = self.alpaca.get_latest_price(symbol)
            elif self.ibkr:
                current_price = self.ibkr.get_latest_price(symbol)
            else:
                current_price = 0
            if current_price <= 0:
                log.warning(f"Options {symbol}: could not get current price")
                return None

            # Get real available expiries from Tastytrade
            available_expiries = []
            if self.tt:
                try:
                    data = self.tt._request("GET", f"/option-chains/{symbol}/nested")
                    items = data.get("data", {}).get("items", [])
                    if items:
                        for exp in items[0].get("expirations", []):
                            ed = exp.get("expiration-date", "")
                            if ed:
                                from datetime import datetime
                                days = (datetime.strptime(ed, "%Y-%m-%d").date() - date.today()).days
                                if 5 <= days <= 45:
                                    available_expiries.append(ed)
                except: pass

            if not available_expiries:
                available_expiries = []
                for days_out in [7, 14, 21, 30]:
                    target = date.today() + timedelta(days=days_out)
                    if target.weekday() == 5: target += timedelta(days=2)
                    if target.weekday() == 6: target += timedelta(days=1)
                    available_expiries.append(target.strftime("%Y-%m-%d"))

            for expiry in available_expiries:
                # Use Tastytrade for chain lookup
                if self.tt:
                    contracts = self.tt.get_option_chain(symbol, expiry, option_type)
                else:
                    contracts = []
                if not contracts:
                    continue

                candidates = []
                for c in contracts:
                    strike = float(c.get("strike_price", 0))
                    if strike <= 0:
                        continue
                    if option_type == "call":
                        pct_otm = (strike - current_price) / current_price
                    else:
                        pct_otm = (current_price - strike) / current_price

                    if not (0.005 <= pct_otm <= 0.08):
                        continue

                    # Get live price from Tastytrade or IBKR
                    if self.tt:
                        price = self.tt.get_option_price(c.get("symbol", ""))
                    elif self.ibkr:
                        price = self.ibkr.get_option_price(symbol, expiry, strike, option_type)
                    else:
                        price = 0.0
                    cost = price * 100
                    if price <= 0 or cost <= 0:
                        continue
                    if cost > budget:
                        continue

                    # Delta filter — target 0.25-0.55 (sweet spot)
                    delta = self.get_option_delta(symbol, expiry, strike, option_type)
                    if not (0.25 <= delta <= 0.55):
                        continue
                    c["delta"] = delta
                    c["close_price"] = price
                    c["cost"] = cost
                    candidates.append(c)

                if candidates:
                    # Pick candidate with delta closest to 0.40 (ideal sweet spot)
                    best = sorted(candidates, key=lambda c: abs(c.get("delta", 0.35) - 0.40))[0]
                    log.info(f"Options {symbol}: {option_type} strike={best['strike_price']} expiry={expiry} price=${best['close_price']:.2f} delta={best.get('delta',0):.2f}")
                    return best

            log.info(f"Options {symbol}: no suitable {option_type} contract found")
            return None
        except Exception as e:
            log.error(f"find_best_option {symbol}: {e}")
            return None

    def place_option_order(self, contract_symbol, qty=1, side="buy"):
        """Place option order via IBKR (paper or live)."""
        # Use Tastytrade for all options orders (paper and live)
        if self.tt:
            return self.tt.place_option_order(contract_symbol, qty, side)
        # Fallback to IBKR
        if not config.IS_LIVE:
            log.info(f"[PAPER] OPTIONS {side.upper()} {contract_symbol} x{qty}")
            return {
                "success":   True,
                "paper":     True,
                "symbol":    contract_symbol,
                "qty":       qty,
                "side":      side,
                "timestamp": datetime.utcnow().isoformat(),
            }
        if not self.ibkr:
            log.error("OptionsClient: no client available")
            return {"success": False}
        try:
            action = "BUY" if side.lower() == "buy" else "SELL"
            result = self.ibkr.place_option_order(contract_symbol, action, qty)
            if result:
                return {"success": True, "symbol": contract_symbol, "qty": qty,
                        "side": side, "order_id": result.get("id")}
            return {"success": False}
        except Exception as e:
            log.error(f"place_option_order {contract_symbol}: {e}")
            return {"success": False, "error": str(e)}

    def get_option_price(self, contract_symbol):
        """Get current price of an open option position."""
        # Try Tastytrade first
        if self.tt:
            try:
                price = self.tt.get_option_price(contract_symbol)
                if price > 0:
                    return price
            except Exception as e:
                log.warning(f"get_option_price TT {contract_symbol}: {e}")
        return 0.0
