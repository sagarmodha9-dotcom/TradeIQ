import json
import anthropic
import config
from logger import log

SYSTEM_PROMPT = """You are an expert prediction market trader on Kalshi.
Analyze the market and output a precise JSON trading signal.

Rules:
- Only recommend YES/NO when confidence >= 0.65
- Consider the current yes_ask price — good value is when probability seems mispriced
- YES means you think the event WILL happen, NO means it WON'T
- Respond with ONLY valid JSON, no markdown, no preamble

Schema:
{
  "action": "YES" | "NO" | "PASS",
  "confidence": <float 0.0-1.0>,
  "yes_price": <int 1-99>,
  "contracts": <int 1-10>,
  "cost_usd": <float>,
  "reasoning": "<1-2 sentences>",
  "edge": "<why this is mispriced>"
}"""

class KalshiAnalyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def analyze(self, market: dict):
        ticker    = market.get("ticker", "")
        title     = market.get("title", "")
        yes_ask   = float(market.get("yes_ask_dollars") or 0)
        yes_bid   = float(market.get("yes_bid_dollars") or 0)
        no_ask    = float(market.get("no_ask_dollars") or 0)
        last      = float(market.get("last_price_dollars") or 0)
        volume    = float(market.get("volume_fp") or 0)
        close     = market.get("close_time", "")
        liquidity = float(market.get("liquidity_dollars") or 0)

        # Skip if no price data
        if yes_ask == 0 and yes_bid == 0:
            return None

        user_msg = (
            f"Kalshi market: {title}\n"
            f"Ticker: {ticker}\n"
            f"Yes ask: ${yes_ask:.2f} | Yes bid: ${yes_bid:.2f} | No ask: ${no_ask:.2f}\n"
            f"Last price: ${last:.2f} | Volume: {volume} | Liquidity: ${liquidity:.2f}\n"
            f"Closes: {close}\n\n"
            f"Should I bet YES or NO? Max $50 position. Each contract = $1 max payout."
        )

        try:
            resp = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=256,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            signal = json.loads(raw.strip())
            signal["ticker"]  = ticker
            signal["title"]   = title
            signal["market"]  = "kalshi"
            signal["product_id"] = ticker
            log.info(f"Kalshi {ticker[:30]}: {signal['action']} | conf={signal['confidence']:.0%}")
            return signal
        except Exception as e:
            log.error(f"Kalshi analyze error {ticker[:30]}: {e}")
            return None
