"""
options_client.py — Clean options trading via Tastytrade
- Gets full option chain once per symbol
- Uses delta approximation to filter (no per-contract API calls)
- Fetches price ONLY for the single winning contract
"""
from datetime import datetime, date, timedelta
from logger import log
import config


class OptionsClient:
    def __init__(self, alpaca_client=None, ibkr_client=None):
        self.alpaca = alpaca_client
        self.ibkr   = ibkr_client
        try:
            from tastytrade_client import TastytradeClient
            self.tt = TastytradeClient()
        except Exception as e:
            log.warning(f"Tastytrade not available: {e}")
            self.tt = None

    def _get_current_price(self, symbol):
        if self.alpaca:
            return self.alpaca.get_latest_price(symbol)
        if self.ibkr:
            return self.ibkr.get_latest_price(symbol)
        return 0.0

    def _approx_delta(self, pct_otm, days_to_expiry):
        """Approximate delta without any API call using moneyness + time."""
        if days_to_expiry <= 0:
            return 0.0
        # Simple heuristic: closer to ATM and more time = higher delta
        if pct_otm < 0.01:
            return 0.50
        elif pct_otm < 0.02:
            return 0.45 if days_to_expiry > 14 else 0.38
        elif pct_otm < 0.03:
            return 0.40 if days_to_expiry > 14 else 0.32
        elif pct_otm < 0.05:
            return 0.35 if days_to_expiry > 14 else 0.25
        elif pct_otm < 0.08:
            return 0.28 if days_to_expiry > 14 else 0.18
        else:
            return 0.10

    def find_best_option(self, symbol, direction, budget=1000):
        """
        Find best option contract:
        1. Get available expiry dates from Tastytrade (one API call)
        2. Get option chain for each expiry (one API call per expiry)
        3. Filter by OTM% and delta approximation — zero extra API calls
        4. Fetch price only for the single best candidate (one API call)
        """
        if not self.tt:
            log.warning("OptionsClient: Tastytrade not available")
            return None

        try:
            option_type = direction
            current_price = self._get_current_price(symbol)
            if not current_price or current_price <= 0:
                log.warning(f"Options {symbol}: could not get current price")
                return None

            # Step 1: Get real available expiry dates (one API call)
            available_expiries = []
            try:
                data = self.tt._request("GET", f"/option-chains/{symbol}/nested")
                items = (data or {}).get("data", {}).get("items", [])
                if items:
                    for exp in items[0].get("expirations", []):
                        ed = exp.get("expiration-date", "")
                        if ed:
                            days = (datetime.strptime(ed, "%Y-%m-%d").date() - date.today()).days
                            if 8 <= days <= 45:
                                available_expiries.append((ed, days))
            except Exception as e:
                log.warning(f"Options {symbol}: could not get expiries: {e}")
                return None

            if not available_expiries:
                log.info(f"Options {symbol}: no suitable expiries found")
                return None

            # Step 2+3: For each expiry, filter contracts by OTM% and delta
            best_candidates = []
            for expiry, days_out in available_expiries:
                try:
                    contracts = self.tt.get_option_chain(symbol, expiry, option_type)
                except Exception:
                    continue
                if not contracts:
                    continue

                for c in contracts:
                    strike = float(c.get("strike_price", 0))
                    if strike <= 0:
                        continue

                    if option_type == "call":
                        pct_otm = (strike - current_price) / current_price
                    else:
                        pct_otm = (current_price - strike) / current_price

                    # Only consider 0.5% to 8% OTM
                    if not (0.005 <= pct_otm <= 0.08):
                        continue

                    # Approximate delta — no API call
                    delta = self._approx_delta(pct_otm, days_out)
                    if not (0.25 <= delta <= 0.55):
                        continue

                    c["delta"]           = delta
                    c["expiration_date"] = expiry
                    c["days_out"]        = days_out
                    best_candidates.append(c)

            if not best_candidates:
                log.info(f"Options {symbol}: no suitable {option_type} contract found")
                return None

            # Pick candidate with delta closest to 0.40
            best = sorted(best_candidates, key=lambda c: abs(c.get("delta", 0.35) - 0.40))[0]

            # Step 4: Fetch price for the single winner only (one API call)
            price = self.tt.get_option_price(best.get("symbol", ""))
            if price <= 0:
                log.info(f"Options {symbol}: winner price unavailable (market closed?)")
                return None

            cost = price * 100
            if cost > budget:
                log.info(f"Options {symbol}: winner cost ${cost:.0f} exceeds budget ${budget:.0f}")
                return None

            best["close_price"] = price
            best["cost"]        = cost
            log.info(f"Options {symbol}: {option_type} strike={best['strike_price']} expiry={best['expiration_date']} price=${price:.2f} delta={best['delta']:.2f}")
            return best

        except Exception as e:
            log.error(f"find_best_option {symbol}: {e}")
            return None

    def place_option_order(self, contract_symbol, qty=1, side="buy"):
        """Place option order via Tastytrade."""
        if self.tt:
            return self.tt.place_option_order(contract_symbol, qty, side)
        log.error("OptionsClient: no broker available for options orders")
        return {"success": False, "error": "no broker"}

    def get_option_delta(self, symbol, expiry, strike, option_type):
        """Return approximate delta — no API call needed."""
        try:
            price = self._get_current_price(symbol)
            if price <= 0:
                return 0.35
            if option_type == "call":
                pct_otm = (strike - price) / price
            else:
                pct_otm = (price - strike) / price
            days = max((datetime.strptime(expiry, "%Y-%m-%d").date() - date.today()).days, 1)
            return self._approx_delta(pct_otm, days)
        except:
            return 0.35

    def get_option_price(self, contract_symbol):
        """Get current price for an open position — delegates to Tastytrade."""
        if self.tt:
            return self.tt.get_option_price(contract_symbol)
        return 0.0

