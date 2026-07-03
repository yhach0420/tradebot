# Phase602 PUSH Raw Timestamp Trace Audit

**Verdict:** `phase602_push_raw_timestamp_trace_audit_done`

**Classification:** A (kabu PUSH feed spec) — not B/C/D

**Target:** 2026-06-29 AM/PM, focus symbols 4265.T, 5592.T, 9417.T, 3192.T, 7352.T, 6327.T, 4664.T, 6522.T

**Constraints:** Runtime / ENTRY / EXIT / CAP unchanged — audit and virtual fallback counts only.

---

## Summary

`data_stale_price` on 6/29 is traced end-to-end from saved push JSONL → freshness recompute → `entry_scan_audit` reject rows. Raw and internal payloads match for all timestamp fields. The dominant pattern is **board-fresh / price-ts-stale**: `BidTime`/`AskTime` update every tick while `CurrentPriceTime` is null (pre-first-trade) or frozen at the last trade print.

Example **4265.T PM** at 12:57:20: raw `CurrentPriceTime=10:11:43`, `BidTime/AskTime=12:57:20`, `price_age≈9937s`, `board_age≈0.3s` — identical in push JSONL and audit log.

---

## Mandatory answers

### 1. raw PUSH時点でCurrentPriceTimeは古かったか

**Yes.** In push JSONL at eval time, stale/missing `CurrentPriceTime` is already present in raw payloads:

| Pattern | Example |
|---------|---------|
| Missing (pre-trade) | 4265.T AM 09:17: `CurrentPrice=null`, `CurrentPriceTime=null`, `CalcPrice=398`, board fresh |
| Frozen (post-trade gap) | 4265.T PM 12:57: `CurrentPriceTime=10:11:43` (last trade), board at 12:57:20 |
| Slightly stale (>3s) | 6522.T AM: price ts 8s old, board 0s old |

35,368 push rows (focus symbols, full day) are `price_ts_stale_board_fresh`. Not introduced by Runtime.

### 2. rawとinternalでCurrentPriceTimeは一致していたか

**Yes.** `live_feature_bridge.enrich_payload()` copies the payload dict unchanged; time fields are not rewritten. Trace CSV shows `internal_matches_raw=True` for all 400 sampled stale rejects. Diff CSV: 0 mismatches on time-field preservation.

### 3. parse/timezone問題はあったか

**No.** `parse_kabu_time` + JST reproduces audit `price_age_sec` within 2s for 342/400 trace samples (58 null-CPT cases flagged inconsistent only because both sides are null — behavior still matches). Timezone audit CSV confirms `+09:00` strings parse correctly; no UTC/JST drift.

### 4. CurrentPriceTime古いがBidTime/AskTime新しいケースは何件か

**35,368** push-row cases across 8 focus symbols (full 6/29 day). Per symbol:

| Symbol | Count | % of push rows |
|--------|------:|---------------:|
| 4265.T | 4,315 | 44.1% |
| 5592.T | 4,197 | 58.2% |
| 9417.T | 636 | 70.4% |
| 3192.T | 710 | 67.4% |
| 7352.T | 6,743 | 46.1% |
| 6327.T | 9,519 | 56.8% |
| 4664.T | 5,671 | 34.8% |
| 6522.T | 3,577 | 66.5% |

Additionally **519** rows with `CurrentPriceTime` null (4265.T only, 5.3% of its pushes — opening auction before first print).

### 5. 大きく動いた銘柄はなぜstaleになったか

**4265.T (+22.9%):** Early session board-only updates (`CalcPrice` 398→406) without `CurrentPrice`/`CurrentPriceTime` until first trade at 09:31. After 10:11 last trade, PM board continued updating but no new trades → `CurrentPriceTime` frozen ~2h45m while price stayed 430.

**Other movers (5592, 7352, 6327, etc.):** Same mechanism — high board tick rate vs slower trade print rate; `CurrentPriceTime` lags board by >3s on most eval cycles. Not a Runtime bug.

