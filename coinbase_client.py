import time
import uuid
import requests
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import jwt
import config
from logger import log

class CoinbaseClient:
    BASE = config.CB_BASE_URL
    PATH = config.CB_API_PATH

    def __init__(self):
        self.api_key     = config.COINBASE_API_KEY
        self.private_key = config.COINBASE_PRIVATE_KEY
        self.is_live     = config.IS_LIVE

    def _build_jwt(self, method, path_with_query):
        private_key_obj = serialization.load_pem_private_key(
            self.private_key.encode(), password=None, backend=default_backend()
        )
        # Strip query string — JWT uri must be path only, no ?params
        clean_path = path_with_query.split("?")[0]
        uri = f"{method} api.coinbase.com{clean_path}"
        payload = {
            "sub": self.api_key,
            "iss": "coinbase-cloud",
            "nbf": int(time.time()),
            "exp": int(time.time()) + 120,
            "uri": uri,
        }
        return jwt.encode(
            payload, private_key_obj, algorithm="ES256",
            headers={"kid": self.api_key, "nonce": uuid.uuid4().hex}
        )

    def _request(self, method, endpoint, body=None, retries=3):
        path    = f"{self.PATH}{endpoint}"
        url     = f"{self.BASE}{path}"
        token   = self._build_jwt(method.upper(), path)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        }
        for attempt in range(retries):
            try:
                resp = requests.request(
                    method, url, headers=headers, json=body, timeout=15
                )
                if resp.status_code == 429:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                log.error(f"Request error ({attempt+1}/{retries}): {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)

    def get_best_bid_ask(self, product_id):
        data = self._request("GET", f"/best_bid_ask?product_ids={product_id}")
        pb   = data.get("pricebooks", [{}])[0]
        bid  = float(pb.get("bids", [{}])[0].get("price", 0))
        ask  = float(pb.get("asks", [{}])[0].get("price", 0))
        return {"product_id": product_id, "bid": bid, "ask": ask, "mid": (bid + ask) / 2}

    def get_candles(self, product_id, granularity=None, limit=None):
        gran  = granularity or config.CANDLE_GRANULARITY
        lim   = limit or config.CANDLE_LIMIT
        end   = int(time.time())
        gran_sec = {
            "ONE_MINUTE": 60, "FIVE_MINUTE": 300, "FIFTEEN_MINUTE": 900,
            "THIRTY_MINUTE": 1800, "ONE_HOUR": 3600, "TWO_HOUR": 7200,
            "SIX_HOUR": 21600, "ONE_DAY": 86400,
        }
        start = end - (gran_sec.get(gran, 3600) * lim)
        data  = self._request(
            "GET",
            f"/products/{product_id}/candles?start={start}&end={end}&granularity={gran}"
        )
        candles = [
            {
                "time":   int(c["start"]),
                "open":   float(c["open"]),
                "high":   float(c["high"]),
                "low":    float(c["low"]),
                "close":  float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in data.get("candles", [])
        ]
        return sorted(candles, key=lambda x: x["time"])

    def get_accounts(self):
        return self._request("GET", "/accounts").get("accounts", [])

    def get_portfolio_value(self):
        if not self.is_live:
            import config
            return config.PAPER_BALANCE
        total = 0.0
        for acct in self.get_accounts():
            currency = acct.get("currency", "")
            balance  = float(acct.get("available_balance", {}).get("value", 0))
            if balance <= 0:
                continue
            if currency in ("USD", "USDC"):
                total += balance
            else:
                try:
                    total += balance * self.get_best_bid_ask(f"{currency}-USD")["mid"]
                except Exception:
                    pass
        return round(total, 2)

    def get_usd_balance(self):
        if not self.is_live:
            import config
            return config.PAPER_BALANCE
        bal = 0.0
        for acct in self.get_accounts():
            if acct.get("currency") in ("USD", "USDC"):
                bal += float(acct.get("available_balance", {}).get("value", 0))
        return round(bal, 2)

    def place_market_order(self, product_id, side, quote_size=None, base_size=None):
        client_order_id = f"tradeiq-{uuid.uuid4().hex[:12]}"
        if not self.is_live:
            price_data = self.get_best_bid_ask(product_id)
            fill_price = price_data["ask"] if side == "BUY" else price_data["bid"]
            fill_qty   = (quote_size / fill_price) if side == "BUY" else base_size
            log.info(f"[PAPER] {side} {product_id} qty={fill_qty:.6f} @ ${fill_price:,.2f}")
            return {
                "success":        True,
                "paper":          True,
                "order_id":       client_order_id,
                "product_id":     product_id,
                "side":           side,
                "fill_price":     fill_price,
                "fill_quantity":  fill_qty,
                "fill_value_usd": quote_size or (base_size * fill_price),
                "timestamp":      datetime.now(timezone.utc).isoformat(),
            }
        order_config = {"market_market_ioc": {}}
        if side == "BUY" and quote_size:
            order_config["market_market_ioc"]["quote_size"] = str(round(quote_size, 2))
        elif side == "SELL" and base_size:
            order_config["market_market_ioc"]["base_size"] = str(base_size)
        else:
            raise ValueError("Provide quote_size for BUY or base_size for SELL")
        body = {
            "client_order_id":     client_order_id,
            "product_id":          product_id,
            "side":                side,
            "order_configuration": order_config,
        }
        resp = self._request("POST", "/orders", body=body)
        if not resp.get("success"):
            raise RuntimeError(
                f"Order failed: {resp.get('error_response', {}).get('message', 'Unknown')}"
            )
        fill = resp.get("order", {})
        return {
            "success":        True,
            "paper":          False,
            "order_id":       fill.get("order_id", client_order_id),
            "product_id":     product_id,
            "side":           side,
            "fill_price":     float(fill.get("average_filled_price", 0)),
            "fill_quantity":  float(fill.get("filled_size", 0)),
            "fill_value_usd": float(fill.get("filled_value", 0)),
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        }

    def list_open_orders(self):
        return self._request(
            "GET", "/orders/historical/batch?order_status=OPEN"
        ).get("orders", [])

    def fetch_top_pairs(self, count=50):
        """
        Fetch top N USD pairs by 24h volume from Coinbase.
        Updates config.CRYPTO_PAIRS in place.
        """
        import config
        try:
            data = self._request("GET", "/products")
            products = data.get("products", [])
            usd_pairs = [
                p for p in products
                if p.get("quote_currency_id") == "USD"
                and p.get("status") == "online"
                and not p.get("is_disabled", False)
            ]
            sorted_pairs = sorted(
                usd_pairs,
                key=lambda x: float(x.get("approximate_quote_24h_volume", 0) or 0),
                reverse=True
            )
            top = [p["product_id"] for p in sorted_pairs[:count]]
            config.CRYPTO_PAIRS = top
            log.info(f"Loaded top {len(top)} pairs by volume: {', '.join(top[:10])}...")
            return top
        except Exception as e:
            log.error(f"fetch_top_pairs failed: {e}")
            # Fallback to default pairs
            config.CRYPTO_PAIRS = ["BTC-USD","ETH-USD","SOL-USD","XRP-USD","AVAX-USD","LINK-USD","DOGE-USD","ADA-USD","MATIC-USD","DOT-USD"]
            return config.CRYPTO_PAIRS
