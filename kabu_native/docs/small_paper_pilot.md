# Small Paper Pilot (Phase 44–47)



Phase 43 で **revised_candidate=true**。Phase 44 は replay dry-run。Phase 45 は **平日場中フルセッション live PUSH 観測**（**実発注禁止・観測専用**）。Phase 47 は **live feature bridge** — replay と同じ continuation_quality 入力を PUSH から近似生成（**gate 式・閾値 0.55・v13 は変更なし**）。



## なぜ small paper へ進むか



| 根拠 | 値 |

|------|-----|

| Combined gate trades | ≥ 100 |

| PF | ≥ 1.20 |

| Symbols coverage | ≥ 70% |

| Exposure gate | quality≥0.55、max_concurrent=3 |

| Phase 43 | 未達は集計バグのみと分類 |



## 採用条件（`configs/small_paper_pilot.yaml`）



| 項目 | 値 |

|------|-----|

| `profile` | `momentum_volume_v13_combined`（EXIT frozen） |

| `entry_profile` | `momentum_volume_v2` |

| `min_continuation_quality` | ≥ 0.55 |

| `max_concurrent_positions` | 3 |

| `order_enabled` | **false**（固定） |

| `paper_only` | **true** |

| Exposure | risk_cluster_block + daily_loss_guard 有効 |

| `require_phase43_pass` | true |



## 禁止事項



- **実発注** — `order_enabled` を true にしない

- 新 EXIT / 新 ENTRY / threshold 最適化

- legacy `paper_trade` / `kabu_signal_shadow` への接続

- Discord 経由の発注パス



## Safety check



```bash

python kabu_native/scripts/check_small_paper_safety.py

python kabu_native/scripts/check_small_paper_safety.py --full-session

```



確認: `order_enabled=false`, `paper_only=true`, `--dry-run` 必須、Phase43 pass、kabu 接続、出力先書込、legacy paper_trade 警告（起動中なら WARNING）。



## Dry-run（必須）



```bash

python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source replay

```



| `--source` | 動作 |

|------------|------|

| `replay`（既定） | 参照 trades CSV を exposure gate 再適用 |

| `poll` | kabu board 観測のみ |

| `live` | kabu PUSH + exposure gate（**`--dry-run` 必須**） |



## Phase 45: 場中フル dry-run（終日観測）



**前提:** kabu ステーション起動、PUSH 可能、legacy `paper_trade` は起動しない。



### 実行前チェック



1. `order_enabled=false`, `paper_only=true`, `discord_observer_only=true`

2. `KABU_API_PASSWORD` 設定

3. `check_small_paper_safety.py`（フルセッション時は `--full-session`）



### 場中フル dry-run（無人運用）



```bash

python kabu_native/scripts/check_small_paper_safety.py --full-session



python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live --full-session --poll-interval-sec 5

```



| オプション | 説明 |

|------------|------|

| `--full-session` | 09:00–15:30 JST を想定（場内なら即開始、15:30 で自動終了） |

| `--session-start` / `--session-end` | 既定 `09:00` / `15:30` |

| `--auto-stop` / `--no-auto-stop` | セッション終了で停止（既定 ON） |

| `--heartbeat-sec` | 既定 300（5分ごと heartbeat + summary 更新） |

| `--wait-until-session` | 開始前なら 09:00 まで待機（未指定時は安全終了） |

| `--poll-interval-sec` | 銘柄ごとの gate 評価デバウンス（既定 5） |



**挙動:**



- セッション外起動（`--wait-until-session` なし）→ summary のみで安全終了

- API エラーは `errors.jsonl` に記録して継続（連続エラー閾値超で安全停止）

- Ctrl+C でも summary を flush

- 途中クラッシュしても JSONL/CSV は追記済み分が残る



### 短時間テスト



```bash

python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live --duration-sec 600 --poll-interval-sec 5

```



### 終了後に見る項目



| ファイル | 確認内容 |

|----------|----------|

| `live_session_safety_report.json` | 起動前 safety |

| `live_session_config.json` | `config_sha256`, `full_session`, `order_enabled=false` |

| `small_paper_summary.json` | `runtime_sec`, `heartbeat_count`, `session_bucket_summary`, `quality_distribution`, `pilot_continue_review` |

| `heartbeat.jsonl` | 5分ごとの生存確認 |

| `errors.jsonl` | API エラー・再接続 |

| `small_paper_events.jsonl` | candidate / accepted / rejected 時系列 |



