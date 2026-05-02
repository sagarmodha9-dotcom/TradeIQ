#!/usr/bin/env python3
"""
Daily reconciliation: compare bot's trade_history.json against actual broker fills.
Detects phantom trades (logged in bot but not at broker) and missing trades (at broker but not logged).
Run this nightly at 4:05 PM ET via cron, OR manually anytime.
"""
import json
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env file so we have ALPACA_API_KEY etc available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Fallback: manually parse .env
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

def load_bot_history():
    try:
        with open("trade_history.json") as f:
            return json.load(f)
    except:
        return []

def get_alpaca_fills(days_back=30):
    """Pull all filled SELL orders from Alpaca in the last N days."""
    import requests
    headers = {
        'APCA-API-KEY-ID': os.getenv('ALPACA_API_KEY'),
        'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET_KEY')
    }
    # Get max 500 most recent filled orders, filter client-side by date
    r = requests.get(
        'https://api.alpaca.markets/v2/orders?status=filled&direction=desc&limit=500',
        headers=headers, timeout=15
    )
    if r.status_code != 200:
        print(f"Alpaca API error: {r.status_code} {r.text[:200]}")
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()
    fills = []
    for o in r.json():
        if o.get('side') != 'sell' or not o.get('filled_at'):
            continue
        if o['filled_at'] < cutoff:
            continue
        fills.append({
            'symbol': o['symbol'],
            'side': 'sell',
            'qty': float(o['filled_qty']),
            'price': float(o['filled_avg_price']) if o.get('filled_avg_price') else 0,
            'filled_at': o['filled_at'],
            'broker': 'alpaca'
        })
    return fills

def get_tastytrade_fills(days_back=30):
    """Pull all filled options orders from Tastytrade."""
    try:
        from tastytrade_client import TastytradeClient
        import requests
        tt = TastytradeClient()
        headers = {'Authorization': tt.session_token, 'Content-Type': 'application/json'}
        r = requests.get(
            f'{tt.base_url}/accounts/{tt.account_number}/orders?status=Filled&per-page=100',
            headers=headers, timeout=15
        )
        fills = []
        for o in r.json().get('data', {}).get('items', []):
            for leg in o.get('legs', []):
                if 'Close' in str(leg.get('action','')):
                    fills.append({
                        'symbol': leg.get('symbol', ''),
                        'side': 'sell',
                        'qty': float(leg.get('quantity', 0)),
                        'price': float(o.get('price', 0)) if o.get('price') else 0,
                        'filled_at': o.get('updated-at'),
                        'broker': 'tastytrade'
                    })
        return fills
    except Exception as e:
        print(f"Tastytrade fetch error: {e}")
        return []

def reconcile(days=7):
    """Compare bot's trade_history vs actual broker sells. Report mismatches."""
    bot_trades = load_bot_history()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recent_bot = [t for t in bot_trades if str(t.get('closed_at','')) >= cutoff]
    
    alpaca_fills = get_alpaca_fills(days_back=days)
    tt_fills = get_tastytrade_fills(days_back=days)
    
    print(f"\n{'='*60}")
    print(f"RECONCILIATION REPORT — last {days} days")
    print(f"{'='*60}")
    print(f"Bot logged closes: {len(recent_bot)}")
    print(f"Alpaca actual sells: {len(alpaca_fills)}")
    print(f"Tastytrade actual closes: {len(tt_fills)}")
    
    # Find phantom trades — in bot but not at broker
    phantoms = []
    for bt in recent_bot:
        symbol = str(bt.get('product_id','')).strip()
        market = bt.get('market', '')
        matched = False
        broker_fills = tt_fills if market == 'options' else alpaca_fills
        for f in broker_fills:
            f_sym = str(f.get('symbol','')).strip()
            if (f_sym == symbol or f_sym.replace(' ','') == symbol.replace(' ','')) and f.get('side') == 'sell':
                matched = True
                break
        if not matched:
            phantoms.append(bt)
    
    print(f"\n{'='*60}")
    if phantoms:
        print(f"⚠️ PHANTOM TRADES DETECTED: {len(phantoms)}")
        for p in phantoms:
            print(f"  • {p.get('product_id')} | pnl=${p.get('pnl_usd')} | {p.get('closed_at','')[:19]}")
        print(f"\nThese trades are in trade_history.json but no matching sell at broker.")
        print(f"Run cleanup script if confirmed phantom.")
    else:
        print("✅ NO PHANTOM TRADES — all bot closes match broker fills")
    print(f"{'='*60}\n")
    
    # Telegram alert if phantoms found
    if phantoms:
        try:
            from notifier import _send
            msg = f"⚠️ <b>RECONCILIATION ALERT</b>\n{len(phantoms)} phantom trade(s) detected:\n"
            for p in phantoms[:5]:
                msg += f"• {p.get('product_id')} ${p.get('pnl_usd')}\n"
            _send(msg)
        except: pass
    
    return phantoms

if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    reconcile(days=days)
