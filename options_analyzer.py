import json
import anthropic
import config
from logger import log

SYSTEM_PROMPT = """You are an expert options trader. Analyze the stock signal and recommend the best options strategy.
Respond with ONLY valid JSON, no markdown.
Schema:
{
  "strategy": "buy_call" | "buy_put" | "bull_call_spread" | "bear_put_spread" | "covered_call" | "pass",
  "confidence": <float 0.0-1.0>,
  "direction": "bullish" | "bearish" | "neutral",
  "expiry": "0DTE" | "weekly" | "monthly",
  "reasoning": "<string>",
  "max_loss": <float>,
  "target_return_pct": <float>
}"""

class OptionsAnalyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def analyze(self, symbol, stock_signal, current_price):
        try:
            user_msg = (
                f"Stock: {symbol} @ ${current_price:,.2f}\n"
                f"AI Signal: {stock_signal['action']} | Conf: {stock_signal['confidence']:.0%}\n"
                f"Entry: ${float(stock_signal.get('entry_price') or current_price):,.2f} | "
                f"SL: ${float(stock_signal.get('stop_loss') or 0):,.2f} | "
                f"TP: ${float(stock_signal.get('take_profit') or 0):,.2f}\n"
                f"Reasoning: {stock_signal.get('reasoning','')}\n\n"
                f"Budget: $250 max per trade. Should I trade options on this signal?\n"
                f"Consider: 0DTE for high confidence, weekly for moderate. "
                f"Only recommend if confidence >= 0.72."
            )
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
            signal["symbol"] = symbol
            log.info(f"Options {symbol}: {signal['strategy']} | conf={signal['confidence']:.0%}")
            return signal
        except Exception as e:
            log.error(f"Options analyze error {symbol}: {e}")
            return None
