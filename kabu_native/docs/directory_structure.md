# kabu_native — ディレクトリ構造

**更新:** スキャフォールド段階（実装ファイルは未配置）

## 全体像

```text
tradebotfile/                          # リポジトリルート
├── yahoo_kabu_watch.py                # 【旧系】メインエントリ（維持）
├── watchlist.json, symbols.csv        # 【旧系】銘柄定義
├── configs/                           # 【旧系】Replay / paper_trade 用 JSON
├── data/intraday_1m/                  # 【旧系】Yahoo 由来 1分足キャッシュ
├── results/                           # 【旧系】Replay・paper_trade・kabu_api_check 等
├── src/                               # 【旧系＋プロトタイプ】signal_engine, providers, kabu_* 試作
├── scripts/                           # 【旧系】watchdog, kabu_api_check 等
├── docs/DESIGN.md                     # 【旧系】総合設計
│
└── kabu_native/                       # 【新系】kabuステーション native（本ツリー）
    ├── README.md
    ├── configs/
    ├── src/
    │   ├── api/
    │   ├── universe/
    │   ├── screening/
    │   ├── signals/
    │   ├── replay/
    │   └── storage/
    ├── scripts/
    ├── data/
    │   ├── universe/
    │   ├── daily/
    │   ├── intraday_1m/
    │   └── push_jsonl/
    ├── results/
    │   ├── morning_screen/
    │   ├── replay/
    │   ├── shadow/
    │   └── reports/
    └── docs/
        ├── directory_structure.md     # 本ファイル
        └── architecture.md
```

## 新系ディレクトリの責務

### `configs/`

新系専用の設定ファイル（JSON / YAML 等）。

- 朝スクリーニング閾値、universe ソース、リプレイ日付範囲、`kabu_signal_v1` パラメータなど。
- **旧系の `configs/replay_*.json` とは別管理**（コピーして流用する場合は明示的にドキュメント化する）。

### `src/api/`

kabuステーション API との境界層。

- トークン取得、REST（板・銘柄情報）、PUSH 受信のラッパ。
- ルート `src/kabu_api_client.py` / `kabu_push_client.py` の **将来の移行先**（現時点では未移動）。

### `src/universe/`

監視・スクリーニング対象の銘柄集合。

- 東証銘柄リストの取り込み、流動性フィルタ、API 登録銘柄との突合。
- 出力・スナップショット: `data/universe/`。

### `src/screening/`

朝スクリーニング（寄り前候補のスコアリング・ランキング）。

- 旧系 `yahoo_kabu_watch.py --morning-screen` に相当する **kabu データ版** をここに実装予定。
- 成果物: `results/morning_screen/`。

### `src/signals/`

`kabu_signal_v1` — PUSH/REST スナップショットからのエントリー・更新・無効化判定。

- 設計の正: ルート `docs/kabu_signal_design.md`。
- 旧系 `src/signal_engine.py`（Yahoo プロファイル）とは **別モジュール** として維持。

### `src/replay/`

保存データまたは記録済み PUSH からの過去再生・集計。

- 目標: **直近約1か月** の検証（データ可用性は kabu / 保存ポリシーに依存）。
- 成果物: `results/replay/`。
- ルート `src/kabu_signal_replay.py` はプロトタイプ参照用（未移動）。

### `src/storage/`

JSONL / CSV / SQLite 等への永続化、日付パーティション、メタデータ。

- `data/daily/` — 日足・スクリーニング用集計
- `data/intraday_1m/` — 新系で確定した 1分足（kabu 由来の定義）
- `data/push_jsonl/` — PUSH 生ログ（監査・リプレイ入力）

### `scripts/`

新系 CLI のエントリポイント。

- 例（将来）: `morning_screen.py`、`replay_month.py`、`shadow_run.py`。
- ルート `scripts/kabu_api_check.py` は接続確認用として **旧ツリーに残してよい**（必要ならラッパをここに追加）。

### `data/`

新系のローカルデータ。**git 管理方針はリポジトリ `.gitignore` に従う**（ルートの `results/` 等と同様、成果物は原則コミットしない想定）。

| パス | 用途 |
|------|------|
| `data/universe/` | 銘柄マスタ・日次スナップショット |
| `data/daily/` | 日足・スクリーニング補助 |
| `data/intraday_1m/` | 新系定義の 1分足 CSV |
| `data/push_jsonl/` | PUSH イベントの追記ログ |

### `results/`

新系の実行出力（日付・run_id サブフォルダは実装時に規約化）。

| パス | 用途 |
|------|------|
| `results/morning_screen/` | 朝スクリーニング CSV / JSON |
| `results/replay/` | リプレイトレード・サマリ |
| `results/shadow/` | 本番発注なしの shadow / paper 相当 |
| `results/reports/` | 集計レポート・検証サマリ |

### `docs/`

新系専用の設計・運用ドキュメント（本ファイル、`architecture.md`）。

- 旧系横断の調査（`docs/kabu_signal_validation.md` 等）は **ルート `docs/` に残す** か、移管時にリンクを張る。

## 旧系とのパス対応（参考）

| 旧系（ルート） | 新系（`kabu_native/`） | 備考 |
|----------------|------------------------|------|
| `yahoo_kabu_watch.py` | `scripts/`（将来の専用 CLI） | 旧ファイルは削除しない |
| `symbols.csv` / `watchlist.json` | `data/universe/` + 設定 | 移行時に import スクリプトを検討 |
| `data/intraday_1m/`（Yahoo） | `data/intraday_1m/`（kabu） | **別ディレクトリ** — 混在しない |
| `results/`（Replay 等） | `results/replay/` 等 | プレフィックスで識別 |
| `src/signal_engine.py` | `src/signals/` | Yahoo 版は旧系のまま |
| `src/kabu_*`（試作） | `src/api/`, `src/signals/`, … | 段階的移行・未着手 |

## `.gitkeep`

空ディレクトリを Git で追跡するため、スキャフォールド時点では各葉に `.gitkeep` を置いています。実装が入ったら不要なものは削除して構いません。

**注意:** ルート `.gitignore` の `results/` は `kabu_native/results/` にも効きます。成果物をコミットする場合は ignore 例外を別途検討してください。
