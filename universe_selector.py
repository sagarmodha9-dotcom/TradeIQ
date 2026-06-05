"""Daily universe selector: rank 130-symbol universe by day-trading fitness,
pick top N that fit the bracket. One batched data call, zero Claude cost.
Fallback: static config.STOCK_SYMBOLS on any failure."""
import json, os
from datetime import datetime
from logger import log
import config

RANGE_MIN, RANGE_MAX = 0.025, 0.065   # avg day range band that fits 0.75/1.2 brackets
DOLLAR_VOL_PERCENTILE = 0.5           # keep top half of universe by $ volume
TOP_N = 30
ALWAYS = ["SPY", "QQQ"]               # regime anchors, never dropped

def select_universe(ibkr, top_n=TOP_N):
    try:
        universe = json.load(open("universe.json"))
        bars = ibkr.get_bars_multi(universe, timeframe="1Day", limit=15)
        scored = []
        for sym, rows in bars.items():
            if len(rows) < 10:
                continue
            last10 = rows[-11:-1] if len(rows) > 10 else rows[:-1]  # exclude today's partial
            ranges = [(b["high"] - b["low"]) / b["close"] for b in last10 if b["close"] > 0]
            vols   = [b["volume"] for b in last10]
            if not ranges or not vols:
                continue
            avg_range = sum(ranges) / len(ranges)
            avg_vol   = sum(vols) / len(vols)
            avg_close = sum(b["close"] for b in last10) / len(last10)
            dollar_vol = avg_vol * avg_close
            today_vol = rows[-1]["volume"]
            rel_vol   = today_vol / avg_vol if avg_vol > 0 else 0
            if not (RANGE_MIN <= avg_range <= RANGE_MAX):
                continue
            # score: range fitness centered in band, boosted by today's relative volume
            center = (RANGE_MIN + RANGE_MAX) / 2
            fit = 1 - abs(avg_range - center) / (RANGE_MAX - RANGE_MIN)
            score = fit + min(rel_vol, 3) * 0.3
            scored.append((score, sym, avg_range, rel_vol, dollar_vol))
        # liquidity gate: keep top half by dollar volume, then rank by score
        scored.sort(key=lambda x: x[4], reverse=True)
        scored = scored[:max(top_n, int(len(scored) * DOLLAR_VOL_PERCENTILE))]
        scored.sort(reverse=True)
        picks = [s for _, s, _, _, _ in scored[:top_n]]
        for a in ALWAYS:
            if a not in picks:
                picks.append(a)
        if len(picks) < 10:
            raise ValueError(f"only {len(picks)} picks — falling back")
        json.dump({"date": datetime.now().strftime("%Y-%m-%d"), "symbols": picks,
                   "detail": [(s, round(r,4), round(v,2)) for _, s, r, v, _ in scored[:top_n]]},
                  open("universe_today.json", "w"), indent=1)
        log.info(f"🎯 UNIVERSE SELECTED: {len(picks)} symbols: {', '.join(picks)}")
        return picks
    except Exception as e:
        log.warning(f"universe selector failed ({e}) — using static STOCK_SYMBOLS")
        return list(config.STOCK_SYMBOLS)

def get_active_symbols(ibkr):
    """Return today's symbols; select fresh if stale or missing."""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        d = json.load(open("universe_today.json"))
        if d.get("date") == today and d.get("symbols"):
            return d["symbols"]
    except Exception:
        pass
    return select_universe(ibkr)
