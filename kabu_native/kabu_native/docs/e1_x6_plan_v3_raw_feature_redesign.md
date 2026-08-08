# E1_X6_PLAN_V3_RAW_FEATURE_REDESIGN (正本)

Plan Version 3.0 — 既存scoreに依存しないENTRY・相場状態・EXITの全面再設計。

## 位置づけ

- Plan Version 2.1、Stage 1 (200戦略)、Stage 2 (196戦略)、および
  `E1_X6_NO_ROBUST_JOINT_STRATEGY` は履歴として無変更で保存する。
- 当該verdictの適用範囲は「既存scoreまたはscore派生条件を使用した396戦略では、
  特定日依存を解消できなかった」に限定し、ENTRY全面再設計の最終結論として扱わない。

## Phase A verdict

- 成功: `E1_X6_RAW_REDESIGN_P1_READY`
- データ不足: `E1_X6_RAW_REDESIGN_P1_BLOCKED`
- Paper優先停止: `E1_X6_RESEARCH_PAUSED_FOR_PAPER`

Phase AはCandidate RegistryのP1凍結完了で停止する。9日間のPnL replay・候補選定・
Shadow開始は行わない(明示承認まで)。

## 安全条件

- Paper Trade最優先。Paper runner / pilot / Shadow / Forward / Windows Task /
  production YAML / Discord / broker接続 / submit-cancel経路 / 実行中プロセスは
  変更・停止・再起動しない。`submit/cancel/live = 0/0/0` 維持。
