# Phase497 — Near Day High Decomposition

**Verdict:** `overfit_feature`
**Period:** 20260529 — 20260622
**Near-high filter:** day_high_distance <= 1.0

## 必須回答

- **1_max_separation_feature:** vwap_structure_score
- **1_max_cohens_d:** -0.5725
- **2_loser_primary_pattern:** elevated vwap_structure_score (L median vs W), stop_hit_rate=1.0, low mfe median=0.0246
- **3_winner_primary_pattern:** lower near-high decay / fresher high context, positive r5/r10 medians, median_mfe=0.1711
- **4_reproducibility:** LOO stable 1.0 min_d=0.1904
- **5_6976_dependent:** True
- **6_overfit_risk:** high
- **7_top10_new_features:** ['r10_after_high', 'time_since_last_high', 'r5_after_high', 'vwap_above_duration', 'high_stall_duration', 'high_age_vs_distance', 'board_change_after_high', 'recent_high_failure_count', 'high_break_retry_count', 'near_high_decay_score']
- **8_replay_candidate:** none — existing near-high distance sufficient
- **9_shadow_candidate:** ['r10_after_high', 'time_since_last_high', 'r5_after_high']
- **10_runtime_candidate:** False
- **11_next_action:** Continue dhd soft gate shadow only; decomposition adds marginal signal

- **near_high W/L:** 29 W / 15 L

## Top10 new features

```json
[
  {
    "feature_id": "r10_after_high",
    "feature_type": "new",
    "is_new": true,
    "group_w_mean": 1.599131,
    "group_w_median": 1.3157,
    "group_l_mean": 2.120156,
    "group_l_median": 1.7391,
    "missing_rate_w": 0.4483,
    "missing_rate_l": 0.4,
    "cohens_d": 0.4722,
    "ks_statistic": 0.333333,
    "mutual_information": 0.0093,
    "feature_direction": "higher_in_loser",
    "loo_min_abs_d": 0.2483,
    "loo_median_abs_d": 0.4722,
    "loo_stable_days_pct": 1.0,
    "loo_robust": true,
    "exclude_6976_abs_d": 0.4722,
    "exclude_top_day_abs_d": 0.4722,
    "rank": 1
  },
  {
    "feature_id": "time_since_last_high",
    "feature_type": "new",
    "is_new": true,
    "group_w_mean": 4.0292,
    "group_w_median": 0.6,
    "group_l_mean": 19.437692,
    "group_l_median": 0.7,
    "missing_rate_w": 0.1379,
    "missing_rate_l": 0.1333,
    "cohens_d": 0.4318,
    "ks_statistic": 0.206154,
    "mutual_information": 0.0022,
    "feature_direction": "higher_in_loser",
    "loo_min_abs_d": 0.1708,
    "loo_median_abs_d": 0.445,
    "loo_stable_days_pct": 1.0,
    "loo_robust": true,
    "exclude_6976_abs_d": 0.4269,
    "exclude_top_day_abs_d": 0.4275,
    "rank": 2
  },
  {
    "feature_id": "r5_after_high",
    "feature_type": "new",
    "is_new": true,
    "group_w_mean": 1.188255,
    "group_w_median": 0.83005,
    "group_l_mean": 0.76679,
    "group_l_median": 0.30495,
    "missing_rate_w": 0.3103,
    "missing_rate_l": 0.3333,
    "cohens_d": -0.2948,
    "ks_statistic": 0.35,
    "mutual_information": 0.0589,
    "feature_direction": "lower_in_loser",
    "loo_min_abs_d": 0.0573,
    "loo_median_abs_d": 0.2948,
    "loo_stable_days_pct": 0.8889,
    "loo_robust": false,
    "exclude_6976_abs_d": 0.2948,
    "exclude_top_day_abs_d": 0.2948,
    "rank": 3
  },
  {
    "feature_id": "vwap_above_duration",
    "feature_type": "new",
    "is_new": true,
    "group_w_mean": 425.245332,
    "group_w_median": 166.0,
    "group_l_mean": 334.8718,
    "group_l_median": 208.0,
    "missing_rate_w": 0.1379,
    "missing_rate_l": 0.1333,
    "cohens_d": -0.1617,
    "ks_statistic": 0.150769,
    "mutual_information": 0.0022,
    "feature_direction": "lower_in_loser",
    "loo_min_abs_d": 0.001,
    "loo_median_abs_d": 0.1517,
    "loo_stable_days_pct": 0.6667,
    "loo_robust": false,
    "exclude_6976_abs_d": 0.104,
    "exclude_top_day_abs_d": 0.1915,
    "rank": 4
  },
  {
    "feature_id": "high_stall_duration",
    "feature_type": "new",
    "is_new": true,
    "group_w_mean": 2.810605,
    "group_w_median": 0.052788,
    "group_l_mean": 3.681167,
    "group_l_median": 0.306557,
    "missing_rate_w": 0.4483,
    "missing_rate_l": 0.4,
    "cohens_d": 0.1271,
    "ks_statistic": 0.180556,
    "mutual_information": 0.0093,
    "feature_direction": "higher_in_loser",
    "loo_min_abs_d": 0.0431,
    "loo_median_abs_d": 0.1479,
    "loo_stable_days_pct": 0.4444,
    "loo_robust": false,
    "exclude_6976_abs_d": 0.1271,
    "exclude_top_day_abs_d": 0.1271,
    "rank": 5
  },
  {
    "feature_id": "high_age_vs_distance",
    "feature_type": "new",
    "is_new": true,
    "group_w_mean": 18.316393,
    "group_w_median": 1.899184,
    "group_l_mean": 25.386855,
    "group_l_median": 1.697531,
    "missing_rate_w": 0.1379,
    "missing_rate_l": 0.1333,
    "cohens_d": 0.1182,
    "ks_statistic": 0.249231,
    "mutual_information": 0.0022,
    "feature_direction": "higher_in_loser",
    "loo_min_abs_d": 0.0955,
    "loo_median_abs_d": 0.1204,
    "loo_stable_days_pct": 0.3333,
    "loo_robust": false,
    "exclude_6976_abs_d": 0.1045,
    "exclude_top_day_abs_d": 0.1076,
    "rank": 6
  },
  {
    "feature_id": "board_change_after_high",
    "feature_type": "new",
    "is_new": true,
    "group_w_mean": -0.035877,
    "group_w_median": -0.034641,
    "group_l_mean": -0.023476,
    "group_l_median": -0.012544,
    "missing_rate_w": 0.4483,
    "missing_rate_l": 0.3333,
    "cohens_d": 0.0872,
    "ks_statistic": 0.325,
    "mutual_information": 0.0737,
    "feature_direction": "higher_in_loser",
    "loo_min_abs_d": 0.0655,
    "loo_median_abs_d": 0.0915,
    "loo_stable_days_pct": 0.3333,
    "loo_robust": false,
    "exclude_6976_abs_d": 0.102,
    "exclude_top_day_abs_d": 0.0872,
    "rank": 7
  },
  {
    "feature_id": "recent_high_failure_count",
    "feature_type": "new",
    "is_new": true,
    "cohens_d": null,
    "feature_direction": "insufficient_data",
    "rank": 8
  },
  {
    "feature_id": "high_break_retry_count",
    "feature_type": "new",
    "is_new": true,
    "cohens_d": null,
    "feature_direction": "insufficient_data",
    "rank": 9
  },
  {
    "feature_id": "near_high_decay_score",
    "feature_type": "new",
    "is_new": true,
    "cohens_d": null,
    "feature_direction": "insufficient_data",
    "rank": 10
  }
]
```