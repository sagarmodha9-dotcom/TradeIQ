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
        self.ibkr   = None  # IBKR removed — Alpaca only
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
                            if 21 <= days <= 45:
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

                    # Prefer slightly ITM to modest OTM for higher win probability
                    if not (-0.03 <= pct_otm <= 0.04):
                        continue

                    # Try real Greeks first, fall back to approximation
                    streamer = c.get("symbol", "").strip()
                    # Convert contract symbol to streamer format if needed
                    greeks = None
                    # Use approximation for filtering (fast), real Greeks for winner only
                    delta = self._approx_delta(pct_otm, days_out)
                    if not (0.45 <= delta <= 0.60):
                        continue

                    c["delta"]           = delta
                    c["expiration_date"] = expiry
                    c["days_out"]        = days_out
                    best_candidates.append(c)

            if not best_candidates:
                log.info(f"Options {symbol}: no suitable {option_type} contract found")
                return None

            # Pick candidate with delta closest to 0.52 for better win probability
            best = sorted(best_candidates, key=lambda c: abs(c.get("delta", 0.52) - 0.52))[0]

            # Step 4: Fetch real Greeks + price for winner only
            # Get streamer symbol from chain data
            expiry    = best.get("expiration_date", "")
            strike    = float(best.get("strike_price", 0))
            streamer  = self.tt.get_streamer_symbol(symbol, expiry, strike, option_type)
            if streamer:
                greeks = self.tt.get_real_greeks(streamer)
                if greeks:
                    best["delta"] = greeks.get("delta", best["delta"])
                    best["gamma"] = greeks.get("gamma", 0)
                    best["theta"] = greeks.get("theta", 0)
                    best["vega"]  = greeks.get("vega",  0)
                    best["iv"]    = greeks.get("iv",    0)
                    log.info(f"Options {symbol}: real Greeks — delta={best['delta']:.3f} theta={best['theta']:.3f} iv={best['iv']:.3f}")

                    # Profit-quality filter: avoid low-probability / heavy-theta contracts
                    real_delta = abs(float(best.get("delta", 0) or 0))
                    real_theta = float(best.get("theta", 0) or 0)
                    real_iv = float(best.get("iv", 0) or 0)

                    if not (0.45 <= real_delta <= 0.65):
                        log.info(f"Options {symbol}: reject delta {real_delta:.3f} outside 0.45-0.65")
                        return None

                    if real_theta < -0.45:
                        log.info(f"Options {symbol}: reject theta {real_theta:.3f} too negative")
                        return None

                    # IV filter: avoid dead contracts and overpriced contracts
                    if real_iv < 0.25:
                        log.info(f"Options {symbol}: reject IV {real_iv:.1%} too low — weak movement")
                        return None

                    if real_iv > 0.70:
                        log.info(f"Options {symbol}: reject IV {real_iv:.1%} too high — overpriced")
                        return None

                    # Liquidity filter: avoid wide spreads / dead contracts when data is available
                    bid = float(best.get("bid", best.get("bid-price", 0)) or 0)
                    ask = float(best.get("ask", best.get("ask-price", 0)) or 0)
                    volume = int(float(best.get("volume", 0) or 0))
                    open_interest = int(float(best.get("open_interest", best.get("open-interest", 0)) or 0))

                    if bid > 0 and ask > 0:
                        spread = (ask - bid) / max(ask, 0.01)
                        if spread > 0.18:
                            log.info(f"Options {symbol}: reject wide spread ({spread:.1%})")
                            return None

                    if volume > 0 and volume < 50:
                        log.info(f"Options {symbol}: reject low option volume ({volume})")
                        return None

                    if open_interest > 0 and open_interest < 100:
                        log.info(f"Options {symbol}: reject low open interest ({open_interest})")
                        return None

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
        # HARD SAFETY: Never send a Tastytrade sell unless broker confirms we own the contract.
        # Prevents accidental Sell-to-Open / uncovered option rejection on Limited accounts.
        try:
            if str(side).lower() in ("sell", "sell_to_close", "stc"):
                log.warning(f"SELL ATTEMPT DETECTED: {symbol}")

                broker_positions = self.tt.get_positions() if getattr(self, "tt", None) else []
                
                owned = False
                actual_qty = 0

                for bp in broker_positions or []:
                    if str(symbol) in str(bp.get("symbol", "")):
                        actual_qty = int(float(bp.get("quantity", 0) or 0))
                        if actual_qty > 0:
                            owned = True
                            break

                if not owned:
                    log.error(f"BLOCKED OPTION SELL: {symbol} — no ownership confirmed")
                    return {"success": False, "blocked": True}

                qty = min(int(qty), actual_qty)

                broker_positions = self.tt.get_positions() if getattr(self, "tt", None) else []
                broker_pos = next(
                    (
                        bp for bp in (broker_positions or [])
                        if str(bp.get("symbol", "")).strip() == str(symbol).strip()
                    ),
                    None
                )

                actual_qty = int(float((broker_pos or {}).get("quantity", 0) or 0))

                if not broker_pos or actual_qty <= 0:
                    log.error(f"BLOCKED OPTION SELL: no matching Tastytrade position for {symbol}; refusing to send sell order")
                    return {
                        "success": False,
                        "filled": False,
                        "blocked": True,
                        "reason": "no broker position; prevented possible sell-to-open",
                        "symbol": symbol,
                    }

                qty = min(int(float(qty or 1)), actual_qty)

        except Exception as e:
            log.error(f"BLOCKED OPTION SELL: broker position verification failed for {symbol}: {e}")
            return {
                "success": False,
                "filled": False,
                "blocked": True,
                "reason": f"broker verification failed: {e}",
                "symbol": symbol,
            }

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

    def get_option_price(self, contract_symbol):
        """Get current price for an open position — delegates to Tastytrade."""
        if self.tt:
            return self.tt.get_option_price(contract_symbol)
        return 0.0

