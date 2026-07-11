# tradebotfile

日本株デイトレ支援（Yahoo 非公式 API ベースの監視・Replay・paper_trade 等）。設計の詳細は **`docs/DESIGN.md`** を参照。

## paper_trade 遅延対策（概要）

- **検出〜通知の遅延**を CSV（`signal_detected_at_jst` / `notify_sent_at_jst` / `signal_lag_sec`）と Discord embed（検出時刻・通知時刻・遅延）で記録します。
- **lag guard**（既定 120 秒超は Discord 送信せず `STALE_SIGNAL_LAG_GT_*` で CSV に残す）と、**同一銘柄は最新シグナルのみ通知**（poll 内で後勝ち）を行います。
- **replay config** のトップレベル **`paper_trade`** ブロック、または CLI（`--max-signal-notify-lag-sec` / `--paper-trade-fetch-timeouts` / `--paper-trade-opening-light` / `--paper-trade-dynamic-watchlist` / `--paper-trade-lag-guard-off`）で上書きできます。既定では **fetch タイムアウト強化は OFF**、**opening_light は OFF**、**Tier1/Tier2 動的ウォッチは OFF** です。
- **候補状態:** Entry/Stop/Take の変化（閾値以上で **UPDATE**）・有力候補の無効化（**INVALIDATED**）を Discord（既存 `build_embed_match` + **footer**）と CSV 列で追跡。クールダウン・設定は **`docs/DESIGN.md` §6.16**。
- 詳細は **`docs/DESIGN.md` §6.16–§6.17** を参照。

## 依存関係

```text
pip install -r requirements.txt
```

`watchdog` 運用には **`psutil`** と **`tzdata`**（Windows で `zoneinfo` の IANA 名を使う場合）が含まれます。未導入でも watchdog は **UTC+9 固定オフセット**にフォールバックします。

## Windows 自動復帰（watchdog）

PC 再起動・更新後も **`discord_issue_bot`** と **`paper_trade`** を自動で立ち上げ直す場合、**タスク スケジューラ 1 本 + watchdog** で足ります。

| スクリプト | 説明 |
|------------|------|
| `scripts/start_watchdog.bat` | **ランチャー**: `ROOT` 正規化・`watchdog_launcher_YYYYMMDD.log` に環境診断・**`check_watchdog_running.ps1`** で二重起動抑止後、**`run_watchdog_inner.bat`** を `start` で起動。タスク スケジューラの **cwd / PATH が空でも** inner 側でルート固定と `where python` 先頭を使う。 |
| `scripts/run_watchdog_inner.bat` | **実体**: `pushd` でルート固定、`where python` の **先頭 1 件**で `python "%ROOT%\\scripts\\watchdog.py"` を実行し、**`PYTHONUNBUFFERED=1`**。stdout/stderr を **`logs/runtime/watchdog_YYYYMMDD.log`** へ追記。 |
| `scripts/check_watchdog_running.ps1` | `Get-CimInstance -ClassName Win32_Process` で **`watchdog.py` を含む `python.exe`** がいればスキップ（ランチャーログへ PID/CommandLine）。 |
| `scripts/watchdog.py` | 5 分ごとにプロセス確認。起動時に **cwd / `sys.executable` / `.env` 絶対パス / 各 bat 絶対パス** をログ。`discord_issue_bot.py` は常時。`python -m market.yahoo.watch --paper-trade`（旧 `yahoo_kabu_watch.py` シム可）は **平日 JST 08:45〜15:40** のみ。 |
| `scripts/start_issue_bot.bat` | Issue Bot 起動（`run_issue_bot_inner.bat` 経由・**`check_issue_bot_running.ps1`** で重複抑止・`logs/runtime/issue_bot_YYYYMMDD.log`）。確認手順は **README「Issue Bot（bat 経由）の動作確認」** を参照。 |
| `scripts/start_paper_trade.bat` | paper_trade 既定コマンド起動（二重起動抑止・`logs/runtime/paper_trade_YYYYMMDD.log`） |

ルートに **`.env`** があり、**`PAPER_LOG_CHANNEL_ID`** と **`DISCORD_TOKEN`**（または旧 **`DISCORD_BOT_TOKEN`**）が設定されていれば、watchdog が復帰した際にそのチャンネルへ短文通知します（任意）。

### タスク スケジューラ設定例

