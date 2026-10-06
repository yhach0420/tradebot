"""Frozen cross-sectional leader-laggard research contract. No outcomes. No retune knobs."""
from __future__ import annotations

from typing import Any

from research.causal_driver_pb1 import C1_FIRST, C1_LAST, DEV_FIRST, DEV_LAST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.causal_driver_pb1.cross_sectional_precommit import (
    BACKUP_DRIVER,
    BOOTSTRAP_METHOD,
    BOOTSTRAP_N,
    BOOTSTRAP_SEED,
    CLOCK_FIRST,
    CLOCK_LAST,
    DRIVER_FAMILY_ID,
    FAMILY_N,
    FDR_FAMILY_N,
    FDR_METHOD,
    FDR_Q,
    FUTURES_STATUS,
    GLOBAL_MIN_VALID_LEADERS,
    GLOBAL_TARGET_MIN_FRAC,
    GLOBAL_TARGET_MIN_VALID,
    HORIZON_LAST_T,
    LEADER_N,
    LEADER_PRICE_AGE_SEC,
    LOOKBACKS_MIN,
    MKT_EX_LEADERS_MIN_FRAC,
    MKT_EX_LEADERS_MIN_VALID,
    OFFSETS_MIN,
    PAST_CONTROL_MIN,
    PEER_CLOCK_COVERAGE_MIN,
    PEER_MIN_N,
    PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
    PRECOMMIT_ID,
    PRIMARY_NEXT_DRIVER,
    RESOLVER,
    RESPONSE_HORIZONS_MIN,
    SAME_SESSION_ONLY,
    SCOPE_N,
    STOCK_PRICE_FIELD,
    STOCK_TIMESTAMP_SEMANTICS,
    STRICT_TARGET_PRICE_AGE_SEC,
    TARGET_PRICE_AGE_SEC,
    DAY_SHUFFLE_METHOD,
    DAY_SHUFFLE_N,
    DAY_SHUFFLE_SEED,
)
from research.causal_driver_pb1.identity.ids import sha256_obj


