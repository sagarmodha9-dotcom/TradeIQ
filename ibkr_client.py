import re
import logging
from datetime import datetime
from ib_insync import IB, Stock, Option, MarketOrder, LimitOrder
import pytz

log = logging.getLogger("tradeiq")

class IBKRClient:
    def __init__(self, host="127.0.0.1", port=None, client_id=1):
        import config as _config
        if port is None:
            port = _config.IBKR_PORT
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self._connect()

    def _connect(self):
        try:
            if not self.ib.isConnected():
                self.ib.connect(self.host, self.port, clientId=self.client_id)
                log.info(f"Connected to IBKR — account: {self.ib.managedAccounts()}")
        except Exception as e:
            log.error(f"IBKR connection failed: {e}")

    def _ensure_connected(self):
        if not self.ib.isConnected():
            import time
            for attempt in range(5):
                try:
                    self._connect()
                    if self.ib.isConnected():
                        log.info(f"IBKR reconnected on attempt {attempt + 1}")
                        return
                except Exception as e:
                    log.warning(f"IBKR reconnect attempt {attempt + 1} failed: {e}")
                time.sleep(5)
            log.error("IBKR reconnect failed after 5 attempts")

    def get_account(self):
        self._ensure_connected()
        try:
            for v in self.ib.accountValues():
                if v.tag == "NetLiquidation" and v.currency == "USD":
                    return {"portfolio_value": float(v.value)}
        except Exception as e:
            log.error(f"IBKR get_account: {e}")
        return {"portfolio_value": 0.0}

    def get_portfolio_value(self):
        self._ensure_connected()
        try:
            for v in self.ib.accountValues():
                if v.tag == "NetLiquidation" and v.currency == "USD":
                    return float(v.value)
        except Exception as e:
            log.error(f"IBKR get_portfolio_value: {e}")
        return 0.0

    def get_cash_balance(self):
        self._ensure_connected()
        try:
            for v in self.ib.accountValues():
                if v.tag == "CashBalance" and v.currency == "USD":
                    return float(v.value)
        except Exception as e:
            log.error(f"IBKR get_cash_balance: {e}")
        return 0.0

    def is_market_open(self):
        now = datetime.now(pytz.timezone("US/Eastern"))
        if now.weekday() >= 5:
            return False
        # NYSE holidays 2026
        holidays = {
            (1,1), (1,19), (2,16), (4,3), (5,25),
            (7,3), (9,7), (11,26), (12,25)
        }
        if (now.month, now.day) in holidays:
            return False
        o = now.replace(hour=9,  minute=30, second=0, microsecond=0)
        c = now.replace(hour=16, minute=0,  second=0, microsecond=0)
        return o <= now <= c

    def get_bars(self, symbol, timeframe="1Hour", limit=100):
        self._ensure_connected()
        try:
            bar_map = {"1Hour": "1 hour", "1Day": "1 day", "1Min": "1 min"}
            bar_size = bar_map.get(timeframe, "1 hour")
            days = max(5, limit // 8 + 1)
            contract = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            bars = self.ib.reqHistoricalData(
                contract, endDateTime="", durationStr=f"{days} D",
                barSizeSetting=bar_size, whatToShow="TRADES",
                useRTH=True, formatDate=1
            )
            result = [{"t": str(b.date), "o": b.open, "h": b.high,
                       "l": b.low, "c": b.close, "v": b.volume}
                      for b in bars]
            return result[-limit:]
        except Exception as e:
            log.error(f"IBKR get_bars {symbol}: {e}")
            return []

    def get_latest_price(self, symbol):
        try:
            bars = self.get_bars(symbol, "1Hour", 2)
            if bars:
                return float(bars[-1]["c"])
            return 0.0
        except Exception as e:
            log.error(f"IBKR get_latest_price {symbol}: {e}")
            return 0.0

    def place_market_order(self, symbol, side, notional=None, qty=None):
        self._ensure_connected()
        try:
            contract = Stock(symbol, "SMART", "USD")
            self.ib.qualifyContracts(contract)
            action = "BUY" if side.upper() == "BUY" else "SELL"
            if qty is None and notional:
                price = self.get_latest_price(symbol)
                qty = max(1, int(notional / price)) if price > 0 else 1
            order = MarketOrder(action, qty)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(2)
            log.info(f"IBKR order: {action} {qty} {symbol} — {trade.orderStatus.status}")
            return {"id": str(trade.order.orderId), "status": trade.orderStatus.status,
                    "symbol": symbol, "side": action, "qty": qty}
        except Exception as e:
            log.error(f"IBKR place_market_order {symbol}: {e}")
            return None

    def get_positions(self):
        self._ensure_connected()
        try:
            positions = []
            for p in self.ib.positions():
                if p.contract.secType == "STK" and p.position != 0:
                    positions.append({
                        "symbol": p.contract.symbol,
                        "qty": abs(p.position),
                        "side": "BUY" if p.position > 0 else "SELL",
                        "avg_entry_price": p.avgCost,
                        "market_value": abs(p.position) * p.avgCost,
                        "market": "stocks"
                    })
            return positions
        except Exception as e:
            log.error(f"IBKR get_positions: {e}")
            return []

    def close_position(self, symbol):
        self._ensure_connected()
        try:
            for p in self.ib.positions():
                if p.contract.symbol == symbol and p.position != 0:
                    action = "SELL" if p.position > 0 else "BUY"
                    contract = Stock(symbol, "SMART", "USD")
                    self.ib.qualifyContracts(contract)
                    order = MarketOrder(action, abs(p.position))
                    trade = self.ib.placeOrder(contract, order)
                    self.ib.sleep(2)
                    log.info(f"IBKR close {symbol}: {trade.orderStatus.status}")
                    return {"status": trade.orderStatus.status}
            log.warning(f"IBKR close_position: no position found for {symbol}")
            return None
        except Exception as e:
            log.error(f"IBKR close_position {symbol}: {e}")
            return None

    def place_option_order(self, contract_symbol, action, qty=1):
        self._ensure_connected()
        try:
            m = re.match(r"^([A-Z]+)(\d{6})([CP])(\d{8})$", contract_symbol)
            if not m:
                log.error(f"Invalid option symbol: {contract_symbol}")
                return None
            sym, exp, cp, strike = m.groups()
            expiry = "20" + exp
            strike_price = int(strike) / 1000
            right = "C" if cp == "C" else "P"
            contract = Option(sym, expiry, strike_price, right, "SMART")
            self.ib.qualifyContracts(contract)
            order = MarketOrder(action.upper(), qty)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(2)
            log.info(f"IBKR option: {action} {contract_symbol} — {trade.orderStatus.status}")
            return {"id": str(trade.order.orderId), "status": trade.orderStatus.status}
        except Exception as e:
            log.error(f"IBKR option order {contract_symbol}: {e}")
            return None


    def get_option_chain(self, symbol, expiry, option_type="call"):
        """Get option contracts from IBKR for a given symbol and expiry."""
        self._ensure_connected()
        try:
            from ib_insync import Option
            right = "C" if option_type == "call" else "P"
            # Get underlying price first
            underlying_price = self.get_latest_price(symbol)
            # Request option chain
            chains = self.ib.reqSecDefOptParams(symbol, "", "STK", 0)
            self.ib.sleep(1)
            contracts = []
            for chain in chains:
                if expiry.replace("-","") in [e for e in chain.expirations]:
                    for strike in chain.strikes:
                        if underlying_price > 0:
                            pct_otm = abs(strike - underlying_price) / underlying_price
                            if pct_otm > 0.10:
                                continue
                        contract = Option(symbol, expiry.replace("-",""), strike, right, "SMART")
                        contracts.append({
                            "symbol": f"{symbol}{expiry.replace('-','')[2:]}{'C' if right=='C' else 'P'}{int(strike*1000):08d}",
                            "strike_price": strike,
                            "expiration_date": expiry,
                            "type": option_type,
                            "close_price": 0,
                        })
            return contracts
        except Exception as e:
            log.error(f"IBKR get_option_chain {symbol}: {e}")
            return []

    def get_option_price(self, symbol, expiry, strike, option_type="call"):
        """Get current price of an option contract via IBKR."""
        self._ensure_connected()
        try:
            from ib_insync import Option
            right = "C" if option_type == "call" else "P"
            contract = Option(symbol, expiry.replace("-",""), strike, right, "SMART")
            self.ib.qualifyContracts(contract)
            ticker = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(2)
            price = ticker.marketPrice()
            self.ib.cancelMktData(contract)
            return float(price) if price and price > 0 else 0.0
        except Exception as e:
            log.error(f"IBKR get_option_price {symbol}: {e}")
            return 0.0

    def get_prices(self, symbols):
        """Get latest prices for multiple symbols."""
        prices = {}
        for symbol in symbols:
            try:
                price = self.get_latest_price(symbol)
                if price > 0:
                    prices[symbol] = price
            except Exception:
                pass
        return prices
    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
