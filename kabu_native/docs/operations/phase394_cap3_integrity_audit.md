# Phase394 — CAP3 Integrity Audit

## PASS

2026-06-15 PM session (`live_session_122531`) において、**Exposure Gate による actual position（virtual hold スロット）の同時保有数は常に ≤3** であった。`max_active_positions_actual = 3`（期待値 ≤3 を満たす）。

15:23 に発生した **12件の EXIT 通知は CAP 違反ではなく**、`force_close: 15:23` / `afternoon_session_close` による **structural observer の一括セッションクローズ**が原因である。

---

## 調査条件

| 項目 | 値 |
|------|-----|
| 対象日 | 2026-06-15 |
| PM session | `results/small_paper/20260615/live_session_122531/` |
| 設定 | `trailing_mfe_shadow.yaml` / `max_concurrent_positions: 3` |
| `force_close` | 15:23 (`afternoon_session_close`) |
| `observer_exit_mode` | `combined_structural_exit_notification_only` |
| 制約 | Runtime / Universe / Entry / Exit / YAML 変更なし（調査のみ） |

---

## 1. Actual position 時系列再構築（Exposure Gate virtual hold）

**定義:** `accepted` イベントの `(entry_time, exit_time)` 区間で同時にアクティブな銘柄数。Exposure Gate の `open_slots` ロジック（`exit_time` 経過でスロット解放）と一致。

### サマリー

| 指標 | 値 |
|------|-----|
| PM accepted 総数 | 90 |
| `peak_open_slots`（summary） | 3 |
| 再構築 `max_active_positions_actual` | **3** |
| 最大同時保有の初出 | 2026-06-15T12:47:46+09:00 |
| `active_positions > 3` の発生 | **0件** |

### 主要スナップショット

| timestamp | active_positions | symbols |
|-----------|------------------|---------|
| 2026-06-15T12:47:46+09:00 | 3 | 4062, 6855, 7717 |
| 2026-06-15T14:00:00+09:00 | 3 | 215A, 6227, 9984 |
| 2026-06-15T15:16:45+09:00 | 3 | 6227, 6323, 9984 |
| 2026-06-15T15:21:45+09:00 | 0 | （全 virtual hold 満了） |
| 2026-06-15T15:23:00+09:00 | 0 | （gate スロット空） |

### 時系列（変化点のみ、抜粋）

| timestamp | active_positions | symbols |
|-----------|------------------|---------|
| 12:47:30 | 1 | 4062 |
| 12:47:41 | 2 | 4062, 7717 |
| 12:47:46 | **3** | 4062, 6855, 7717 |
| 12:52:44 | 3 | 5367, 6779, 6855 |
| 13:23:13 | 3 | 215A, 6323, 9984 |
| 14:40:01 | 3 | 4588, 6613, 6962 |
| 15:16:45 | 3 | 6227, 6323, 9984 |
| 15:20:23 | 2 | 6227, 9984 |
| 15:21:34 | 1 | 9984 |
| 15:21:45 | 0 | — |

全変化点: 172件（`small_paper_events.csv` の accepted 90件から導出）。

---

## 2. max_active_positions_actual

```
max_active_positions_actual = 3
```

- 期待値 `<= 3` → **合格**
- `small_paper_summary.json` の `peak_open_slots: 3`, `open_slots_end: 3` と一致

---

## 3. 15:23 EXIT 通知の分類

### 概要

| 項目 | 値 |
|------|-----|
| 15:23 `observer_exit` 件数 | **12** |
| 全件の `structural_exit_reason` | `afternoon_session_close` |
| 全件の `session_close` | `True` |
| トリガー | `live_session_config.json` → `force_close: 15:23` |

### 分類（actual vs structural observer）

| # | symbol | 通知由来 | 根拠 |
|---|--------|----------|------|
| 1 | 6962 | **structural observer** | `observer_exit` / `afternoon_session_close` / `close_all()` |
| 2 | 6264 | **structural observer** | 同上 |
| 3 | 6613 | **structural observer** | 同上 |
| 4 | 4588 | **structural observer** | 同上 |
| 5 | 215A | **structural observer** | 同上 |
| 6 | 6779 | **structural observer** | 同上 |
| 7 | 3907 | **structural observer** | 同上 |
| 8 | 7717 | **structural observer** | 同上 |
| 9 | 4047 | **structural observer** | 同上 |
| 10 | 6323 | **structural observer** | 同上 |
| 11 | 6227 | **structural observer** | 同上 |
| 12 | 9984 | **structural observer** | 同上 |

