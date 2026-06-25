# Phase407A — No Progress Exit Lookahead / Logic Audit

Generated: 2026-06-25T07:17:00+09:00
Period: 20260529 – 20260615
Trades audited: 755 (position_cap_accepted)
Verdict: **WARN**

Phase407A WARN: no future MFE; net_delta ¥274912.4 reproduced; 75 no_progress fires after baseline exit — forward shadow only

## Policy under audit (Phase404 best)

- hold_sec: 900.0
- max_mfe_pct: 0.8
- current_pnl_pct: 0.2
- high_update_mode: none
- vwap_dev_mode: none

## Audit checks (7 items)

### 1_mfe_is_so_far_at_judgment: max_mfe is MFE_so_far at judgment (not final MFE)
- Status: **PASS**
- violations: 0
- detail: peak_mfe in state is cumulative max up to tick; verified vs recompute

### 2_current_pnl_at_judgment_price: current_pnl uses judgment-time price only
- Status: **PASS**
- violations: 0
- scope: no_progress_exit only
- detail: pnl uses same-tick candidate price at judgment ts

### 3_exit_price_exists_at_judgment: shadow_exit_price is an actual candidate tick
- Status: **PASS**
- violations: 0
- scope: no_progress_exit only
- detail: shadow_exit_price equals candidate tick price at exit_ts

### 4_not_after_structural_exit: no_progress does not fire after baseline structural exit
- Status: **WARN**
- post_baseline_no_progress_count: 75
- post_baseline_share: 0.487
- detail: simulation uses session-wide candidate path beyond baseline exit_time (counterfactual hold)

### 5_single_exit_judgment: single exit judgment per trade
- Status: **PASS**
- violations: 0
- detail: simulate returns on first no_progress match

### 6_net_delta_reproduced: Phase404 +274,912 yen reproduced without lookahead
- Status: **PASS**
- expected_yen: 274912.4
- actual_yen: 274912.4
- delta_diff_yen: 0.0
- detail: full-session path replay

### 7_sparse_ticks_at_900s: 900s threshold with sparse candidate ticks
- Status: **WARN**
- trades_with_tick_within_60s_of_900s: 718
- trades_without: 37
- detail: no interpolation; first candidate tick at/after threshold triggers rule

## Portfolio reproduction

| Metric | Value |
|--------|-------|
| baseline PnL | ¥127467.6 |
| shadow PnL | ¥402380.0 |
| net_delta (full path) | ¥274912.4 |
| net_delta (capped at baseline exit) | ¥67872.4 |
| saved_loss | ¥485657.7 |
| lost_upside | ¥394149.14 |
| no_progress exits | 154 |
| post-baseline no_progress | 75 |

## Interpretation

The full-path net_delta (+¥274,912) replays each trade on the session-wide
candidate price series and may exit **after** the baseline structural exit time
(counterfactual hold). The capped-at-exit net_delta (+¥67,872) truncates ticks
at baseline exit and is a conservative lower bound for deployable improvement.

No final-MFE or future-price lookahead was found in `build_tick_states` /
`no_progress_matches`. MFE is cumulative (`peak_mfe`) up to each tick.

## Data quality notes

- Price path from session candidate events via _build_price_index (entry_time as tick ts)
- session_end_ts extends to last session candidate tick for symbol, not baseline exit only
- Post-baseline path is intentional counterfactual hold, not final-MFE lookahead
- Capped-at-exit net_delta ¥67872.4 vs full-path ¥274912.4

## Conclusion

No future-MFE bug detected. Improvement magnitude partly relies on post-baseline-exit candidate prices (counterfactual hold). Continue as forward shadow; do not treat capped-at-exit delta as production guarantee.

- Runtime / YAML / Exit unchanged
