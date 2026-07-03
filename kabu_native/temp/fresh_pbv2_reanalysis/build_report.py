"""Final report.json for fresh_pbv2_reanalysis."""
import json

OUT = "results/reports/fresh_pbv2_reanalysis"

report = {
    "verdict": "fresh_pbv2_reanalysis_done",
    "phase": "fresh_pbv2_reanalysis",
    "generated_at": "2026-07-03",
    "scope_days": ["20260624", "20260625", "20260629", "20260630", "20260701"],
    "headline": (
        "PBv2 accepted collapse (6/29-6/30, PBv2 pool = 0) is caused by the Phase549 "
        "entry_cluster_guard: with reject_csubs=[0,2,3,5] the frozen csub classifier assigned "
        "~100% of live PBv2 finalists to c3_s5 (csub feature vector degenerates to zeros/medians "
        "on live data) and rejected them all. The reject reason was invisible because the OR "
        "overlay (introduced 6/26) overwrites every PBv2 reject with 'or_overlay_not_candidate' "
        "for symbols without an open position. The 7/1 recovery (PBv2=37) is explained by the "
        "Phase606 rollback (reject_csubs=[], stop_low_mfe off), not by freshness semantics v2 "
        "(which contributed 7 of 43 entries)."
    ),
    "key_numbers": {
        "pbv2_pool_accepted": {"20260625": 43, "20260629": 0, "20260630": 0, "20260701": 37},
        "or_pool_accepted": {"20260625": 10, "20260629": 12, "20260630": 6, "20260701": 6},
        "cluster_guard_rejects_c3_s5": {"20260629_AM": 4269, "20260629_PM": 5519, "20260630": 6339, "20260701": 0},
        "finalists_reaching_cluster_stage(recon)": {"20260629_AM": 4271, "20260629_PM": 5915, "20260630": 6345, "20260701": 7444},
        "cluster_kill_rate_at_final_stage_pct": {"20260629_AM": 99.7, "20260629_PM": 100.0, "20260630": 99.9, "20260701": 0.0},
        "freshness_v1_pass_rate_pct(all days)": "35-45% (stable 6/24..7/1; no anomaly on 6/29)",
        "freshness_v2_rescued_entries_20260701": "7 of 43 accepted (16%)",
        "classifier_validation_on_20260701": "recon vs pbv2_internal_reason: diagonal confusion matrix (12600/12600 high_drift, 8744/8751 suitability, 5178/5178 near_day, 4869/4871 cap, 2919/2922 momentum)",
    },
    "required_answers": {
        "q1_when_did_decline_start": (
            "Last healthy sessions: 6/25 AM (PBv2=43+OR=10) and 6/25 PM. 6/26-6/28 have no usable "
            "session outputs (empty session dirs; Phase552 'model path fix' on the cluster guard in "
            "the 6/28 commit suggests startup failures in between). First observed collapsed "
            "sessions: 6/29 AM (accepted=12, all OR pool, PBv2 pool=0) and 6/29 PM (accepted=0)."
        ),
        "q2_first_metric_to_change": (
            "pbv2_count (PBv2-pool accepted): 43 -> 0, and the appearance of "
            "cluster_guard_reject_count=4269/5519 (100% c3_s5) in the 6/29 summaries. Upstream "
            "funnel metrics (push volume, freshness pass rate ~36%, score3-fresh candidate counts, "
            "momentum/board input distributions) show no regime change vs 6/24-6/25."
        ),
        "q3_same_execution_structure": (
            "No. 6/24 ran a pre-OR-overlay runtime; 6/25 ran an OR-overlay runtime (f50c5a7-era "
            "working tree); 6/29-6/30 ran the 924bb1e-era working tree with cluster guard, "
            "stop_low_mfe guard, vol_liq startup cache, volume-gate shadow and live-order dry-run "
            "wired; 7/1 ran the Phase616 restructured core (core_runtime_mode=FULL_EXTENSION) with "
            "freshness semantics v2. Config sha256 differs every day; none matches a commit exactly "
            "(daily uncommitted working-tree runs)."
        ),
        "q4_config_diffs": (
            "Yes. Between 6/25 and 6/29: +entry_cluster_guard (reject_clusters=[5], "
            "reject_csubs=[0,2,3,5]), +stop_low_mfe_guard, +vol_liq_startup_cache, "
            "+volume_gate_relaxation_shadow, +live order dry-run flags. Between 6/30 and 7/1: "
            "stop_low_mfe_guard_enabled false, entry_cluster_guard_reject_csubs=[], "
            "freshness_semantics_v2_enabled true, board fallback flag false. Gate thresholds "
            "(entry_score_v2_min=3, momentum cutoff 0.2546, suitability threshold 54.6957, CAP 4+1) "
            "unchanged across all days. See fresh_pbv2_reanalysis_config_diff.csv."
        ),
        "q5_input_data_diffs": (
            "No material difference. Price-age p50 4-7s, board-age p50 ~0.6s on every day; "
            "freshness v1 pass rate 31-45% on all days incl. 6/29; momentum p50 0.05-0.25, "
            "imbalance p50 0.51-0.56, board mid/high share ~75-85% - all stable. push_messages "
            "same order of magnitude. Input data does not explain the collapse."
        ),
        "q6_top_pbv2_internal_blocker": (
            "On 6/29-6/30 among fresh candidates: high_drift_pullback and daytrade_suitability are "
            "the largest early-stage blockers (as on healthy days), but the *decisive* blocker is "
            "entry_cluster_guard at the final stage: it rejected 4269/4281 (6/29 AM), 5519/5519 "
            "(6/29 PM), 6339/6345 (6/30) of the candidates that had passed every other PBv2 gate. "
            "On 7/1 (ground truth logging): high_drift 12600 > suitability 8751 > near_day 5178 > "
            "cap 4871 > momentum 2922; cluster=0."
        ),
        "q7_why_score3_fresh_rejected": (
            "score=3 (Momentum:low + Board:mid|high) and fresh is necessary but far from "
            "sufficient: those candidates still die at high_drift/near-day-high guards, "
            "daytrade_suitability (vol_liq below 54.6957 or inputs missing), reentry-RSI/quality "
            "guards, and on 6/29-6/30 the survivors were then 99.7-100% killed by "
            "entry_cluster_guard c3_s5. The 6/25 accepted profile classifies as c3_s5 under the "
            "frozen model: all 10 sampled healthy-day winners would have been cluster-rejected on 6/29."
        ),
        "q8_structure_alone_explains_zero": (
            "Yes, for the PBv2 pool: decision-parity counterfactual on identical recorded "
            "candidates shows S1 (6/29 runtime, csubs 0/2/3/5) passes 0 finalists while S3 "
            "(6/25-equivalent, no cluster guard) and S4 (HEAD, csubs=[]) pass all 4271/5915/6345 - "
            "100% of divergence at exactly one stage (entry_cluster_guard). No market/input change needed."
        ),
        "q9_freshness_change_explains_recovery": (
            "No. 6/30 already had freshness relief (board fallback active: 16571 fallback uses, "
            "stale rejects fell from 60% to 5%) yet PBv2 stayed 0. On 7/1 freshness v2 rescued "
            "15981 evaluations (trade_stale tag) but only 7 of 43 accepted entries came from that "
            "path; the other 36 would have passed v1 too. Cluster-guard rollback is the recovery driver."
        ),
        "q10_bridge_state_batch_prior_trades_impact": (
            "Feature-bridge quality is stable (live_feature_complete 93-96% every day, quality "
            "fallback 9-15%). No evidence of state/batch/prior_trades involvement: "
            "same_symbol_overlap and cap rejects vanish on 6/29-30 simply because no positions ever "
            "opened; prior_trades/cooloff counters show no anomaly. However, the csub feature "
            "vector (relative_board_*, volume_accel_*, momentum_decay_* ...) is effectively "
            "missing at live-entry time and is zero-filled, which is what funnels ~100% of "
            "finalists into csub 5 - an offline-trained model applied to live-degraded features."
        ),
        "q11_or_overlay_masking": (
            "Confirmed and quantified. _maybe_try_or_overlay_entry replaces every PBv2 reject "
            "decision (for symbols without an open position) with the OR-overlay decision, so the "
            "logged reason becomes or_overlay_not_candidate. On 7/1, 34651 final "
            "'or_overlay_not_candidate' rows decompose into internal reasons: high_drift 11595, "
            "suitability 8715, cap 4870, near_day 4831, momentum 2219, reentry_rsi 1466, etc. On "
            "6/29 PM (zero open positions) 100% of PBv2-internal reasons were masked - including "
            "all 5519 cluster-guard rejects. This masking is why the cluster guard cause was invisible."
        ),
        "q12_most_supported_hypothesis": (
            "Implementation problem: Phase549 entry_cluster_guard csub-reject list [0,2,3,5] "
            "combined with degenerate live csub features (zero-filled) classifies ~100% of PBv2 "
            "finalists as c3_s5 and rejects them; OR-overlay reason masking hid it. Evidence: "
            "cluster_guard_blocked_cluster_counts={'c3_s5': all}, parity counterfactual (S1 pass=0 "
            "vs S3/S4 pass=100%), 7/1 rollback restoring PBv2=37, healthy-day winners classifying c3_s5."
        ),
        "q13_rejected_hypotheses": [
            "Market factor: input distributions, push volume, freshness ages, score3-fresh counts stable across 6/24-7/1; OR pool kept accepting on 6/29-30. REJECTED.",
            "Data problem (feed degradation): live_feature_complete 93-96% and board age p50 0.6s on all days; stale rate on 6/29 (60-67%) within 6/24-25 range (53-67%). REJECTED as cause of collapse.",
            "Freshness definition as cause of collapse: v1 pass rate on 6/29 equals healthy days; 6/30 fallback removed staleness yet PBv2=0. REJECTED.",
            "Freshness semantics v2 as recovery driver: only 7/43 entries on 7/1 came via trade_stale rescue. REJECTED as primary driver (minor positive contributor).",
            "OR overlay as cause of reduced accepts: it only runs after PBv2 reject and added OR entries (12 on 6/29); it masks logging but does not reject PBv2 candidates. REJECTED as cause (CONFIRMED as observability bug).",
            "Structural interference from feature bridge/state/batch/prior_trades: no supporting deltas found. REJECTED (except csub feature availability, which is part of the cluster-guard failure mode).",
        ],
        "q14_mainline_fixes": [
            "Keep entry_cluster_guard_reject_csubs=[] (Phase606 rollback) until the csub classifier is re-trained/validated on live-available features; add a guard-side precondition that csub rejection only applies when >=N csub features are actually present (not zero-filled).",
            "Fix reject-reason masking: persist pbv2_internal_reason for every masked reject (already added in working tree) AND surface entry_cluster_guard/stop_low_mfe etc. in reject_reason_counts (e.g. log final reason as or_overlay_not_candidate:pbv2=<internal>).",
            "Add a runtime alert when any single PBv2 gate rejects >X% of finalists in a session (cluster guard would have alerted at 99.7% on 6/29 AM).",
            "Commit the runtime code/config actually used for production sessions (every collapse day ran uncommitted working-tree state; 6/26-6/28 outputs are missing entirely).",
            "Record cluster_guard decisions (cluster_id/csub/liquidity_burst) on candidate events to make this class of failure auditable without reconstruction.",
        ],
        "q15_additional_verification": [
            "Re-validate Phase549 csub model offline vs live: measure csub feature availability at live entry time; if mostly missing, the model is untrainable for runtime use.",
            "6/26-6/28: recover or explain empty session dirs (crash logs, Phase552 model-path failure window).",
            "Quantify PnL impact of freshness v2 vs v1 on multi-day forward window (only 7/1+ has v2 logging; PF/DD comparison needs more sessions).",
            "Verify stop_low_mfe_guard (reject_count=0 on 6/29-30) really never fired and can be re-enabled safely or removed.",
            "Confirm OR pool behavior unchanged across the restructure (OR accepted 10->12->6->6; small sample).",
        ],
    },
    "artifacts": [
        "fresh_pbv2_reanalysis_daily_funnel.csv",
        "fresh_pbv2_reanalysis_config_diff.csv",
        "fresh_pbv2_reanalysis_code_diff.csv",
        "fresh_pbv2_reanalysis_input_distribution.csv",
        "fresh_pbv2_reanalysis_pbv2_internal_blockers.csv",
        "fresh_pbv2_reanalysis_score3_fresh_trace.csv.gz",
        "fresh_pbv2_reanalysis_structure_parity.csv",
        "fresh_pbv2_reanalysis_first_divergence.csv.gz",
        "fresh_pbv2_reanalysis_freshness_counterfactual.csv",
        "fresh_pbv2_reanalysis_case_trace.csv.gz",
        "fresh_pbv2_reanalysis_report.json",
    ],
    "method_notes": [
        "No mainline code was modified; no orders were placed; analysis is read-only over recorded session artifacts + git history.",
        "PBv2 internal blockers on masked days (6/29-6/30) were reconstructed from candidate event fields + volume_gate_shadow_eval joins + frozen cluster model; the reconstruction was validated against 7/1 pbv2_internal_reason ground truth (near-perfect diagonal confusion).",
        "Structure parity (Investigation E) was computed as a per-candidate decision counterfactual on identical recorded inputs rather than full runtime re-execution (disk/parallel constraints); the shared gate prefix is identical across structures, so divergence localizes exactly.",
        "Freshness counterfactual approximates event_stale as fresh for pre-7/1 sessions (recorded_at age not logged before v2); board_stale and trade_stale are exact from audit ages.",
        "Disk constraint note: C: usage was already 90.8% before this analysis started (constraint '<=76%' pre-violated by existing data); this analysis added only small aggregates (<5MB) and cleaned temp files afterwards.",
    ],
}

with open(f"{OUT}/fresh_pbv2_reanalysis_report.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
print("report written")
