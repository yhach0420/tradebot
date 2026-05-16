# 市場セッション制御（ENTRY 時間枠）

## 目的

`no_entry_until=09:30` のような **バックテスト上の時間最適化** ではなく、東証現物の **市場構造・板の安定化** に基づく一般的な ENTRY 制限。

## 正式ルール（JST）

| 項目 | 値 |
|------|-----|
| ENTRY 開始 | **09:05** |
| ENTRY 終了 | **14:50**（この時刻以降は新規 ENTRY 不可） |
| 14:50 以降の保有 | 通常の `kabu_exit_v1` または **EOD close** |

実装: `kabu_native/src/replay/session_control.py` の `entry_allowed()`

## 廃止したもの

- **`no_entry_until`** — 寄り後 N 分まで禁止するパラメータ（Phase 8〜10 の 09:30 ゲート等）
- スイープによる「何時から入ると損が減るか」探索を ENTRY ルールの根拠にしない

## legacy baseline（比較用）

`market_session_control=false` のときはリプレイ比較用に **09:00 以降 ENTRY 可・終了時刻なし**（旧 baseline 相当）。

## 設定

- `kabu_native/configs/session_control.yaml`
- `kabu_native/configs/replay.yaml` の `market_session_control` / `session_entry_*`