1. **タスク スケジューラ** を開く → **タスクの作成**（基本でよい場合は「簡易タスクの作成」でも可）。
2. **全般:** 名前を **`tradebot_watchdog_start`** にする。ユーザーがログオンしているかどうかにかかわらず実行する場合は「ユーザーがログオンしているかどうかにかかわらず実行する」を検討（管理者権限が必要なことがある）。
3. **トリガー:** **「コンピューターの起動時」** または **「ログオン時」**。
4. **操作:** **プログラムの開始**  
   - **プログラム/スクリプト:**  
     `C:\Users\<あなたのユーザー>\Documents\tradebotfile\scripts\start_watchdog.bat`  
     （実際の clone 先の **絶対パス** に置き換える）  
   - **「開始」**（作業フォルダー）:  
     `C:\Users\<あなたのユーザー>\Documents\tradebotfile`  
     （リポジトリのルート。未指定でも bat 内で `cd` するが、指定を推奨）
5. **条件:** ノート PC なら「AC 電源を使用している場合のみタスクを開始する」の **オフ** を検討（バッテリー駆動でも動かす場合）。
6. **設定:** 「タスクが失敗した場合の再起動の間隔」は運用に合わせて任意。

このタスクだけで、watchdog が常駐し **issue_bot の自動復帰** と **営業時間帯の paper_trade の自動復帰** を行います。

詳細仕様は **`docs/DESIGN.md` §8.1** を参照。

### Issue Bot（bat 経由）の動作確認

手動で `python .\\discord_issue_bot\\discord_issue_bot.py` を実行したときと同様に Discord で `!watch list` 等が反応するか確認する手順です。

1. 既存の Issue Bot 用 `python.exe` を終了する（タスク マネージャーで `discord_issue_bot.py` を含むプロセスを停止するか、該当コンソールで `Ctrl+C`）。
2. リポジトリルートで `scripts\\start_issue_bot.bat` を実行する（ダブルクリックでも可）。
3. `logs\\runtime\\issue_bot_YYYYMMDD.log` を開き、`where python` / `python --version` / `CD` / `CMD=python .\\discord_issue_bot\\...` が想定どおりか、続く `discord` のログで Gateway 接続まで進んでいるかを確認する。
4. Discord 上で **`!watch list`** を実行し、応答があることを確認する。

**環境変数:** Issue Bot のトークン等は **`discord_issue_bot.py` の現行仕様どおり**（**`discord_issue_bot/.env`** のみ。今回の bat 変更では触れていない）。`watchdog.py` は従来どおりリポジトリ直下の **`.env`** を読み込む（Discord 通知用）。

### Watchdog（タスク スケジューラ）の動作確認

タスク スケジューラは **ログオン時の PowerShell 手動実行**と比べ **PATH・カレントディレクトリ・ユーザー環境**が異なることがあります。本リポジトリでは **`run_watchdog_inner.bat`** で **ルート `cd` 固定**と **`where python` の先頭**により差を吸収します。

1. タスク **`tradebot_watchdog_start`** を手動で「実行」する（または一度ログオフ／再起動してトリガーを再現する）。
2. 次で **`watchdog.py`** と **`discord_issue_bot.py`** のプロセスを確認する（PowerShell）:

```powershell
Get-CimInstance -ClassName Win32_Process -Filter "name='python.exe'" |
  Where-Object { $_.CommandLine -like '*watchdog.py*' } |
  Select-Object ProcessId, CommandLine
Get-CimInstance -ClassName Win32_Process -Filter "name='python.exe'" |
  Where-Object { $_.CommandLine -like '*discord_issue_bot.py*' } |
  Select-Object ProcessId, CommandLine
```

3. ログを確認する:
   - **`logs/runtime/watchdog_launcher_YYYYMMDD.log`** … 起動時刻、`ROOT`、`CD_AT_LAUNCH` / `CD_AFTER_pushd_ROOT`、`whoami`、`where python`、`python --version`、**`PATH`**、`check_watchdog_running.ps1` の終了コード、`start` 直後の `errorlevel`（環境によっては **`start` が成功でも `errorlevel=1`** になることがある。その場合は手順 2 と **`watchdog_*.log` に Python が回り始めたか**で判断する）
   - **`logs/runtime/watchdog_YYYYMMDD.log`** … `watchdog bootstrap:` 行、`issue_bot` / `paper_trade` の **restart attempt / spawn / post-restart** 行
4. Discord で **`!watch list`** が応答することを確認する（issue_bot が生きていることの実地確認）。

**`tzdata`:** Windows の Python では **`zoneinfo` が `Asia/Tokyo` を読むために `tzdata` パッケージ**が必要なことがあります。`requirements.txt` に含めています。未インストールでも watchdog は **JST=UTC+9 固定**にフォールバックします（サマータイム無しの日本時間想定）。
