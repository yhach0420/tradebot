# Phase637: Discord Notification Enhancement

## Purpose

Discord Daily / AM / PM Summary だけで、Paper Trade の状態・利益改善候補・Shadow 効果・システム健全性が読めるようにする。

## Design

- **既存 Summary（詳細 / canonical）は変更しない**
- Phase637 は operator status セクションを **追加**（Phase490 observability ブロックを Daily Summary 上で置換）
- 各セクションは **実測 summary / events のみ**（推測禁止）
- Research Shadow は `omit_operator_covered=True` で重複行を省略し、1画面長に収める

## Sections

| # | Section | Source (measured) |
|---|---------|-------------------|
| 1 | PBv2 Summary | `pbv2_count`, `or_count`, `accepted_count`, exits |
| 2 | Rise5 Shadow Summary | `pbv2_rise5_shadow_*` |
| 3 | Freshness Summary | event/board stale rejects, trade_stale tags |
| 4 | Cluster Guard Summary | `cluster_guard_*` |
| 5 | Gate Dominance Summary | `gate_dominance_*` (level=none でも表示) |
| 6 | ENTRY Quality Summary | `entry_quality_guard_*` |
| 7 | EXIT Summary | exit bucket breakdown + exit monitor metrics |
| 8 | Shadow Summary | Rise5 / PullbackMisread / BoardDynamic / EXIT T2·T3 Δ |
| 9 | Today's Insight | 上記実測から導いた簡潔コメントのみ |
| 10 | System Health | api_errors / data_gaps / feature_complete / peak_slots |

## Wire

- `discord_message_builder.build_operator_status_embed_fields`
- `discord_notifier._production_summary_fields`
  1. 詳細（canonical）
  2. Operator status (10 sections, present only when data exists)
  3. Research Shadow（operator 重複除外）

`build_observability_embed_fields` は残置（後方互換 / 単体利用可）。

## Tests

```bash
python -m pytest tests/test_phase637_discord_notification.py tests/test_phase490_observability_upgrade.py -q
```

## Artifacts

- `results/reports/phase637_discord_notification/phase637_report.json`
- `docs/operations/phase637_discord_notification.md`

## Verdict

`phase637_discord_notification_done`
