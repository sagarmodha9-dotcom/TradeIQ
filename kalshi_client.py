import time
import uuid
import base64
import requests
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
import config
from logger import log

class KalshiClient:
    BASE = "https://api.elections.kalshi.com"

    def __init__(self):
        self.api_key     = config.KALSHI_API_KEY
        self.private_key = config.KALSHI_PRIVATE_KEY
        self._load_key()

    def _load_key(self):
        try:
            self.pk = serialization.load_pem_private_key(
                self.private_key.encode(), password=None, backend=default_backend()
            )
            log.info("Kalshi: private key loaded OK")
        except Exception as e:
            log.error(f"Kalshi: key load failed: {e}")
            self.pk = None

    def _sign(self, ts_ms, method, path):
        msg = f"{ts_ms}{method}{path}".encode()
        sig = self.pk.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256()
        )
        return base64.b64encode(sig).decode()

    def _headers(self, method, path):
        ts = str(int(time.time() * 1000))
        return {
            "Content-Type":            "application/json",
            "KALSHI-ACCESS-KEY":       self.api_key,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self._sign(ts, method.upper(), path),
        }

    def _request(self, method, endpoint, body=None, params=None, retries=3):
        path = f"/trade-api/v2{endpoint}"
        url  = f"{self.BASE}{path}"
        for attempt in range(retries):
            try:
                headers = self._headers(method, path)
                resp = requests.request(method, url, headers=headers, json=body, params=params, timeout=15)
                if resp.status_code == 429:
                    time.sleep(2 ** attempt); continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                log.error(f"Kalshi error ({attempt+1}/{retries}): {e}")
                if attempt == retries - 1: raise
                time.sleep(2 ** attempt)

    def get_balance(self):
        try:
            data = self._request("GET", "/portfolio/balance")
            return float(data.get("balance", 0)) / 100
        except Exception:
            return 0.0

    def get_markets(self, limit=20):
        try:
            data = self._request("GET", "/markets", params={"limit": limit})
            return data.get("markets", [])
        except Exception as e:
            log.error(f"Kalshi get_markets: {e}")
            return []

    def get_top_markets(self, limit=20):
        markets = self.get_markets(limit=limit)
        log.info(f"Kalshi: loaded {len(markets)} markets")
        return markets

    def place_order(self, ticker, side, count, yes_price):
        client_order_id = f"tradeiq-{uuid.uuid4().hex[:12]}"
        if not config.IS_LIVE:
            cost = count * yes_price / 100
            log.info(f"[PAPER] Kalshi {side.upper()} {ticker} x{count} @ {yes_price}¢ = ${cost:.2f}")
            return {
                "success": True, "paper": True, "order_id": client_order_id,
                "ticker": ticker, "side": side, "count": count,
                "yes_price": yes_price, "cost_usd": round(cost, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        body = {
            "ticker": ticker, "client_order_id": client_order_id,
            "type": "limit", "action": "buy",
            "side": side, "count": count, "yes_price": yes_price,
        }
        resp  = self._request("POST", "/portfolio/orders", body=body)
        order = resp.get("order", {})
        return {
            "success": True, "paper": False,
            "order_id": order.get("order_id", client_order_id),
            "ticker": ticker, "side": side, "count": count,
            "yes_price": yes_price, "cost_usd": count * yes_price / 100,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_positions(self):
        try:
            data = self._request("GET", "/portfolio/positions")
            return data.get("market_positions", [])
        except Exception as e:
            log.error(f"Kalshi get_positions: {e}")
            return []
