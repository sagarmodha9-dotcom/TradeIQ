"""
options_client.py — IBKR-based options trading (paper and live)
All contract lookup, pricing, and order placement goes through IBKR.
"""
from datetime import datetime, date, timedelta
from logger import log
import config

class OptionsClient:
    def __init__(self, ibkr_client=None):
        self.ibkr = ibkr_client
        # Use Tastytrade for options
        try:
            from tastytrade_client import TastytradeClient
            self.tt = TastytradeClient()
        except Exception as e:
            from logger import log
            log.warning(f"Tastytrade not available: {e}")
            self.tt = None

    def find_best_option(self, symbol, direction, budget=250):
        """Find best option contract via IBKR — 30-45 day expiry, 1-6% OTM."""
        if not self.ibkr:
            log.warning("OptionsClient: no IBKR client available")
            return None
        try:
            option_type = direction
            current_price = self.ibkr.get_latest_price(symbol)
            if current_price <= 0:
                log.warning(f"Options {symbol}: could not get current price")
                return None

            for days_out in [7, 14, 21, 30]:
                target = date.today() + timedelta(days=days_out)
                if target.weekday() == 5: target += timedelta(days=2)
                if target.weekday() == 6: target += timedelta(days=1)
                expiry = target.strftime("%Y-%m-%d")

                # Use Tastytrade for chain lookup, fallback to IBKR
                if self.tt:
                    contracts = self.tt.get_option_chain(symbol, expiry, option_type)
                elif self.ibkr:
                    contracts = self.ibkr.get_option_chain(symbol, expiry, option_type)
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

                    if not (0.005 <= pct_otm <= 0.10):
                        continue

                    # Get live price from Tastytrade or IBKR
                    if self.tt:
                        price = self.tt.get_option_price(c.get("symbol", ""))
                    elif self.ibkr:
                        price = self.ibkr.get_option_price(symbol, expiry, strike, option_type)
                    else:
                        price = 0.0
                    cost = price * 100
                    if price < 0.10 or cost > budget:
                        continue

                    c["close_price"] = price
                    c["cost"] = cost
                    candidates.append(c)

                if candidates:
                    best = sorted(candidates, key=lambda c: float(c.get("close_price", 0)), reverse=True)[0]
                    log.info(f"Options {symbol}: {option_type} strike={best['strike_price']} expiry={expiry} price=${best['close_price']:.2f}")
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
        try:
            import yfinance as yf
            ticker = yf.Ticker(contract_symbol)
            hist = ticker.history(period="1d", interval="1m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e:
            log.warning(f"get_option_price {contract_symbol}: {e}")
        return 0.0