**actual position（gate virtual hold）由来の通知: 0件**

15:23 時点で gate スロットは **0**（最後の virtual hold は 15:21:45 に満了）。12件すべては observer tracker の `close_all(reason=afternoon_session_close)` による structural 一括クローズ通知である。

---

## 4. 通知銘柄一覧（15:23 セッションクローズ leg）

| symbol | ENTRY時刻 | EXIT時刻 | actual採用 | CAP判定 | position_id (message_index) |
|--------|-----------|----------|------------|---------|----------------------------|
| 6962 | 2026-06-15T14:25:00+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 324962 |
| 6264 | 2026-06-15T14:35:23+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 362441 |
| 6613 | 2026-06-15T14:40:01+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 381597 |
| 4588 | 2026-06-15T14:40:27+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 383349 |
| 215A | 2026-06-15T14:51:11+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 419718 |
| 6779 | 2026-06-15T15:01:24+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 455629 |
| 3907 | 2026-06-15T15:05:12+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 468872 |
| 7717 | 2026-06-15T15:10:20+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 486693 |
| 4047 | 2026-06-15T15:11:27+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 490505 |
| 6323 | 2026-06-15T15:15:23+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 503945 |
| 6227 | 2026-06-15T15:16:34+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 508277 |
| 9984 | 2026-06-15T15:16:45+09:00 | 2026-06-15T15:23:00+09:00 | Yes | PASS (≤3) | 508831 |

- **actual採用:** 全12銘柄とも当該 `entry_time` で `accepted`（`gate_accept=True`）が存在
- **CAP判定:** 各 ENTRY 時点で gate スロットは最大3以内（セッション全体で `max_active_positions_actual=3`）
- **position_id:** ENTRY 時の `message_index`。EXIT 時の `message_index`（533733）は force-close 時の push メッセージ ID で共通

---

## 5. active_positions > 3 の有無

### 結論

**Exposure Gate（actual position）では発生なし。**

### 12件 EXIT との関係（見かけ上の矛盾の解消）

| 観点 | 15:23 時点の値 |
|------|----------------|
| Gate virtual hold（CAP 対象） | **0** |
| Observer structural open（`close_all` 対象） | **12** |

**原因:**

1. **CAP=3 は Exposure Gate の virtual hold スロットにのみ適用**される（`max_concurrent_positions: 3`）。各 accepted は約5分の `exit_time` 後にスロット解放され、新規 accepted が入る。
2. **Observer は銘柄ごとに1ポジション**を保持し、structural exit（trailing_mfe / stop_hit / overlap_replaced / session_close）までクローズしない。gate スロット解放後も observer 上はオープンのまま残る。
3. **15:23 の `force_close`** で observer に残存していた12銘柄が一括 `close_all()` され、Discord に12件の EXIT 通知が同時刻に送出された。

**影響:**

- CAP 制約の実運用（同時エントリー可否）には **影響なし**
- Discord 上は「12件同時 EXIT」に見えるが、**セッション終了クローズの通知バースト**であり、同時保有 CAP 違反ではない
- `open_slots_end: 3` は force-close 直前の gate 状態（observer クローズとは独立）を反映

---

## 参照ファイル

- `results/small_paper/20260615/live_session_122531/small_paper_events.csv`
- `results/small_paper/20260615/live_session_122531/small_paper_summary.json`
- `results/small_paper/20260615/live_session_122531/structural_trades.csv`
- `results/small_paper/20260615/live_session_122531/live_session_config.json`
- `src/research/exposure_gate.py`（`open_slots` / `max_concurrent_positions`）
- `src/small_paper/observer_position_tracker.py`（`close_all`）
- `src/small_paper/pilot_runner.py`（`_maybe_am_pm_force_close`）

---

## 監査実施

- 日時: 2026-06-14（調査実施日）
- 方式: セッションアーティファクトのオフライン再構築（コード変更なし）
