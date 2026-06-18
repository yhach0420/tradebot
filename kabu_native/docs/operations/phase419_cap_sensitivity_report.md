# Phase419 — CAP Sensitivity Study (Post-Phase414)

Generated: 2026-06-16T23:54:08+09:00
Status: **cap_sensitivity_complete**

## 必須回答

1. **最適CAP**: CAP5
2. **CAP3との差**: pnl=58501.06 yen, pf=0.0555, maxDD=-5999.82 yen, acceptedΔ=9, rejectedΔ=-9
3. **本番変更推奨か**: Researchのみ（Runtime/YAML変更は禁止）。採用判断は CAP差分と reject EV を見て別Phaseで実施。
4. **資産シミュ変更推奨か**: Baseline Bでは entry_price 補完が必須（Phase418で再検証済み）。
5. **rollback方法**: Runtime変更なし。将来CAP変更する場合は設定をCAP3に戻すだけ（影響は max_concurrent_positions のみ）。

## Grid summary

- 出力CSV: `results/reports/phase419_cap_sensitivity_grid.csv`

## CAP rejects expected value (max_concurrent_positions)

- 出力JSON: `results/reports/phase419_cap_sensitivity_summary.json` 内 `cap_reject_expected_value`
