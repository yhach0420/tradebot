# Phase472 — PBv2-3 Runtime Adoption

**Status:** Production ENTRY update (paper_only / order_enabled=false unchanged)

---

## 変更内容

### Before (Phase452)
- `Momentum:low` token (tertile) + Board:mid/high + High Drift + Weak Shape

### After (Phase472 PBv2-3)
- `momentum_continuation_score <= 0.2546` (explicit cutoff; Phase471 C≡A)
- Board:mid/high (entry_score_v2 >= 3)
- High Drift guard
- Weak Shape reject
- **Late Chase Guard** (new)

### Late Chase Guard
```
entry_rise_10min_pct < 0.3719
AND day_high_distance_pct < 1.1872
```
- `reject_reason`: `late_chase_guard`
- Rollback: `late_chase_guard_enabled: false`

---

## ファイル

| ファイル | 変更 |
|---|---|
| `src/small_paper/late_chase_entry_guard.py` | 新規 guard |
| `src/small_paper/entry_expectancy_score_shadow.py` | `momentum_score_cutoff_pass()` |
| `src/research/exposure_gate.py` | explicit cutoff + late chase |
| `src/small_paper/config.py` | `late_chase_guard_enabled`, `momentum_score_cutoff_max` |
| `src/small_paper/pilot_runner.py` | reject log + summary |
| `src/small_paper/discord_message_builder.py` | `LateChase Guard: reject=N` |
| `configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml` | PBv2 flags |

---

## 確認項目

| 項目 | 期待 |
|---|---|
| paper_only | true |
| order_enabled | false |
| momentum cutoff | <= 0.2546 |
| Board mid/high | 維持 |
| High Drift / Weak Shape | 維持 |
| late_chase_guard | rejects.csv / summary / Discord |

---

## Rollback

```yaml
late_chase_guard_enabled: false
```

Momentum cutoff は token 相当のまま維持（cutoff 0.2546）。

---

## テスト

```bash
python -m unittest tests.test_phase472_pbv2_runtime_adoption -v
```
