# Phase397 — Capital Constraint Runtime Audit

Generated: 2026-06-15T22:18:16+09:00

## 判定: **FAIL**

position_cap_only accepted=16 vs capital_constrained=22; Runtime lacks BP enforcement.

---

## エグゼクティブサマリー（必須）

| 質問 | 回答 |
|------|------|
| **Phase396は何を一致させたか** | **Position-CAP（observer open ≤3、EXITまで拘束）** を `structural_trades.csv` タイムライン上の **資産シミュエンジン（`simulate_cap` / `CapScenarioState`）** と照合。Live Runtime のイベント逐次リプレイではない。 |
| **Runtimeに買付余力制約があるか** | **ない**。`exposure_gate.py` / `pilot_runner.py` / `position_cap_mode.py` に `buying_power`・`maintenance_ratio`・`initial_equity` の ENTRY 判定は未実装。 |
| **150万円資産シミュと完全一致しているか** | **いいえ（Runtime本線）**。Phase396 validation（C）は capital sim（B）と一致（accepted=22）。Runtime 実装は CAP のみで BP/維持率なし。Position-CAP-only（A）は同じ CSV でも accepted=16 と **パス依存で乖離**。 |
| **今後Runtimeにcapital constraintsが必要か** | **必要**（資産シミュと Live paper の accepted ストリームを一致させるなら）。現状は CAP のみ一致。 |

---

## 確認1: 三モデル比較（2026-06-15 PM `live_session_122531`）

入力: `structural_trades.csv` エントリー/エグジット時系列（90トレード）。

| model | accepted_count | rejected_by_cap | rejected_by_buying_power | rejected_by_maintenance | rejected_other | final_pnl_yen_100 | final_equity | accepted_symbols_count | rejected_symbols_count |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| position_cap_only | 16 | 74 | 0 | 0 | 0 | 14500.0 | 1514500.0 | 12 | 22 |
| capital_constrained | 22 | 58 | 10 | 0 | 0 | 18700.0 | 1518700.0 | 14 | 23 |
| phase396_runtime_validation | 22 | 58 | 10 | 0 | 0 | 18700.0 | 1518700.0 | 14 | 23 |

### 解釈

| モデル | 説明 |
|--------|------|
| **A. position_cap_only** | CAP=3、EXITまで拘束。買付余力・レバ・維持率なし。 |
| **B. capital_constrained** | 1.5M / lev2 / 100株 / CAP3 + `compute_buying_power` + maintenance（Phase385 `CapScenarioState`） |
| **C. phase396_runtime_validation** | Phase396 スクリプトの合格基準（= B と同じエンジン・同じ入力） |

**Phase396 accepted=22 の正体:** `simulate_cap`（**B: capital_constrained**）の accepted 件数。Position-CAP-only（A）との差は **-6** 件。

### accepted / rejected シンボル差分（A vs B）

差分行数: **10**（`results/reports/phase397_capital_constraint_diff.csv`）

---

## 確認2: 6/15 PM 買付余力 reject

| 指標 | 件数 |
|------|------|
| `rejected_by_buying_power`（capital sim） | **10** |
| `rejected_by_maintenance` | **0** |
| `rejected_by_cap` | **58** |

**10件** — 買付余力rejectあり。Phase396 Runtime と資産シミュは **まだ不一致**（Runtime に BP 判定なし）。 Position-CAP-only=16 accepted、Capital-constrained=22 accepted。 件数差は **受理パス依存**（BP reject は枠を消費しないため、後続の CAP 空きが変わる）。

---

## 確認3: Runtime コード監査

| コンポーネント | 資金制約 | 根拠 |
|----------------|----------|------|
| `exposure_gate.py` | No — position_cap_mode uses observer_open_count only | observer_open_count=3 hits; buying_power=0 |
| `pilot_runner.py` | No — entry path has no buying_power / maintenance checks | buying_power=0, compute_buying_power=0 |
| `config.py` | No runtime equity/leverage fields for gate | initial_equity=0, position_cap_mode=7 |
| `position_cap_mode.py` | No — CAP tracking and legacy VH shadow only | buying_power=0 |
| `phase385 CapScenarioState (research)` | Yes — buying_power, maintenance, leverage in try_entry | Used by Phase267–274 capital sim; not wired to Runtime |

### 資産シミュが見る条件（Runtime 未実装）

| 条件 | 資産シミュ（Phase385） | Runtime（Phase396） |
|------|------------------------|---------------------|
| `initial_equity` | ¥1,500,000 | なし |
| `leverage_limit` | 2.0 | なし |
| `buying_power` | `equity * lev - gross` | なし |
| `maintenance_ratio` | WARNING / STOP_ENTRY / FORCE_EXIT | なし |
| `max_concurrent_positions` | `open_positions` 数 | `observer.open_count()` ✓ |

---

## 判定ロジック

| 条件 | 結果 |
|------|------|
| Runtime に capital constraints あり & accepted 一致 | PASS |
| Runtime に capital constraints なし & 6/15 BP reject=0 & accepted 一致 | **WARN**（偶然一致） |
| accepted / reject が capital sim と異なる | FAIL |

**今回: FAIL** — Runtime capital constraints = `False`。

---

## 成果物

- `results/reports/phase397_capital_constraint_comparison.csv`
- `results/reports/phase397_capital_constraint_diff.csv`
- `results/reports/phase397_capital_constraint_summary.json`

---

## 推奨（調査のみ・未実装）

次フェーズ候補: Runtime ENTRY 前に **shadow** で `CapScenarioState.try_entry` 同等の買付余力チェックを並列記録し、reject 差分を可視化。本番 ENTRY ロジック変更は別 Phase。
