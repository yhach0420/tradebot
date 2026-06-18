# Phase420 — CAP5 Adoption Review & Runtime Alignment

Generated: 2026-06-17T00:06:05+09:00
Status: **adoption_review_complete**

## 必須回答

1. **CAP5採用可否**: 採用候補（条件OK）
2. **CAP3との差**: pnl=58501.06 yen, pf=0.0555, maxDD=-5999.82 yen, acceptedΔ=9, rejectedΔ=-9
3. **買付余力問題**: CAP5 buying_power_reject=3 (CAP3=3)
4. **Boundaryとの相性**: eligible_rate CAP3=0.547085 CAP5=0.550147; would_hit CAP3=0 CAP5=0
5. **Runtime変更するべきか**: Part B へ進める
6. **Phase273再推奨値**: scale_candidate_3000k (cap5 policy band)
7. **Phase274再推奨値**: auto-transition uses 1500k cap3 -> consider cap5 for 1500k band
8. **rollback方法**: position_cap を 5→3 に戻す（他変更なし）

## Adoption conditions

- PF>=CAP3: True
- PnL>=CAP3: True
- maxDD<=CAP3+10%: True
- buying_power_reject within guardrail: True
- not single-day dependent: True (pos_days=4, max_share=0.430774)

## Outputs

- `results/reports/phase420_cap5_adoption_review_summary.json`
- `results/reports/phase420_cap5_vs_cap3_daily.csv`
- `results/reports/phase420_cap5_runtime_readiness.json`
- `docs/operations/phase420_cap5_adoption_review.md`
