# Phase503 — Classic Late Chase + RSI Over80 Runtime Adoption

**Verdict:** `runtime_adopted`

## 目的

Phase502 で有効だった guard **C (late_chase AND rsi_over80)** を本番 Runtime ENTRY reject として採用する。

## 採用 Guard

| 項目 | 値 |
|------|-----|
| guard 名 | `classic_late_chase_rsi_guard` |
| reject_reason | `classic_late_chase_rsi_over80` |
| 条件 | `late_chase_flag == true` **AND** `RSI14 >= 80` |

### late_chase_flag 定義

Phase493 `late_chase_after_rally_vwap_trap` クラスタと同一。固定 loser medians（PBv2 20260529–20260622）:

| median | 値 |
|--------|-----|
| r10 | 0.88965 |
| r15_minus_r5 | 0.0 |
| r30_minus_r5 | 0.1468 |
| vwap_dev_pct | -0.1324 |

`(r10 > med OR r30_minus_r5 > med OR r15_minus_r5 > med) AND vwap_dev > med`

## Phase502 結果（参考）

| Guard | delta PnL | blocked W/L | 採用 |
|-------|-----------|-------------|------|
| **B** rsi_over80 (standalone) | +16,290 | 2/9 | 参考 |
| **C** late_chase AND rsi_over80 | +15,600 | **1/6** | **採用** |
| F MST_near_high AND rsi_over80 | +7,900 | 1/4 | 不採用 |
| **D** falling_knife AND macd_weak | -47,501 | 13/5 | **禁止** |
| **G** C OR D | -31,901 | 14/11 | **禁止** |

MACD weak / falling_knife 系、MST_near_high 単独は採用禁止。

## Config

```yaml
classic_late_chase_rsi_guard_enabled: true
classic_late_chase_rsi_threshold: 80
```

**Rollback:** `classic_late_chase_rsi_guard_enabled: false`

## Runtime 変更

| ファイル | 変更 |
|----------|------|
| `src/small_paper/classic_late_chase_rsi_guard.py` | 新規 guard |
| `src/research/exposure_gate.py` | gate 配線 |
| `src/small_paper/config.py` | config 追加 |
| `src/small_paper/pilot_runner.py` | enrich / reject / summary |
| `src/small_paper/discord_message_builder.py` | Daily Summary 表示 |
| `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml` | enabled=true |

## ログ / 観測

### ENTRY 詳細ログ

- `rsi14`
- `rsi_over80`
- `late_chase_flag`
- `classic_late_chase_rsi_guard_pass`

### rejects.csv

- `symbol`
- `time`
- `rsi14`
- `late_chase_flag`
- `reject_reason`

### Daily Summary / Discord

- `classic_late_chase_rsi_over80: N`（Reject Funnel にも `classic_late_chase_rsi_over80` として集計）

## 禁止事項（遵守）

- Exit 変更なし
- Order 変更なし
- CAP 変更なし
- Universe 変更なし
- 銘柄除外なし
- 時間帯除外なし

## テスト

```powershell
cd kabu_native
$env:PYTHONPATH="src"
python -m pytest tests/test_phase503_classic_late_chase_rsi_guard.py -q
```

## 判定

`runtime_adopted` — Phase502 guard C のみ Runtime ENTRY reject として有効化。