**翌日:** Phase 40/41/43 再評価用に events / summary / push_jsonl（有効時）を参照。



### Discord Observer（Phase 46–47）

場中に PC を触れないため、**状況確認・判断イベントの可視化のみ** Discord に送る。実発注経路はない（observer mode）。

| 設定 | 既定 | 説明 |
|------|------|------|
| `discord_enabled` | `true` | 通知 ON（`order_enabled=false` のときのみ） |
| `discord_observer_only` | `true` | 必須。observer のみ |
| `discord_send_rejects` | `false` | REJECT 通知（任意） |
| `discord_heartbeat_min` | `30` | Discord heartbeat 間隔（分） |
| `discord_hold_min` | `15` | HOLD 定期通知間隔（分） |
| `discord_hold_quality_delta` | `0.03` | quality 上昇で HOLD 通知 |
| `discord_take_quality_drop` | `0.08` | peak からの quality 低下で TAKE シグナル |
| `discord_webhook_env` | `KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL` | **small paper 専用**（下記） |

#### Webhook 分離（必須）

small paper observer は **ENTRY / HOLD / TAKE / EXIT / HEARTBEAT** と通知量が多いため、既存の `KABU_SHADOW_DISCORD_WEBHOOK_URL`（shadow / kabu paper 参考通知）や Yahoo `DISCORD_WEBHOOK_URL`（legacy paper_trade）とは **別チャンネル・別 Webhook** を使う。

| 用途 | 環境変数 | 影響 |
|------|----------|------|
| small paper observer | `KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL` | 本 pilot のみ |
| kabu shadow / 仮想 paper 参考 | `KABU_SHADOW_DISCORD_WEBHOOK_URL` | **変更しない** |
| Yahoo legacy paper_trade | `DISCORD_WEBHOOK_URL` | **変更しない** |

**推奨:** Discord で `#small-paper-observer`（など）専用チャンネルを作り、その Incoming Webhook URL を `.env` に設定する。

```bash
# .env（リポジトリ直下）
KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

`safety check` は `discord_enabled=true` のとき、上記 env が設定されていることと `discord_webhook_env` が専用名であることを確認する。

#### 判断イベント（先頭タグ）

各メッセージは次の3行で始まる:

```
[SMALL PAPER DRY RUN]
[<EVENT>]
[NO ORDER]
```

| イベント | 意味 | いつ来るか |
|----------|------|------------|
| **ENTRY** | exposure gate 通過（仮想エントリー観測） | accepted 時 |
| **HOLD** | なぜ保持継続か | 15分ごと / continuation_quality 上昇時 |
| **TAKE** | 利確シグナル（観測のみ・発注なし） | 表示 take 到達 / quality 低下 / favorable fade 等 |
| **EXIT** | 仮想ポジションクローズ観測 | stop / virtual hold 満了 / セッション終了 |
| **REJECT** | gate 拒否 | `discord_send_rejects: true` 時 |
| **HEARTBEAT** | 生存・集計 | 30分ごと |
| **SUMMARY** | 引け後サマリ | セッション終了 |

#### HOLD の読み方

- `hold_reason=continuation_quality_rising` — quality が前回通知より上昇 → 保持妥当性が増した
- `hold_reason=periodic_hold_update` — 定期チェック（15分）→ まだ監視中
- `bullish_continuation` / `momentum_continuation` / `favorable_continuation` — frozen v13 系の continuation 成分（新ロジックなし）
- `bearish_accumulation` が高い — 保持に慎重

#### continuation_quality の見方

- **≥ 0.55** — top_quartile 帯（small paper 採用閾値）
- **0.42–0.55** — above_median
- **&lt; 0.42** — 通常は gate で REJECT（`low_quality`）

HEARTBEAT には `entry` / `holding` / `exited` / `take_signals` / `rejected` を表示。SESSION END には ENTRY/EXIT 数、平均保持時間、quality 分布、reject 理由、worst period を表示。

テスト: `python kabu_native/scripts/test_small_paper_discord.py`（`KABU_SMALL_PAPER_DISCORD_WEBHOOK_URL` に HEARTBEAT テスト送信）

**引け後:** SUMMARY（SESSION END）と `small_paper_summary.json` を照合 → Phase 40/41/43 再評価。



## Phase 47: Live Feature Bridge



### なぜ必要か（Phase 45 `accepted=0` の原因）



| 経路 | continuation_quality の入力 |
|------|------------------------------|
| **Replay / OOS** | 完了トレードの `max_favorable_excursion_pct` / `max_adverse_excursion_pct`、duration、favorable 等 |
| **Live（Phase 45 まで）** | kabu PUSH は板のみ → MFE/MAE 未供給 → **全候補が fallback 0.323** → `quality>=0.55` 到達不可 |



新しい gate 式や threshold 変更はしていない。**replay で使っていた特徴量を live でも生成する bridge** のみ。



### 実装



- `src/small_paper/live_feature_bridge.py` — 銘柄ごと rolling window
- `pilot_runner.py` — `PUSH → bridge.update → enrich → continuation_quality_ranking → gate`
- 診断: `python kabu_native/scripts/diagnose_live_feature_bridge.py --session-dir <dir>` または `--push-dir kabu_native/data/push_jsonl/YYYY-MM-DD`



### Replay quality と Live quality の違い



| 項目 | Replay | Live（bridge 後） |
|------|--------|-------------------|
| MFE/MAE | エントリー〜イグジット全期間 | 直近 ~120 tick の rolling（`tracking_reset_sec=300`） |
| duration | トレード内 favorable 連続 | PUSH ごとの favorable streak |
| momentum | bar 集計 proxy | 価格変化 + VWAP 距離 + MFE proxy の live 近似 |



`continuation_quality_ranking.py` は **変更しない**。



### Phase 48: Offline Push Replay



場外でも Phase47 と同じ pipeline（`live_feature_bridge` → `continuation_quality_ranking` → `exposure_gate`）を検証する。



```bash
python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source push-replay \
  --push-dir kabu_native/data/push_jsonl/2026-05-18 \
  --poll-interval-sec 0 --skip-safety