### 6. Runtime実装ミスかfeed仕様か

**Feed spec (A), not Runtime (B).** Evidence:

- Raw JSONL already stale/missing before any Runtime transform
- `enrich_payload` does not alter timestamps
- Freshness recompute matches live audit for trade-timestamp cases
- kabu PUSH: `CurrentPriceTime` / `TradingVolumeTime` update on executions; `BidTime`/`AskTime`/`CalcPrice` update on board changes

Replay artifact (Phase600 `datetime.now` vs payload time) is separate issue D — not the live 6/29 root cause.

### 7. data_stale_priceをこのまま維持すべきか

**Yes for live ENTRY.** The 3s guard correctly blocks entry on prices whose last *trade* timestamp is unknown or stale. Board quotes alone do not confirm an executable trade price. Maintain guard; explore fallbacks only via shadow/replay (Phase603).

### 8. board timestamp fallbackは安全か

**Conditionally — shadow/replay only, not direct ENTRY unlock.**

- F1 (use `min(BidTime,AskTime)` as price ts when board fresh): technically workable for freshness pass, but admits symbols with no recent trade — quote may not be fillable at `CurrentPrice`.
- F2 (CalcPrice + board): conflates theoretical price with last trade.
- F3 (shadow-only, no accept): **safest** observability path.

Do not enable F1/F2 for live accept without Phase603 shadow replay validation.

### 9. fallbackを入れると何件PBv2/OR評価まで進むか

Virtual counterfactual on `entry_scan_audit` stale rejects (freshness gate only — does not imply PBv2 accept):

| Fallback | Virtual freshness pass |
|----------|----------------------:|
| F1 board-ts | **8,378** evals (of ~8,400+ stale rejects in focus set) |
| F2 CalcPrice+board | ~8,400 evals |

Per-symbol detail: `phase602_fallback_virtual_pass_counts.csv`. These evals would reach downstream gates; actual PBv2/OR accept count requires Phase603 replay.

### 10. fallback候補の危険性

| ID | Risk |
|----|------|
| F1 | Entry signal on bid/ask quote without confirmed trade; spread/wide-book symbols may show fresh board but stale fair value |
| F2 | `CalcPrice` can move on index/arbitrage adjustments without tradable liquidity at that level |
| F3 | Low risk — logging/shadow only |

4265.T PM: fallback would pass freshness while last trade was 2h45m ago — momentum features may be computed on `CurrentPrice=430` with misleading recency if ts fallback applied.

### 11. Runtime修正が必要か

**No** for this issue. Optional future enhancement (separate phase): configurable price-ts source priority (`CurrentPriceTime` → `TradingVolumeTime` → board ts) behind shadow flag. Not a bug fix.

### 12. 次Phase

**phase603_board_ts_fallback_shadow_replay** — replay 6/29 with F1/F3 shadow paths; measure PBv2/OR counterfactual accepts vs risk cases; no live ENTRY change until validated.

---

## Outputs

| File | Description |
|------|-------------|
| `results/reports/phase602_push_raw_timestamp_trace.csv` | raw→freshness→audit chain (400 stale samples) |
| `results/reports/phase602_raw_internal_freshness_diff.csv` | raw vs internal field diff |
| `results/reports/phase602_price_ts_stale_board_fresh_cases.csv` | per-symbol stale-price/fresh-board counts |
| `results/reports/phase602_current_price_time_missing_cases.csv` | null CurrentPriceTime + CalcPrice-without-ts |
| `results/reports/phase602_timezone_parse_audit.csv` | parse/timezone spot checks |
| `results/reports/phase602_price_ts_fallback_candidates.csv` | F1/F2/F3 definitions + virtual pass totals |
| `results/reports/phase602_fallback_virtual_pass_counts.csv` | per-symbol/session virtual pass |
| `results/reports/phase602_report.json` | full JSON report |

**Script:** `scripts/run_phase602_push_raw_timestamp_trace_audit.py`
