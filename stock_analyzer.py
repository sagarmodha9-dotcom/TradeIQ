import json
import anthropic
import numpy as np
import config
from logger import log

def compute_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    deltas   = np.diff(closes)
    gains    = np.where(deltas > 0, deltas, 0)
    losses   = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
    if avg_loss == 0: return 100.0
    return round(100 - (100 / (1 + avg_gain/avg_loss)), 2)

def compute_ema(values, period):
    ema = [values[0]]
    k   = 2 / (period + 1)
    for v in values[1:]: ema.append(v * k + ema[-1] * (1-k))
    return ema

def compute_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return {"macd":0,"signal":0,"histogram":0,"crossover":"neutral"}
    macd_line = [f-s for f,s in zip(compute_ema(closes,fast), compute_ema(closes,slow))]
    sig_line  = compute_ema(macd_line, signal)
    histogram = [m-s for m,s in zip(macd_line, sig_line)]
    crossover = "neutral"
    if len(histogram) >= 2:
        if histogram[-1] > 0 and histogram[-2] <= 0: crossover = "bullish"
        elif histogram[-1] < 0 and histogram[-2] >= 0: crossover = "bearish"
    return {"macd":round(macd_line[-1],4),"signal":round(sig_line[-1],4),"histogram":round(histogram[-1],4),"crossover":crossover}

def compute_bollinger(closes, period=20, std_dev=2.0):
    if len(closes) < period:
        return {"upper":0,"middle":0,"lower":0,"pct_b":0.5}
    window = closes[-period:]
    mid    = np.mean(window)
    std    = np.std(window)
    upper  = mid + std_dev * std
    lower  = mid - std_dev * std
    pct_b  = (closes[-1]-lower)/(upper-lower) if upper != lower else 0.5
    return {"upper":round(upper,4),"middle":round(mid,4),"lower":round(lower,4),"pct_b":round(pct_b,3)}

def compute_indicators(bars):
    closes  = [b.get("c") or b.get("close") or 0 for b in bars]
    highs   = [b.get("h") or b.get("high") or 0 for b in bars]
    lows    = [b.get("l") or b.get("low") or 0 for b in bars]
    current = closes[-1]
    ema20   = compute_ema(closes, 20)[-1]
    ema50   = compute_ema(closes, 50)[-1] if len(closes) >= 50 else None
    return {
        "current_price":  round(current, 4),
        "high_24h":       round(max(highs[-8:]) if len(highs) >= 8 else max(highs), 4),
        "low_24h":        round(min(lows[-8:])  if len(lows)  >= 8 else min(lows),  4),
        "rsi_14":         compute_rsi(closes),
        "macd":           compute_macd(closes),
        "bollinger":      compute_bollinger(closes),
        "ema_20":         round(ema20, 4),
        "ema_50":         round(ema50, 4) if ema50 else None,
        "price_vs_ema20": round((current/ema20-1)*100, 2),
    }

SYSTEM_PROMPT = """You are an expert stock trader using a BALANCED risk/reward strategy.
Analyze the technical indicators and output a precise JSON trading signal.
Rules: Only BUY/SELL when confidence >= 0.65. Prefer HOLD when signals are mixed.
Stop loss ~3% from entry. Take profit ~6% from entry (2:1 RR minimum).
Respond with ONLY valid JSON, no markdown, no preamble.
Schema: {"action":"BUY"|"SELL"|"HOLD","confidence":<float>,"entry_price":<float>,"stop_loss":<float>,"take_profit":<float>,"risk_reward":<float>,"reasoning":"<string>","key_signals":["<s1>","<s2>","<s3>"]}"""

class StockAnalyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def analyze(self, symbol, bars):
        if len(bars) < 30: return None
        try:
            indicators = compute_indicators(bars)
        except Exception as e:
            log.error(f"{symbol}: Indicator error: {e}")
            return None
        user_msg = (
            f"Analyze stock {symbol}.\n"
            f"Price: ${indicators['current_price']:,.2f} | "
            f"High: ${indicators['high_24h']:,.2f} | Low: ${indicators['low_24h']:,.2f}\n"
            f"RSI(14): {indicators['rsi_14']} | "
            f"MACD: {indicators['macd']['macd']} crossover={indicators['macd']['crossover']}\n"
            f"Bollinger %B: {indicators['bollinger']['pct_b']} | "
            f"EMA20: ${indicators['ema_20']:,.2f} ({indicators['price_vs_ema20']:+.2f}%)\n"
            f"Apply balanced strategy: 5% position, {config.STOCK_SL_PCT*100:.0f}% SL, {config.STOCK_TP_PCT*100:.0f}% TP."
        )
        try:
            resp = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role":"user","content":user_msg}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"): raw = raw[4:]
            signal = json.loads(raw.strip())
            signal["symbol"]     = symbol
            signal["market"]     = "stocks"
            signal["indicators"] = indicators
            log.info(f"{symbol}: {signal['action']} | conf={signal['confidence']:.0%} | entry=${signal['entry_price']:,.2f}")
            return signal
        except Exception as e:
            log.error(f"{symbol}: Analysis error: {e}")
            return None