```



| オプション | 意味 |
|------------|------|
| `--push-dir` | `push_jsonl/YYYY-MM-DD/` ディレクトリ（必須） |
| `--poll-interval-sec 0` | 銘柄ごとの評価間隔 0 = 可能な限り高速 |
| `--poll-interval-sec 5` | live フルセッションと同様の 5 秒デバウンス |
| `--replay-speed` | 評価行ごとの sleep 秒（0=なし） |
| `--max-push-rows` | 読み込み行数上限（テスト用） |
| `--enable-discord` | 既定 OFF；必要時のみ Discord 有効化 |



出力: `kabu_native/results/small_paper/YYYYMMDD/push_replay_HHMMSS/`



`summary` の `source` は `push-replay`。`push_rows` / `quality_ge_0_55_count` / `quality_fallback_rate_pct` / `accepted_count` 等を確認。



### Phase 51: Live Observer Trial `q070_cap3`



Phase50 what-if 最良候補（**本採用ではない試験ポリシー**）:



| 項目 | Baseline `q055_cap3` | Trial `q070_cap3_trial` |
|------|----------------------|-------------------------|
| min_quality | 0.55 | **0.70** |
| cap | 3 | 3 |
| push-replay PF (参考) | ~0.94 | **~1.38** |
| push-replay avg pnl | ~-0.008% | **~+0.038%** |



設定ファイル: `configs/small_paper_pilot_q070_cap3.yaml`



```bash
python kabu_native/scripts/check_small_paper_safety.py \
  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source push-replay \
  --push-dir kabu_native/data/push_jsonl/2026-05-18 \
  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml \
  --poll-interval-sec 5 --skip-safety

python kabu_native/scripts/review_small_paper_push_replay.py --session-dir <push_replay_dir>
python kabu_native/scripts/review_runtime_pilot_policy.py --session-dir <push_replay_dir>
```



`small_paper_summary.json` に `policy_label` / `policy_trial` / `baseline_policy` を出力。Discord の ENTRY/HOLD/TAKE/EXIT/HEARTBEAT/SUMMARY に `policy_label` と `min_quality` を表示。



**TAKE** は観測通知のみ（自動売買しない）。live 再開前の確認:



- push-replay で trial が PF≥1.2、avg pnl&gt;0、trade≥50
- `check_small_paper_safety.py` overall_pass
- TAKE 早期警告率を `take_observer_review.json` で確認



### Phase 50: Runtime Pilot Policy Review (what-if)



Phase49 結果を踏まえ、**閾値・cap を変えた場合の成績を再シミュレーション**（本番 yaml は変更しない）。



```bash
python kabu_native/scripts/review_runtime_pilot_policy.py \
  --session-dir kabu_native/results/small_paper/20260518/push_replay_HHMMSS
