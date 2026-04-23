"""
news_sentiment.py — Claude-powered news sentiment filter.
Fetches recent headlines and asks Claude if it's safe to trade.
Blocks trades when news sentiment is strongly negative.
"""
import anthropic
import yfinance as yf
import json
from datetime import datetime, timezone
from logger import log
import config

_sentiment_cache = {}
client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

SENTIMENT_PROMPT = """You are a trading risk filter. Given recent news headlines for a stock, 
assess if it's safe to enter a BUY trade right now.

Respond with ONLY valid JSON:
{"sentiment": "positive"|"neutral"|"negative", "safe_to_trade": true|false, "confidence_adjustment": <float between -0.05 and 0.03>, only adjust if sentiment is clearly positive or negative, return 0 for neutral, "reason": "<brief reason>"}

Rules:
- "safe_to_trade": false if there are earnings surprises, fraud allegations, SEC investigations, major lawsuits, CEO departure, or product failures
- "confidence_adjustment": negative if bad news, positive if strong catalyst, 0 if neutral
- Be conservative — when in doubt, return safe_to_trade: false"""

def get_news_sentiment(symbol: str) -> dict:
    """Get news sentiment for a symbol, cached for 30 minutes."""
    now = datetime.now(timezone.utc)
    if symbol in _sentiment_cache:
        cached_val, cached_at = _sentiment_cache[symbol]
        if (now - cached_at).total_seconds() < 1800:
            return cached_val

    default = {"sentiment": "neutral", "safe_to_trade": True, 
                "confidence_adjustment": 0.0, "reason": "no news data"}
    try:
        ticker = yf.Ticker(symbol)
        news   = ticker.news
        if not news:
            _sentiment_cache[symbol] = (default, now)
            return default

        headlines = []
        for item in news[:5]:
            title = item.get("content", {}).get("title", "") or item.get("title", "")
            if title:
                headlines.append(title)

        if not headlines:
            _sentiment_cache[symbol] = (default, now)
            return default

        headlines_text = "\n".join(f"- {h}" for h in headlines)
        prompt = f"Stock: {symbol}\nRecent headlines:\n{headlines_text}\n\nAssess trading safety:"

        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            system=SENTIMENT_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        result = json.loads(raw.strip())
        log.info(f"[NEWS] {symbol}: {result['sentiment']} | safe={result['safe_to_trade']} | {result['reason'][:60]}")
        _sentiment_cache[symbol] = (result, now)
        return result

    except Exception as e:
        log.warning(f"news_sentiment {symbol}: {e}")
        _sentiment_cache[symbol] = (default, now)
        return default

def adjust_signal_for_news(symbol: str, signal: dict) -> dict:
    # Whitelist — bypass news block for known false positives
    NEWS_WHITELIST = ["AAPL", "MSFT", "GOOGL", "AMZN"]
    if symbol in NEWS_WHITELIST:
        return signal
    """
    Adjust signal confidence based on news sentiment.
    Returns modified signal or None if news blocks the trade.
    """
    sentiment = get_news_sentiment(symbol)
    if not sentiment.get("safe_to_trade", True):
        log.warning(f"[NEWS BLOCK] {symbol}: {sentiment['reason']}")
        return None
    adj = sentiment.get("confidence_adjustment", 0.0)
    if adj != 0:
        old_conf = signal.get("confidence", 0)
        signal["confidence"] = round(min(0.99, max(0.0, old_conf + adj)), 3)
        log.info(f"[NEWS ADJ] {symbol}: confidence {old_conf:.0%} → {signal['confidence']:.0%}")
    return signal
