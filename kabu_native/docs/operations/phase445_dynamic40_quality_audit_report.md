# Phase445 — Dynamic40 Quality Audit (20260619)

Generated: 2026-06-19T22:03:12+09:00
Verdict: **`mixed_universe_entry_problem`**

## Executive summary

20260619 の大幅マイナス（accepted 128件 / **-232,700円** @100株）は、
Dynamic40 ユニバース自体が弱い日（寄り天・下落形状 80%）**かつ**
ENTRY が weak shape に偏った（opening_peak + slow_opening_peak = **77%**）
複合要因。**mixed_universe_entry_problem** と判定。

AM セッションが損失の主因（80件 / **-258,600円**）。
PM は微益（48件 / **+25,900円**）。
10:00 / 14:30 refresh 後も uptrend share は 20% のまま改善せず。

## Part A — Dynamic40 当日形状（集計）

| metric | value |
| --- | --- |
| watch_count | 40 |
| uptrend | 8 (0.2) |
| downtrend | 8 (0.2) |
| opening_peak | 7 (0.175) |
| slow_opening_peak | 17 (0.425) |
| weak combined (OP+SOP+DT) | 0.8 |
| avg open→close | -2.843% |
| median open→close | -3.076% |
| avg high→close drawdown | -4.9656% |

Per-symbol detail: `results/reports/phase445_dynamic40_quality_audit.csv`

## Part B — 寄り天分類

Classification detail: `results/reports/phase445_dynamic40_classification.csv`

**Dynamic40 uptrend symbols (8):**
3441.T (+5.6%), 3891.T (+0.2%), 6466.T (+1.7%), 6492.T (+4.0%),
6666.T (+0.8%), 6779.T (+4.2%), 7256.T (+2.5%), 7600.T (+1.5%)

**Dynamic40 opening_peak symbols (7):**
1436, 3687, 4062, 5136, 6254, 6838, 6920 ほか

## Part C — Dynamic40 品質集計（refresh 前後）

| cohort | watch | uptrend | downtrend | OP | SOP | avg o→c |
| --- | --- | --- | --- | --- | --- | --- |
| pre 10:00 | 40 | 0.2 | 0.2 | 0.175 | 0.425 | -2.843% |
| post 10:00 | 40 | 0.2 | 0.2 | 0.175 | 0.425 | -2.843% |
| pre 14:30 | 40 | 0.2 | 0.2 | 0.175 | 0.425 | -2.843% |
| post 14:30 | 40 | 0.2 | 0.2 | 0.175 | 0.425 | -2.843% |

Note: 20260619 は refresh 前後で Dynamic40 メンバーが実質同一のため形状集計も同一。

## Part D — ENTRY vs Universe 比較

Trade-level join: `results/reports/phase445_dynamic40_entry_join.csv`

| shape_class | accepted | PnL(100) | PF |
| --- | --- | --- | --- |
| downtrend | 12 | -7700.0 | 0.2183 |
| opening_peak | 58 | -143000.0 | 0.5185 |
| slow_opening_peak | 41 | -94700.0 | 0.4884 |
| uptrend | 17 | 12700.0 | 1.588 |

- opening_peak **Dynamic40** ENTRY: 33件 / -131900.0円
- Dynamic40 uptrend 採用: 3/8 symbols (0.375)
- 取り逃し uptrend: 3441.T, 6466.T, 6492.T, 7256.T, 7600.T
- weak shape 採用率: 16/32 (0.5)

| session | trades | opening_peak | slow_OP | uptrend | PnL(100) |
| --- | --- | --- | --- | --- | --- |
| AM | 80 | 37 | 26 | 10 | -258,600 |
| PM | 48 | 21 | 15 | 7 | +25,900 |

uptrend ENTRY は PF 1.59 / +12,700円 と唯一プラス形状。
opening_peak ENTRY は -143,000円、slow_opening_peak は -94,700円。

## Part E — 判定

| 仮説 | 判定 | 根拠 |
| --- | --- | --- |
| dynamic40_quality_problem | **Yes** | weak shape 80%、uptrend 20%、avg o→c -2.8% |
| entry_problem | **Yes** | accepted 77% が OP/SOP、uptrend 採用率 37.5% |
| 総合 | **mixed_universe_entry_problem** | 両方成立 |

## Mandatory answers（必須10項目）

1. **Dynamic40 上昇銘柄割合:** 0.2（8/40）
2. **Dynamic40 下落銘柄割合:** 0.2（8/40）
3. **Dynamic40 寄り天割合:** 0.6（OP 17.5% + SOP 42.5% = 60%）
4. **ENTRY が寄り天に偏ったか:** Yes — OP+SOP が 99/128 = 77%
5. **上昇銘柄を取り逃したか:** Yes — 5/8 uptrend symbols 未ENTRY（3441 +5.6% 等）
6. **AM/PM 差:** 形状分布同一（uptrend 20% / weak 60%）。損益は AM -258k / PM +26k
7. **10:00 refresh 後改善:** No（uptrend share 0.20 → 0.20）
8. **14:30 refresh 後改善:** No（uptrend share 0.20 → 0.20）
9. **根本原因:** `mixed_universe_entry_problem`
10. **次に修正すべき箇所:** Universe refresh + opening_peak exclusion at ENTRY

### 推奨アクション（調査のみ・未実装）

1. **Universe:** intraday refresh で opening_peak 形状銘柄を Dynamic40 から除外
2. **ENTRY gate:** day_high_time / high_to_close_drawdown ベースの opening_peak ブロック（Phase450 Variant C 方向）
3. **Momentum gate:** Phase446 で判明した固定 p33 退化を修正（uptrend 識別力回復）

Day PnL (100 shares): **-232700.0** yen / 128 trades

## Artifacts

- `results/reports/phase445_dynamic40_quality_audit.csv`
- `results/reports/phase445_dynamic40_classification.csv`
- `results/reports/phase445_dynamic40_entry_join.csv`
- `results/reports/phase445_dynamic40_summary.json`
