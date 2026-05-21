#!/usr/bin/env python3
"""Analyze losing trades for patterns. Run weekly to find what to fix."""
import json
from collections import defaultdict
from datetime import datetime

with open('trade_history.json') as f:
    trades = json.load(f)

stocks = [t for t in trades if t.get('market') == 'stocks']
losses = [t for t in stocks if float(t.get('pnl_usd', 0)) < 0]
wins = [t for t in stocks if float(t.get('pnl_usd', 0)) > 0]

print(f"=== LOSS PATTERN ANALYSIS ===")
print(f"Total stock trades: {len(stocks)}")
print(f"Wins: {len(wins)} | Losses: {len(losses)} | WR: {len(wins)/max(len(stocks),1)*100:.1f}%")
print()

# By symbol
print("Loss count by symbol:")
sym_loss = defaultdict(int)
sym_wins = defaultdict(int)
for t in losses: sym_loss[t.get('product_id','?')] += 1
for t in wins: sym_wins[t.get('product_id','?')] += 1
for s in sorted(set(list(sym_loss.keys()) + list(sym_wins.keys()))):
    w = sym_wins.get(s, 0); l = sym_loss.get(s, 0)
    wr = w/(w+l)*100 if (w+l) else 0
    print(f"  {s:8s}: {w}W/{l}L = {wr:.0f}% (n={w+l})")
print()

# By hour
print("Loss count by hour (close time):")
hour_loss = defaultdict(int)
hour_wins = defaultdict(int)
for t in losses:
    try:
        h = int(str(t.get('closed_at',''))[11:13])
        hour_loss[h] += 1
    except: pass
for t in wins:
    try:
        h = int(str(t.get('closed_at',''))[11:13])
        hour_wins[h] += 1
    except: pass
for h in sorted(set(list(hour_loss.keys()) + list(hour_wins.keys()))):
    w = hour_wins.get(h, 0); l = hour_loss.get(h, 0)
    wr = w/(w+l)*100 if (w+l) else 0
    print(f"  {h:02d}:00 UTC: {w}W/{l}L = {wr:.0f}%")
print()

# By exit status
print("By exit status:")
status_pnl = defaultdict(lambda: {'count': 0, 'pnl': 0})
for t in stocks:
    s = t.get('status','?')
    status_pnl[s]['count'] += 1
    status_pnl[s]['pnl'] += float(t.get('pnl_usd', 0))
for s, d in sorted(status_pnl.items(), key=lambda x: -x[1]['pnl']):
    print(f"  {s:25s}: {d['count']:3d} trades, net ${d['pnl']:+.2f}")