```



| 出力 | 内容 |
|------|------|
| `runtime_policy_review.json` | quality/cap/grid + `recommendation` |
| `runtime_policy_grid.csv` | what-if 一覧 |
| `take_observer_review.json` | TAKE 観測シグナル分析（**売買指示ではない**） |



what-if 対象:



- **min_quality**: 0.55 / 0.60 / 0.65 / 0.70 / 0.75（cap=3 固定）
- **max_concurrent**: 3 / 4 / 5（quality=0.55 固定）
- **grid**: 0.65+cap3/4、0.70+cap3/4、0.75+cap3 など



`recommend_policy_candidate` 条件（参考）: PF≥1.2、avg pnl&gt;0、trade≥50、max loss 許容、高品質ブロック過多でない。



**live 再開の目安**



1. Phase47 bridge が fallback 低いこと（push-replay）
2. what-if で採用候補ポリシーが PF/avg pnl 条件を満たすこと
3. `take_observer_review.json` で TAKE を「通知」として扱うこと（自動決済しない）
4. 本番 `small_paper_pilot.yaml` の変更は **明示承認後**



### Phase 49: Push-Replay Performance Review



Phase48 の `push_replay_*` 出力を読み、accepted 195 件の成績・リスク・observer 判断を検証する（**分析のみ**）。



```bash
python kabu_native/scripts/review_small_paper_push_replay.py \
  --session-dir kabu_native/results/small_paper/20260518/push_replay_HHMMSS
```



| 出力 | 内容 |
|------|------|
| `small_paper_performance_review.json` | 集計 + `verdict` |
| `small_paper_trades_review.csv` | accepted ごとの仮想ホールド成績 |
| `small_paper_reject_quality_review.csv` | reject 理由別 quality 集計 |



`verdict`:



- `move_to_live_observer_again` — PF≥1.2、avg pnl&gt;0、trade≥100、max loss 許容、cap 許容
- `fix_runtime_before_live` — bridge は OK でも成績・cap・observer に課題



### bridge 後に見る指標（`small_paper_summary.json`）



- `quality_distribution` — 0.323 固定でないか
- `quality_fallback_rate_pct` — fallback 大幅減少か
- `live_feature_complete_rate_pct`
- `accepted_count` — 0.55 以上が出れば発生しうる（**実発注は依然禁止**）



イベント CSV に追加: `quality_fallback_path`, `live_feature_complete`, `rolling_mfe_pct`, `rolling_mae_pct`, `momentum_continuation_score`, `favorable_continuation`, `max_continuation_duration`, `adverse_shrinking`, `quality_components_json`



## 出力



### Replay / poll



`kabu_native/results/small_paper/YYYYMMDD/`



### Live フルセッション



`kabu_native/results/small_paper/YYYYMMDD/live_full_session_HHMMSS/`



| ファイル | 内容 |

|----------|------|

| `small_paper_events.csv` / `.jsonl` | candidate / accepted / rejected（Phase47: bridge 特徴量列付き） |

| `small_paper_rejects.csv` | 拒否のみ（同上） |

| `live_feature_bridge_diagnosis.json` | `diagnose_live_feature_bridge.py` 出力 |

| `quality_top_debug.csv` / `.json` | push-replay / live 後の top quality 候補 |

| `small_paper_positions.csv` | スロット推移 |

| `small_paper_summary.json` | 終日集計 |

| `live_session_config.json` | セッション設定 |

| `live_session_safety_report.json` | safety 結果 |

| `heartbeat.jsonl` | 5分 heartbeat |

| `errors.jsonl` | API エラー |



## 人手確認（pilot 開始前）



1. safety 合格（legacy paper_trade 警告なしが理想）

2. replay dry-run で accepted/rejected が妥当

3. kabu ステーションに別経路の発注設定がないこと

4. **pilot は dry-run のみ** — 実発注経路なし



## Phase 52 — 許可取引時間帯と構造診断



### 許可取引時間（運用安全制約 — **時間帯最適化ではない**）



市場構造上の不安定時間（寄り直後・昼休み・大引け直前）を除外するための**固定ウィンドウのみ**。



| 区分 | 時刻 (JST) |

|------|------------|

| 前場 | 09:05–11:23 |

| 後場 | 12:33–15:20 |



`configs/small_paper_pilot.yaml` と trial `small_paper_pilot_q070_cap3.yaml` の `allowed_trading_windows` で設定。



**禁止（過学習リスク）:** afternoon 停止、午前のみ稼働、13:30 以降停止、session 別 threshold、時刻別 quality 調整、時間帯別 PF 最適化、特定時刻での ENTRY/EXIT 調整。



候補評価の**前**に `entry_time` がウィンドウ外なら `outside_allowed_trading_window` で reject（品質計算より先）。



### Runtime Weakness Diagnosis（構造のみ）



```bash

