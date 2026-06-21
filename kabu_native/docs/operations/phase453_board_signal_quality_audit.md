# Phase453 — Board Signal Quality Audit

Generated: 2026-06-19T23:37:25+09:00  
Period: 20260529..20260619  
Eval pool: Momentum:low + (Board:mid OR Board:high) + NOT high_drift (**n=681**, mid=651 / high=30)

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.

## Mandatory answers

| # | Question | Answer |
|---|----------|--------|
| 1 | Board:mid PF | **1.1233** (CAP5 replay) |
| 2 | Board:high PF | **1.2318** (CAP5 replay) |
| 3 | Board:high優位は本物か | **Yes (CAP5 PF edge +10.9%)** — but **n=30**; trade-level PF 0.80; verdict **partial** |
| 4 | Board強度と期待値は単調増加か | **No** — bins 0.53–0.56 PF 0.22 → 0.56–0.60 PF 2.73 → 0.60–0.65 PF 0.62 → 0.65+ PF 2.20 |
| 5 | Board:high主な勝ちパターン | **EOD uptrend** (11/20 wins); AM **10h** block; dist≈5.8%; VWAP dev≈−1.6%; r5>0 |
| 6 | Board:high主な負けパターン | **opening_peak** (8/20 losses); PM **13h**; dist≈4.9%; r15/r30 negative |
| 7 | 6976 bucket | **Board:mid only** (46/46 in pool); pool PnL +137,499 yen — **not driving Board:high PF** |
| 8 | 6920 bucket | **Board:mid only** (8/8); pool PnL −71,000 yen |
| 9 | BoardをENTRY中心にして良いか | **Conditional yes** — board tertile useful with shape filter; not standalone |
| 10 | 次のRuntime改善候補 | See below |

**Verdict:** `board_signal_partial`

---

## Part A — Board bucket (CAP5 replay)

| Bucket | Candidates | Accepted | PnL | PF | WinRate | StopRate | MaxDD |
|--------|------------|----------|-----|-----|---------|----------|-------|
| Board:mid | 651 | 609 | 122,311 | 1.1233 | 0.5616 | 0.1248 | 160,051 |
| Board:high | 30 | 30 | 4,311 | 1.2318 | 0.4333 | 0.2000 | 8,590 |

Trade-level (all eval-pool candidates, no cap):

| Bucket | Count | PnL | PF | WinRate | StopRate |
|--------|-------|-----|-----|---------|----------|
| Board:mid | 651 | −99,391 | 0.929 | 0.564 | 0.143 |
| Board:high | 30 | −4,989 | 0.796 | 0.467 | 0.267 |

CAP5 replay PF matches Phase451B cohort table; raw candidate PF is lower (overlap/cap interaction).

---

## Part B — Shape breakdown (trade-level)

### Board:mid

| Shape | Count | PnL | PF |
|-------|-------|-----|-----|
| uptrend | 195 | +262,565 | 1.89 |
| opening_peak | 113 | −51,459 | 0.68 |
| slow_opening_peak | 141 | +3,101 | 1.01 |
| downtrend | 48 | −77,898 | 0.46 |

### Board:high

| Shape | Count | PnL | PF |
|-------|-------|-----|-----|
| **uptrend** | **14** | **+7,010** | **1.92** |
| opening_peak | 9 | −10,500 | 0.10 |
| downtrend | 3 | −1,600 | 0.65 |

**Key:** Board:high PF edge is **concentrated in uptrend** (PF 1.92, n=14). opening_peak on Board:high is toxic (PF 0.10).

---

## Part C/D — TOP20 feature summary

**Board:high winners:** uptrend-dominant, 10h entry, day_high dist ~5.8%, below VWAP (−1.6%).

**Board:high losers:** opening_peak-dominant, 13h entry, similar dist but worse r15/r30.

**Board:mid winners/losers:** same shape skew — uptrend wins, opening_peak/downtrend lose (see CSV).

---

## Part E — Imbalance strength bins (eval pool)

| Bin | Count | PnL | PF |
|-----|-------|-----|-----|
| 0.53–0.56 | 6 | −11,399 | 0.22 |
| 0.56–0.60 | 4 | +3,800 | 2.73 |
| 0.60–0.65 | 7 | −1,600 | 0.62 |
| 0.65+ | 13 | +4,211 | 2.20 |

Not monotonic — weak linear signal alone.

---

## Part F — Correlation (entry_order_book_imbalance)

| Pair | Pearson r |
|------|-----------|
| imbalance ↔ PnL | **−0.008** |
| imbalance ↔ stop | +0.040 |
| imbalance ↔ uptrend (EOD) | +0.071 |

Board imbalance alone has **negligible PnL correlation**; uptrend alignment is weak.

---

## Interpretation

1. **Board:high PF 1.23 vs mid 1.12 is real in CAP5 replay** but sample is tiny (30).
2. Signal is **not universal** — works mainly on **uptrend days**; opening_peak on high board fails badly.
3. **6976/6920 are all Board:mid** in this pool — high-bucket PF is not symbol-concentration from 6976.
4. Board should stay **secondary to shape/momentum**, not sole ENTRY center.

## Next Runtime improvement candidates

1. **Board:high + uptrend proxy** (r15/r30 pass) — tighten high bucket to continuation days only
2. **Keep Phase452 weak_shape_reject** — blocks opening_peak where Board:high loses most
3. **Board strength tier** — avoid 0.53–0.56 band; consider 0.56+ with shape filter
4. **Live monitoring** — Board:high accepted count / PF with n<50 caution flag

## Outputs

- `results/reports/phase453_board_signal_quality.csv`
- `results/reports/phase453_board_bucket_analysis.csv`
- `results/reports/phase453_board_summary.json`

Run: `python scripts/run_phase453_board_signal_quality_audit.py`
