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

def compute_vwap(bars):
    """Volume Weighted Average Price."""
    try:
        total_pv = sum((b.get("c") or b.get("close") or 0) * (b.get("v") or b.get("volume") or 1) for b in bars)
        total_v  = sum(b.get("v") or b.get("volume") or 1 for b in bars)
        return round(total_pv / total_v, 4) if total_v > 0 else 0
    except:
        return 0

def compute_volume_ratio(bars):
    """Current volume vs 20-bar average."""
    try:
        volumes = [b.get("v") or b.get("volume") or 0 for b in bars]
        if len(volumes) < 5: return 1.0
        avg_vol = np.mean(volumes[-20:]) if len(volumes) >= 20 else np.mean(volumes)
        cur_vol = volumes[-1]
        return round(cur_vol / avg_vol, 2) if avg_vol > 0 else 1.0
    except:
        return 1.0

def compute_atr(bars, period=14):
    """Average True Range — measures volatility."""
    try:
        trs = []
        closes = [b.get("c") or b.get("close") or 0 for b in bars]
        highs  = [b.get("h") or b.get("high") or 0 for b in bars]
        lows   = [b.get("l") or b.get("low") or 0 for b in bars]
        for i in range(1, len(bars)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            trs.append(tr)
        if not trs: return 0
        return round(np.mean(trs[-period:]), 4)
    except:
        return 0

def compute_rsi_divergence(closes, bars_back=5):
    """
    Detect RSI divergence:
    Bullish: price making lower lows but RSI making higher lows
    Bearish: price making higher highs but RSI making lower highs
    """
    if len(closes) < 20:
        return "none"
    try:
        rsi_recent = compute_rsi(closes[-10:])
        rsi_prev   = compute_rsi(closes[-15:-5])
        price_recent = closes[-1]
        price_prev   = closes[-6]
        if price_recent < price_prev and rsi_recent > rsi_prev:
            return "bullish"  # price down, RSI up = hidden strength
        if price_recent > price_prev and rsi_recent < rsi_prev:
            return "bearish"  # price up, RSI down = hidden weakness
        return "none"
    except:
        return "none"

def compute_indicators(bars):
    closes  = [b.get("c") or b.get("close") or 0 for b in bars]
    highs   = [b.get("h") or b.get("high") or 0 for b in bars]
    lows    = [b.get("l") or b.get("low") or 0 for b in bars]
    current = closes[-1]
    ema20   = compute_ema(closes, 20)[-1]
    ema9    = compute_ema(closes, 9)[-1]
    ema50   = compute_ema(closes, 50)[-1] if len(closes) >= 50 else None
    macd    = compute_macd(closes)
    bb      = compute_bollinger(closes)
    rsi     = compute_rsi(closes)
    vwap    = compute_vwap(bars)
    vol_ratio = compute_volume_ratio(bars)
    atr     = compute_atr(bars)
    rsi_div = compute_rsi_divergence(closes)

    # EMA alignment — bullish if 9 > 20 > 50
    ema_aligned = "bullish" if ema9 > ema20 and (ema50 is None or ema20 > ema50) else                   "bearish" if ema9 < ema20 and (ema50 is None or ema20 < ema50) else "mixed"

    # Bollinger squeeze — low volatility before big move
    bb_width = (bb["upper"] - bb["lower"]) / bb["middle"] if bb["middle"] > 0 else 0
    bb_squeeze = bb_width < 0.03  # tight bands = squeeze

    # Price vs VWAP
    price_vs_vwap = round((current/vwap-1)*100, 2) if vwap > 0 else 0

    return {
        "current_price":   round(current, 4) if current else 0.0,
        "high_24h":        round(max(highs[-8:]) if len(highs) >= 8 else max(highs), 4) if highs else 0.0,
        "low_24h":         round(min(lows[-8:])  if len(lows)  >= 8 else min(lows),  4) if lows else 0.0,
        "rsi_14":          rsi if rsi else 50.0,
        "rsi_divergence":  rsi_div if rsi_div else "none",
        "macd":            macd if macd else {"macd": 0, "signal": 0, "histogram": 0, "crossover": "none"},
        "bollinger":       bb if bb else {"upper": 0, "middle": 0, "lower": 0, "pct_b": 0.5},
        "bollinger_squeeze": bb_squeeze if bb_squeeze is not None else False,
        "ema_9":           round(ema9, 4) if ema9 else 0.0,
        "ema_20":          round(ema20, 4) if ema20 else 0.0,
        "ema_50":          round(ema50, 4) if ema50 else 0.0,
        "ema_alignment":   ema_aligned if ema_aligned else "mixed",
        "price_vs_ema20":  round((current/ema20-1)*100, 2) if ema20 else 0.0,
        "vwap":            vwap if vwap else 0.0,
        "price_vs_vwap":   price_vs_vwap if price_vs_vwap else 0.0,
        "volume_ratio":    vol_ratio if vol_ratio else 1.0,
        "atr":             atr if atr else 0.0,
    }

SYSTEM_PROMPT = """You are an expert stock trader using a BALANCED risk/reward strategy.
Analyze the technical indicators and output a precise JSON trading signal.

Rules:
- BUY when confidence >= 0.65 and technical picture is positive
- HOLD when signals are mixed or unclear  
- Confidence MUST vary by symbol — do not give every stock the same score
- Use full range: 0.35 weak, 0.50 mixed, 0.65 decent, 0.72 good, 0.80 strong, 0.85 very strong
- Do NOT be overly conservative — if RSI is healthy, MACD positive, price above EMA20 = BUY at 0.72+
- Stop loss ~3% from entry. Take profit ~6% from entry (2:1 RR minimum)

Good BUY signals:
- RSI between 40-65 with positive MACD = strong buy
- Price above EMA20 and EMA9 with volume confirmation = buy
- Bullish RSI divergence = hidden strength, good entry
- MACD bullish crossover = momentum starting

Avoid:
- RSI above 75 or below 25
- Price more than 3% below EMA20
- MACD strongly bearish with no reversal signs

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
        try:
          indicators = {k: (v if v is not None else 0) for k, v in indicators.items()}
        except: pass
        user_msg = (
            f"Analyze stock {symbol} for precise entry timing.\n"
            f"Price: ${indicators.get('current_price', 0):,.2f} | "
            f"High: ${indicators.get('high_24h', 0):,.2f} | Low: ${indicators.get('low_24h', 0):,.2f}\n"
            f"RSI(14): {indicators.get('rsi_14', 50)} | RSI Divergence: {indicators.get('rsi_divergence', 'none')}\n"
            f"MACD: {indicators.get('macd',{}).get('macd',0):.4f} | Signal: {indicators.get('macd',{}).get('signal',0):.4f} | "
            f"Histogram: {indicators.get('macd',{}).get('histogram',0):.4f} | Crossover: {indicators.get('macd',{}).get('crossover','none')}\n"
            f"Bollinger %B: {indicators.get('bollinger',{}).get('pct_b',0.5)} | "
            f"Squeeze: {indicators.get('bollinger_squeeze', False)}\n"
            f"EMA9: ${indicators.get('ema_9', 0):,.2f} | EMA20: ${indicators.get('ema_20', 0):,.2f} | "
            f"EMA Alignment: {indicators.get('ema_alignment', 'unknown')}\n"
            f"Price vs EMA20: {indicators.get('price_vs_ema20', 0.0):+.2f}% | "
            f"Price vs VWAP: {indicators.get('price_vs_vwap', 0.0):+.2f}%\n"
            f"Volume Ratio: {indicators.get('volume_ratio', 1.0)}x avg | ATR: ${indicators.get('atr', 0.0):.2f}\n"
            f"Strategy: 5% position, {config.STOCK_SL_PCT*100:.0f}% SL, {config.STOCK_TP_PCT*100:.0f}% TP. "
            f"Only BUY if entry timing is confirmed by multiple indicators."
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