python kabu_native/scripts/review_runtime_weakness.py \\

  --session-dir kabu_native/results/small_paper/YYYYMMDD/push_replay_HHMMSS \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



| 出力 | 内容 |

|------|------|

| `runtime_weakness_diagnosis.json` | 9 観点の構造診断 + live observer 可否 |

| `weakness_by_symbol.csv` | 銘柄別 |

| `weakness_by_feature.csv` | quality / hold / MFE / MAE / momentum 帯 |

| `trade_path_examples.csv` | 損益ワースト・ベスト例 |

| `rejected_outside_window.csv` | 許可時間外 reject |



観点: quality 過大評価、遅延 high quality、cap で良候補喪失、HOLD 損失化、TAKE 早すぎ、decay 未検出、銘柄集中、**負けは時間帯ではなく momentum/liquidity/cap/hold 構造**。



### Phase 52 再評価（push-replay）



```bash

python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source push-replay \\

  --push-dir kabu_native/data/push_jsonl/2026-05-18 \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml \\

  --poll-interval-sec 5 --skip-safety



python kabu_native/scripts/review_small_paper_push_replay.py --session-dir <push_replay_dir>

python kabu_native/scripts/review_runtime_pilot_policy.py --session-dir <push_replay_dir>

python kabu_native/scripts/review_runtime_weakness.py --session-dir <push_replay_dir>

```



## Phase 53 — Exposure Cap What-if（本採用ではない）



**cap 引き上げはロジック改善ではなく exposure policy の what-if 検証。** リスク評価込みで live observer trial 可否を判断する。`allowed_trading_windows` 以外の時間帯調整は禁止。



固定: `min_quality=0.70`, allowed windows（09:05–11:23 / 12:33–15:20）, q070 trial, 実発注禁止, observer only。



検証 cap: **3 / 4 / 5**



```bash

python kabu_native/scripts/review_exposure_cap_whatif.py \\

  --session-dir kabu_native/results/small_paper/YYYYMMDD/push_replay_HHMMSS \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



| 出力 | 内容 |

|------|------|

| `exposure_cap_whatif.json` | cap 別成績・HQ 取り逃し・`recommend_cap_candidate` |

| `exposure_cap_grid.csv` | cap 3/4/5 グリッド |

| `rejected_high_quality_opportunity.csv` | cap3 で max_concurrent 拒否された HQ 候補 + would_be_pnl |

| `exposure_risk_review.csv` | 飽和率・同時損失クラスタ・銘柄重複・worst-case |



採用候補条件（`recommend_cap_candidate`）: PF≥1.20, avg pnl>0, max_loss が cap3 より 25% 以上悪化しない, 連敗・集中度・HQ 取り逃し・リスク増が許容範囲。



## Phase 54 — TAKE / HOLD / EXIT Runtime Review



**TAKE は Discord 通知であり売買判断ではない。** HOLD/EXIT は observer の仮想ポジション追跡のみ。what-if は本採用ではなく、次回 live observer 前のレビュー用。



```bash

python kabu_native/scripts/review_runtime_exit.py \\

  --session-dir kabu_native/results/small_paper/YYYYMMDD/push_replay_HHMMSS \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



| 出力 | 内容 |

|------|------|

| `runtime_exit_review.json` | TAKE/HOLD/EXIT 集計 + `recommend_runtime_fix` |

| `take_path_review.csv` | TAKE 時 quality/pnl・30/60/120/300s 上昇・伸び続け |

| `hold_path_review.csv` | hold 時間・decay・MFE giveback・長期 HOLD 損失 |

| `exit_path_review.csv` | exit_reason・virtual_hold 偏重・EXIT 遅れ |

| `exit_policy_whatif.csv` | TAKE-as-exit / decay / trailing / hold_max 比較 |



`recommend_runtime_fix`: `take_is_too_early` | `hold_is_too_long` | `exit_decay_missing` | `trailing_needed` | `no_change`



