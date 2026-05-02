#!/usr/bin/env python3
"""
Performance metrics calculator for TradeIQ bot.
Computes real trading metrics from trade_history.json.
"""
import json
import os
import math
from datetime import datetime
from collections import defaultdict


def _load_trades():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_history.json")) as f:
            return json.load(f)
    except Exception:
        return []


def _safe_pnl(t):
    try:
        return float(t.get("pnl_usd", 0))
    except (TypeError, ValueError):
        return 0.0


def _is_win(t):
    return _safe_pnl(t) > 0


def _is_loss(t):
    return _safe_pnl(t) < 0


def _sample_size_warning(n):
    if n < 10:
        return "INSUFFICIENT_DATA"
    if n < 30:
        return "LOW_CONFIDENCE"
    if n < 100:
        return "MODERATE_CONFIDENCE"
    return "RELIABLE"


def calculate_basic_stats(trades):
    """Basic win/loss stats."""
    if not trades:
        return {"trade_count": 0, "warning": "NO_TRADES"}
    
    wins = [t for t in trades if _is_win(t)]
    losses = [t for t in trades if _is_loss(t)]
    breakeven = [t for t in trades if _safe_pnl(t) == 0]
    
    total_pnl = sum(_safe_pnl(t) for t in trades)
    win_pnl = sum(_safe_pnl(t) for t in wins)
    loss_pnl = sum(_safe_pnl(t) for t in losses)  # negative number
    
    avg_win = win_pnl / len(wins) if wins else 0
    avg_loss = loss_pnl / len(losses) if losses else 0  # negative
    avg_loss_abs = abs(avg_loss)
    
    win_rate = len(wins) / len(trades) if trades else 0
    
    # Profit factor: gross wins / gross losses (need > 1.5 for real edge)
    profit_factor = (win_pnl / abs(loss_pnl)) if loss_pnl < 0 else float('inf') if win_pnl > 0 else 0
    
    # Win/loss ratio: avg win size / avg loss size
    win_loss_ratio = (avg_win / avg_loss_abs) if avg_loss_abs > 0 else float('inf') if avg_win > 0 else 0
    
    # Expectancy: average $ per trade
    expectancy = total_pnl / len(trades)
    
    return {
        "trade_count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(breakeven),
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_pnl": round(win_pnl, 2),
        "loss_pnl": round(loss_pnl, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "infinite",
        "win_loss_ratio": round(win_loss_ratio, 2) if win_loss_ratio != float('inf') else "infinite",
        "expectancy_per_trade": round(expectancy, 2),
        "best_trade": round(max((_safe_pnl(t) for t in trades), default=0), 2),
        "worst_trade": round(min((_safe_pnl(t) for t in trades), default=0), 2),
        "sample_warning": _sample_size_warning(len(trades)),
    }


def calculate_drawdown(trades):
    """Max drawdown from equity curve."""
    if not trades:
        return {"max_drawdown": 0, "max_drawdown_pct": 0}
    
    # Sort by closed_at
    sorted_trades = sorted(trades, key=lambda t: str(t.get("closed_at", "")))
    
    equity = 0
    peak = 0
    max_dd = 0
    max_dd_pct = 0
    
    for t in sorted_trades:
        equity += _safe_pnl(t)
        peak = max(peak, equity)
        dd = peak - equity
        dd_pct = (dd / peak * 100) if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd_pct
    
    return {
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "current_equity": round(equity, 2),
        "peak_equity": round(peak, 2),
    }


def calculate_consecutive_streaks(trades):
    """Track consecutive wins and losses."""
    if not trades:
        return {"max_consecutive_wins": 0, "max_consecutive_losses": 0, "current_streak": 0}
    
    sorted_trades = sorted(trades, key=lambda t: str(t.get("closed_at", "")))
    
    max_win_streak = 0
    max_loss_streak = 0
    current_win_streak = 0
    current_loss_streak = 0
    
    for t in sorted_trades:
        if _is_win(t):
            current_win_streak += 1
            current_loss_streak = 0
            max_win_streak = max(max_win_streak, current_win_streak)
        elif _is_loss(t):
            current_loss_streak += 1
            current_win_streak = 0
            max_loss_streak = max(max_loss_streak, current_loss_streak)
    
    # Current streak: positive = wins, negative = losses
    current_streak = current_win_streak if current_win_streak > 0 else -current_loss_streak
    
    return {
        "max_consecutive_wins": max_win_streak,
        "max_consecutive_losses": max_loss_streak,
        "current_streak": current_streak,
    }


def calculate_per_symbol(trades):
    """Performance grouped by symbol."""
    by_symbol = defaultdict(list)
    for t in trades:
        # Extract underlying symbol — use "underlying" field, or strip product_id
        sym = t.get("underlying") or str(t.get("product_id", "")).split()[0]
        if sym:
            by_symbol[sym].append(t)
    
    stats = {}
    for sym, syms_trades in by_symbol.items():
        wins = [t for t in syms_trades if _is_win(t)]
        total = sum(_safe_pnl(t) for t in syms_trades)
        stats[sym] = {
            "trades": len(syms_trades),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(syms_trades), 2) if syms_trades else 0,
            "total_pnl": round(total, 2),
            "avg_pnl_per_trade": round(total / len(syms_trades), 2) if syms_trades else 0,
        }
    
    # Sort by total P&L descending
    return dict(sorted(stats.items(), key=lambda x: x[1]["total_pnl"], reverse=True))


