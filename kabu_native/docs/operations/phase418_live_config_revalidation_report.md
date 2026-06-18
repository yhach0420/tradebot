# Phase418 — Phase273 / Phase274 Full Revalidation (no_overlap_replace Baseline B)

Generated: 2026-06-17T00:15:17+09:00
Status: **revalidation_complete**

## 必須回答

- **Phase273 recommendation**: `scale_candidate_3000k` (maintained)
- **Phase274 adoption**: `adopt` (changed_reject_to_adopt)
- **150万円運用継続妥当か**: 妥当（verdict=adopt）
- **200万/300万候補の意味**: あり（いずれか adopt）
- **明日以降の資金系shadow**: Phase273 forward daily equity on Baseline B structural stream, Phase274 auto-transition band crossing watch (2M threshold), accepted/rejected trade events with reject_reason breakdown, no_overlap_replace overlap chain length / hold_sec drift
- **無効化すべき過去結論**:
  - Phase416 Baseline B Phase274 adoption_verdict=reject (distorted by missing entry_price)
  - Phase416 Baseline B live_start_candidate_1500k verdict=reject (654 invalid_price rejects)
  - Any capital-sim metric on Baseline B before entry_price enrichment

## Input validation

- trade_count: 681
- period_days: 11
- entry_price enrichment: missing=0

## Phase273 candidates (Baseline B enriched)

- live_start 1500k: final=1641767.98 accepted=678 rejected=3 verdict=adopt
- scale 2000k+: final=2133897.88 accepted=681 rejected=0 verdict=adopt
- scale 3000k: final=3133897.88 accepted=681 rejected=0 verdict=adopt
- recommendation: `scale_candidate_3000k`

## Phase274 auto-transition

- final_equity: 1641767.98
- active_policy_band: 1500k
- transition_day_to_2000k: None
- adoption_verdict: `adopt`

## 1500k reject reason (if any)

not_rejected

## no_overlap_replace 前後の adoption 変化

{
  "phase273_recommendation": {
    "baseline_a_pre_no_overlap": "scale_candidate_3000k",
    "baseline_b_post_no_overlap": "scale_candidate_3000k"
  },
  "phase274": {
    "baseline_a_pre_no_overlap": "adopt",
    "baseline_b_unenriched_phase416": "reject",
    "baseline_b_enriched_phase418": "adopt"
  },
  "live_start_1500k": {
    "baseline_a": {
      "final_equity": 1513300.0,
      "accepted_count": 546,
      "rejected_count": 983,
      "verdict": "adopt",
      "recommended": "scale_candidate_3000k"
    },
    "baseline_b_unenriched": {
      "final_equity": 1472500.0,
      "accepted_count": 20,
      "rejected_count": 661,
      "verdict": "reject"
    },
    "baseline_b_enriched": {
      "final_equity": 1641767.98,
      "accepted_count": 678,
      "rejected_count": 3,
      "verdict": "adopt"
    }
  }
}
