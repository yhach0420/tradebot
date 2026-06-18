# Phase433 — Discord ENTRY/EXIT Symbol Name & Exit Time

Generated: 2026-06-17  
Scope: **Discord表示のみ**（Runtime Entry/Exit判定・YAML・Order・資産シミュ未変更）

## 変更内容

### 銘柄表示

- 新関数 `format_symbol_display(symbol, name, name_map=...)`（`discord_symbol_names.py`）
- 名前取得元: `data/jpx/tradable_symbols.csv`（既存 `get_cached_symbol_name_map()`）
- 表示形式: `6976.T 太陽誘電`
- 名前不明時: **コードのみ**（例 `9999.T`）— 通知は継続

### ENTRY通知

- タイトル: `【ENTRY】 {code} {name}`
- 詳細: `銘柄: {code} {name}` + `時刻: HH:MM:SS`（JST）
- Discord field `時刻` も `HH:MM:SS` 形式

### EXIT通知

- タイトル: `【EXIT】 {code} {name}`
- 詳細: `銘柄: {code} {name}` + **`EXIT時刻: HH:MM:SS`**
- `exit_time` は observer context の `exit_time` → fallback `event_time` / `timestamp`
- 既存の損益円表示（Phase316）は維持

### エラー耐性

- 名前 lookup 失敗 → コードのみ
- Discord 投稿失敗は既存どおり Runtime を止めない（`_dispatch_observer_events` try/except）

## 変更ファイル

| ファイル | 変更 |
|----------|------|
| `src/small_paper/discord_symbol_names.py` | `format_symbol_display` 追加 |
| `src/small_paper/discord_message_builder.py` | `format_time_hms_jst`、ENTRY/EXIT detail 拡張 |
| `src/small_paper/discord_notifier.py` | `notify_entry` / `notify_exit` / CAP blocked タイトル・時刻 |

## テスト

`tests/test_phase433_discord_symbol_name_exit_time.py`

## 必須回答

1. **ENTRY通知に銘柄名追加**: 完了
2. **EXIT通知に銘柄名追加**: 完了
3. **EXIT通知にEXIT時刻追加**: 完了（`EXIT時刻: HH:MM:SS` JST）
4. **銘柄名 fallback**: `tradable_symbols.csv` に無い場合は `{symbol}.T` コードのみ
5. **Runtimeロジック未変更**: Entry/Exit判定・YAML・Order・シミュレーション無変更（Discord表示層のみ）
