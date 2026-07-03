# Phase619 — Split Freshness Semantics (Event / Board / Trade Stale)

**Verdict:** `phase619_stale_semantics_split_done`  
**Mode:** Shadow / replay classification only — **no production code change**

## Proposed Semantics

| Class | Definition (age at ENTRY eval vs threshold 3.0s) | Proposed action |
|-------|---------------------------------------------------|-----------------|
| **event_stale** | `eval_ts − push.recorded_at` | Reject **`event_stale_price`** |
| **board_stale** | `eval_ts − min(BidTime, AskTime)` | Reject **`data_stale_board`** (unchanged) |
| **trade_stale** | `eval_ts − CurrentPriceTime` (or missing) | Tag **`liquidity_stale_trade`** — liquidity guard, **not** freshness reject |

Stop using **`data_stale_price`** for CurrentPriceTime-only staleness.

---

## Sessions Analyzed

| Day | Session | Cohort | Evals |
|-----|---------|--------|-------|
| 6/25 AM | live_session_080340 | GOOD | 58,332 |
| 6/25 PM | live_session_122535 | GOOD | 62,218 |
| 6/29 AM | live_session_080236 | BAD | 51,557 |
| 6/29 PM | live_session_122526 | BAD | 51,753 |
| 6/30 AM | live_session_091118 | BAD | 34,287 |

Join: `entry_scan_audit.jsonl` eval_ts ↔ nearest `push_jsonl` `recorded_at` + payload timestamps.

---

## Stale Flag Rates (aggregate)

| Cohort | event_stale | board_stale | trade_stale | live `data_stale_price` |
|--------|-------------|-------------|-------------|-------------------------|
| 6/25 GOOD | ~48% | ~50% | ~62% | ~56% |
| 6/29–30 BAD | ~40–52% | ~42–52% | ~66–74% | ~60–66% (630 AM: 4%) |

Many evals carry **multiple** stale flags (e.g. `event+board+trade`).

---

## 6/25 — PBv2 candidates that passed (audit `entry_decision=true`)

| stale_combo at eval | Count (AM+PM) |
|---------------------|---------------|
| **none** (all fresh) | **1,565** |
| trade only | 205 |
| event+board+trade | 153 |
| board+trade | 11 |

**Finding:** GOOD day PBv2 passes mostly with **no stale flags** or **trade-only stale** (205) — i.e. current `data_stale_price` would have blocked many of these under strict CPT rule, but gate still accepted when freshness passed via timing/other paths. Split semantics align trade-only with liquidity guard instead of hard reject.

---

## 6/29–30 — score=3 blocked by trade_stale only

| Metric | Count |
|--------|-------|
| score=3, event NOT stale, trade stale, live `data_stale_price` | **26,857** |
| Would reach PBv2 under liquidity guard (trade-only rescue) | **34,736** (629–630) |

These are candidates **not event-stale** but blocked today solely because `CurrentPriceTime` is old/missing.

---

## PBv2 Reach Delta (virtual)

| Session | Current `data_stale_price` blocks | Liquidity guard rescue | P603 board fallback rescue | Δ (liq − P603) |
|---------|-----------------------------------|------------------------|----------------------------|----------------|
| 625 AM | 30,450 | 16,942 | 19,931 | −2,989 |
| 625 PM | 37,583 | 19,111 | 22,177 | −3,066 |
| 629 AM | 30,914 | 17,023 | 18,845 | −1,822 |
| 629 PM | 34,141 | 15,714 | 17,495 | −1,781 |
| 630 AM | 1,466 | 249 | 76 | +173 |
| **Total** | | **69,039** | **78,524** | **−9,485** |

- **Liquidity guard alone** rescues **fewer** evals than Phase603 board fallback (−9.5k) because P603 also passes **board-fresh + trade-stale + spread OK** even when **event_stale** is true.
- **Recommended hybrid:** `event_stale_price` reject + keep **P603 board fallback** for trade-stale + **`liquidity_stale_trade`** soft guard for remaining trade-only cases.

---

## PnL / PF

Baseline session accepts unchanged (shadow). Virtual ENTRY/PnL requires replay (Phase620) — events lack `pnl_pct` on many rows in audit join.

---

## Artifacts

- `results/reports/phase619_stale_split_summary.csv`
- `results/reports/phase619_event_board_trade_stale_breakdown.csv`
- `results/reports/phase619_pbv2_reach_delta.csv`
- `results/reports/phase619_trade_stale_liquidity_guard_analysis.csv`
- `results/reports/phase619_report.json`

---

## Next Steps (not Phase619)

1. Implement shadow flags in `entry_scan_controller` (no reject path change).
2. Replay A/B: baseline vs split semantics on 625/629/630.
3. Production cutover only after Phase620 PnL/PF parity check.
4. Do **not** drop P603 board fallback — combine with event_stale reject for best coverage.
