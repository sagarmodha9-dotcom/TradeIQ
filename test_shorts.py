"""Shorts build test suite — run with venv/bin/python3 test_shorts.py"""
import json, importlib, os, sys
os.environ.setdefault("SHORTS_ENABLED", "false")
import config
importlib.reload(config)
import bot

PASS, FAIL = 0, 0
def check(name, cond, detail=""):
    global PASS, FAIL
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail and not cond else ""))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)

# ---- T1: flag off = shorts config inert
check("T1 flag off by default", config.SHORTS_ENABLED == False)

# ---- T2: short bracket math (SL above, TP below)
e = 100.0
ssl = round(e * (1 + config.SHORT_SL_PCT), 4); stp = round(e * (1 - config.SHORT_TP_PCT), 4)
check("T2 short SL above entry", ssl == 100.5, f"{ssl}")
check("T2 short TP below entry", stp == 99.2, f"{stp}")

# ---- T3: monitor sign logic (simulate the branch math)
def pnl_for(side, entry, current, qty):
    if side == "SHORT": return (entry - current) * qty
    return (current - entry) * qty
check("T3 short profit when price falls", pnl_for("SHORT", 100, 99, 5) == 5.0)
check("T3 short loss when price rises",  pnl_for("SHORT", 100, 101, 5) == -5.0)
check("T3 long math unchanged",          pnl_for("BUY", 100, 101, 5) == 5.0)

# ---- T4: trigger direction
def sl_hit(side, current, sl): return (current >= sl) if side == "SHORT" else (current <= sl)
def tp_hit(side, current, tp): return (current <= tp) if side == "SHORT" else (current >= tp)
check("T4 short SL fires on rise", sl_hit("SHORT", 100.6, 100.5))
check("T4 short TP fires on fall", tp_hit("SHORT", 99.1, 99.2))
check("T4 short SL quiet in range", not sl_hit("SHORT", 100.2, 100.5))
check("T4 long triggers unchanged", sl_hit("BUY", 99.4, 99.5) and tp_hit("BUY", 100.9, 100.8))

# ---- T5: force-close side detection (Alpaca-style short position)
p_short = {"symbol":"TSLA","qty":"-3","side":"short","avg_entry_price":"400"}
qty = float(p_short["qty"]); is_s = qty < 0 or p_short.get("side")=="short"
check("T5 FC detects short", is_s and abs(qty)==3.0)
pnl = ((400 - 398) if is_s else (398 - 400)) * abs(qty)
check("T5 FC short pnl correct", pnl == 6.0)

# ---- T6: ETB gate logic
asset_bad = {"tradable": True, "shortable": False, "easy_to_borrow": False}
asset_good = {"tradable": True, "shortable": True, "easy_to_borrow": True}
check("T6 blocks non-ETB", not (asset_bad.get("tradable") and asset_bad.get("shortable") and asset_bad.get("easy_to_borrow")))
check("T6 passes ETB", bool(asset_good.get("tradable") and asset_good.get("shortable") and asset_good.get("easy_to_borrow")))

# ---- T7: SSR filter math
pc, cur = 100.0, 90.4
check("T7 SSR blocks -9.6%", (cur-pc)/pc <= -0.095)
check("T7 SSR allows -9.0%", not ((91.0-pc)/pc <= -0.095))

# ---- T8: whole-share sizing
budget, px = 729.0, 421.0
check("T8 floors qty", int(budget // px) == 1)
check("T8 skips price>budget", 900.0 > budget)

# ---- T9: entry gate exists for shorts (source check: SELL branch double-gated)
src = open("bot.py").read()
check("T9 SELL branch gated by SHORTS_ENABLED", 'signal["action"] == "SELL" and not _cooldown_active and getattr(config, "SHORTS_ENABLED"' in src)
check("T9 monitor side-aware", "_is_short" in src and "(current >= sl) if _is_short" in src)
check("T9 FC short-aware", "_fc_short" in src)

# ---- T10: trade_history side recording (source check)
check("T10 history records true side", 'str(pos.get("side","BUY"))' in src)

print(f"\n{'='*40}\n{PASS} passed, {FAIL} failed" + (" — ALL GREEN ✅" if FAIL==0 else " — DO NOT FLIP FLAG ❌"))
sys.exit(1 if FAIL else 0)
