"""
portfolio_tracker.py — Tracks running portfolio balances for crypto and stocks
Persists to portfolio_state.json so balances survive bot restarts
"""
import json
import os
from datetime import datetime

PORTFOLIO_FILE = "portfolio_state.json"

DEFAULT_STATE = {
    "crypto_balance":  5000.0,
    "kalshi_balance": 1000.0,
    "kalshi_pnl":     0.0,
    "kalshi_trades":  0,
    "kalshi_wins":    0,
    "stock_balance":   5000.0,
    "crypto_pnl":      0.0,
    "stock_pnl":       0.0,
    "crypto_trades":   0,
    "crypto_wins":     0,
    "stock_trades":    0,
    "stock_wins":      0,
    "created_at":      datetime.utcnow().isoformat(),
    "updated_at":      datetime.utcnow().isoformat(),
}

class PortfolioTracker:
    def __init__(self):
        self.state = self._load()

    def _load(self):
        if os.path.exists(PORTFOLIO_FILE):
            try:
                with open(PORTFOLIO_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        self._save(DEFAULT_STATE.copy())
        return DEFAULT_STATE.copy()

    def _save(self, state=None):
        s = state or self.state
        s["updated_at"] = datetime.utcnow().isoformat()
        with open(PORTFOLIO_FILE, "w") as f:
            json.dump(s, f, indent=2)

    def record_crypto_trade(self, pnl_usd: float, win: bool):
        self.state["crypto_balance"] += pnl_usd
        self.state["crypto_pnl"]    += pnl_usd
        self.state["crypto_trades"] += 1
        if win:
            self.state["crypto_wins"] += 1
        self._save()

    def record_stock_trade(self, pnl_usd: float, win: bool):
        self.state["stock_balance"] += pnl_usd
        self.state["stock_pnl"]    += pnl_usd
        self.state["stock_trades"] += 1
        if win:
            self.state["stock_wins"] += 1
        self._save()

    def update_stock_balance(self, alpaca_equity: float, initial: float = 100000.0):
        """Update stock balance from Alpaca account equity."""
        pnl = alpaca_equity - initial
        self.state["stock_pnl"] = round(pnl, 2)
        self.state["stock_balance"] = round(5000 + pnl, 2)
        self._save()

    @property
    def crypto_balance(self):
        return round(self.state["crypto_balance"], 2)

    @property
    def stock_balance(self):
        return round(self.state["stock_balance"], 2)

    @property
    def total_balance(self):
        return round(self.state["crypto_balance"] + self.state["stock_balance"], 2)

    @property
    def crypto_pnl(self):
        return round(self.state["crypto_pnl"], 2)

    @property
    def stock_pnl(self):
        return round(self.state["stock_pnl"], 2)

    @property
    def total_pnl(self):
        return round(self.state["crypto_pnl"] + self.state["stock_pnl"], 2)

    @property
    def crypto_win_rate(self):
        t = self.state["crypto_trades"]
        return round(self.state["crypto_wins"] / t, 3) if t > 0 else 0

    @property
    def stock_win_rate(self):
        t = self.state["stock_trades"]
        return round(self.state["stock_wins"] / t, 3) if t > 0 else 0

    def to_dict(self):
        return {
            "crypto_balance":   self.crypto_balance,
            "stock_balance":    self.stock_balance,
            "total_balance":    self.total_balance,
            "crypto_pnl":       self.crypto_pnl,
            "stock_pnl":        self.stock_pnl,
            "total_pnl":        self.total_pnl,
            "crypto_trades":    self.state["crypto_trades"],
            "crypto_wins":      self.state["crypto_wins"],
            "crypto_win_rate":  self.crypto_win_rate,
            "stock_trades":     self.state["stock_trades"],
            "stock_wins":       self.state["stock_wins"],
            "stock_win_rate":   self.stock_win_rate,
        }

    def record_kalshi_trade(self, pnl_usd: float, win: bool):
        self.state.setdefault("kalshi_balance", 1000.0)
        self.state.setdefault("kalshi_pnl", 0.0)
        self.state.setdefault("kalshi_trades", 0)
        self.state.setdefault("kalshi_wins", 0)
        self.state["kalshi_balance"] += pnl_usd
        self.state["kalshi_pnl"]     += pnl_usd
        self.state["kalshi_trades"]  += 1
        if win: self.state["kalshi_wins"] += 1
        self._save()
