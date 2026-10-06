"""Run discovery, freeze candidates, then one-shot FV. No alpha."""
from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

from research.causal_driver_pb1 import (
    EXPECTED_COMPLETE_STRATEGY_SHA256,
    EXPECTED_V4_MACHINE_SHA256,
    FV_FIRST,
    PROSPECTIVE_FROM,
)
from research.causal_driver_pb1.contracts.enums import AccessKind, DatasetRole, RunMode
from research.causal_driver_pb1.contracts.errors import FirewallDenied
from research.causal_driver_pb1.contracts.time import JST
from research.causal_driver_pb1.cross_sectional_precommit.contamination import contamination_ledger
from research.causal_driver_pb1.datasets.firewall import AccessLedger, request_dataset
from research.causal_driver_pb1.identity.ids import sha256_bytes, sha256_obj
from research.causal_driver_pb1.identity.pin import bind_identities, frozen_source_hashes
from research.causal_driver_pb1.phase1.dates import assert_ingest_date_allowed
from research.causal_driver_pb1.phase1.errors import IngestDateDenied
from research.causal_driver_pb1.sector_breadth_precommit_v1_1.inference import draw_date_block_indices
from research.causal_driver_pb1.sector_state_transmission import (
    ANALYSIS_ID,
    CASE_BLOCKED,
    CASE_FOUND,
    CASE_NOT_FOUND,
    NEXT_ALPHA,
    NEXT_REASSESS,
    PRECOMMIT_ID,
    PRECOMMIT_SHA256,
)
from research.causal_driver_pb1.sector_state_transmission.features import prepare
from research.causal_driver_pb1.sector_state_transmission.identity import verify_identity
from research.causal_driver_pb1.sector_state_transmission.infer import bh_monotone, fit_hypothesis, gate_discovery, gate_fv
from research.causal_driver_pb1.sector_state_transmission.panel import load_prices
from research.causal_driver_pb1.sector_state_transmission_precommit import BOOTSTRAP_N, FV_BOOTSTRAP_SEED
from research.causal_driver_pb1.sector_state_transmission_precommit.bootstrap import _freeze
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1 import FV_BOOTSTRAP_SHA256
from research.causal_driver_pb1.sector_state_transmission_precommit_v1_1.regimes import REGIME_MULTI, REGIME_NONE, REGIME_SINGLE


def _now() -> str:
    return datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S.%f+0900")


def _firewall() -> dict[str, Any]:
    ledger = AccessLedger(run_id="SYMBOL_TRANSMISSION_V1", run_mode=RunMode.DRIVER_DISCOVERY)
    fv = pr = False
    try:
        request_dataset(ledger=ledger, run_mode=RunMode.DRIVER_DISCOVERY, dataset_id="EQUITY_MINUTE_FV", dataset_role=DatasetRole.FROZEN_VALIDATION, requested_fields=("close",), access_kind=AccessKind.PAYLOAD)
    except FirewallDenied:
        fv = True
    try:
        request_dataset(ledger=ledger, run_mode=RunMode.DRIVER_DISCOVERY, dataset_id="EQUITY_MINUTE_PROSPECTIVE", dataset_role=DatasetRole.PROSPECTIVE, requested_fields=("close",), access_kind=AccessKind.PAYLOAD)
    except FirewallDenied:
        pr = True
    date_ok = True
    for day in (FV_FIRST, PROSPECTIVE_FROM):
        try:
            assert_ingest_date_allowed(day)
            date_ok = False
        except IngestDateDenied:
            pass
    return {"fv_economic_payload_denied": fv, "prospective_denied": pr, "pass": fv and pr and date_ok}


def _cols(prep: dict[str, Any], si: int, global_mech: bool, w: int) -> list[np.ndarray]:
    drv = prep["breadth"][(int(w), bool(global_mech))][si]
    self_r = prep["past"][si]
    mkt = prep["market"][si]
    if bool(prep["omit_sector"][si]):
        return [drv, self_r, mkt]
    return [drv, self_r, prep["sector_x"][si], mkt]


def _months(dates: list[str]) -> np.ndarray:
    return np.array([int(d[:6]) for d in dates], dtype=np.int32)


def _mask(dates: list[str], keep: set[str]) -> np.ndarray:
    return np.array([d in keep for d in dates], dtype=np.bool_)


