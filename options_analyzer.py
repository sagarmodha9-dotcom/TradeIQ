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

    def _parse_json_robust(self, raw):
        """Try multiple strategies to extract valid JSON from LLM response."""
        import re
        # Strip markdown code fences
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text[3:]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        
        # Strategy 1: Parse as-is
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        
        # Strategy 2: Extract first JSON object via regex
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        
        # Strategy 3: Strip trailing comma before } or ]
        cleaned = re.sub(r',(\s*[}\]])', r'\1', text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        
        # Strategy 4: Find largest balanced { ... } block
        depth = 0
        start = -1
        for i, c in enumerate(text):
            if c == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start:i+1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        continue
        
        return None

    def analyze(self, symbol, stock_signal, current_price):
        user_msg = (
            f"Stock: {symbol} @ ${current_price:,.2f}\n"
            f"AI Signal: {stock_signal['action']} | Conf: {stock_signal['confidence']:.0%}\n"
            f"Entry: ${float(stock_signal.get('entry_price') or current_price):,.2f} | "
            f"SL: ${float(stock_signal.get('stop_loss') or 0):,.2f} | "
            f"TP: ${float(stock_signal.get('take_profit') or 0):,.2f}\n"
            f"Reasoning: {stock_signal.get('reasoning','')}\n\n"
            f"Budget: $250 max per trade. Should I trade options on this signal?\n"
            f"Consider: 0DTE for high confidence, weekly for moderate. "
            f"Only recommend if confidence >= 0.72.\n"
            f"IMPORTANT: Respond with ONLY a valid JSON object, no markdown, no commentary."
        )
        
        # Try up to 2 attempts
        for attempt in range(2):
            try:
                resp = self.client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=512,  # increased from 256 to reduce truncation
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw = resp.content[0].text.strip()
                signal = self._parse_json_robust(raw)
                if signal:
                    signal["symbol"] = symbol
                    log.info(f"Options {symbol}: {signal.get('strategy', '?')} | conf={signal.get('confidence', 0):.0%}")
                    return signal
                if attempt == 0:
                    log.warning(f"Options {symbol}: JSON parse failed, retrying...")
                else:
                    log.error(f"Options {symbol}: JSON parse failed after retry. Raw: {raw[:200]}")
            except Exception as e:
                log.error(f"Options analyze error {symbol} (attempt {attempt+1}): {e}")
                if attempt == 0:
                    continue
        return None
