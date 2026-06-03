# TradeIQ — Short-Selling Build Spec
**Drafted:** June 3, 2026 (launch eve, no code touched)
**Build day:** Saturday June 6 · **Test day:** Sunday June 7 · **Earliest enable:** your call, flag-gated
**Branch:** `shorts` off `day-trading` · **Gate:** `SHORTS_ENABLED=false` in `.env` until tests green

> Rationale: directional completeness. Long-only day trading can only act on half the tape.
> This is a design decision, not a reaction to week-1 data — but Friday's data feeds the spec
> (see §9 open questions).

---

## 1. Scope

**In:** Short stock entries (sell-to-open), buy-to-close exits, inverted SL/TP brackets,
borrow eligibility checks, HTB rejection handling, short-aware force-close, sizing, alerts,
dashboard labeling, defense-layer test suite.

**Out:** Options (still 0), crypto (shelved), any change to long-side logic, any param
changes to week-1 config.

---

## 2. Config (all in `.env` — remember: .env overrides config.py)

```
SHORTS_ENABLED=false          # master gate; bot must behave byte-identically when false
SHORT_SL_PCT=0.005            # SL is ABOVE entry for shorts
SHORT_TP_PCT=0.008            # TP is BELOW entry
SHORT_HTB_COOLDOWN_MIN=120    # symbol-level cooldown after a borrow rejection
```

Shorts share the existing global limits — they are NOT a separate budget:
- Count toward `MAX_TRADES_PER_DAY=15`
- Count toward `MAX_OPEN_POSITIONS=4` (long + short combined)
- Losses count toward `DAILY_LOSS_LIMIT_USD` and the 3-losses/30min halt
- Same entry window (9:45+) and force-close (15:50)

---

## 3. Entry path

1. Signal fires short (criteria TBD — §9).
2. **Pre-flight borrow check:** `GET /v2/assets/{symbol}` → require `tradable=true`,
   `shortable=true`, `easy_to_borrow=true`. Alpaca only supports ETB shorts; HTB names
   are rejected at the broker anyway, so check first and skip cleanly.
3. **No-flip guard:** if any long position exists in the symbol, skip. Alpaca rejects
   orders that would flip a position long→short in one order, and we never want
   accidental netting logic. One direction per symbol per day (simplest correct rule).
4. **Whole shares only:** fractional shorting is not supported. Qty = floor(position_usd / price),
   min 1 share. If price > position_usd (e.g. a $900 name at ~$730 sizing), skip the symbol —
   do NOT round up.
5. Place `side="sell"` market order (or bracket — §4).

---

## 4. Bracket / exit math (inverted)

For entry price E:
- **Stop-loss:** BUY stop at `E × (1 + SHORT_SL_PCT)` — above entry
- **Take-profit:** BUY limit at `E × (1 - SHORT_TP_PCT)` — below entry
- P&L per share = `E - exit_price` (positive when price fell)

Audit every place the monitor computes `(current - entry) / entry` — that sign flips for
shorts. Grep targets: SL check, TP check, unrealized P&L for dashboard, daily-loss
accumulation, Telegram message formatting. A sign error here turns the SL into a TP.

**Force-close 15:50:** must submit BUY market for short positions, SELL for longs.
The existing force-close assumes sell-to-close — this is a guaranteed change point.

---

## 5. Rejection & failure handling

- **Borrow/HTB rejection at order time** (race between pre-flight and placement):
  log as `SKIP-HTB`, apply `SHORT_HTB_COOLDOWN_MIN` to the symbol, do NOT count against
  `MAX_TRADES_PER_DAY`, do NOT count as a retry-storm strike. Add an explicit exclusion
  in `crash_monitor.check_retry_storm` (same pattern the old PDT-hold exclusion used).
- **SSR (Reg SHO 201):** when a stock is down ≥10% from prior close, short sales are
  uptick-only for the rest of day + next day. Symptom: rejections or non-fills on names
  that pass the ETB check. Cheap mitigation: skip short entries on any symbol down ≥9.5%
  from prior close. (Ironically these are exactly the names a short signal will flag, so
  this filter WILL fire — log it visibly as `SKIP-SSR`.)
- **Partial fills on bracket legs:** same handling as long side; verify broker-truth
  reconciliation treats negative qty correctly.

---

## 6. State & reconciliation

- `bot_state.json` positions need a `side` field (or signed qty). Decide one convention
  and enforce it everywhere — mixed conventions are how reconciliation bugs happen.
- Alpaca reports short positions with `side: "short"` and negative `qty`. Bidirectional
  reconciliation must not "correct" a legit short into a phantom long.
- Dashboard `/status`: label side per position; unrealized P&L math per §4.
- Telegram alerts: all 5 paths (open/TP/SL/force-close/halt) must say SHORT and show
  the right sign. Pipe-test each.

---

## 7. Risk notes (one-time, acknowledged, not lifestyle commentary)

- Short losses are uncapped above entry; the 0.5% SL is the protection, and it's a STOP,
  not a guarantee — a halt-and-gap-up fills you wherever it reopens. The $60 daily limit
  and 4-position cap bound the realistic blast radius.
- Intraday-only means no borrow fees or dividend liability accrue (positions never held
  overnight). Force-close is therefore a hard safety requirement for shorts, not a
  preference — its short-side test is the most important one in §8.

---

## 8. Defense-layer test checklist (Sunday — all must pass before flag flip)

1. `SHORTS_ENABLED=false` → zero behavior change vs day-trading branch (diff the logs of
   a dry-run scan).
2. Pre-flight skips a non-shortable symbol cleanly (mock asset response).
3. No-flip guard: existing long in symbol blocks short entry.
4. Bracket math: SL above / TP below entry, verified against hand-computed values.
5. Sign correctness: simulated price moves up → SL fires, P&L negative; down → TP fires,
   P&L positive. Dashboard and Telegram show matching signs.
6. HTB rejection → cooldown applied, no retry-storm strike, no trade-slot burn.
7. SSR filter skips a symbol down ≥9.5%.
8. Force-close submits BUY for a short (fake position injection, same method as the
   dashboard pipeline test).
9. Daily-loss limit accumulates short losses correctly and halts.
10. Reconciliation round-trip: inject Alpaca-style short position, confirm bot_state
    matches and survives restart.

(Mirrors the long-side 10/10 suite — the one that caught the equity-floor launch blocker.)

---

## 9. Open questions for Friday-evening spec session (need 2 days of live data)

1. **Signal:** pure mirror of the long signal, or separate criteria? (Claude API prompt
   change either way — what does the long signal's day-1/2 quality look like?)
2. **Slippage reality:** is 0.5% SL survivable on the short side given observed fills?
   Shorts fill on the bid — entry slippage is directionally worse.
3. **Universe:** same symbols both directions, or a short-specific list? (COIN/TSLA are
   ETB; verify the rest of the universe.)
4. **Cooldowns:** do 12/35 win/loss cooldowns apply per-direction or globally per symbol?
   (Recommend globally — simpler, and §3.3 already enforces one direction per symbol/day.)

---

## 10. Build-day sequence (Saturday)

1. Branch `shorts`, add config flags, wire the master gate first.
2. Entry path (§3) → bracket math (§4) → monitor/exit sign audit → force-close (§4).
3. Rejection handling (§5) → state/reconciliation (§6) → alerts/dashboard.
4. Syntax check, commit per logical unit, push.
5. Sunday: run §8 suite. Fix, re-run until 10/10. Do NOT merge to day-trading until green.
6. Flag stays false in production until you explicitly flip it.