def _canonical(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "parent_mechanism_id": row["parent_mechanism_id"],
        "target_symbol": row["target_symbol"],
        "target_sector": row["target_sector"],
        "direction": "UP",
        "lookback": int(row["lookback"]),
        "horizon": int(row["horizon"]),
        "control_regime": row["control_regime"],
        "beta_DEV": row["beta_dev"],
        "beta_C1": row["beta_c1"],
        "beta_pooled": row["beta_pooled"],
        "CI_low": row["ci_lo"],
        "CI_high": row["ci_hi"],
        "p": row["p"],
        "q": row["q"],
        "beta_60": row["beta_60"],
        "CI60_low": row["ci60_lo"],
        "CI60_high": row["ci60_hi"],
        "T1": row["T1"],
        "T2": row["T2"],
        "T3": row["T3"],
        "T4": row["T4"],
        "T5": row["T5"],
        "T6": row["T6"],
    }


def _fit_rows(*, family_rows: list[dict[str, Any]], prep: dict[str, Any], symbols: list[str], dates: list[str], boot_index: np.ndarray, extra: dict[str, np.ndarray], by_symbol: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    pos = {s: i for i, s in enumerate(symbols)}
    months = _months(dates)
    out = []
    for k, rec in enumerate(family_rows, start=1):
        si = pos[str(rec["target_symbol"])]
        spec = by_symbol[str(rec["target_symbol"])]
        if bool(rec["global"]) and spec["control_regime"] != REGIME_MULTI and spec["pool_n_peer_pit"] >= 2:
            raise RuntimeError("regime_mismatch")
        if (not bool(rec["global"])) and spec["control_regime"] != REGIME_MULTI:
            raise RuntimeError("sector3650_not_multi")
        fit = fit_hypothesis(
            y=prep["y120"][int(rec["horizon"])][si],
            y60=prep["y60"][int(rec["horizon"])][si],
            cols=_cols(prep, si, bool(rec["global"]), int(rec["lookback"])),
            months=months,
            n_dates=len(dates),
            boot_index=boot_index,
            extra_date_masks=extra,
        )
        out.append(
            {
                "hypothesis_id": rec["hypothesis_id"],
                "parent_mechanism_id": rec["parent_mechanism_id"],
                "target_symbol": rec["target_symbol"],
                "target_sector": spec["target_sector"],
                "control_regime": spec["control_regime"],
                "N_PEER_PIT": spec["pool_n_peer_pit"],
                "sole_peer": spec["sole_peer"],
                "direction": "UP",
                "lookback": int(rec["lookback"]),
                "horizon": int(rec["horizon"]),
                "global": bool(rec["global"]),
                "TARGET_SELF_IN_DRIVER": False,
                **fit,
            }
        )
        if k % 15 == 0 or k == len(family_rows):
            print(f"TRANSMISSION_FIT {k}/{len(family_rows)}", flush=True)
    return out


def _coverage(confirmed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parents = ("M1", "M2", "M3", "M4")
    rows = []
    for pid in parents:
        group = [r for r in confirmed if r["parent_mechanism_id"] == pid]
        multi = [r for r in group if r["control_regime"] == REGIME_MULTI and int(r["N_PEER_PIT"]) >= 2]
        single = [r for r in group if r["control_regime"] == REGIME_SINGLE]
        none = [r for r in group if r["control_regime"] == REGIME_NONE]
        if pid in {"M1", "M2"}:
            sectors: dict[str, int] = {}
            for r in multi:
                sectors[str(r["target_sector"])] = sectors.get(str(r["target_sector"]), 0) + 1
            distinct = len(sectors)
            share = (max(sectors.values()) / len(multi)) if multi else None
            passed = len(multi) >= 11 and distinct >= 4 and share is not None and share <= 0.40
            div: Any = distinct
            share_out: Any = share
        else:
            passed = len(group) >= 5
            div = "NOT_APPLICABLE"
            share_out = "NOT_APPLICABLE"
        rows.append(
            {
                "parent_mechanism_id": pid,
                "FV_confirmed_target_n": len(group),
                "MULTI_PEER_confirmed_n": len(multi),
                "SINGLE_PEER_confirmed_n": len(single),
                "NO_PEER_confirmed_n": len(none),
                "GLOBAL_STANDARD_CONFIRMED_TARGET_N": len(multi) if pid in {"M1", "M2"} else "NOT_APPLICABLE",
                "distinct_confirmed_sector_n": div,
                "largest_confirmed_sector_share": share_out,
                "coverage_gate_pass": bool(passed),
                "confirmed_symbols": [r["target_symbol"] for r in group],
                "standard_symbols": [r["target_symbol"] for r in multi],
            }
        )
    return rows


def evaluate() -> dict[str, Any]:
    identity = bind_identities()
    pre_hashes = frozen_source_hashes()
    blockers: list[str] = []
    if identity.get("V4_MACHINE_SHA256") != EXPECTED_V4_MACHINE_SHA256:
        blockers.append("V4_CHANGED")
    if identity.get("COMPLETE_STRATEGY_SHA256") != EXPECTED_COMPLETE_STRATEGY_SHA256:
        blockers.append("COMPLETE_STRATEGY_CHANGED")
    fw = _firewall()
    if not fw.get("pass"):
        blockers.append("FIREWALL")
    bound = verify_identity()
    blockers.extend(bound.get("blockers") or [])
    fv_rows_before = 0
    base = {
        "precommit_id": PRECOMMIT_ID,
        "precommit_sha256": PRECOMMIT_SHA256 if not blockers else None,
        "parent_precommit_sha256": "83c2b92056941f5cae72d1123499c549e9e34ea3280d3ec4588e63a261509a80",
        "parent_mechanism_set_sha256": "4e77b827a5c7cabd5e858688336a4bf7e100492b8a778595bb54e2b089c43552",
        "family_n": (bound.get("family") or {}).get("family_n"),
        "family_serialization_sha256": (bound.get("family") or {}).get("family_sha256"),
        "family_hypothesis_sha256": (bound.get("hashes") or {}).get("family_hypothesis_sha256"),
        "model_contract_sha256": (bound.get("hashes") or {}).get("model_contract_sha256"),
        "symbol_control_contract_sha256": (bound.get("hashes") or {}).get("symbol_control_contract_sha256"),
        "mechanisms": (bound.get("parent") or {}).get("mechanisms"),
        "FV_future_return_rows_read_before_candidate_freeze": fv_rows_before,
        "symbol_outcomes_opened": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "firewall": fw,
    }
    if blockers:
        return {**base, "ok": False, "VERDICT": CASE_BLOCKED, "NEXT": None, "reason": blockers[0], "blockers": blockers, "research_only": True, "ALPHA_CREATED": False, "MECHANISM_FROZEN": False, "PB1_BOUND": False, "COMPLETE_STRATEGY_RUN": False, "V4_CHANGED": "V4_CHANGED" in blockers, "V5_CREATED": False, "submit": 0, "cancel": 0, "live": 0}
    targets = bound["targets"]
    symbols = list(targets["symbols"])
    sector_of = dict(targets["sector_of"])
    by_symbol = dict(bound["assignment"]["by_symbol"])
    family_rows = list(bound["family"]["rows"])
    dates = list(bound["discovery_dates"])
    print("TRANSMISSION_DISCOVERY_LOAD", flush=True)
    panel = load_prices(symbols=symbols, dates=dates, stage="DISCOVERY")
    prep = prepare(px=panel["px"], age=panel["age"], listed=panel["listed"], symbols=symbols, sector_of=sector_of, assignment_by_symbol=by_symbol, y_by_horizon=None)
    del panel
    dev = _mask(dates, set(bound["dev_dates"]))
    c1 = _mask(dates, set(bound["c1_dates"]))
    fitted = _fit_rows(
        family_rows=family_rows,
        prep=prep,
        symbols=symbols,
        dates=dates,
        boot_index=bound["discovery_boot_index"],
        extra={"beta_dev": dev, "beta_c1": c1},
        by_symbol=by_symbol,
    )
    failed = [r["hypothesis_id"] for r in fitted if not r.get("ok")]
    if failed:
        blockers.append("BOOTSTRAP_REPLICATE_OR_ROWS_FAILED")
        return {**base, "ok": False, "VERDICT": CASE_BLOCKED, "NEXT": None, "reason": "BOOTSTRAP_REPLICATE_OR_ROWS_FAILED", "blockers": blockers, "failed_hypotheses": failed[:20], "discovery_rows": fitted, "research_only": True, "ALPHA_CREATED": False, "MECHANISM_FROZEN": False, "PB1_BOUND": False, "COMPLETE_STRATEGY_RUN": False, "V4_CHANGED": False, "V5_CREATED": False, "submit": 0, "cancel": 0, "live": 0, "symbol_outcomes_opened": True}
    p = np.array([float(r["p"]) for r in fitted], dtype=np.float64)
    qv = bh_monotone(p)
    for r, q in zip(fitted, qv):
        r.update(gate_discovery(r, float(q)))
    candidates = [r for r in fitted if r["discovery_candidate"]]
    candidates = sorted(candidates, key=lambda r: (r["parent_mechanism_id"], r["target_symbol"]))
    canon = [_canonical(r) for r in candidates]
    candidate_sha = sha256_obj({"namespace": "TRANSMISSION_CANDIDATE_LIST_V1", "n": len(canon), "candidates": canon}) if canon else None
    freeze_ts = _now() if canon else None
    counts = {f"T{i}_pass_n": sum(1 for r in fitted if r[f"T{i}"]) for i in range(1, 7)}
    disc_by_parent = []
    for pid in ("M1", "M2", "M3", "M4"):
        subset = [r for r in fitted if r["parent_mechanism_id"] == pid and r["discovery_candidate"]]
        disc_by_parent.append({"parent_mechanism_id": pid, "discovery_candidate_target_n": len(subset), "discovery_candidate_symbols": [r["target_symbol"] for r in subset]})
    common = {
        **base,
        "symbol_outcomes_opened": True,
        "discovery_evaluated_n": len(fitted),
        **counts,
        "transmission_candidate_n": len(candidates),
        "transmission_candidate_list": canon,
        "transmission_candidate_list_sha256": candidate_sha,
        "candidate_freeze_timestamp": freeze_ts,
        "discovery_rows": fitted,
        "discovery_by_parent": disc_by_parent,
        "contamination": {
            **contamination_ledger(),
            "discovery_period_already_exposed": True,
            "discovery_is_not_fresh_blind_evidence": True,
            "fv_before_candidate_freeze": "unopened",
            "prospective": "unopened",
            "concentration_identities_not_used": True,
        },
    }
    if not candidates:
        post = frozen_source_hashes()
        if pre_hashes != post:
            common["blockers"] = ["RUNTIME_NONIMPACT"]
            common["VERDICT"] = CASE_BLOCKED
            common["NEXT"] = None
            return common
        common.update({"ok": True, "VERDICT": CASE_NOT_FOUND, "NEXT": NEXT_REASSESS, "reason": "TRANSMISSION_CANDIDATE_N_0", "FV_evaluated_n": 0, "FV_confirmed_n": 0, "first_FV_outcome_access_timestamp": None, "FROZEN_VALIDATION_ECONOMIC_OPENED": False, "parent_results": [{**r, "FV_confirmed_target_n": 0, "coverage_gate_pass": False, "distinct_confirmed_sector_n": "NOT_APPLICABLE" if r["parent_mechanism_id"] in {"M3", "M4"} else 0, "largest_confirmed_sector_share": "NOT_APPLICABLE" if r["parent_mechanism_id"] in {"M3", "M4"} else None} for r in disc_by_parent], "validated_parent_mechanisms": [], "validated_target_symbol_sets": {}, "validated_transmission_sha256": None, "fv_not_run": True, "research_only": True, "ALPHA_CREATED": False, "MECHANISM_FROZEN": False, "PB1_BOUND": False, "COMPLETE_STRATEGY_RUN": False, "V4_CHANGED": False, "V5_CREATED": False, "submit": 0, "cancel": 0, "live": 0})
        return common
    if fv_rows_before != 0 or candidate_sha is None or freeze_ts is None:
        common.update({"VERDICT": CASE_BLOCKED, "NEXT": None, "reason": "FV_READ_BEFORE_CANDIDATE_FREEZE", "blockers": ["FV_READ_BEFORE_CANDIDATE_FREEZE"]})
        return common
    fv_dates = list(bound["fv_dates"])
    fv_boot = draw_date_block_indices(n_day=len(fv_dates), n_boot=BOOTSTRAP_N, seed=FV_BOOTSTRAP_SEED)
    check = _freeze(period="TRANSMISSION_FROZEN_VALIDATION", dates=fv_dates, seed=FV_BOOTSTRAP_SEED)
    if check["bootstrap_index_sha256"] != FV_BOOTSTRAP_SHA256 or sha256_bytes(np.ascontiguousarray(fv_boot, dtype="<i8").tobytes()) != check["index_sha256"]:
        common.update({"VERDICT": CASE_BLOCKED, "NEXT": None, "reason": "FV_BOOTSTRAP_SHA_MISMATCH", "blockers": ["FV_BOOTSTRAP_SHA_MISMATCH"], "FROZEN_VALIDATION_ECONOMIC_OPENED": False})
        return common
    first_fv = _now()
    if not (first_fv > freeze_ts):
        common.update({"VERDICT": CASE_BLOCKED, "NEXT": None, "reason": "FV_TIMESTAMP_NOT_AFTER_FREEZE", "blockers": ["FV_TIMESTAMP_NOT_AFTER_FREEZE"]})
        return common
    print("TRANSMISSION_FV_LOAD", flush=True)
    fv_panel = load_prices(symbols=symbols, dates=fv_dates, stage="FV")
    cand_by_h: dict[int, set[str]] = {1: set(), 3: set()}
    for r in candidates:
        cand_by_h[int(r["horizon"])].add(str(r["target_symbol"]))
    fv_prep = prepare(px=fv_panel["px"], age=fv_panel["age"], listed=fv_panel["listed"], symbols=symbols, sector_of=sector_of, assignment_by_symbol=by_symbol, y_by_horizon=cand_by_h)
    fv_future_rows = int(sum(np.isfinite(fv_prep["y120"][h]).sum() + np.isfinite(fv_prep["y60"][h]).sum() for h in (1, 3)))
    del fv_panel
    folds = bound["fv_folds"]
    extra = {
        "beta_early": _mask(fv_dates, set(folds["FV_EARLY"])),
        "beta_middle": _mask(fv_dates, set(folds["FV_MIDDLE"])),
        "beta_late": _mask(fv_dates, set(folds["FV_LATE"])),
    }
    cand_family = [r for r in family_rows if r["hypothesis_id"] in {c["hypothesis_id"] for c in candidates}]
    fv_fit = _fit_rows(family_rows=cand_family, prep=fv_prep, symbols=symbols, dates=fv_dates, boot_index=fv_boot, extra=extra, by_symbol=by_symbol)
    bad = [r["hypothesis_id"] for r in fv_fit if not r.get("ok")]
    if bad:
        common.update({"VERDICT": CASE_BLOCKED, "NEXT": None, "reason": "FV_BOOTSTRAP_REPLICATE_FAILED", "blockers": ["FV_BOOTSTRAP_REPLICATE_FAILED"], "failed_hypotheses": bad[:20], "first_FV_outcome_access_timestamp": first_fv, "FROZEN_VALIDATION_ECONOMIC_OPENED": True, "fv_rows": fv_fit})
        return common
    fp = np.array([float(r["p"]) for r in fv_fit], dtype=np.float64)
    fq = bh_monotone(fp)
    for r, q in zip(fv_fit, fq):
        r["beta_fv"] = r["beta_pooled"]
        r.update(gate_fv(r, float(q)))
    confirmed = [r for r in fv_fit if r["fv_confirmed"]]
    coverage = _coverage(confirmed)
    for row in coverage:
        match = next(d for d in disc_by_parent if d["parent_mechanism_id"] == row["parent_mechanism_id"])
        row["discovery_candidate_target_n"] = match["discovery_candidate_target_n"]
    validated = [r for r in coverage if r["coverage_gate_pass"]]
    sets = {r["parent_mechanism_id"]: sorted(r["confirmed_symbols"]) for r in validated}
    val_sha = sha256_obj({"namespace": "VALIDATED_TRANSMISSION_V1", "parents": [{"parent_mechanism_id": r["parent_mechanism_id"], "symbols": sets[r["parent_mechanism_id"]]} for r in validated]}) if validated else None
    found = bool(validated)
    post = frozen_source_hashes()
    if pre_hashes != post:
        common.update({"VERDICT": CASE_BLOCKED, "NEXT": None, "reason": "RUNTIME_NONIMPACT", "blockers": ["RUNTIME_NONIMPACT"], "FROZEN_VALIDATION_ECONOMIC_OPENED": True, "first_FV_outcome_access_timestamp": first_fv})
        return common
    fcounts = {f"F{i}_pass_n": sum(1 for r in fv_fit if r[f"F{i}"]) for i in range(1, 7)}
    common.update(
        {
            "ok": True,
            "VERDICT": CASE_FOUND if found else CASE_NOT_FOUND,
            "NEXT": NEXT_ALPHA if found else NEXT_REASSESS,
            "reason": None if found else "NO_PARENT_PASSED_FV_COVERAGE",
            "first_FV_outcome_access_timestamp": first_fv,
            "FV_evaluated_n": len(fv_fit),
            "FV_confirmed_n": len(confirmed),
            **fcounts,
            "fv_rows": fv_fit,
            "parent_results": coverage,
            "validated_parent_mechanisms": [r["parent_mechanism_id"] for r in validated],
            "validated_target_symbol_sets": sets,
            "validated_transmission_sha256": val_sha,
            "FROZEN_VALIDATION_ECONOMIC_OPENED": True,
            "fv_future_return_row_n": fv_future_rows,
            "research_only": True,
            "ALPHA_CREATED": False,
            "MECHANISM_FROZEN": False,
            "PB1_BOUND": False,
            "COMPLETE_STRATEGY_RUN": False,
            "V4_CHANGED": False,
            "V5_CREATED": False,
            "submit": 0,
            "cancel": 0,
            "live": 0,
        }
    )
    return common
