"""Already-executed check, C0/C14 pin, AM Full Causal harvest, frozen 18-day fit → stress score."""
from __future__ import annotations

import json
import os
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from research.am_c0_reused_history_stress_20260828_20260902_v1 import (
    ANALYSIS_ID,
    DEVELOPMENT_DAYS,
    EXPECTED_C0_SPEC_SHA256,
    FORBIDDEN_INPUT_DAYS,
    MAX_RESEARCH_DATE,
    STRESS_DAYS,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.isolation import (
    C14_PATH,
    CACHE,
    C0_DECISION_OUT,
    LABELED_X14,
    NATIVE,
    OUT,
    REGIME_LABELED,
    TODAY,
)
from research.am_c0_reused_history_stress_20260828_20260902_v1.spec import live_c0_spec_sha256
from research.am_current_utility_augment.analyze import preservation_audit
from research.am_current_utility_augment.ensemble import attach_ensemble
from research.am_current_utility_augment.overlay import overlay_replay
from research.am_entry_architecture_final_reassessment import A3_REUSE_ARM, A5_REUSE_ARM, AUGMENT_MAX_PER_COHORT, C0, CURRENT_PRIORITY
from research.am_entry_architecture_final_reassessment.consensus import cohort_decisions, merge_model_fields, tag_consensus
from research.am_entry_fixed_spec_oof import (
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
)
from research.am_entry_fixed_spec_oof.oof import evaluate_policy
from research.am_entry_information_expansion import RF_CLF_PARAMS, TARGET, X14_BUNDLE
from research.am_entry_information_expansion.features import join_x14
from research.am_entry_information_expansion.labels import attach_target
from research.am_entry_profit_improvement import C14_ID, DEV_WAIT_SEC, ELIGIBLE_DAYS, PARITY_ABS_TOL, REPRESENTATION_N
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.labels import apply_utility
from research.am_entry_profit_improvement.metrics import _pf_num, economic_pack, paired_delta
from research.am_entry_profit_improvement.replay_day import process_exit_day
from research.am_entry_research_final_decision.spec import canonical_c0_spec
from research.am_entry_temporal_regime_information.cohort_state import attach_all_cohort_state
from research.am_entry_temporal_regime_information.models import fit_rf, score_rf
from research.am_entry_temporal_regime_information.oof import arm_spec_grid
from research.am_expanded_entry_risk_integration.ensemble import joint_map
from research.am_utility_augment_execution_risk.analyze import preservation_pass
from research.anchor_vs_event_driven.run_comparison import find_capture_dir
from research.canonical_entry_performance_rebase.analyze import row_key, session_of
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_target_architecture.replay import process_day as process_path_day
from research.joint_feature_architecture.replay import process_day as process_feat_day
from research.passive_wait_policy_reassessment.analyze import _close, wait_body
from research.passive_wait_policy_reassessment.replay import process_day as process_wait_day
from research.v1r_exit_v2_asymmetric.states import build_trade_bundle
from small_paper.v1r_exit_v2_contract import apply_arch_e_to_bundle
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

MAX_WORKERS = 2
REGIME_PROBA = NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information"


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def prior_search() -> dict[str, Any]:
    """Exact spec + exact 4 days + C0+C14 Full Causal already done?"""
    hits: list[dict[str, Any]] = []
    root = NATIVE / "results" / "research"
    if not root.is_dir():
        return {"ALREADY_EXECUTED": False, "hits": []}
    for p in sorted(root.glob("*/report.json")):
        try:
            body = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(body, dict):
            continue
        aid = str(body.get("ANALYSIS_ID") or "")
        days = body.get("STRESS_DAYS") or body.get("stress_days") or (body.get("required") or {}).get("STRESS_DAYS")
        if isinstance(days, list):
            day_t = tuple(str(d) for d in days)
        else:
            day_t = ()
        sha = str(
            body.get("C0_SPEC_SHA256")
            or body.get("C0_PROSPECTIVE_SPEC_SHA256")
            or (body.get("required") or {}).get("C0_SPEC_SHA256")
            or ""
        )
        entry = str(body.get("ENTRY") or body.get("architecture_id") or (body.get("required") or {}).get("ENTRY") or "")
        if aid == ANALYSIS_ID or (
            day_t == STRESS_DAYS
            and sha == EXPECTED_C0_SPEC_SHA256
            and C0 in entry
        ):
            hits.append({"path": str(p), "ANALYSIS_ID": aid, "STRESS_DAYS": list(day_t), "C0_SPEC_SHA256": sha})
    out_rep = OUT / "report.json"
    reuse = None
    if out_rep.is_file():
        body = _load(out_rep)
        day_t = tuple(str(d) for d in (body.get("STRESS_DAYS") or []))
        sha = str(body.get("C0_SPEC_SHA256") or "")
        if str(body.get("ANALYSIS_ID") or "") == ANALYSIS_ID and day_t == STRESS_DAYS and sha == EXPECTED_C0_SPEC_SHA256:
            reuse = body
    return {
        "ALREADY_EXECUTED": reuse is not None,
        "hits": hits,
        "reuse": reuse,
    }


def pin_identity() -> dict[str, Any]:
    blockers: list[str] = []
    live_sha = live_c0_spec_sha256()
    sha_ok = live_sha == EXPECTED_C0_SPEC_SHA256
    if not sha_ok:
        blockers.append("C0_SPEC_SHA_MISMATCH")
    c14 = _load(C14_PATH)
    c14_ok = str(c14.get("candidate_id") or "") == C14_ID
    if not c14_ok:
        blockers.append("C14_IDENTITY_MISMATCH")
    if not C14_PATH.is_file():
        blockers.append("C14_FILE_MISSING")
    c14_fn_ok = callable(apply_arch_e_to_bundle) and callable(build_trade_bundle)
    if not c14_fn_ok:
        blockers.append("C14_REPLAY_SOURCE_MISSING")
    feat_ok = list(FEATURE_ORDER) == [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]
    if not feat_ok:
        blockers.append("FEATURE_ORDER_DRIFT")
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        blockers.append("RANK_PASS_GATE_DRIFT")
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        blockers.append("RUNTIME_WAIT_DRIFT")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        blockers.append("DEV_WAIT_DRIFT")
    if int(POSITION_CAP) != 5:
        blockers.append("CAP_DRIFT")
    if int(AUGMENT_MAX_PER_COHORT) != 1 or CURRENT_PRIORITY is not True:
        blockers.append("AUGMENT_CONTRACT_DRIFT")
    if int(RF_CLF_PARAMS.get("n_estimators") or 0) != 500:
        blockers.append("RF_PARAM_DRIFT")
    if list(X14_BUNDLE) != [
        "distance_from_vwap_bps",
        "rebound_from_recent_low_bps",
        "volume_rate_60s",
        "trading_value_delta_60s",
        "volume_percentile_60s",
        "trading_value_percentile_180s",
    ]:
        blockers.append("X14_BUNDLE_DRIFT")
    if not REGIME_LABELED.is_file() and not LABELED_X14.is_file():
        blockers.append("DEVELOPMENT_LABELED_MISSING")
    if TODAY in STRESS_DAYS or TODAY in DEVELOPMENT_DAYS:
        blockers.append("ACTIVE_DAY_IN_RESEARCH_INPUT")
    if any(d > MAX_RESEARCH_DATE for d in STRESS_DAYS):
        blockers.append("FUTURE_DATA")
    if any(d in FORBIDDEN_INPUT_DAYS for d in STRESS_DAYS):
        blockers.append("FORBIDDEN_INPUT_DAY")
    oof_n = 0
    for arm in (A3_REUSE_ARM, A5_REUSE_ARM):
        for spec in arm_spec_grid(arm):
            rid = str(spec.get("representation_id") or "").replace("|", "_")
            cache = REGIME_PROBA / f"{arm}_{rid}_oof_proba.json"
            if cache.is_file():
                oof_n += 1
    c0_src_ok = callable(canonical_c0_spec) and callable(cohort_decisions) and callable(overlay_replay)
    if not c0_src_ok:
        blockers.append("C0_SOURCE_MISSING")
    return {
        "C0_SPEC_SHA256": live_sha,
        "C0_SPEC_SHA_PARITY": sha_ok,
        "C0_SOURCE_REPRODUCIBLE": c0_src_ok and sha_ok,
        "C14_REPRODUCIBLE": c14_ok and c14_fn_ok,
        "C14_CANDIDATE_ID": str(c14.get("candidate_id") or ""),
        "DEVELOPMENT_MODEL_FROZEN": oof_n == int(REPRESENTATION_N) * 2 and (REGIME_LABELED.is_file() or LABELED_X14.is_file()),
        "B0_B1_OOF_CACHE_N": oof_n,
        "EXPECTED_OOF_CACHE_N": int(REPRESENTATION_N) * 2,
        "REGIME_LABELED_EXISTS": REGIME_LABELED.is_file(),
        "C0_DECISION_SHA256_BEFORE": file_sha256(C0_DECISION_OUT / "report.json"),
        "blockers": blockers,
        "ok": not blockers,
    }


def development_current_parity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    top3 = independent_top3(rows)
    obs = {
        "AM_LABELED_N": len(rows),
        "AM_Y_FILL5_POS_N": sum(1 for r in rows if int(r.get("Y_FILL5") or 0) == 1),
        "AM_CURRENT_TOP3_FILL5_RATE": top3.get("AM_CURRENT_TOP3_FILL5_RATE"),
    }
    parity_pop = freeze_parity(obs)
    current_eval = evaluate_policy(rows, list(ELIGIBLE_DAYS), score_key="current_score")
    replay_ok = (
        bool(parity_pop.get("ok"))
        and int(current_eval.get("trade_count") or -1) == int(V2_CURRENT_TRADE_N)
        and _close(current_eval.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        and _close(current_eval.get("profit_factor"), V2_CURRENT_PF, 1e-12)
        and _close(current_eval.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
    )
    baseline = overlay_replay(rows, include_augment=False)
    base_pack = economic_pack(list(baseline.get("trades") or []), list(ELIGIBLE_DAYS))
    overlay_ok = (
        int(base_pack.get("trade_count") or -1) == int(V2_CURRENT_TRADE_N)
        and _close(base_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        and _close(_pf_num(base_pack.get("profit_factor")), V2_CURRENT_PF, 1e-12)
        and _close(base_pack.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
    )
    stress_in_dev = sorted({str(r.get("date") or "") for r in rows} & set(STRESS_DAYS))
    return {
        "PM_ROWS_USED_N": pm_n,
        "parity_pop": parity_pop,
        "evaluate_policy": {
            "trade_count": current_eval.get("trade_count"),
            "net_pnl_yen_100": current_eval.get("net_pnl_yen_100"),
            "profit_factor": current_eval.get("profit_factor"),
            "max_drawdown_yen_100": current_eval.get("max_drawdown_yen_100"),
        },
        "overlay_current": {
            "trade_count": base_pack.get("trade_count"),
            "net_pnl_yen_100": base_pack.get("net_pnl_yen_100"),
            "profit_factor": base_pack.get("profit_factor"),
            "max_drawdown_yen_100": base_pack.get("max_drawdown_yen_100"),
        },
        "replay_ok": bool(replay_ok),
        "overlay_ok": bool(overlay_ok),
        "STRESS_DATES_IN_DEVELOPMENT_ROWS": stress_in_dev,
        "ok": bool(replay_ok and overlay_ok and pm_n == 0 and not stress_in_dev),
    }


def sealed_caps() -> tuple[list[dict[str, Any]], list[str]]:
    from _p1_inventory import resolve_universe

    out: list[dict[str, Any]] = []
    blockers: list[str] = []
    for day in STRESS_DAYS:
        if str(day) == str(TODAY):
            blockers.append(f"ACTIVE_DAY:{day}")
            continue
        if str(day) > MAX_RESEARCH_DATE:
            blockers.append(f"FUTURE:{day}")
            continue
        cap = find_capture_dir(str(day))
        uni = resolve_universe(str(day), cap) if cap is not None else {}
        rec = {
            "date": str(day),
            "capture_path": str(cap) if cap is not None else "",
            "universe_symbols": list(uni.get("symbols") or []),
            "ok": cap is not None and bool(uni.get("symbols")),
        }
        if not rec["ok"]:
            blockers.append(f"CAPTURE_OR_UNIVERSE_MISSING:{day}")
        out.append(rec)
    return out, blockers


def _stage_cache(day: str, stage: str) -> Path:
    return CACHE / f"{day}_{stage}.json"


def _run_cached(path: Path, fn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    saved = _load(path)
    if saved.get("ok") and str(saved.get("date") or "") == str(payload.get("date") or ""):
        return saved
    body = fn(payload)
    if body.get("ok"):
        _dump(path, body)
    return body


def harvest_day_path(inv: dict[str, Any]) -> dict[str, Any]:
    return _run_cached(
        _stage_cache(str(inv["date"]), "PATH"),
        process_path_day,
        {
            "date": inv["date"],
            "capture_path": inv["capture_path"],
            "universe": list(inv["universe_symbols"]),
        },
    )


def harvest_day_wait(inv: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
    path = _stage_cache(str(inv["date"]), "WAIT")
    saved = _load(path)
    if (
        saved.get("ok")
        and str(saved.get("date") or "") == str(inv["date"])
        and int(saved.get("candidate_n") or -1) == len(candidates)
    ):
        return saved
    body = process_wait_day(
        {
            "date": inv["date"],
            "capture_path": inv["capture_path"],
            "universe": list(inv["universe_symbols"]),
            "candidates": candidates,
        }
    )
    body["candidate_n"] = len(candidates)
    if body.get("ok"):
        _dump(path, body)
    return body


def harvest_day_feat(inv: dict[str, Any]) -> dict[str, Any]:
    return _run_cached(
        _stage_cache(str(inv["date"]), "FEAT"),
        process_feat_day,
        {
            "date": inv["date"],
            "capture_path": inv["capture_path"],
            "universe": list(inv["universe_symbols"]),
        },
    )


def harvest_day_exit(inv: dict[str, Any], fills: list[dict[str, Any]]) -> dict[str, Any]:
    path = _stage_cache(str(inv["date"]), "EXIT")
    saved = _load(path)
    if saved.get("ok") and str(saved.get("date") or "") == str(inv["date"]) and int(saved.get("fill_n") or -1) == len(fills):
        return saved
    body = process_exit_day(
        {
            "date": inv["date"],
            "capture_path": inv["capture_path"],
            "universe": list(inv["universe_symbols"]),
            "fills": fills,
        }
    )
    body["fill_n"] = len(fills)
    if body.get("ok"):
        _dump(path, body)
    return body


def am_path_rows(path_body: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in list(path_body.get("rows") or []):
        rec = dict(r)
        rec["date"] = str(rec.get("date") or path_body.get("date") or "")
        if session_of(rec) != "AM":
            continue
        rec["session"] = "AM"
        out.append(rec)
    return out


def attach_wait(rows: list[dict[str, Any]], wait_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by = {row_key(h): h for h in wait_rows}
    miss = 0
    fill_n = 0
    for r in rows:
        h = by.get(row_key(r))
        if h is None:
            miss += 1
            r["t0"] = r.get("t0")
            r["limit"] = None
            r["fill_t"] = None
            r["fill_price"] = None
            r["WOULD_FILL5"] = False
            r["Y_FILL5"] = 0
            continue
        w = wait_body(h, "W5")
        filled = bool(w.get("WOULD_FILL"))
        r["t0"] = h.get("t0") if h.get("t0") is not None else r.get("t0")
        r["limit"] = h.get("limit")
        r["fill_t"] = w.get("fill_t") if filled else None
        r["fill_price"] = w.get("fill_price") if filled else None
        r["WOULD_FILL5"] = filled
        r["Y_FILL5"] = 1 if filled else 0
        if filled:
            fill_n += 1
    return {"JOIN_MISS_N": miss, "Y_FILL5_POS_N": fill_n}


def labeled_from_day(
    day: str,
    path_rows: list[dict[str, Any]],
    wait_body_rows: list[dict[str, Any]],
    feat_rows: list[dict[str, Any]],
    exit_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [dict(r) for r in path_rows]
    wstats = attach_wait(rows, wait_body_rows)
    x14 = join_x14(rows, feat_rows)
    rows = list(x14.get("rows") or [])
    exits = {str(r.get("key") or row_key(r)): r for r in exit_rows}
    ustats = apply_utility(rows, exits)
    rows = attach_target(rows)
    meta = {
        "date": day,
        "path_n": len(path_rows),
        "WAIT_JOIN": wstats,
        "X14_JOIN": {k: x14.get(k) for k in ("JOIN_MISS_N", "FUTURE_FEATURE_USE_N", "X14_COMPLETE_N")},
        "UTILITY": ustats,
        "pm_n": 0,
        "Y_FILL5_POS_N": sum(1 for r in rows if int(r.get("Y_FILL5") or 0) == 1),
    }
    return rows, meta


def process_fit_score(payload: dict[str, Any]) -> dict[str, Any]:
    warnings.filterwarnings("ignore")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    spec = dict(payload.get("spec") or {})
    train = list(_load(Path(str(payload["train_path"]))).get("rows") or [])
    eval_rows = list(_load(Path(str(payload["eval_path"]))).get("rows") or [])
    stress = set(str(d) for d in (payload.get("stress_days") or STRESS_DAYS))
    leak_n = sum(1 for r in train if str(r.get("date") or "") in stress)
    if leak_n:
        return {"ok": False, "blocker": "STRESS_IN_TRAIN", "STRESS_REFIT_N": leak_n, "spec_id": spec.get("spec_id")}
    pm_n = sum(1 for r in train if session_of(r) != "AM") + sum(1 for r in eval_rows if session_of(r) != "AM")
    fit = fit_rf(train, spec)
    scored = score_rf(eval_rows, fit)
    scores: dict[str, Any] = {}
    for r in scored:
        scores[row_key(r)] = {
            "JOINT_SCORE": r.get("JOINT_SCORE"),
            "P_WIN": r.get("P_WIN"),
            "P_LOSS": r.get("P_LOSS"),
            "P_NEUTRAL": r.get("P_NEUTRAL"),
        }
    return {
        "ok": True,
        "spec_id": spec.get("spec_id"),
        "representation_id": spec.get("representation_id"),
        "architecture_id": spec.get("architecture_id"),
        "train_n": int(fit.get("train_n") or 0),
        "kind": fit.get("kind"),
        "STRESS_REFIT_N": 0,
        "PM_ROWS_USED_N": pm_n,
        "scores": scores,
    }


def score_arm(
    arm_id: str,
    train_path: Path,
    eval_path: Path,
    train: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[str], dict[str, Any]]:
    specs = arm_spec_grid(arm_id)
    by_rep: dict[str, dict[str, Any]] = {}
    rep_ids: list[str] = []
    leak: dict[str, Any] = {"STRESS_REFIT_N": 0, "PM_ROWS_USED_N": 0, "NEW_MODEL_N": 0, "blockers": []}
    stress = set(STRESS_DAYS)
    leak["STRESS_REFIT_N"] = sum(1 for r in train if str(r.get("date") or "") in stress)
    jobs = []
    for spec in specs:
        rid = str(spec.get("representation_id") or "")
        rep_ids.append(rid)
        cache = CACHE / f"{arm_id}_{rid.replace('|', '_')}_stress_scores.json"
        saved = _load(cache)
        if saved.get("ok") and saved.get("spec_id") == spec.get("spec_id") and saved.get("scores"):
            by_rep[rid] = dict(saved.get("scores") or {})
            continue
        jobs.append((spec, rid, cache))
    if leak["STRESS_REFIT_N"]:
        leak["blockers"].append("STRESS_IN_TRAIN")
        return by_rep, rep_ids, leak
    if jobs:
        payloads = [
            {
                "spec": spec,
                "train_path": str(train_path),
                "eval_path": str(eval_path),
                "stress_days": list(STRESS_DAYS),
            }
            for spec, _rid, _cache in jobs
        ]
        with ProcessPoolExecutor(max_workers=min(MAX_WORKERS, len(payloads))) as ex:
            futs = {ex.submit(process_fit_score, p): i for i, p in enumerate(payloads)}
            for fut in as_completed(futs):
                i = futs[fut]
                spec, rid, cache = jobs[i]
                body = fut.result()
                if not body.get("ok"):
                    leak["blockers"].append(str(body.get("blocker") or spec.get("spec_id")))
                    continue
                leak["PM_ROWS_USED_N"] = max(int(leak["PM_ROWS_USED_N"] or 0), int(body.get("PM_ROWS_USED_N") or 0))
                by_rep[rid] = dict(body.get("scores") or {})
                _dump(cache, body)
                print(f"score {arm_id} {rid} n={len(by_rep[rid])} kind={body.get('kind')}", flush=True)
    return by_rep, rep_ids, leak


def eval_c0_overlay(tagged: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    baseline = overlay_replay(tagged, include_augment=False)
    overlay = overlay_replay(tagged, include_augment=True, augment_rank="utility")
    ov_trades = list(overlay.get("trades") or [])
    base_trades = list(baseline.get("trades") or [])
    aug_trades = [t for t in ov_trades if str(t.get("arm") or "") == "AUGMENT"]
    base_pack = economic_pack(base_trades, days)
    ov_pack = economic_pack(ov_trades, days)
    aug_pack = economic_pack(aug_trades, days)
    paired = paired_delta(list(ov_pack.get("daily") or []), list(base_pack.get("daily") or []))
    pres = preservation_audit(baseline, overlay)
    pres["CURRENT_PRESERVATION_PASS"] = preservation_pass(pres)
    pres["CURRENT_ENTRY_MISMATCH_N"] = int(pres.get("CURRENT_ADMISSION_LOST_N") or 0)
    aug_adm = [a for a in (overlay.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    aug_fill = [f for f in (overlay.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    candidates = list(overlay.get("augment_candidates") or [])
    return {
        "baseline": baseline,
        "overlay": overlay,
        "current_pack": base_pack,
        "overlay_pack": ov_pack,
        "augment_pack": aug_pack,
        "paired": paired,
        "preservation": pres,
        "AUGMENT_CANDIDATE_N": len(candidates),
        "AUGMENT_ADMITTED_N": len(aug_adm),
        "AUGMENT_FILL_N": len(aug_fill),
        "AUGMENT_EXPIRED_N": max(len(aug_adm) - len(aug_fill), 0),
        "geometry": {},
    }


def harvest_stress() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    invs, cap_blockers = sealed_caps()
    if cap_blockers:
        return {"ok": False, "blocker": "CAPTURE", "blockers": cap_blockers, "inventory": invs}
    inv_by = {r["date"]: r for r in invs}
    labeled: list[dict[str, Any]] = []
    day_meta: list[dict[str, Any]] = []
    for day in STRESS_DAYS:
        inv = inv_by[day]
        print(f"PATH {day}", flush=True)
        path_body = harvest_day_path(inv)
        if not path_body.get("ok"):
            return {"ok": False, "blocker": f"PATH:{day}:{path_body.get('blocker')}", "inventory": invs}
        am_rows = am_path_rows(path_body)
        cands = [
            {
                "date": r.get("date"),
                "anchor": r.get("anchor"),
                "symbol": r.get("symbol"),
                "session": "AM",
                "t0": r.get("t0"),
            }
            for r in am_rows
        ]
        print(f"WAIT {day} cands={len(cands)}", flush=True)
        wait_body_d = harvest_day_wait(inv, cands)
        if not wait_body_d.get("ok"):
            return {"ok": False, "blocker": f"WAIT:{day}:{wait_body_d.get('blocker')}", "inventory": invs}
        print(f"FEAT {day}", flush=True)
        feat_body = harvest_day_feat(inv)
        if not feat_body.get("ok"):
            return {"ok": False, "blocker": f"FEAT:{day}:{feat_body.get('blocker')}", "inventory": invs}
        tmp_rows = [dict(r) for r in am_rows]
        attach_wait(tmp_rows, list(wait_body_d.get("rows") or []))
        fills = []
        for r in tmp_rows:
            if int(r.get("Y_FILL5") or 0) != 1:
                continue
            fills.append(
                {
                    "date": r.get("date"),
                    "anchor": r.get("anchor"),
                    "symbol": r.get("symbol"),
                    "session": "AM",
                    "t0": r.get("t0"),
                    "fill_t": r.get("fill_t"),
                    "fill_price": r.get("fill_price"),
                }
            )
        print(f"EXIT {day} fills={len(fills)}", flush=True)
        exit_body = harvest_day_exit(inv, fills)
        if not exit_body.get("ok"):
            return {"ok": False, "blocker": f"EXIT:{day}:{exit_body.get('blocker')}", "inventory": invs}
        rows, meta = labeled_from_day(
            day,
            am_rows,
            list(wait_body_d.get("rows") or []),
            list(feat_body.get("rows") or []),
            list(exit_body.get("rows") or []),
        )
        if int((meta.get("WAIT_JOIN") or {}).get("JOIN_MISS_N") or 0) != 0:
            return {"ok": False, "blocker": f"WAIT_JOIN:{day}", "meta": meta}
        if int((meta.get("X14_JOIN") or {}).get("JOIN_MISS_N") or 0) != 0:
            return {"ok": False, "blocker": f"X14_JOIN:{day}", "meta": meta}
        if int((meta.get("UTILITY") or {}).get("UTILITY_EXIT_MISS_N") or 0) != 0:
            return {"ok": False, "blocker": f"EXIT_MISS:{day}", "meta": meta}
        labeled.extend(rows)
        day_meta.append(meta)
        print(f"labeled {day} n={len(rows)} fill5={meta.get('Y_FILL5_POS_N')}", flush=True)
    tagged, state_meta = attach_all_cohort_state(labeled)
    if int(state_meta.get("AM_SLOT_UNKNOWN_N") or 0) != 0:
        return {"ok": False, "blocker": "AM_SLOT_UNKNOWN", "state": state_meta}
    pm_n = sum(1 for r in tagged if session_of(r) != "AM")
    if pm_n:
        return {"ok": False, "blocker": "PM_ROWS", "PM_ROWS_USED_N": pm_n}
    _dump(CACHE / "stress_labeled.json", {"ok": True, "rows": tagged, "day_meta": day_meta})
    return {"ok": True, "rows": tagged, "day_meta": day_meta, "state": state_meta, "inventory": invs}
