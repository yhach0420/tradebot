"""Two-stage sector breadth/dispersion discovery. STAGE_A hard-stops at 20251126."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.causal_driver_pb1 import (
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FV_FIRST,
    PROSPECTIVE_FROM,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, DriverFamily, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.cross_sectional_discovery.confirm import driver_date_map, merge_leader_fx_maps
from research.causal_driver_pb1.cross_sectional_discovery.features import build_am, listed_mask
from research.causal_driver_pb1.cross_sectional_discovery.panels import load_equity_stage
from research.causal_driver_pb1.cross_sectional_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.phase2_discovery.access import StageLedger
from research.causal_driver_pb1.response.cases import run_resolver_tests
from research.causal_driver_pb1.sector_breadth_discovery import (
    CASE_BLOCKED,
    CASE_FOUND,
    CASE_NOT_FOUND,
    EXPECTED_FAMILY_N,
    EXPECTED_PRECOMMIT_SHA256,
    LOOKBACKS,
    METRICS,
    NEXT_REASSESS,
    NEXT_RESOLVE,
    NEXT_TRANSMISSION,
    PRECOMMIT_ID,
    REASON_ZERO_DEV,
    STAGE_A_HARD_STOP,
)
from research.causal_driver_pb1.sector_breadth_discovery.bind import bind_precommit
from research.causal_driver_pb1.sector_breadth_discovery.confirm import confirm_candidates
from research.causal_driver_pb1.sector_breadth_discovery.family import run_dev_family
from research.causal_driver_pb1.sector_breadth_discovery.features import listing_from_px, merge_listing
from research.causal_driver_pb1.sector_breadth_discovery.freeze import freeze_candidates
from research.causal_driver_pb1.sector_breadth_precommit import PHASE0_DRIVER_FAMILY_VALUES

FAIL_RANK = {
    "D1": 1,
    "D2": 2,
    "D3": 3,
    "D4": 4,
    "D5": 5,
    "D6": 6,
    "D7": 7,
    "C1": 8,
    "C2": 9,
    "C3": 10,
    "C4": 11,
    "C5": 12,
    "C6": 13,
    "C7": 14,
    "OFFSET": 15,
    "SHUFFLE": 16,
    "IDENTITY": 17,
    "DRIVER_CONCENTRATION": 18,
    "TARGET_CONCENTRATION": 19,
    "CONCENTRATION": 19,
    "COMMON_FACTOR": 20,
    "PASS": 99,
}


def _firewall(stage: str) -> dict[str, Any]:
    ledger = AccessLedger(run_id=f"SBD_DISC_{stage}", run_mode=RunMode.DRIVER_DISCOVERY)
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id="RESEARCH_OBSERVATION_UNIVERSE_105",
        dataset_role=DatasetRole.DEVELOPMENT,
        requested_fields=("symbol", "tse33_code", "tse33_name"),
        access_kind=AccessKind.METADATA,
    )
    role = DatasetRole.DEVELOPMENT if stage == "STAGE_A" else DatasetRole.ECONOMIC_DEVELOPMENT_EXPOSED
    request_dataset(
        ledger=ledger,
        run_mode=RunMode.DRIVER_DISCOVERY,
        dataset_id=f"EQUITY_MINUTE_{stage}",
        dataset_role=role,
        requested_fields=("date", "time_label", "close"),
        access_kind=AccessKind.PAYLOAD,
    )
    fv = pr = False
    try:
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="EQUITY_MINUTE_FV",
            dataset_role=DatasetRole.FROZEN_VALIDATION,
            requested_fields=("close",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        fv = True
    try:
        request_dataset(
            ledger=ledger,
            run_mode=RunMode.DRIVER_DISCOVERY,
            dataset_id="EQUITY_MINUTE_PROSPECTIVE",
            dataset_role=DatasetRole.PROSPECTIVE,
            requested_fields=("close",),
            access_kind=AccessKind.PAYLOAD,
        )
    except FirewallDenied:
        pr = True
    date_ok = True
    for day in (FV_FIRST, PROSPECTIVE_FROM):
        try:
            assert_ingest_date_allowed(day)
            date_ok = False
        except IngestDateDenied:
            pass
    return {"fv_denied": fv, "prospective_denied": pr, "pass": fv and pr and date_ok, "stage": stage}


def _slim_row(r: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "test_id",
        "metric",
        "scope_id",
        "mechanism",
        "sector_id",
        "lookback",
        "horizon",
        "direction",
        "global",
        "ok",
        "n",
        "b_fx",
        "ci_lo",
        "ci_hi",
        "p_boot",
        "q",
        "q5_minus_q1",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "D6",
        "D7",
        "candidate",
        "failed_at",
        "beta_60",
        "ci_60",
        "early_b_fx",
        "late_b_fx",
        "lomo_same_sign_frac",
        "subgrid_same_sign_n",
        "C1",
        "C2",
        "C3",
        "C4",
        "C5",
        "C6",
        "C7",
        "c1_confirmed",
        "beta_C1_60",
        "offset_pass",
        "shuffle_pass",
        "identity_pass",
        "sector_identity_pass",
        "driver_concentration_pass",
        "target_concentration_pass",
        "concentration_pass",
        "common_factor_pass",
        "final_pass",
        "offset",
        "shuffle",
        "identity",
        "sector_identity",
        "driver_concentration",
        "target_concentration",
        "concentration",
        "common_factor",
        "quintile_boundaries",
        "DEV_beta_primary",
        "DEV_q_value",
        "DEV_p",
        "p_value_method",
        "ci_method",
        "ci_excludes_0",
        "boot_n",
    )
    return {k: r.get(k) for k in keep if k in r}


def _top_failed(rows: list[dict[str, Any]], n: int = 10) -> list[dict[str, Any]]:
    failed = [r for r in rows if r.get("failed_at")]
    failed.sort(key=lambda r: (-FAIL_RANK.get(str(r.get("failed_at")), 0), -abs(float(r.get("b_fx") or r.get("DEV_beta_primary") or 0.0))))
    return [_slim_row(r) for r in failed[:n]]


def _access_audit(stage: StageLedger) -> dict[str, Any]:
    snap = stage.snapshot()
    freeze_e = next((e for e in stage.events if e.get("kind") == "CANDIDATE_FREEZE"), None)
    c1_e = next((e for e in stage.events if e.get("kind") == "C1_STOCK_ROWS"), None)
    return {
        "DEV_STAGE_COMPLETE": bool(snap.get("STAGE_A_COMPLETE")),
        "candidate_list_sha256": snap.get("candidate_list_sha256"),
        "candidate_freeze_timestamp": None if freeze_e is None else freeze_e.get("t"),
        "first_C1_outcome_access_timestamp": None if c1_e is None else c1_e.get("t"),
        "C1_rows_read_before_candidate_freeze": int(snap.get("C1_ROWS_READ_BEFORE_CANDIDATE_FREEZE") or 0),
        "c1_after_freeze": snap.get("c1_after_freeze"),
    }


def _first_fail_full(counts: dict[str, int]) -> dict[str, int]:
    out = {k: int(counts.get(k) or 0) for k in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "PASS")}
    return out


def _blocked(blockers, bound, identity, stage, extra=None) -> dict[str, Any]:
    extra = extra or {}
    return {
        "ok": False,
        "VERDICT": CASE_BLOCKED,
        "NEXT": NEXT_RESOLVE,
        "reason": "DATA_OR_CONTRACT_BLOCKER",
        "blockers": list(dict.fromkeys(blockers)),
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": bound.get("precommit_sha256"),
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "access": _access_audit(stage),
        "C1_opened": False,
        "C1_rows_read_before_candidate_freeze": stage.c1_rows_read_before_candidate_freeze,
        "DEV_all_n": extra.get("DEV_all_n", 0),
        "DEV_candidate_n": extra.get("DEV_candidate_n", 0),
        "discovery_not_complete": True,
        **{k: v for k, v in extra.items() if k not in {"DEV_all_n", "DEV_candidate_n"}},
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "V4_CHANGED": "V4_CHANGED" in blockers,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "LEADER_LAGGARD_REOPENED": False,
        "USDJPY_REOPENED": False,
        "research_only": True,
    }


def evaluate() -> dict[str, Any]:
    blockers: list[str] = []
    resolver_tests = run_resolver_tests()
    if not all(r.get("pass") for r in resolver_tests):
        blockers.append("RESOLVER_UNIT_TEST_FAIL")
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")
    if tuple(m.value for m in DriverFamily) != PHASE0_DRIVER_FAMILY_VALUES:
        blockers.append("PHASE0_DRIVERFAMILY_ENUM_MUTATED")
    bound = bind_precommit()
    blockers.extend(bound.get("blockers") or [])
    if bound.get("precommit_sha256") != EXPECTED_PRECOMMIT_SHA256:
        blockers.append("PRECOMMIT_SHA_MISMATCH")
    fw_a = _firewall("STAGE_A")
    if not fw_a.get("pass"):
        blockers.append("FIREWALL")
    stage = StageLedger()
    stage.record("STAGE_A_START", precommit_sha256=EXPECTED_PRECOMMIT_SHA256, hard_stop=STAGE_A_HARD_STOP)
    if blockers:
        return _blocked(blockers, bound, identity, stage, extra={"resolver_tests": resolver_tests, "firewall": fw_a})

    symbols = tuple(r["symbol"] for r in (bound["sectors"].get("rows") or []))
    sector_of = {r["symbol"]: str(r["sector_id"]) for r in (bound["sectors"].get("rows") or [])}
    scopes = list(bound.get("scopes") or [])
    tests = list(bound.get("tests") or [])
    if len(tests) != EXPECTED_FAMILY_N:
        blockers.append("FAMILY_N_MISMATCH")
        return _blocked(blockers, bound, identity, stage, extra={"resolver_tests": resolver_tests, "firewall": fw_a})
    print("STAGE_A STOCK", flush=True)
    stock = load_equity_stage(symbols=symbols, dates=list(bound["development_dates"]), ledger=stage, stage="STAGE_A")
    if stock.get("max_date") and stock["max_date"] > STAGE_A_HARD_STOP:
        blockers.append("STAGE_A_HARD_STOP_BREACH")
    am = build_am(close=stock["close"])
    listing = listing_from_px(px=am["px"], symbols=list(symbols), dates=list(bound["development_dates"]))
    listed = listed_mask(symbols=list(symbols), dates=list(bound["development_dates"]), listing_start=listing)
    print("STAGE_A FAMILY", flush=True)
    fam = run_dev_family(
        px=am["px"],
        age=am["age"],
        symbols=list(symbols),
        dates=list(bound["development_dates"]),
        listed=listed,
        scopes=scopes,
        tests=tests,
        sector_of=sector_of,
        folds=bound["folds"],
        boot_index=bound["dev_boot_index"],
    )
    family = fam["family"]
    cands_raw = [r for r in family if r.get("candidate")]
    frozen_list, cand_sha = freeze_candidates(cands_raw)
    stage.freeze_candidates(cand_sha, len(frozen_list))
    access = _access_audit(stage)
    if int(access["C1_rows_read_before_candidate_freeze"] or 0) > 0:
        blockers.append("C1_ROWS_BEFORE_FREEZE")
    post_hashes = frozen_source_hashes()
    if pre_hashes != post_hashes:
        blockers.append("RUNTIME_NONIMPACT")
    fail_counts = _first_fail_full(fam.get("first_fail_counts") or {})
    if blockers:
        return _blocked(
            blockers,
            bound,
            identity,
            stage,
            extra={
                "resolver_tests": resolver_tests,
                "firewall": fw_a,
                "DEV_all_n": EXPECTED_FAMILY_N,
                "DEV_candidate_n": len(frozen_list),
                "family": [_slim_row(r) for r in family],
                "DEV_first_fail_counts": fail_counts,
            },
        )

    bound_pub = {
        k: v
        for k, v in bound.items()
        if k not in {"sectors", "shuffle", "dev_boot_index", "c1_boot_index", "tests", "scopes"}
    }
    base = {
        "ok": True,
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": EXPECTED_PRECOMMIT_SHA256,
        "precommit_sha_verified": True,
        "family_384_sha256": bound.get("family_384_sha256"),
        "eligible_day_sha256": bound["eligible_day_sha256"],
        "fold_boundary_sha256": bound["fold_boundary_sha256"],
        "permutation_sha256": bound["permutation_sha256"],
        "bootstrap_index_sha256": bound["bootstrap_index_sha256"],
        "universe105_sha256": bound["universe105_sha256"],
        "sector_mapping_sha256": bound["sector_mapping_sha256"],
        "eligible_dev_n": len(bound["development_dates"]),
        "eligible_c1_n": len(bound["c1_dates"]),
        "DEV_all_n": EXPECTED_FAMILY_N,
        "DEV_candidate_n": len(frozen_list),
        "DEV_first_fail_counts": fail_counts,
        "candidate_list_sha256": cand_sha,
        "frozen_candidates": frozen_list,
        "family": [_slim_row(r) for r in family],
        "p_boot": fam.get("p_boot"),
        "q_boot": fam.get("q_boot"),
        "access": access,
        "C1_rows_read_before_candidate_freeze": 0,
        "C1_opened": False,
        "C1_confirmed_n": 0,
        "offset_pass_n": 0,
        "shuffle_pass_n": 0,
        "identity_pass_n": 0,
        "sector_identity_pass_n": 0,
        "driver_concentration_pass_n": 0,
        "target_concentration_pass_n": 0,
        "concentration_pass_n": 0,
        "common_factor_pass_n": 0,
        "final_pass_n": 0,
        "final_candidates": [],
        "top_failed_candidates": _top_failed(family),
        "failure_stage_counts": fail_counts,
        "resolver_tests": resolver_tests,
        "firewall": fw_a,
        "identity": {k: v for k, v in identity.items() if k != "source_inventory"},
        "contamination": {
            **contamination_ledger(),
            "leader_laggard_near_misses_used": False,
            "leader_laggard_reopened": False,
            "usdjpy_reopened": False,
            "old_peer_winner_names_used": False,
            "pb1_economic_performance_used": False,
            "recorded_after_verdict_only": True,
        },
        "bound": bound_pub,
        "scopes": scopes,
        "ALPHA_CREATED": False,
        "MECHANISM_FROZEN": False,
        "PB1_BOUND": False,
        "COMPLETE_STRATEGY_RUN": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "FV_opened": False,
        "prospective_opened": False,
        "V4_CHANGED": False,
        "V5_CREATED": False,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "USDJPY_REOPENED": False,
        "LEADER_LAGGARD_REOPENED": False,
        "research_only": True,
    }

    if not frozen_list:
        return {
            **base,
            "VERDICT": CASE_NOT_FOUND,
            "NEXT": NEXT_REASSESS,
            "reason": REASON_ZERO_DEV,
            "C1_not_run": True,
        }

    print("STAGE_B C1", flush=True)
    fw_b = _firewall("STAGE_B")
    if not fw_b.get("pass"):
        blockers.append("FIREWALL_B")
        return _blocked(blockers, bound, identity, stage, extra=base)
    stage.enter_stage_b()
    stock_c1 = load_equity_stage(symbols=symbols, dates=list(bound["c1_dates"]), ledger=stage, stage="STAGE_B")
    access = _access_audit(stage)
    if int(access["C1_rows_read_before_candidate_freeze"] or 0) > 0:
        blockers.append("C1_ROWS_BEFORE_FREEZE")
        return _blocked(blockers, bound, identity, stage, extra=base)
    am_c1 = build_am(close=stock_c1["close"])
    listing_c1 = listing_from_px(px=am_c1["px"], symbols=list(symbols), dates=list(bound["c1_dates"]))
    listing_all = merge_listing(listing, listing_c1)
    from research.causal_driver_pb1.sector_breadth_discovery.features import precompute_drivers

    listed_c1 = listed_mask(symbols=list(symbols), dates=list(bound["c1_dates"]), listing_start=listing_all)
    drivers_dev = fam["drivers"]
    drivers_c1 = precompute_drivers(
        px=am_c1["px"],
        age=am_c1["age"],
        listed=listed_c1,
        scopes=scopes,
        symbols=list(symbols),
        sector_of=sector_of,
    )
    driver_maps: dict[tuple[str, str, int], dict[str, Any]] = {}
    for sc in scopes:
        sid = str(sc["scope_id"])
        for metric in METRICS:
            for w in LOOKBACKS:
                driver_maps[(sid, metric, int(w))] = merge_leader_fx_maps(
                    driver_date_map(drivers_dev["by_scope"][sid][metric][int(w)], list(bound["development_dates"])),
                    driver_date_map(drivers_c1["by_scope"][sid][metric][int(w)], list(bound["c1_dates"])),
                )

    c1_rows = confirm_candidates(
        px=am_c1["px"],
        age=am_c1["age"],
        symbols=list(symbols),
        dates=list(bound["c1_dates"]),
        listing_start=listing_all,
        scopes=scopes,
        sector_of=sector_of,
        folds=bound["folds"],
        candidates=frozen_list,
        driver_fx_by_date=driver_maps,
        eligible_dates=list(bound["eligible_dates"]),
        boot_index=bound["c1_boot_index"],
    )
    slim_c1 = [_slim_row(r) for r in c1_rows]
    confirmed = [r for r in c1_rows if r.get("c1_confirmed")]
    final = [r for r in c1_rows if r.get("final_pass")]
    merged_fail = Counter(fail_counts)
    for r in c1_rows:
        k = r.get("failed_at") or ("PASS" if r.get("final_pass") else None)
        if k and not str(k).startswith("D"):
            merged_fail[k] += 1
    base.update(
        {
            "C1_opened": True,
            "C1_confirmed_n": len(confirmed),
            "c1_rows": slim_c1,
            "access": access,
            "firewall_b": fw_b,
            "offset_pass_n": int(sum(1 for r in c1_rows if r.get("offset_pass"))),
            "shuffle_pass_n": int(sum(1 for r in c1_rows if r.get("shuffle_pass"))),
            "identity_pass_n": int(sum(1 for r in c1_rows if r.get("identity_pass"))),
            "sector_identity_pass_n": int(sum(1 for r in c1_rows if r.get("sector_identity_pass") or r.get("identity_pass"))),
            "driver_concentration_pass_n": int(sum(1 for r in c1_rows if r.get("driver_concentration_pass"))),
            "target_concentration_pass_n": int(sum(1 for r in c1_rows if r.get("target_concentration_pass"))),
            "concentration_pass_n": int(sum(1 for r in c1_rows if r.get("concentration_pass"))),
            "common_factor_pass_n": int(sum(1 for r in c1_rows if r.get("common_factor_pass"))),
            "final_pass_n": len(final),
            "final_candidates": [_slim_row(r) for r in final],
            "top_failed_candidates": _top_failed(list(family) + list(c1_rows)),
            "failure_stage_counts": dict(merged_fail),
            "C1_not_run": False,
        }
    )
    if final:
        base.update({"VERDICT": CASE_FOUND, "NEXT": NEXT_TRANSMISSION, "reason": None, "ok": True})
    else:
        base.update({"VERDICT": CASE_NOT_FOUND, "NEXT": NEXT_REASSESS, "reason": "NO_C1_CONFIRMED_ROBUST_CANDIDATE", "ok": True})
    return base
