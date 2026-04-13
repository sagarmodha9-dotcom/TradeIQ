"""
win_rate_tracker.py — Per-symbol win rate analysis.
Tracks which of the 16 symbols are actually profitable.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from logger import log

def get_symbol_stats(days: int = 30) -> dict:
    """Return win rate and P&L stats per symbol for last N days."""
    try:
        with open("trade_history.json") as f:
            trades = json.load(f)
    except Exception:
        return {}

    cutoff = datetime.now() - timedelta(days=days)
    stats = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "avg_pnl": 0.0,
        "win_rate": 0.0, "best_trade": 0.0,
        "worst_trade": 0.0, "market": "stocks"
    })

    for t in trades:
        try:
            closed_at = t.get("closed_at", "")
            if closed_at:
                dt = datetime.fromisoformat(closed_at.replace("Z", ""))
                if dt < cutoff:
                    continue
            sym = t.get("product_id", "")
            pnl = float(t.get("pnl_usd", 0))
            s = stats[sym]
            s["trades"] += 1
            s["total_pnl"] = round(s["total_pnl"] + pnl, 4)
            s["market"] = t.get("market", "stocks")
            if pnl > 0:
                s["wins"] += 1
            else:
                s["losses"] += 1
            if pnl > s["best_trade"]:
                s["best_trade"] = round(pnl, 2)
            if pnl < s["worst_trade"]:
                s["worst_trade"] = round(pnl, 2)
        except Exception:
            continue

    for sym, s in stats.items():
        if s["trades"] > 0:
            s["win_rate"] = round(s["wins"] / s["trades"], 3)
            s["avg_pnl"]  = round(s["total_pnl"] / s["trades"], 4)

    return dict(sorted(stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True))

def get_weak_symbols(min_trades: int = 5, max_win_rate: float = 0.40) -> list:
    """Return symbols with poor win rate — candidates for removal."""
    stats = get_symbol_stats()
    weak = []
    for sym, s in stats.items():
        if s["trades"] >= min_trades and s["win_rate"] <= max_win_rate:
            weak.append(sym)
    return weak

def should_skip_symbol(symbol: str) -> tuple[bool, str]:
    """
    Returns (True, reason) if symbol has poor recent performance.
    Used as an extra filter in scan loop.
    """
    stats = get_symbol_stats(days=7)
    s = stats.get(symbol)
    if not s or s["trades"] < 3:
        return False, "insufficient data"
    if s["win_rate"] < 0.30 and s["trades"] >= 5:
        return True, f"win_rate={s['win_rate']:.0%} on {s['trades']} trades"
    if s["total_pnl"] < -200:
        return True, f"total_pnl=${s['total_pnl']:.2f} last 7d"
    return False, "ok"