## Phase 55 — Live Observer Re-trial（q070_cap3）



Phase54: **baseline_observer PF≥1.2**（push-replay）。cap 引き上げ・単純 decay/trailing は baseline 未満のため、**現行 observer のまま** live 再試験。



### Readiness



```bash

python kabu_native/scripts/check_live_observer_readiness.py \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



出力: `kabu_native/results/reports/live_observer_readiness_YYYYMMDD.json`



確認: Phase51 config、Phase52 windows、Phase53 cap非推奨、Phase54 PF、TAKE=通知のみ、`order_enabled=false`、`paper_only=true`、Discord、kabu 接続、output 書込。



### Live 実行（実発注なし）



```bash

python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source live \\

  --full-session --wait-until-session \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml \\

  --poll-interval-sec 5

```



`small_paper_summary.json` に追加: `runtime_policy`, `exit_policy`, `take_is_observer_only`, `allowed_trading_windows`, `phase54_reference_pf`



TAKE Discord: **OBSERVER SIGNAL ONLY / NOT EXIT** + replay 伸び続け注意



### 引け後レビュー



```bash

python kabu_native/scripts/review_runtime_exit.py --session-dir <live_full_session_dir> \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

python kabu_native/scripts/review_runtime_weakness.py --session-dir <live_full_session_dir>

python kabu_native/scripts/review_exposure_cap_whatif.py --session-dir <live_full_session_dir> \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



## Phase 56 — TAKE 診断 / Quality Inflation（分析のみ）



ENTRY/EXIT/cap/quality 閾値/時間帯は**変更禁止**。診断のみ。



```bash

python kabu_native/scripts/review_phase56_diagnosis.py \\

  --session-dir kabu_native/results/small_paper/YYYYMMDD/live_full_session_HHMMSS \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



| 出力 | 内容 |

|------|------|

| `take_signal_decomposition.json` | TAKE 要因分解・+30/60/120/300s 上昇継続率 |

| `take_signal_breakdown.csv` | 各 TAKE 行 |

| `quality_band_review.json` | 0.70–0.85+ 帯別 PF |

| `quality_band_review.csv` | 取引別 band |

| `verdict.phase56_verdict` | `continue_current_trial` or `need_quality_review` |



## Phase 58 — structural_observer_v1（公式 PF / 構造ベース評価）



ENTRY/EXIT v13/cap/quality/時間帯は**変更禁止**。レビュー専用の擬似売買 PF。



- **公式:** `structural_pf`（`virtual_hold_expired` / 固定 horizon / `hold_max_*` 不使用）
- **参考のみ:** `legacy_virtual_hold_pf`（300 秒窓・`legacy_` 接頭辞）
- TAKE は売却扱いしない（`take_pnl_pct` は参考）



```bash

python kabu_native/scripts/review_structural_observer.py \\

  --session-dir kabu_native/results/small_paper/YYYYMMDD/live_full_session_HHMMSS \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



| 出力 | 内容 |

|------|------|

| `structural_observer_review.json` | `structural_pf`, `official_verdict`, `legacy_virtual_hold_pf` |

| `structural_trades.csv` | 構造 EXIT / session_end で確定した取引 |

| `structural_events.csv` | entry / hold / take / structural_exit |

| `structural_exit_reasons.csv` | `exit_reason_distribution` 内訳 |



`official_verdict`: `structural_pass` \| `structural_needs_exit_design` \| `structural_needs_more_sessions` \| `structural_fail`



## Phase 60 — combined_structural_exit_v1 公式評価



Phase58 baseline に加え、**combined_structural_exit_v1** を公式 EXIT policy として選択可能。



```bash

python kabu_native/scripts/review_structural_observer.py \\

  --session-dir kabu_native/results/small_paper/20260519/live_full_session_081047 \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml \\

  --structural-exit-policy combined_structural_exit_v1

```



| 追加出力 | 内容 |

|----------|------|

| `structural_policy_comparison.csv` | baseline vs combined PF |

| `structural_exit_policy_summary.csv` | 公式 policy・legacy 参考値 |



- 既定 `--structural-exit-policy structural_observer_v1`（明示は JSON の `structural_exit_policy`）
- 公式判定は選択 policy のみ（`legacy_virtual_hold_pf` は参考）
- combined 許可 EXIT: stop / quality_decay / momentum_fade / favorable_fade / vwap_break / mfe_giveback / overlap / session_end