def calculate_per_market(trades):
    """Stocks vs options performance split."""
    stocks = [t for t in trades if t.get("market") == "stocks"]
    options = [t for t in trades if t.get("market") == "options"]
    
    return {
        "stocks": calculate_basic_stats(stocks),
        "options": calculate_basic_stats(options),
    }


def calculate_per_strategy(trades):
    """Performance by strategy (TP, SL, time exit, earnings, etc)."""
    by_status = defaultdict(list)
    for t in trades:
        status = t.get("status", "unknown")
        by_status[status].append(t)
    
    stats = {}
    for status, status_trades in by_status.items():
        total = sum(_safe_pnl(t) for t in status_trades)
        wins = [t for t in status_trades if _is_win(t)]
        stats[status] = {
            "count": len(status_trades),
            "win_rate": round(len(wins) / len(status_trades), 2) if status_trades else 0,
            "total_pnl": round(total, 2),
            "avg_pnl": round(total / len(status_trades), 2) if status_trades else 0,
        }
    
    return stats


def calculate_simple_sharpe(trades):
    """Simplified Sharpe ratio. Need lots of data for this to be meaningful."""
    if len(trades) < 10:
        return {"sharpe": None, "note": "Need 10+ trades for meaningful Sharpe"}
    
    pnls = [_safe_pnl(t) for t in trades]
    mean_pnl = sum(pnls) / len(pnls)
    variance = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
    std_dev = math.sqrt(variance)
    
    sharpe = (mean_pnl / std_dev) if std_dev > 0 else 0
    
    return {
        "sharpe": round(sharpe, 2),
        "mean_pnl": round(mean_pnl, 2),
        "std_dev": round(std_dev, 2),
        "note": "Per-trade Sharpe (not annualized)" if len(trades) < 100 else None,
    }


def get_full_metrics():
    """Compute all metrics. This is the main entry point for dashboard."""
    trades = _load_trades()
    
    return {
        "computed_at": datetime.now().isoformat(),
        "overall": calculate_basic_stats(trades),
        "drawdown": calculate_drawdown(trades),
        "streaks": calculate_consecutive_streaks(trades),
        "by_market": calculate_per_market(trades),
        "by_symbol": calculate_per_symbol(trades),
        "by_strategy": calculate_per_strategy(trades),
        "sharpe": calculate_simple_sharpe(trades),
    }


if __name__ == "__main__":
    import sys
    metrics = get_full_metrics()
    print(json.dumps(metrics, indent=2, default=str))