- 研究コードはbroker API・注文・Discordモジュールをimportしない。外部通信なし。
- 保存先は `C:\Users\yhach\e1x6_research_store\raw_feature_redesign\<run_id>\` のみ。
- Paper保護manifest (entrypoint/bat/ps1/configs/src/small_paper 全ファイルSHA) の
  before/after完全一致が必須gate。
- 稼働ガード: Paperプロセス検出 / heartbeat鮮度 / 平日08:30–15:45 JST / 状態不明の
  いずれかでcheckpoint保存し正常終了 (`E1_X6_RESEARCH_PAUSED_FOR_PAPER`)。
- 研究プロセスはBelowNormal優先度、worker 1、数値ライブラリ1スレッド、chunk処理。
  C使用率>75%のみではblockせず、実際の空き不足・書込み失敗リスク時のみ停止。

## 実装コード (研究専用・Paper import経路外)

- `kabu_native/research/e1_x6_raw_redesign/` — guard / store / protected_manifest /
  source_manifest / raw_inventory / event_input / features / regime / setups /
  exits / registry / evaluation_plan / p1 / report / run_phase_a
- `kabu_native/tests/research/e1_x6_raw_redesign/test_phase_a_contracts.py`

## 仕様の要点 (詳細な式・閾値はP1 lockとreport.jsonが機械正本)

- 対象9日: 2026-07-21〜24, 27〜31。AM 09:00–11:30 / PM 12:30–15:30 JST。
- 固定5秒グリッド、1銘柄1グリッド最大1評価、as-of (timestamp<=t) のみ、
  未来補間禁止、欠損は埋めない。freshness gate 30秒、spread上限50bps、
  warmup 5分、終了10分前以降新規ENTRY禁止、AM/PM rolling分離、
  市場特徴量はleave-one-out。NOT_EVALUABLE理由を保存。
- 禁止入力: entry_score_v2・既存score系・旧ENTRY採否・E1_X5 decision・
  将来MFE/MAE/PnL・EXIT後確定情報・日付/銘柄固有条件・後付け時間帯除外。
- Regime: TREND_UP / RANGE_LOW_VOL / EXPANSION_UP / RISK_OFF_UNSTABLE / NEUTRAL。
  persistence + 2グリッドhysteresis。RISK_OFF_UNSTABLEでENTRY禁止。
  Standard/Strict閾値はP1に凍結 (結果PnLで変更しない)。
- ENTRY: IDLE→SETUP→CONFIRM→TRIGGER→OPEN。CONT (上昇継続) / PULL (押し目再加速) /
  BREAK (レンジ圧縮上放れ)。Confirmation STANDARD/STRICT。追いかけ買い拒否は
  (mid−trigger_level)/mid×1e4 > 0.5×rv_300s_bps。同一episode再ENTRY禁止。
- EXIT: EXIT_A_STRUCTURAL (structural stop / invalidation / 180s no-progress /
  max hold 600s / session close)、EXIT_B_STRUCTURAL_TRAIL (120s no-progress /
  +1R後trailing / max hold 420s)。同時成立時の優先順位はP1に固定。
- Candidate Registry: 最大24 (`X6R3_<CONT|PULL|BREAK>_<STANDARD|STRICT>_
  <REG_STANDARD|REG_STRICT>_<EXIT_A|EXIT_B>`)。coverage不足候補は結果を見る前に
  無効化。水増し禁止。経済結果は生成・参照しない。
- Phase B評価計画 (定義のみP1へ保存): ask買い/bid売り、5bps、100株、CAP5、
  9日equal-weight、日依存排除ゲート一式、Rolling-origin、RefitLODO、A/B一致、
  INVALID_SOURCE=0。順位: ex-best-2日PnL → 日別中央値 → 下位25% → 集中率 →
  最大DD → PF → 単純さ → 総PnL。
- 成果物: report.json (唯一のSoT) → report.md / audit.xlsx をrender。
  temp sibling生成後atomic publish、SHA相互参照 (第二writerなし)。


---

# Amendment A-R1 (2026-08-03): P1 contract repair before Phase B

Run e1x6r3_20260802_233645_144c3aab (P1 c100039605b74d6375b8fcf164bd635885c1e7e8c0be6bc0e1237574ffc3a347)
is preserved as SUPERSEDED_PRE_ECONOMICS (no economics were ever generated from it).

1. Field coverage is TRUE AS-OF coverage: denominator = universe symbols x quality-valid
   fixed 5s grids; at each grid t only the latest state with ingress<=t, source ts<=t,
   finite value, field age<=30s (no >30s forward hold, no future interpolation,
   no gap/session/window crossing). Event-row missing rates are NEVER used as coverage.
   USABLE iff as-of coverage >=0.90 in every mask-included session. volume/board are
   DIAGNOSTIC-ONLY for the current 24 candidates; VWAP (if usable) only in the
   pre-registered PULL support condition.
2. Availability (ingress) order is the ONLY replay order; tie-break source sequence,
   else fixed event key. Source timestamps are freshness-only. Late events never
   backfill past grids; the prior feature ledger is immutable. Canonical timestamp
   regressions are audited separately from raw ingress inversions.
3. Strategy-independent analysis_mask_id frozen before Phase B: 300s continuous
   lookback, 600s (or regular close) EXIT horizon, no gaps, no AM/PM crossing,
   valid quote. NOT_EVALUABLE_INCOMPLETE_EXIT_HORIZON is geometry-only. Censored/
   open/orphan are counted separately, never zeroed, never silently dropped;
   leftover non-completed accepted ENTRY => gate FAIL.
4. Fixed TICK=0.1 is abolished: dynamic JPX tick resolver per symbol class and price,
   proven from observed increments; unresolved class => P1_R1_BLOCKED (no fallback).
   Same resolver for trigger, stop, no-progress.
5. State order corrected: IDLE -> SETUP -> TRIGGERED -> CONFIRM -> OPEN. At TRIGGERED
   the trigger_level / stop reference / pullback_low / compression range / tick /
   trigger ts / episode_id are frozen. Confirmation counts the trigger grid as #1
   (STANDARD 2/3, STRICT 3/4). After confirmation failure the episode stays locked
   until the setup clearly breaks.
6. PULL: mid>=low_300s removed (tautological). Causal swing episode: swing_low ->
   swing_high (<=180s old, low before high), rise>=30bps, pullback_low frozen,
   retracement 0.20-0.60, no break below pullback_low, deceleration, re-break of
   30s high + real tick. Swing tie-break: max rise -> newest high -> oldest low.
7. BREAK: BOTH range_ratio_60_300<=0.45 AND vol_ratio_60_300<=0.75 for 12 consecutive
   grids (NOT_EVALUABLE resets); range fixed excluding current grid; post-trigger
   vol>=1.10, spread not degraded, holds above range high per confirmation.
8. EXIT prices are executable: entry=ask at OPEN grid, exit=bid; STOP fires on bid;
   invalidation state on mid, fill at bid; unrealized/MFE/+1R/no-progress/trailing
   on current bid; R = entry_ask - stop_level; 5bps once per round trip; EXIT_B
   trailing = numeric formula (arm at +1R on bid, floor = entry_ask + 0.5*M*R,
   fire on bid<=floor, equality fires).
9. Phase B conventions frozen: Rolling-origin 5 folds (build 7/21.. -> confirm
   7/27..7/31), LODO = FIXED_SPEC_DAY_DELETION + RESELECT_LODO_STABILITY (no REFIT
   wording), 7/22 sensitivity block, independent CAP5 with tie-break (trigger ts,
   decision grid, symbol) + full CAP-blocked ledger, E1_X5 BASE bound to the plan21
   stage-1 artifact by path+SHA (mismatch => NOT_COMPARABLE_BASE => P1_R1_BLOCKED).


---

# Amendment A-R2 (2026-08-03): decision coverage contract

R1 run e1x6r3r1_20260803_031244_a7d98591 (P1 4ff63a47866a08b8e4f78eda442a281fc13e74be5a9db2ee0289970714010dfc,
verdict E1_X6_RAW_REDESIGN_P1_R1_BLOCKED) is preserved unchanged as VALID_BLOCK_EVIDENCE_R1.
Its full-grid quote min coverage 0.752485 remains saved as a diagnostic.

R2 repairs the decision coverage contract without changing Universe, 0.90 thresholds,
30s freshness, sessions, or candidate ENTRY/EXIT formulas:

1. Feature state stays on the 5s grid; ENTRY state machines evaluate a symbol-grid
   ONLY when >=1 raw PUSH of that symbol arrived in the grid (availability order,
   last state, 1 eval/symbol/grid). NOT_DUE_NO_SYMBOL_UPDATE holds state and is
   excluded from the decision-coverage denominator.
2. Three coverages: A FULL_GRID_STATE_COVERAGE (diagnostic), B DECISION_QUOTE_COVERAGE
   (gate >=0.90 per included session), C MARKET_CONTEXT_COVERAGE with LOO
   mkt_evaluable_n>=30 (gate >=0.90).
3. usable_ts=max(ingress,source) abolished; availability_ts=ingress only; snapshot
   age vs field source age separated; BidTime/AskTime semantics proven from data
   (LAST_CHANGE vs OBSERVATION); UNKNOWN source times never used as availability.
4. Incomplete 300s lookback is NOT_EVALUABLE_INCOMPLETE_LOOKBACK per opportunity,
   never a field USABLE/UNUSABLE kill.
5. Tick class from official JPX master scale_category; empirical increments are
   cross-check only; BOTH_CONSISTENT_COARSER_CHOSEN is never final.
6. E1_X5 BASE re-cut onto the identical X6 analysis mask (warmup+600s horizon);
   otherwise NOT_COMPARABLE_BASE. No X6 candidate economics.


---

# Amendment A-R3 (2026-08-03): structural coverage vs spread + official tick evidence

R2 run e1x6r3r2_20260803_040009_4d87ffa4 is preserved as VALID_BLOCK_EVIDENCE_R2.
R3 separates STRUCTURAL_DECISION_QUOTE_COVERAGE (gate, no spread) from SPREAD_TRADEABILITY
(strategy filter at 50bps). Official JPX evidence with effective dates resolves 581A/584A/593A/598A
as Growth domestic common stock NOT in TOPIX500 => OTHER. No economics.
