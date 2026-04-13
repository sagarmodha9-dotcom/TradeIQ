import json
import anthropic
import numpy as np
import config
from logger import log

def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas   = np.diff(closes)
    gains    = np.where(deltas > 0, deltas, 0)
    losses   = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

def compute_ema(values, period):
    ema = [values[0]]
    k   = 2 / (period + 1)
    for v in values[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema

def compute_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal:
        return {"macd": 0, "signal": 0, "histogram": 0, "crossover": "neutral"}
    macd_line = [f - s for f, s in zip(compute_ema(closes, fast), compute_ema(closes, slow))]
    sig_line  = compute_ema(macd_line, signal)
    histogram = [m - s for m, s in zip(macd_line, sig_line)]
    crossover = "neutral"
    if len(histogram) >= 2:
        if histogram[-1] > 0 and histogram[-2] <= 0:
            crossover = "bullish"
        elif histogram[-1] < 0 and histogram[-2] >= 0:
            crossover = "bearish"
    return {
        "macd":      float(round(macd_line[-1], 4)),
        "signal":    float(round(sig_line[-1], 4)),
        "histogram": float(round(histogram[-1], 4)),
        "crossover": crossover,
    }

def compute_bollinger(closes, period=20, std_dev=2.0):
    if len(closes) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "pct_b": 0.5}
    window = closes[-period:]
    mid    = np.mean(window)
    std    = np.std(window)
    upper  = mid + std_dev * std
    lower  = mid - std_dev * std
    pct_b  = (closes[-1] - lower) / (upper - lower) if upper != lower else 0.5
    return {
        "upper":  float(round(upper, 4)),
        "middle": float(round(mid, 4)),
        "lower":  float(round(lower, 4)),
        "pct_b":  float(round(pct_b, 3)),
    }

def compute_volume_trend(candles, period=10):
    if len(candles) < period + 1:
        return "neutral"
    recent = np.mean([c["volume"] for c in candles[-period:]])
    prior  = np.mean([c["volume"] for c in candles[-period * 2:-period]])
    ratio  = recent / prior if prior > 0 else 1
    return "rising" if ratio > 1.2 else "falling" if ratio < 0.8 else "neutral"

def compute_indicators(candles):
    closes  = [float(c["close"]) for c in candles]
    highs   = [float(c["high"])  for c in candles]
    lows    = [float(c["low"])   for c in candles]
    current = closes[-1]
    ema20   = float(compute_ema(closes, 20)[-1]) if len(closes) >= 20 else float(closes[-1])
    ema50   = float(compute_ema(closes, 50)[-1]) if len(closes) >= 50 else None
    rsi = compute_rsi(closes)
    macd = compute_macd(closes)
    boll = compute_bollinger(closes)
    return {
        "current_price":  float(round(current, 4)),
        "high_24h":       float(round(max(highs[-24:]) if len(highs) >= 24 else max(highs), 4)),
        "low_24h":        float(round(min(lows[-24:]) if len(lows) >= 24 else min(lows), 4)),
        "rsi_14":         float(round(rsi, 2)),
        "macd":           {k: float(v) if isinstance(v, (int, float)) or hasattr(v, '__float__') else v for k, v in macd.items()},
        "bollinger":      {k: float(v) if isinstance(v, (int, float)) or hasattr(v, '__float__') else v for k, v in boll.items()},
        "volume_trend":   compute_volume_trend(candles),
        "ema_20":         float(round(ema20, 4)),
        "ema_50":         float(round(ema50, 4)) if ema50 else None,
        "price_vs_ema20": float(round((current / ema20 - 1) * 100, 2)),
    }

SYSTEM_PROMPT = """You are an expert quantitative crypto trader using a BALANCED risk/reward strategy.
Analyze the technical indicators and output a precise JSON trading signal.
Rules: Only BUY/SELL when confidence >= 0.65. Prefer HOLD when signals are mixed.
Stop loss ~3% from entry. Take profit ~6% from entry (2:1 RR minimum).
Respond with ONLY valid JSON, no markdown, no preamble.
Schema: {"action":"BUY"|"SELL"|"HOLD","confidence":<float>,"entry_price":<float>,"stop_loss":<float>,"take_profit":<float>,"risk_reward":<float>,"reasoning":"<string>","key_signals":["<s1>","<s2>","<s3>"]}"""

class Analyzer:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def analyze(self, product_id, candles):
        if len(candles) < 30:
            return None
        try:
            indicators = compute_indicators(candles)
        except Exception as e:
            log.error(f"{product_id}: Indicator error: {e}")
            return None
        cp   = float(indicators.get("current_price") or 0)
        hi   = float(indicators.get("high_24h") or 0)
        lo   = float(indicators.get("low_24h") or 0)
        rsi  = float(indicators.get("rsi_14") or 50)
        macd_val = float((indicators.get("macd") or {}).get("macd") or 0)
        macd_cross = str((indicators.get("macd") or {}).get("crossover") or "neutral")
        pct_b = float((indicators.get("bollinger") or {}).get("pct_b") or 0.5)
        ema20 = float(indicators.get("ema_20") or indicators.get("current_price") or 0)
        pve   = float(indicators.get("price_vs_ema20") or 0)
        vtrd  = str(indicators.get("volume_trend") or "neutral")
        sl_pct = int(config.STOP_LOSS_PCT * 100)
        tp_pct = int(config.TAKE_PROFIT_PCT * 100)
        user_msg = (
            f"Analyze {product_id}.\n"
            f"Price: ${cp:,.4f} | 24h High: ${hi:,.4f} | Low: ${lo:,.4f}\n"
            f"RSI(14): {rsi} | MACD: {macd_val} crossover={macd_cross}\n"
            f"Bollinger %B: {pct_b} | EMA20: ${ema20:,.4f} ({pve:+.2f}%)\n"
            f"Volume trend: {vtrd}\n"
            f"Apply balanced strategy: 5% position, {sl_pct}% SL, {tp_pct}% TP. Only BUY if price is ABOVE EMA20 and RSI is between 40-65. HOLD if below EMA20 (downtrend)."
        )
        try:
            resp = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            signal = json.loads(raw.strip())
            signal["product_id"] = product_id
            if not signal.get("entry_price"): signal["entry_price"] = cp
            if not signal.get("stop_loss"): signal["stop_loss"] = round(cp * (1 - config.STOP_LOSS_PCT), 4)
            if not signal.get("take_profit"): signal["take_profit"] = round(cp * (1 + config.TAKE_PROFIT_PCT), 4)
            signal["indicators"] = indicators
            log.info(
                f"{product_id}: {signal['action']} | "
                f"conf={signal['confidence']:.0%} | "
                f"entry=${float(signal.get('entry_price') or 0):,.4f}"
            )
            return signal
        except Exception as e:
            log.error(f"{product_id}: Analysis error: {e}")
            return None