## Phase 61 — Live observer + combined_structural_exit_v1（通知のみ）



**実発注なし。** `order_enabled=false`, `paper_only=true`, `structural_exit_policy: combined_structural_exit_v1`



- Live `ObserverPositionTracker` が `structural_exit_policies` で構造 EXIT を判定
- `virtual_hold_expired` は Discord / official summary に出さない（`virtual_hold_expired_ignored_count` のみ）
- Discord EXIT は `[STRUCTURAL EXIT]` のみ（reason / quality / momentum / pnl / MFE/MAE / TAKE was not exit）
- TAKE は従来どおり売却扱いしない



```bash

python kabu_native/scripts/check_live_observer_readiness.py \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml \\

  --structural-session-dir kabu_native/results/small_paper/20260519/live_full_session_081047

```



引け後（同じ policy）:



```bash

python kabu_native/scripts/review_structural_observer.py \\

  --session-dir <live_session_dir> \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml \\

  --structural-exit-policy combined_structural_exit_v1

```



セッション summary 追加キー: `structural_exit_policy`, `structural_exit_count`, `structural_exit_reason_counts`, `virtual_hold_expired_ignored_count`, `official_exit_count`, `session_end_exit_count`



**設計上の位置づけ:** combined structural exit は **observer 通知のみ**。発注・EXIT v13 本番ロジック・ENTRY/quality/cap は変更しない。



## Phase 59 — Structural Exit Design Review（分析のみ）



Phase58 の PF 乖離（structural vs legacy VH）を分解し、構造 EXIT 候補と overlap 方針を比較。



```bash

python kabu_native/scripts/review_structural_exit_design.py \\

  --session-dir kabu_native/results/small_paper/YYYYMMDD/live_full_session_HHMMSS \\

  --config kabu_native/configs/small_paper_pilot_q070_cap3.yaml

```



| 出力 | 内容 |

|------|------|

| `structural_exit_design_review.json` | PF 乖離説明、`recommend_next_step` |

| `structural_loss_decomposition.csv` | 負けトレード分類 |

| `overlap_replacement_review.csv` | overlap 方針 what-if |

| `structural_exit_candidate_matrix.csv` | 構造 EXIT policy 比較（VH/horizon 禁止） |



`recommend_next_step`: `add_structural_exit_v1` \| `fix_duplicate_overlap_first` \| `entry_quality_not_enough` \| `continue_observation_only`



## Phase 67 — MFE-linked favorable trial config

Phase66 の push-replay（gate+cap+`combined_structural_exit_v1`）で B 案が current を上回ったため、**trial 専用** config で favorable を価格連動に変更。

| 項目 | `q070_cap3_trial`（現行維持） | `q070_cap3_mfe_fav_trial`（Phase67） |
|------|------------------------------|-------------------------------------|
| config | `configs/small_paper_pilot_q070_cap3.yaml` | `configs/small_paper_pilot_q070_cap3_mfe_fav.yaml` |
| favorable | tick 連続ヒット率（bridge 既定） | `min(1, rolling_mfe_pct / 0.003)` |
| bridge 時刻 | `time.monotonic()` | `CurrentPriceTime`（`use_market_time_window: true`） |
| 本番 yaml | 変更なし | 変更なし |

```bash
python kabu_native/scripts/check_small_paper_safety.py \
  --config kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav.yaml

python kabu_native/scripts/run_small_paper_pilot.py --dry-run --source push-replay \
  --push-dir kabu_native/data/push_jsonl/2026-05-19 \
  --config kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav.yaml \
  --poll-interval-sec 5 --skip-safety --no-discord

python kabu_native/scripts/review_structural_observer.py \
  --session-dir kabu_native/results/small_paper/YYYYMMDD/push_replay_HHMMSS \
  --config kabu_native/configs/small_paper_pilot_q070_cap3_mfe_fav.yaml \
  --structural-exit-policy combined_structural_exit_v1
```

`small_paper_summary.json` に `favorable_mode` / `structural_pf` / `favorable_fade_exit_count` / `c_group_pass_count` を記録（push-replay 終了時に structural 再評価）。



## 関連



- `configs/small_paper_top_quartile.yaml` — 検証用（Phase 39–40）

- `docs/data_accumulation.md` — データ蓄積（Phase 42）

- `docs/logic_lab.md` — Phase 37–43 研究ゲート

