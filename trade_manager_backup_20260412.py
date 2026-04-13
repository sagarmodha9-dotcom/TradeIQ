import csv
import os
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
import config
from logger import log

@dataclass
class Position:
    product_id:  str
    side:        str
    entry_price: float
    quantity:    float
    usd_value:   float
    stop_loss:   float
    take_profit: float
    order_id:    str
    opened_at:   str
    confidence:  float
    reasoning:   str
    status:      str   = "open"
    exit_price:  float = 0.0
    exit_usd:    float = 0.0
    pnl_usd:     float = 0.0
    pnl_pct:     float = 0.0
    closed_at:   str   = ""

class TradeManager:
    def __init__(self, coinbase_client, portfolio_tracker=None):
        self.cb        = coinbase_client
        self.pt        = portfolio_tracker
        self.positions: Dict[str, Position] = {}
        self.closed:    List[Position]      = []
        self.daily_pnl = 0.0
        self._init_csv()

    def kelly_size(self, portfolio_usd, confidence, risk_reward):
        p = confidence
        q = 1 - p
        b = risk_reward
        full_kelly = (p * b - q) / b if b > 0 else 0
        frac_kelly = max(0, full_kelly * config.KELLY_FRACTION)
        final_pct  = min(frac_kelly, config.MAX_POSITION_PCT)
        size_usd   = round(portfolio_usd * final_pct, 2)
        log.info(f"Kelly: conf={p:.0%} RR={b:.1f} -> {final_pct:.1%} = ${size_usd}")
        return size_usd

    def check_daily_loss_limit(self, portfolio_usd):
        limit = portfolio_usd * config.DAILY_LOSS_LIMIT_PCT
        if self.daily_pnl < -limit:
            log.warning(f"Daily loss limit hit: ${self.daily_pnl:.2f}. Halting.")
            return False
        return True

    def open_trade(self, signal, portfolio_usd):
        pid        = signal["product_id"]
        confidence = signal["confidence"]
        rr         = signal.get("risk_reward", 2.0)
        if confidence < config.MIN_CONFIDENCE:
            log.info(f"{pid}: Skip - low confidence {confidence:.0%}")
            return None
        if len(self.positions) >= config.MAX_OPEN_POSITIONS:
            log.info(f"Max positions reached ({config.MAX_OPEN_POSITIONS}) — skipping {pid}")
            return None
        if pid in self.positions:
            log.info(f"{pid}: Skip - already open")
            return None
        if not self.check_daily_loss_limit(portfolio_usd):
            return None
        if signal["action"] != "BUY":
            return None
        usd_balance = self.cb.get_usd_balance()
        size_usd    = self.kelly_size(portfolio_usd, confidence, rr)
        if size_usd < 1.0:
            return None
        if size_usd > usd_balance:
            size_usd = usd_balance * 0.98
        if size_usd < 1.0:
            return None
        try:
            fill = self.cb.place_market_order(product_id=pid, side="BUY", quote_size=size_usd)
        except Exception as e:
            log.error(f"{pid}: Order failed: {e}")
            return None
        entry_price = fill["fill_price"]
        quantity    = fill["fill_quantity"]
        pos = Position(
            product_id=pid, side="BUY", entry_price=entry_price, quantity=quantity,
            usd_value=fill["fill_value_usd"],
            stop_loss=round(entry_price * (1 - config.STOP_LOSS_PCT), 6),
            take_profit=round(entry_price * (1 + config.TAKE_PROFIT_PCT), 6),
            order_id=fill["order_id"], opened_at=fill["timestamp"],
            confidence=confidence, reasoning=signal.get("reasoning", ""),
        )
        self.positions[pid] = pos
        self._append_csv(pos, "OPENED")
        log.info(f"BUY {pid} qty={quantity:.6f} @ ${entry_price:,.4f} SL=${pos.stop_loss:,.4f} TP=${pos.take_profit:,.4f}")
        return pos

    def close_trade(self, pid, reason, current_price):
        pos = self.positions.get(pid)
        if not pos:
            return None
        try:
            fill = self.cb.place_market_order(product_id=pid, side="SELL", base_size=pos.quantity)
        except Exception as e:
            log.error(f"{pid}: Close failed: {e}")
            return None
        exit_price    = fill["fill_price"] or current_price
        pos.status    = reason
        pos.exit_price = exit_price
        pos.exit_usd  = fill["fill_value_usd"]
        pos.pnl_usd   = round((exit_price - pos.entry_price) * pos.quantity, 4)
        pos.pnl_pct   = round((exit_price / pos.entry_price - 1) * 100, 3)
        pos.closed_at = fill["timestamp"]
        self.daily_pnl += pos.pnl_usd
        self.closed.append(pos)
        try:
            from trade_history import save_trade
            save_trade({"product_id": pid, "side": pos.side, "entry_price": pos.entry_price, "exit_price": exit_price, "pnl_usd": pos.pnl_usd, "pnl_pct": pos.pnl_pct, "status": reason, "market": "crypto", "confidence": pos.confidence, "opened_at": pos.opened_at, "closed_at": pos.closed_at})
        except Exception as _e:
            pass
        del self.positions[pid]
        self._append_csv(pos, "CLOSED")

        # Update portfolio tracker
        if self.pt:
            self.pt.record_crypto_trade(pos.pnl_usd, pos.pnl_usd > 0)

        emoji = "🟢" if pos.pnl_usd >= 0 else "🔴"
        log.info(f"{emoji} CLOSED {pid} [{reason}] PnL: ${pos.pnl_usd:.4f} ({pos.pnl_pct:+.2f}%)")
        try:
            from notifier import alert_trade_closed
            alert_trade_closed(pid, pos.side, pos.entry_price, exit_price, pos.pnl_usd, pos.pnl_pct, reason, "crypto")
        except Exception as _e:
            pass
        return pos

    def monitor_positions(self):
        closed = []
        for pid, pos in list(self.positions.items()):
            try:
                current_price = self.cb.get_best_bid_ask(pid)["mid"]
            except Exception:
                continue
            if current_price <= pos.stop_loss:
                c = self.close_trade(pid, "closed_sl", current_price)
                if c: closed.append(asdict(c))
            elif current_price >= pos.take_profit:
                c = self.close_trade(pid, "closed_tp", current_price)
                if c: closed.append(asdict(c))
        return closed

    def stats(self):
        total = len(self.closed)
        wins  = sum(1 for p in self.closed if p.pnl_usd > 0)
        return {
            "total_trades":   total,
            "wins":           wins,
            "losses":         total - wins,
            "win_rate":       round(wins / total, 3) if total else 0,
            "total_pnl_usd":  round(sum(p.pnl_usd for p in self.closed), 4),
            "daily_pnl":      round(self.daily_pnl, 4),
            "open_positions": len(self.positions),
        }

    def _init_csv(self):
        if not os.path.exists(config.TRADES_CSV):
            with open(config.TRADES_CSV, "w", newline="") as f:
                csv.writer(f).writerow([
                    "event","product_id","side","entry_price","exit_price",
                    "quantity","usd_value","stop_loss","take_profit",
                    "pnl_usd","pnl_pct","confidence","status",
                    "order_id","opened_at","closed_at","reasoning"
                ])

    def _append_csv(self, pos, event):
        with open(config.TRADES_CSV, "a", newline="") as f:
            csv.writer(f).writerow([
                event, pos.product_id, pos.side, pos.entry_price, pos.exit_price,
                pos.quantity, pos.usd_value, pos.stop_loss, pos.take_profit,
                pos.pnl_usd, pos.pnl_pct, pos.confidence, pos.status,
                pos.order_id, pos.opened_at, pos.closed_at, pos.reasoning
            ])
