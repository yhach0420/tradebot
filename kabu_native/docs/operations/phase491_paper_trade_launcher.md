# Phase491 — Paper Trade One-Command Launcher

毎朝のペーパートレード起動を1コマンド（またはダブルクリック）に統一する。
`PYTHONPATH` 設定や `cd` の手入力は不要。

**Runtime / Entry / Exit / Order / Discord ロジック変更なし** — 起動ラッパーのみ。

## ファイル

| パス | 説明 |
|------|------|
| `C:\Users\yhach\Documents\tradebotfile\run_paper_trade.bat` | 本番起動用バッチ |
| `kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py` | 現行 PBv2 AM/PM daily runner |

## 起動方法

### ダブルクリック

エクスプローラーで以下をダブルクリック:

```
C:\Users\yhach\Documents\tradebotfile\run_paper_trade.bat
```

### PowerShell / cmd

リポジトリルート（`tradebotfile`）で:

```powershell
.\run_paper_trade.bat
```

`kabu_native` 配下からでも、絶対パスで `cd` するため同じ bat が使える。

## 実行内容

1. `kabu_native` に移動
2. `PYTHONPATH=src` を設定
3. 以下を実行（画面に echo 表示）:

```text
python scripts\run_core10_dynamic40_am_pm_daily_runner.py ^
  --universe-mode core10-dynamic40-price-risk-filter-shadow ^
  --enable-intraday-refresh ^
  --exit-policy-shadow trailing-mfe
```

4. 終了時に `pause` — 成功・失敗どちらもウィンドウが即閉じない

## 安全設定

Runner は引き続き **paper_only / order_enabled=false** の YAML で動作する。
本 bat は起動経路の短縮のみで、注文ロジックは変更しない。

## 出力

Runner 完了後、JSON 1行が標準出力に出る（`verdict`, `exit_code`, `outputs`）。
日次成果物は例:

- `kabu_native/results/reports/daily_runner_summary_YYYYMMDD.json`
  - `am_summary_path` / `pm_summary_path` → Phase653 preserved snapshots
- `kabu_native/results/reports/daily_runner/daily_summary_am_YYYYMMDD.json`
- `kabu_native/results/reports/daily_runner/daily_summary_pm_YYYYMMDD.json`
- `kabu_native/results/reports/phase148_am_pm_daily_runner_YYYYMMDD.json`
- Per session: `live_session_*/small_paper_summary_am.json` / `small_paper_summary_pm.json` (Phase653)

## トラブルシュート

| 症状 | 確認 |
|------|------|
| `python` が見つからない | Python を PATH に追加、または venv を有効化してから bat を再実行 |
| preflight_blocked | `daily_runner_summary_*.json` の `preflight` / `verdict_notes` を確認 |
| ウィンドウがすぐ閉じる | bat 末尾の `pause` が削除されていないか確認 |

## 検証（Phase491）

PowerShell で repo root から:

```powershell
.\run_paper_trade.bat
```

起動メッセージ `[PAPER TRADE] starting...` と runner の JSON 出力（`verdict` 含む）まで進めば OK。
KabuStation 未起動時は `preflight_blocked` になることがあるが、preflight 自体は実行される。
