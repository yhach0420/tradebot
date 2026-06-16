# Phase413 — No Overlap Replace Runtime Adoption Review

## Conclusion

- **Runtime反映してよいか**: YES (candidate)
- **反映理由**: verdict=`runtime_adoption_candidate` (PnL/PF/maxDD gate + churn reduction)
- **rollback方法**: `same_symbol_open_policy: replace`

## 必須回答

- **1. Phase412と何が違うか**: Phase412は同一銘柄open中の新ENTRYを単純にreject（その結果、既存ポジションがbaselineでは早期に閉じていた分まで失われ得る）。Phase413は overlap_replaced_review 連鎖を“継続ポジション”に連結し、既存ポジション維持（hold延長）を近似する。
- **2. overlap_replaced_review はどれだけ減るか**: 999 → 151 (Δ=848)
- **3. trade_count はどれだけ減るか**: 1529 → 681 (Δ=848)
- **4. PnL/PF/maxDD は改善するか**: PnL Δ=0.0, PF 1.101→1.1234, maxDD 105301.93→102282.41
- **5. 保有時間は自然に伸びるか**: median_hold 55.0→313.0 / avg_hold 310.81→697.84
- **6. Boundary/Phase409評価可能性は上がるか**: eligible 402→375 (rate 26.29%→55.07%), would_hit 355→328 (rate 23.22%→48.16%)
- **7. Runtime反映してよいか**: YES
- **8. 反映するなら rollback 方法**: `same_symbol_open_policy: replace`

## 20260616 (churn day) check

- baseline trades: 774 / shadow trades: 27
- overlap_replaced_review: 747 → 0
- boundary_eligible: 37 → 21