def frozen_contract(
    *,
    stock: dict[str, Any],
    sectors: dict[str, Any],
    leaders: dict[str, Any],
    days: dict[str, Any],
) -> dict[str, Any]:
    frozen = list(leaders.get("frozen_leaders") or [])
    scopes = []
    for row in frozen:
        sid = row["sector_id"]
        scopes.append(
            {
                "scope_id": f"SAME_SECTOR_{sid}",
                "mechanism": "SAME_SECTOR_LEADER",
                "sector_id": sid,
                "sector_name": row["sector_name"],
                "leader_symbol": row["leader_symbol"],
                "target": "equal_weight_non_leader_peers_same_TSE33_sector",
                "leader_excluded_from_peer_basket": True,
            }
        )
    scopes.append(
        {
            "scope_id": "GLOBAL_LEADER_BASKET",
            "mechanism": "GLOBAL_LEADER_BASKET",
            "leader_symbols": [r["leader_symbol"] for r in frozen],
            "target": "equal_weight_all_eligible_non_leader_targets",
            "min_valid_leaders": GLOBAL_MIN_VALID_LEADERS,
        }
    )
    folds = days.get("folds") or {}
    shuffle = days.get("shuffle") or {}
    contract = {
        "precommit_id": PRECOMMIT_ID,
        "research_question": (
            "Can price information observed in a small frozen set of highly liquid leader stocks "
            "at or before T predict subsequent return of OTHER stocks?"
        ),
        "phase_role": "Group-level cross-sectional causal discovery. Not PB1. Not self technical. Not old peer rename. Not Alpha.",
        "driver_family": {
            "PRIMARY_NEXT_DRIVER": PRIMARY_NEXT_DRIVER,
            "DRIVER_FAMILY_ID": DRIVER_FAMILY_ID,
            "BACKUP_DRIVER": BACKUP_DRIVER,
            "FUTURES_DRIVER_DATA_NOT_READY": FUTURES_STATUS,
            "phase0_DriverFamily_enum_mutated": PHASE0_DRIVER_FAMILY_ENUM_MUTATED,
        },
        "eligible_periods": {
            "DEVELOPMENT": {"first": DEV_FIRST, "last": DEV_LAST, "role": "candidate_discovery"},
            "ECONOMIC_DEVELOPMENT_EXPOSED": {"first": C1_FIRST, "last": C1_LAST, "role": "locked_oot_confirmation_not_final_certification"},
            "FROZEN_VALIDATION": {"first": FV_FIRST, "last": FV_LAST, "access": "DENY"},
            "PROSPECTIVE": {"first": PROSPECTIVE_FROM, "access": "DENY"},
        },
        "research_universe": {
            "id": "FIXED_RESEARCH_OBSERVATION_UNIVERSE_105_V1",
            "n": 105,
            "sha256": sectors.get("universe105_sha256"),
            "dynamic40": False,
            "runtime_50_does_not_change_research_universe": True,
        },
        "leader_set": {
            "n": LEADER_N,
            "reason": "8 frozen leaders + up to 42 trade candidates <= 50 Kabu slots",
            "selection": "outcome_free_sector_then_symbol_median_trading_value",
            "one_leader_per_selected_sector": True,
            "listed_by_development_start": DEV_FIRST,
            "frozen": frozen,
            "leader_set_sha256": leaders.get("leader_set_sha256"),
            "LEADER_SET_FROZEN_BEFORE_OUTCOME": True,
            "leave_target_out": True,
        },
        "target_set": {
            "definition": "105 minus 8 frozen leaders",
            "n": leaders.get("target_n"),
            "symbols": leaders.get("target_symbols"),
            "target_set_sha256": leaders.get("target_set_sha256"),
            "disjoint": leaders.get("disjoint"),
            "no_self_prediction": True,
            "point_in_time_not_listed_not_replaced": True,
        },
        "scopes": scopes,
        "family": {
            "scope_n": SCOPE_N,
            "lookbacks": list(LOOKBACKS_MIN),
            "horizons": list(RESPONSE_HORIZONS_MIN),
            "n": FAMILY_N,
            "no_new_tests_after_DEV": True,
            "no_individual_target_winner_search": True,
        },
        "resolver": {
            "price": RESOLVER,
            "field": STOCK_PRICE_FIELD,
            "timestamp_semantics": STOCK_TIMESTAMP_SEMANTICS,
            "same_session_only": SAME_SESSION_ONLY,
            "prior_day_carry": False,
            "fabricated_mid": False,
            "exact_minute_print_required": False,
            "stock_probe": {k: stock.get(k) for k in stock if k != "parquet_schema_names"},
        },
        "freshness": {
            "leader_price_age_sec": LEADER_PRICE_AGE_SEC,
            "target_price_age_sec": TARGET_PRICE_AGE_SEC,
            "strict_target_price_age_sec": STRICT_TARGET_PRICE_AGE_SEC,
            "tuned_on_outcomes": False,
            "strict_cannot_rescue_primary_failure": True,
        },
        "research_clock": {
            "first": CLOCK_FIRST,
            "last": CLOCK_LAST,
            "grid": "native_1m",
            "timezone": "Asia/Tokyo",
            "horizon_last_T": dict(HORIZON_LAST_T),
            "do_not_cross_1130": True,
        },
        "driver_return": {
            "definition": "10000*log(P_leader(T)/P_leader(T-w))",
            "lookbacks_min": list(LOOKBACKS_MIN),
            "both_endpoints_causal": True,
            "global": "equal_weight mean of valid 8 leader returns; require >= 7/8",
            "global_min_valid_leaders": GLOBAL_MIN_VALID_LEADERS,
        },
        "target_response": {
            "class": "INFORMATION_DISCOVERY_OUTCOME",
            "definition": "EQW constituent last-completed return from T to T+h",
            "not_execution_pnl": True,
            "same_sector_peer": {
                "min_peer_n": PEER_MIN_N,
                "min_frac_listed_peers": PEER_CLOCK_COVERAGE_MIN,
                "leader_excluded": True,
            },
            "global_target": {
                "min_valid_nonleaders": GLOBAL_TARGET_MIN_VALID,
                "min_frac_listed_nonleaders": GLOBAL_TARGET_MIN_FRAC,
            },
        },
        "models": {
            "MODEL0": "causal controls only",
            "MODEL1": "MODEL0 + leader driver",
            "same_sector": (
                "peer_future_return = b_leader * sector_leader_ret_w + b_peer * peer_basket_past_5m "
                "+ b_market * MKT105_EX_LEADERS_past_5m + minute_of_day_FE + error"
            ),
            "global": (
                "nonleader_future_return = b_leader * global_leader_basket_ret_w "
                "+ b_target * nonleader_basket_past_5m + minute_of_day_FE + error"
            ),
            "global_does_not_reuse_leader_feature_as_market_control": True,
            "past_control_min": PAST_CONTROL_MIN,
            "mkt_ex_leaders_min_valid": MKT_EX_LEADERS_MIN_VALID,
            "mkt_ex_leaders_min_frac": MKT_EX_LEADERS_MIN_FRAC,
            "minute_of_day_FE": "within_minute_demeaning",
        },
        "inference": {
            "bootstrap_method": BOOTSTRAP_METHOD,
            "bootstrap_n": BOOTSTRAP_N,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "bootstrap_seed_source": "DEV_FIRST calendar identity frozen at precommit; not outcome-selected",
            "fdr_method": FDR_METHOD,
            "fdr_q": FDR_Q,
            "fdr_family_n": FDR_FAMILY_N,
            "quintiles": "DEV-only driver returns freeze Q20/Q40/Q60/Q80 before C1; diagnostic Q5-Q1",
        },
        "dev_gates": {
            "D1": "DEV_EARLY and DEV_LATE b_leader same sign",
            "D2": "pooled DEV date-block bootstrap 95% CI excludes 0",
            "D3": "BH FDR q <= 0.05 on family n=144",
            "D4": "Q5-Q1 sign agrees with b_leader",
            "D5": "minute mod-5 >= 4/5 subgrids same sign",
            "D6": "LOMO >= 75% same sign",
            "D7": "strict target max-age 60s effect same sign",
            "all_required": True,
            "zero_candidates": "CROSS_SECTIONAL_LEADER_LAGGARD_CAUSAL_LEAD_NOT_FOUND_V1; do not open C1; NEXT SELECT_NEXT_DRIVER_SECTOR_BREADTH_DISPERSION_V1; no leader-set redesign",
        },
        "c1_gates": {
            "opened_only_if_frozen_dev_candidate_n_ge_1": True,
            "C1": "pooled b same sign as DEV",
            "C2": "C1 bootstrap 95% CI excludes 0",
            "C3": ">=2/3 chronological C1 folds same sign",
            "C4": "opposite fold with |b| > 1.5x DEV pooled FAIL",
            "C5": "DEV-frozen Q5-Q1 direction confirmed",
            "C6": "LOMO >= 75% same sign",
            "C7": "strict target freshness 60s remains same sign",
        },
        "placebos": {
            "time_offset": {
                "offsets_min": list(OFFSETS_MIN),
                "require_0": True,
                "require_one_of_m1_m3": True,
                "future_only_shape_absent": True,
                "abs_0_ge_0_50_times_max_future_1_3_5": True,
            },
            "day_shuffle": {
                "n": DAY_SHUFFLE_N,
                "seed": DAY_SHUFFLE_SEED,
                "method": DAY_SHUFFLE_METHOD,
                "primary": "(YYYY-MM, weekday)",
                "fallback": "YYYY-MM",
                "no_self_mapping": True,
                "real_abs_ge_empirical_p95": True,
                "permutation_sha256": shuffle.get("permutation_sha256"),
            },
            "leader_identity": {
                "same_sector": "replace actual sector leader with each other frozen leader; actual |effect| must exceed empirical 95th percentile of cross-leader placebo; if only 7 alternatives, use all plus deterministic date resampling; do not choose another leader after failure",
                "global": "not a same-sector identity placebo; concentration and day-shuffle still required",
            },
        },
        "concentration": {
            "same_sector": "leave-one-peer-out >=80% same sign AND remove largest absolute contributor sign remains",
            "global": "drop top 1 and top 5 contributors; both same sign",
        },
        "common_factor_check": {
            "same_sector_only": "MODEL with vs without market past return control; direction must not exist only because the market control changes its sign; robustness only; cannot rescue primary failure",
        },
        "no_threshold_search": True,
        "no_individual_target_winners": True,
        "no_pb1": True,
        "no_complete_strategy": True,
        "pass_verdict": "CROSS_SECTIONAL_LEADER_LAGGARD_CAUSAL_LEAD_FOUND_V1",
        "pass_next": "PRECOMMIT_CROSS_SECTIONAL_SYMBOL_TRANSMISSION_V1",
        "fail_verdict": "CROSS_SECTIONAL_LEADER_LAGGARD_CAUSAL_LEAD_NOT_FOUND_V1",
        "fail_next": "SELECT_NEXT_DRIVER_SECTOR_BREADTH_DISPERSION_V1",
        "folds": {
            "split_rule": folds.get("split_rule"),
            "development_fold_boundaries": folds.get("development_fold_boundaries"),
            "c1_fold_boundaries": folds.get("c1_fold_boundaries"),
            "fold_boundary_sha256": folds.get("fold_boundary_sha256"),
            "eligible_day_sha256": days.get("eligible_day_sha256"),
        },
        "day_eligibility": {
            "definition": "allowed_period AND TSE_cash_trading_day AND all_8_frozen_leaders_AM_decision_minute_coverage_ge_0.95_under_leader_max_age_60s",
            "usdjpy_fx_exclusions_inherited": False,
            "global_feature_min_valid_leaders": GLOBAL_MIN_VALID_LEADERS,
        },
        "LEADER_LAGGARD_OUTCOMES_OPENED": False,
        "USDJPY_REOPENED": False,
        "candidate_list_sha256": None,
        "C1_outcomes_opened": False,
    }
    contract["precommit_sha256"] = sha256_obj(contract)
    return contract
