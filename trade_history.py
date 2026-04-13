import json
import os
from datetime import datetime, timezone

HISTORY_FILE = "trade_history.json"

def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_trade(trade: dict):
    """Append a closed trade to the persistent history file."""
    history = load_history()
    # Add date field for filtering
    trade["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    trade["saved_at"] = datetime.now(timezone.utc).isoformat()
    history.append(trade)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2, default=str)

def get_trades_by_date(date_str: str = None):
    """Get trades for a specific date (YYYY-MM-DD). Defaults to today."""
    if not date_str:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [t for t in load_history() if t.get("date") == date_str]

def get_daily_summary(date_str: str = None):
    trades = get_trades_by_date(date_str)
    wins   = [t for t in trades if t.get("pnl_usd", 0) > 0]
    losses = [t for t in trades if t.get("pnl_usd", 0) <= 0]
    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    return {
        "date":       date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "trades":     trades,
        "total":      len(trades),
        "wins":       len(wins),
        "losses":     len(losses),
        "win_rate":   len(wins) / len(trades) if trades else 0,
        "total_pnl":  round(total_pnl, 4),
    }
