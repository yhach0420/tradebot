# kabu_native — kabuステーション native 版

## このディレクトリについて

**`kabu_native/` は、kabuステーション® API（REST / PUSH）を一次データ源とする新システムの置き場です。**

リポジトリルートの **Yahoo 非公式 API ベースの旧系**（`yahoo_kabu_watch.py` 中心）とは **意図的に分離** しています。混在させず、責務と成果物パスを分けて段階的に開発します。

## 旧系との関係

| 区分 | 場所 | 役割 |
|------|------|------|
| **旧系（維持）** | ルート `yahoo_kabu_watch.py`、`docs/DESIGN.md`、`data/intraday_1m/`（Yahoo 由来キャッシュ）、`results/`（Replay・paper_trade 等） | 既存の監視・Replay・paper_trade・Discord 通知。**削除・大規模移動は行わない。** |
| **新系（これから）** | **`kabu_native/` 配下のみ** | kabu REST/PUSH 前提の universe・朝スクリーニング・リプレイ・`kabu_signal_v1`・将来の発注連携。 |

- **`yahoo_kabu_watch.py` は旧系のエントリポイントとして残します。**
- ルート `src/` にある kabu 関連プロトタイプ（`kabu_api_client.py`、`kabu_signal_engine.py` 等）は **当面そのまま**。新実装は `kabu_native/src/` に置き、移行タイミングは別途決めます。
- 旧系の設計・運用は引き続き **`docs/DESIGN.md`** と **ルート `README.md`** を参照してください。

## 開発方針

1. **旧系を壊さない** — watchdog・paper_trade・既存 Replay の運用は継続。
2. **新系は `kabu_native/` に集約** — 設定・データ・成果物・スクリプトをここに閉じる。
3. **段階的に作る** — まず箱と設計 doc → API 接続 → universe → スクリーニング → リプレイ → シグナル → shadow → 発注連携（将来）。

## 新系の目的

| 目標 | 概要 |
|------|------|
| **銘柄 universe 拡張** | kabu API で扱える銘柄集合の管理・更新（`data/universe/`）。 |
| **朝スクリーニング** | 寄り前の候補抽出（成果物: `results/morning_screen/`）。 |
| **過去1か月リプレイ** | PUSH/REST または保存データに基づく検証（`results/replay/`）。 |
| **kabu_signal_v1** | Yahoo 版 `signal_engine` とは別プロファイルのシグナル設計（ルート `docs/kabu_signal_design.md` を参照）。 |
| **将来の発注連携** | 本番発注は未実装。API 層と shadow で検証してから検討。 |

## ドキュメント

| ファイル | 内容 |
|----------|------|
| [docs/directory_structure.md](docs/directory_structure.md) | ディレクトリ一覧と旧系との対応 |
| [docs/architecture.md](docs/architecture.md) | モジュール構成・データフロー・移行方針 |

関連（リポジトリルート・旧系横断の調査 doc）:

- `docs/kabu_station_setup.md` — API 接続確認
- `docs/kabu_signal_design.md` — `kabu_signal_v1` 設計
- `docs/kabu_provider_validation.md` — Yahoo との比較検証

## ディレクトリ概要

```text
kabu_native/
├── configs/          # 新系専用 JSON 設定
├── src/              # ライブラリ（api / universe / screening / signals / replay / storage）
├── scripts/          # CLI・バッチ・運用スクリプト
├── data/             # 新系のローカルデータ（universe, daily, intraday_1m, push_jsonl）
├── results/          # 新系の実行成果物
└── docs/             # 新系の設計・運用ドキュメント
```

詳細は [docs/directory_structure.md](docs/directory_structure.md) を参照。

## 現状（スキャフォールド）

- **ディレクトリと README / 設計 doc のみ** 作成済み。
- **Python 実装は未配置**（ルート `src/` の移動も未実施）。
- 次の実装ステップは `docs/architecture.md` の「実装フェーズ」を参照。
