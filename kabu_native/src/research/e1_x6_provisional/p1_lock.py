"""P1 revision lock — MUST be saved before any candidate economics."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from research.e1_x6_provisional.constants import (
    ACCEPTANCE_GATES_11_1,
    CANDIDATE_CAP,
    CANDIDATE_REGISTRY_SOT_NAMESPACE,
    COST_BPS_ROUNDTRIP,
    COST_RATE,
    DEDUP_KEY_RULE,
    FOLD_DEFS,
    INTERACTION_MAX,
    LOT,
    OPTIONAL_THREE_PLUS,
    PLAN_REL,
    PREDICTOR_FEATURES,
    PRIMARY_HORIZON_SEC,
    PROVISIONAL_BANNER,
    QUANTILE_GRID,
    SELECTED_SPEC_NAMESPACE,
    STOP_BPS,
    TARGET_BPS,
    THRESHOLD,
    WHITELIST_FIELDS,
)
from research.e1_x6_provisional.cost_contract import ROUNDTRIP_COST_BPS, verify_frozen_e1_x5_cost_contract
from research.e1_x6_provisional.portfolio_replay import CAP as PORTFOLIO_CAP
from research.e1_x6_provisional.replay_lifecycle_contract import (
    EVALUATION_MODE_REQUIRED,
    REPLAY_LIFECYCLE_CONTRACT_TEXT,
)
from research.e1_x6_provisional.util import JST, progress, repo_root, sha256_file, sha256_obj


KEY_MODULE_RELS = (
    "kabu_native/src/research/e1_x6_provisional/canonical_partition_replay.py",
    "kabu_native/src/research/e1_x6_provisional/entry_robustness.py",
    "kabu_native/src/research/e1_x6_provisional/p2_execute.py",
    "kabu_native/src/research/e1_x6_provisional/pipeline.py",
    "kabu_native/src/research/e1_x6_provisional/analysis_mask.py",
    "kabu_native/src/research/e1_x6_provisional/cost_contract.py",
    "kabu_native/src/research/e1_x6_provisional/p1_lock.py",
    "kabu_native/src/research/e1_x6_provisional/replay_lifecycle_contract.py",
    "kabu_native/src/research/e1_x6_provisional/fixture_suite.py",
    "kabu_native/src/research/e1_x6_provisional/publish.py",
    "kabu_native/tests/test_e1_x6_research_builder_contracts.py",
)

DEPENDENCY_PACKAGES = (
    "numpy",
    "pandas",
    "openpyxl",
    "pytest",
)


def _code_file_shas() -> dict[str, Optional[str]]:
    root = repo_root()
    out: dict[str, Optional[str]] = {}
    for rel in KEY_MODULE_RELS:
        p = root / rel
        out[rel] = sha256_file(p) if p.is_file() else None
    return out


def _dependency_versions() -> dict[str, Any]:
    pkgs: dict[str, Optional[str]] = {}
    for name in DEPENDENCY_PACKAGES:
        try:
            pkgs[name] = importlib.metadata.version(name)
        except Exception:
            pkgs[name] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": pkgs,
    }


def _schema_payloads() -> dict[str, Any]:
    """Canonical schema definitions — SHA covers field sets (not runtime values)."""
    feature_schema = {
        "id": "e1_x6_feature_schema_v1",
        "fields": list(WHITELIST_FIELDS),
        "predictors": list(PREDICTOR_FEATURES),
        "types": {
            "score": "float64",
            "spread_bps": "float64",
            "bid": "float64",
            "ask": "float64",
            "mid": "float64",
            "sample_reason": "str",
            "symbol_norm": "str",
            "score_vs_threshold_gap": "float64",
        },
    }
    label_schema = {
        "id": "e1_x6_label_schema_v1",
        "primary": "post_5bps_expectancy_h300",
        "horizon_sec": PRIMARY_HORIZON_SEC,
        "censored_token": "CENSORED",
        "never_impute_zero": True,
    }
    signal_schema = {
        "id": "e1_x6_signal_ledger_v1",
        "required": [
            "day",
            "am_pm",
            "session_id",
            "window_id",
            "analysis_mask_id",
            "quality_class",
            "valid_window_start",
            "valid_window_end",
            "event_scope",
            "in_analysis_mask_signal",
            "ts",
            "symbol",
            "signal",
            "event_id",
        ],
    }
    decision_schema = {
        "id": "e1_x6_decision_ledger_v1",
        "required": [
            "day",
            "am_pm",
            "session_id",
            "window_id",
            "analysis_mask_id",
            "quality_class",
            "valid_window_start",
            "valid_window_end",
            "event_scope",
            "in_analysis_mask_decision",
            "ts",
            "symbol",
            "decision",
            "reason",
        ],
    }
    trade_schema = {
        "id": "e1_x6_completed_trade_ledger_v1",
        "required": [
            "day",
            "am_pm",
            "session_id",
            "window_id",
            "analysis_mask_id",
            "quality_class",
            "valid_window_start",
            "valid_window_end",
            "entry_lineage",
            "exit_lineage",
            "in_analysis_mask_entry",
            "in_analysis_mask_exit",
            "entry_ask",
            "exit_bid",
            "gross_pnl_yen_100",
            "cost_yen_100",
            "net_pnl_yen_100",
            "net_bps",
        ],
    }
    return {
        "feature": feature_schema,
        "label": label_schema,
        "signal": signal_schema,
        "decision": decision_schema,
        "trade": trade_schema,
    }


def _schema_shas(payloads: dict[str, Any]) -> dict[str, str]:
    return {k: sha256_obj(v) for k, v in payloads.items()}


def _canonical_event_sort() -> dict[str, Any]:
    return {
        "dedup_key_rule": DEDUP_KEY_RULE,
        "sort_keys": ["event_time_jst", "session_id", "sequence", "symbol_norm"],
        "tie_break": "session_id|sequence|symbol_norm ascending lex",
        "timezone": "Asia/Tokyo",
        "note": "Identical to Source Manifest / normalize_day canonical order; never re-sort by PnL",
    }


def _numeric_precision() -> dict[str, Any]:
    return {
        "price": "float64; board bid/ask as provided; no float32 demotion",
        "pnl_yen": "float64; Decimal-normalize for FixedSpec additivity atol=0.001 yen",
        "bps": "float64; cost = entry_ask * LOT * COST_RATE once (5bps round-trip)",
        "rounding": "no intermediate yen rounding before ledger write; summarize uses float sum",
        "pf": "sum(win)/abs(sum(loss)); null+NO_LOSS if no losses",
        "sha_encoding": "UTF-8 JSON canonical via sha256_obj (sorted keys, default=str)",
    }


def compute_config_fingerprint(
    *,
    plan_sha256: Optional[str],
    source_manifest_sha256: str,
    analysis_mask_sha256: Optional[str],
    code_file_shas: dict[str, Optional[str]],
    economics_constants: dict[str, Any],
    schema_shas: dict[str, str],
    dependency_versions: dict[str, Any],
) -> str:
    payload = {
        "plan_sha256": plan_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "analysis_mask_sha256": analysis_mask_sha256,
        "code_file_shas": code_file_shas,
        "economics_constants": {
            k: economics_constants.get(k)
            for k in (
                "TARGET_BPS",
                "STOP_BPS",
                "THRESHOLD",
                "COST_RATE",
                "LOT",
                "ROUNDTRIP_COST_BPS",
            )
        },
        "schema_shas": schema_shas,
        "dependency_versions": dependency_versions,
        "candidate_cap": CANDIDATE_CAP,
        "portfolio_cap": PORTFOLIO_CAP,
        "evaluation_mode": EVALUATION_MODE_REQUIRED,
    }
    return sha256_obj(payload)


def build_p1_lock(
    *,
    run_id: str,
    source_manifest_sha256: str,
    analysis_mask_sha256: Optional[str] = None,
    plan_version: Optional[str] = None,
    plan_sha256: Optional[str] = None,
    config_fingerprint: Optional[str] = None,
) -> dict[str, Any]:
    progress("P1: locking study revision BEFORE any candidate economics")
    cost_check = verify_frozen_e1_x5_cost_contract()
    root = repo_root()
    plan_path = root / PLAN_REL
    if plan_sha256 is None and plan_path.is_file():
        plan_sha256 = sha256_file(plan_path)
    if plan_version is None and plan_path.is_file():
        import re

        text = plan_path.read_text(encoding="utf-8")
        m = re.search(r"\|\s*Version\s*\|\s*`?([^`|]+)`?\s*\|", text)
        plan_version = m.group(1).strip() if m else None

    feature_defs = {
        "score": {
            "source": "e1_x5_dmid_score_provider.DMidD4H6ScoreProvider",
            "type": "float64",
            "asof": "decision_time / packet.event_time",
            "missing_policy": "NO_SAMPLE / MISSING — never impute 0 for entry truth",
            "allowed_direction": ["higher_better"],
            "predictor": True,
        },
        "spread_bps": {
            "source": "canonical best_bid_ask spread at decision",
            "type": "float64",
            "asof": "decision_time",
            "missing_policy": "reject INVALID_QUOTE; never treat missing as pass",
            "allowed_direction": ["lower_better"],
            "predictor": True,
        },
        "bid": {
            "source": "canonical board best bid",
            "type": "float64",
            "asof": "decision_time",
            "missing_policy": "FIELD required for entry; missing => no entry",
            "allowed_direction": [],
            "predictor": False,
        },
        "ask": {
            "source": "canonical board best ask",
            "type": "float64",
            "asof": "decision_time",
            "missing_policy": "FIELD required for entry; missing => no entry",
            "allowed_direction": [],
            "predictor": False,
        },
        "mid": {
            "source": "(bid+ask)/2 when both valid else packet.mid",
            "type": "float64",
            "asof": "decision_time",
            "missing_policy": "CENSORED labels if forward mid absent — never 0",
            "allowed_direction": [],
            "predictor": False,
        },
        "sample_reason": {
            "source": "REGULAR_5S | STATE_CHANGE from should_evaluate gate",
            "type": "str",
            "asof": "decision_time",
            "missing_policy": "NO_EVALUATION rows excluded from SCORE population",
            "allowed_direction": [],
            "predictor": False,
        },
        "symbol_norm": {
            "source": "normalized symbol with .T suffix",
            "type": "str",
            "asof": "decision_time",
            "missing_policy": "identity/join only",
            "allowed_direction": [],
            "predictor": False,
            "note": "NOT for prediction — excluded from predictors",
        },
        "score_vs_threshold_gap": {
            "source": "score - THRESHOLD",
            "type": "float64",
            "asof": "decision_time",
            "missing_policy": "only defined on SCORE rows",
            "allowed_direction": ["higher_better"],
            "predictor": True,
        },
    }
    code_shas = _code_file_shas()
    dep_versions = _dependency_versions()
    schema_payloads = _schema_payloads()
    schema_shas = _schema_shas(schema_payloads)
    event_sort = _canonical_event_sort()
    numeric_precision = _numeric_precision()
    test_rel = "kabu_native/tests/test_e1_x6_research_builder_contracts.py"
    test_code_sha = code_shas.get(test_rel)
    fixture_rel = "kabu_native/src/research/e1_x6_provisional/fixture_suite.py"
    fixture_code_sha = code_shas.get(fixture_rel)

    economics_constants = {
        "TARGET_BPS": TARGET_BPS,
        "STOP_BPS": STOP_BPS,
        "THRESHOLD": THRESHOLD,
        "COST_RATE": COST_RATE,
        "LOT": LOT,
        "ROUNDTRIP_COST_BPS": ROUNDTRIP_COST_BPS,
        "COST_BPS_ROUNDTRIP": COST_BPS_ROUNDTRIP,
        "source": "research.e1_x6_provisional.cost_contract + small_paper.e1_x5_forward_shadow.econ",
        "yen_roundtrip_cost_formula": "price * LOT * COST_RATE  (== price*100*0.0005; 1000yen→50yen)",
        "return_scale_cost_bps_formula": "COST_RATE * 10000  (== 5.0 bps once; FORBIDDEN: *2 → 10bps)",
        "dual_10bps_definition": "FORBIDDEN",
        "cost_contract_verify": cost_check,
        "shared_function": "net_pnl_yen / x5_econ used once for BASE, labels, candidate portfolio",
    }

    cfg_fp = config_fingerprint or compute_config_fingerprint(
        plan_sha256=plan_sha256,
        source_manifest_sha256=source_manifest_sha256,
        analysis_mask_sha256=analysis_mask_sha256 or source_manifest_sha256,
        code_file_shas=code_shas,
        economics_constants=economics_constants,
        schema_shas=schema_shas,
        dependency_versions=dep_versions,
    )

    lock: dict[str, Any] = {
        "banner": PROVISIONAL_BANNER,
        "status": "P1_REVISED_LOCKED_PRE_ECONOMICS",
        "prior_p1_status": "SUPERSEDED_PRE_ECONOMICS",
        "provisional_run_id": run_id,
        "fixed_at_jst": datetime.now(JST).isoformat(),
        "plan_version": plan_version,
        "plan_sha256": plan_sha256,
        "source_manifest_sha256": source_manifest_sha256,
        "analysis_mask_sha256": analysis_mask_sha256 or source_manifest_sha256,
        "code_file_shas": code_shas,
        "config_fingerprint": cfg_fp,
        "config_fingerprint_sha256": cfg_fp,
        "dependency_versions": dep_versions,
        "numeric_precision": numeric_precision,
        "schema_ids": {
            "trade_ledger": "e1_x6_completed_trade_ledger_v1",
            "score_row": "e1_x6_score_row_v1",
            "signal_ledger": "e1_x6_signal_ledger_v1",
            "decision_ledger": "e1_x6_decision_ledger_v1",
            "partition_replay": "e1_x6_canonical_partition_replay_v1",
            "evaluation_mode": EVALUATION_MODE_REQUIRED,
        },
        "schema_definitions": schema_payloads,
        "schema_shas": schema_shas,
        "canonical_event_sort": event_sort,
        "canonical_event_sort_sha256": sha256_obj(event_sort),
        "test_code_sha": test_code_sha,
        "fixture_suite_code_sha": fixture_code_sha,
        "seed": "deterministic_no_rng",
        "rng_policy": "none",
        "timezone": "Asia/Tokyo",
        "build_only_rank_formula": (
            "sort by (-(build_support>0), -build_expectancy_proxy, -build_support, candidate_id); "
            "build_expectancy_proxy = mean(post_5bps_expectancy_h300) over matched non-CENSORED build SCORE rows"
        ),
        "has_support_definition": "build_support>0 (matched mask-in SCORE rows under candidate predicate)",
        "confirm_lock_procedure": (
            "Select ONE candidate on build-only ranked registry; lock candidate_id; "
            "confirm = FULL_CANONICAL_EVENT_REPLAY of confirm-day AM+PM partitions with fresh session each; "
            "NO confirm reselection; selected must exist in that fold's CandidateRegistry(200)"
        ),
        "replay_lifecycle_contract": REPLAY_LIFECYCLE_CONTRACT_TEXT.strip(),
        "evaluation_mode_required": EVALUATION_MODE_REQUIRED,
        "field_whitelist": list(WHITELIST_FIELDS),
        "predictor_features": list(PREDICTOR_FEATURES),
        "feature_defs": feature_defs,
        "economics_constants": economics_constants,
        "labels": {
            "primary_label_id": "post_5bps_expectancy_h300",
            "primary_horizon_sec": PRIMARY_HORIZON_SEC,
            "primary_formula": "(mid_{t+300}/mid_t - 1)*10000 - ROUNDTRIP_COST_BPS(5.0); CENSORED if no mid within horizon (NOT 0)",
            "build_only_usage": "row-level 5m label ranks candidates in BUILD only",
            "confirm_usage": "FORBIDDEN as confirm PnL; confirm uses FULL_CANONICAL_EVENT_REPLAY",
            "MISSED_WINNER": {
                "definition": "non-entry SCORE row where post_5bps_expectancy_h300 > +TARGET_BPS",
                "formula": "not entered AND label != CENSORED AND post_5bps_expectancy_h300 > TARGET_BPS",
            },
            "UNNECESSARY_ENTRY": {
                "definition": "entry that hits STOP or post_5bps_expectancy_h300 < 0",
                "formula": "entered AND (exit_reason==STOP OR (label not CENSORED AND post_5bps_expectancy_h300 < 0))",
            },
        },
        "confirm_portfolio_contract": {
            "independent_cap": PORTFOLIO_CAP,
            "no_same_symbol_duplicate_open": True,
            "holding_continuous_signal_not_new_trade": True,
            "entry": "fixed candidate spec from build-only selection via candidate gate (not X5 threshold alone)",
            "exit": "frozen E1_X5 STOP/TARGET/TRAILING/MAX_HOLD/NO_PROGRESS within partition only",
            "cost": "roundtrip 5bps once via cost_contract.net_pnl_yen",
            "no_confirm_reselection": True,
            "evaluation_mode": EVALUATION_MODE_REQUIRED,
            "am_pm_carry": False,
            "window_end_open": "WINDOW_CENSORED / WINDOW_END_OPEN_EXCLUDED",
            "signal_ledger_required": True,
            "vacuous_empty_signal_ledger_pass_forbidden": True,
        },
        "candidate_registry_sot": {
            "namespace": CANDIDATE_REGISTRY_SOT_NAMESPACE,
            "cap": CANDIDATE_CAP,
            "note": "Single SHA for A/B, report.candidates, Excel Index CandidateRegistry, final candidate registry_sha256",
        },
        "selected_spec_namespace": {
            "namespace": SELECTED_SPEC_NAMESPACE,
            "reference": "candidate_id",
            "note": "selected_spec_sha256 is sha256 of selected candidate row only — distinct from registry SoT SHA",
        },
        "candidate_id_formula": 'f"C|{family}|{features_sorted}|{direction}|{threshold_code}"',
        "enumerate_order": [
            "family",
            "feature_tuple_lex",
            "direction",
            "threshold_ascending",
        ],
        "tie_break": "candidate_id lex",
        "candidate_cap": CANDIDATE_CAP,
        "interaction_max": INTERACTION_MAX,
        "OPTIONAL_THREE_PLUS": OPTIONAL_THREE_PLUS,
        "threshold_generation": {
            "method": "build_window_quantiles_only",
            "grid": list(QUANTILE_GRID),
            "no_confirm_day_stats": True,
        },
        "families": ["SINGLE_FEATURE", "TWO_FEATURE_AND"],
        "fold_definition": FOLD_DEFS,
        "acceptance_gates_11_1": ACCEPTANCE_GATES_11_1,
        "metric_contract": {
            "PF": "sum(win_pnl) / abs(sum(loss_pnl)); null+NO_LOSS if no losses",
            "WLD": "sign of post-5bps trade PnL",
            "realized_trade_sequence_max_dd": "cumulative 100-share PnL in JST completed EXIT order",
            "top1_trade_excluded_pnl": "total_pnl - max(trade_pnl)",
            "top1_symbol_excluded_pnl": "total_pnl - max(symbol_pnl)",
            "BASE_compare": "same analysis_mask_id / cost / independent CAP5",
            "economic_rows": "completed trades only; open/orphan/cap-blocked/WINDOW_CENSORED excluded from PnL",
        },
        "noise_audit_classes": ["X5_KEEP", "X5_REMOVED", "X6_ADDED", "BOTH_REJECT"],
        "immutable_after_lock": True,
        "forbidden_after_lock": [
            "open_candidate_economics_before_this_lock",
            "change_search_space_from_results",
            "promote_provisional_to_selection",
            "PORTFOLIO_REPLAY_ON_LABELED_SCORE_ROWS_as_confirm_economics",
        ],
    }

    # Completeness gate — Stage-1 audit fields must be non-null
    critical = {
        "plan_version": lock.get("plan_version"),
        "plan_sha256": lock.get("plan_sha256"),
        "source_manifest_sha256": lock.get("source_manifest_sha256"),
        "analysis_mask_sha256": lock.get("analysis_mask_sha256"),
        "replay_lifecycle_contract": lock.get("replay_lifecycle_contract"),
        "evaluation_mode_required": lock.get("evaluation_mode_required"),
        "seed": lock.get("seed"),
        "timezone": lock.get("timezone"),
        "has_support_definition": lock.get("has_support_definition"),
        "confirm_lock_procedure": lock.get("confirm_lock_procedure"),
        "build_only_rank_formula": lock.get("build_only_rank_formula"),
        "config_fingerprint": lock.get("config_fingerprint"),
        "dependency_versions": lock.get("dependency_versions"),
        "numeric_precision": lock.get("numeric_precision"),
        "schema_shas": lock.get("schema_shas"),
        "canonical_event_sort": lock.get("canonical_event_sort"),
        "test_code_sha": lock.get("test_code_sha"),
    }
    missing = [k for k, v in critical.items() if not v]
    code_missing = [k for k, v in code_shas.items() if not v]
    schema_missing = [k for k, v in schema_shas.items() if not v]
    if missing or code_missing or schema_missing:
        lock["p1_precommit_status"] = "P1_PRECOMMIT_INCOMPLETE"
        lock["p1_precommit_missing"] = {
            "critical_fields": missing,
            "code_files": code_missing,
            "schema_shas": schema_missing,
        }
        progress(f"P1: P1_PRECOMMIT_INCOMPLETE missing={missing} code={code_missing}")
    else:
        lock["p1_precommit_status"] = "P1_PRECOMMIT_COMPLETE"

    lock["p1_lock_sha256"] = sha256_obj({k: v for k, v in lock.items() if k != "p1_lock_sha256"})
    progress(f"P1: locked sha={lock['p1_lock_sha256']} precommit={lock['p1_precommit_status']}")
    return lock
