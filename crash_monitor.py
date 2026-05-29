#!/usr/bin/env python3
"""
Crash monitor: alerts via Telegram if bot crashes too frequently.
Counts banner appearances in last 10 min. If >3, sends alert.
Run via launchd every 10 minutes.
"""
import os
import sys
import subprocess
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

LOG_FILE = "/Users/sagarmodha/tradeiq/bot.log"
STATE_FILE = "/Users/sagarmodha/tradeiq/.crash_monitor_state"
THRESHOLD = 3  # alert if more than 3 crashes in window
WINDOW_MIN = 10

def get_current_banner_count():
    try:
        result = subprocess.run(['grep', '-c', 'TradeIQ AI Bot', LOG_FILE], 
                              capture_output=True, text=True, timeout=10)
        return int(result.stdout.strip()) if result.returncode == 0 else 0
    except Exception:
        return 0

def get_last_count():
    try:
        with open(STATE_FILE) as f:
            parts = f.read().strip().split(',')
            return int(parts[0]), datetime.fromisoformat(parts[1])
    except Exception:
        return None, None

def save_count(count):
    with open(STATE_FILE, 'w') as f:
        f.write(f"{count},{datetime.now().isoformat()}")

def alert(msg):
    try:
        from notifier import _send
        _send(msg)
    except Exception as e:
        print(f"Alert send failed: {e}")

def check_retry_storm():
    """Detect retry storms: 5+ sell rejections or close failures in last 10 min.
    Returns (storm_detected: bool, summary: str)."""
    try:
        # Get last 10 min of log
        result = subprocess.run(['tail', '-2000', LOG_FILE], capture_output=True, text=True, timeout=10)
        lines = result.stdout.split('\n')
        # Filter to last 10 min by timestamp prefix HH:MM:SS
        cutoff = datetime.now() - timedelta(minutes=10)
        cutoff_str = cutoff.strftime("%H:%M:%S")
        recent = []
        for ln in lines:
            if len(ln) >= 8 and ln[2] == ":" and ln[5] == ":":
                ts = ln[:8]
                if ts >= cutoff_str:
                    recent.append(ln)
        # Count problematic patterns
        rejected = sum(1 for ln in recent if ("sell rejected" in ln.lower() or "sell order failed" in ln.lower()) and "pdt-hold" not in ln.lower() and "pdt_held" not in ln.lower())
        close_blocked = sum(1 for ln in recent if "OPTIONS CLOSE BLOCKED" in ln)
        retry_close = sum(1 for ln in recent if "AUTO-CLOSE" in ln and "cutting loss" in ln)
        bp_skipped = sum(1 for ln in recent if "insufficient buying power" in ln.lower())
        total = rejected + retry_close
        if total >= 5 or close_blocked >= 1:
            return True, (f"Storm detected: {rejected} sell rejects, {retry_close} AUTO-CLOSE retries, "
                         f"{close_blocked} blocked positions, {bp_skipped} BP skips in last 10min")
        return False, f"Healthy: {rejected} rejects, {retry_close} retries, {bp_skipped} BP skips"
    except Exception as e:
        return False, f"Storm check error: {e}"

def main():
    current = get_current_banner_count()
    last_count, last_time = get_last_count()
    
    if last_count is None:
        save_count(current)
        print(f"First run, baseline: {current}")
        return
    
    elapsed = (datetime.now() - last_time).total_seconds() / 60
    new_crashes = current - last_count
    
    print(f"Banner count: {current} (was {last_count}) | new: {new_crashes} | elapsed: {elapsed:.1f}min")
    
    if elapsed >= WINDOW_MIN and new_crashes > THRESHOLD:
        alert(f"⚠️ <b>BOT CRASH ALERT</b>\nBot restarted {new_crashes} times in {elapsed:.0f} minutes.\nNormal: 0-1. Investigate immediately.")
        print(f"ALERT SENT: {new_crashes} crashes in {elapsed:.1f}min")
    elif elapsed >= WINDOW_MIN:
        print(f"OK: {new_crashes} restarts in {elapsed:.1f}min (under threshold)")
    
    # NEW: Retry storm detection (independent of crash detection)
    storm, summary = check_retry_storm()
    print(f"Retry storm check: {summary}")
    if storm:
        alert(f"🌪️ <b>RETRY STORM DETECTED</b>\n{summary}\nBot may be stuck in failed-order loop.\nCheck dashboard or logs.")
        print(f"STORM ALERT SENT: {summary}")
    
    save_count(current)

if __name__ == "__main__":
    main()
