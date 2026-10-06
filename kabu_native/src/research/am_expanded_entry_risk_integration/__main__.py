"""Offline CURRENT-preserving EXPANDED_X14_RF risk-arm replay. No Runtime write. No Paper."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_current_utility_augment.analyze import overlay_gate, preservation_audit
from research.am_current_utility_augment.ensemble import attach_ensemble
from research.am_current_utility_augment.overlay import overlay_replay
from research.am_entry_fixed_spec_oof import (
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
)
from research.am_entry_fixed_spec_oof.oof import evaluate_policy
from research.am_entry_information_expansion import (
    ARM_X14_RF,
    AVAILABLE_REP_MIN,
    CLASS_LOSS,
    CLASS_NEUTRAL,
    CLASS_WIN,
    MAX_WORKERS,
    TARGET,
    X14_BUNDLE,
)
from research.am_entry_information_expansion.features import join_x14, load_harvest
from research.am_entry_information_expansion.labels import attach_target
from research.am_entry_information_expansion.oof import arm_spec_grid, process_oof_scores
from research.am_entry_profit_improvement import (
    C14_CHANGED,
    C14_ID,
    CANCEL_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    FEATURE_SEARCH_N,
    LIVE_ORDER_N,
    NEW_FEATURE_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    REPRESENTATION_N,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
    WAIT_SEARCH_N,
)
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.metrics import _pf_num, economic_pack, paired_delta
from research.am_entry_profit_improvement.publish import kv_rows
from research.am_expanded_entry_risk_integration import (
    ANALYSIS_ID,
    AUGMENT_MAX_PER_COHORT,
    CURRENT_PRIORITY,
    FROZEN_ARM,
    PRIOR_AUGMENT_NET,
    PRIOR_AUGMENT_TRADE_N,
    PRIOR_OVERLAY_NET,
    PRIOR_OVERLAY_PF,
    R1,
    R2,
    R3,
    RISK_ARM_IDS,
    RISK_ARM_N,
)
from research.am_expanded_entry_risk_integration.analyze import decide_case, pick_best, profit_concentration
from research.am_expanded_entry_risk_integration.ensemble import attach_win_dominant, joint_map
from research.am_expanded_entry_risk_integration.precommit import precommit_spec, print_precommit, spec_sha256
from research.am_expanded_entry_risk_integration.publish import OUT, build_markdown, write_artifacts
from research.am_utility_augment_execution_risk.analyze import preservation_pass
from research.canonical_entry_performance_rebase.analyze import _f, session_of
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
LABELED = NATIVE / "results" / "research" / "_work_cache" / "am_entry_profit_improvement" / "labeled_am.json"
FEAT_DIR = NATIVE / "results" / "research" / "_work_cache" / "joint_feature_architecture"
EXP_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_information_expansion"
LABELED_X14 = EXP_CACHE / "labeled_am_x14.json"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_expanded_entry_risk_integration"

MECHANISM = {
    R1: "WIN_DOMINANT",
    R2: "CORE_CONFIRMED",
    R3: "WIN_DOMINANT_AND_CORE_CONFIRMED",
}

INTEGRITY_ZERO_KEYS = (
    "OUTER_HELDOUT_FIT_LEAK_N",
    "FUTURE_FEATURE_USE_N",
    "TARGET_CONTAMINATION_N",
    "PM_ROWS_USED_N",
    "FEATURE_SEARCH_N",
    "FEATURE_SUBSET_SEARCH_N",
    "MODEL_SEARCH_N",
    "HYPERPARAMETER_SEARCH_N",
    "SCORE_THRESHOLD_SEARCH_N",
    "PROBABILITY_THRESHOLD_SEARCH_N",
    "CONSENSUS_THRESHOLD_SEARCH_N",
    "WAIT_SEARCH_N",
    "EXIT_CHANGE_N",
    "CURRENT_POLICY_CHANGE_N",
    "ORACLE_SELECTION_USE_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _pool(fn, jobs: list[dict], label: str, key: str) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job.get(key) for job in jobs}
        for fut in as_completed(futs):
            ident = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, key: ident, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get(key) or ident} ok={body.get('ok')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


def _arm_fill_rate(admitted: int, fill_n: int) -> float | None:
    return (float(fill_n) / float(admitted)) if admitted else None


def _empty_required(*, verdict: str, nxt: str, base_parity: bool = False) -> dict[str, Any]:
    req: dict[str, Any] = {
        "BASE_PARITY": base_parity,
        "RISK_ARM_N": RISK_ARM_N,
        "PASS_ARM_N": 0,
        "BEST_PASS_ARM": None,
        "BEST_PASS_NET": None,
        "BEST_PASS_PF": None,
        "BEST_PASS_MAX_DD": None,
        "BEST_PASS_PAIRED_MEDIAN": None,
        "BEST_PASS_EX_TOP3": None,
        "CURRENT_PRESERVATION_PASS_ALL": False,
        "PRIMARY_RISK_MECHANISM": "INTEGRITY_FAILURE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": verdict,
        "NEXT": nxt,
    }
    for prefix in ("R1", "R2", "R3"):
        for suffix in ("NET", "PF", "MAX_DD", "PAIRED_MEDIAN", "POS_DAYS", "NEG_DAYS", "EX_BEST", "EX_TOP3"):
            req[f"{prefix}_{suffix}"] = None
    return req


def _req_arm(arm: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    a = arm or {}
    return {
        f"{prefix}_NET": a.get("OVERLAY_NET_PNL"),
        f"{prefix}_PF": a.get("OVERLAY_PF"),
        f"{prefix}_MAX_DD": a.get("OVERLAY_MAX_DD"),
        f"{prefix}_PAIRED_MEDIAN": a.get("PAIRED_MEDIAN_DAILY_DELTA"),
        f"{prefix}_POS_DAYS": a.get("PAIRED_POS_DAYS"),
        f"{prefix}_NEG_DAYS": a.get("PAIRED_NEG_DAYS"),
        f"{prefix}_EX_BEST": a.get("EX_BEST_DAY_PNL_DELTA"),
        f"{prefix}_EX_TOP3": a.get("EX_TOP3_DAYS_PNL_DELTA"),
    }


def _integrity(msg: str, extra: dict | None = None, *, base_parity: bool = False) -> int:
    decision = decide_case(
        integrity_ok=False,
        arms=[],
        current_net=0.0,
        current_pf=0.0,
        current_dd=0.0,
    )
    required = _empty_required(
        verdict=str(decision.get("VERDICT") or "AM_EXPANDED_RISK_INTEGRATION_INTEGRITY_FAILED"),
        nxt=str(decision.get("NEXT") or "STOP"),
        base_parity=base_parity,
    )
    required["STOP_REASON"] = msg
    required["PRIMARY_RISK_MECHANISM"] = decision.get("PRIMARY_RISK_MECHANISM")
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, {"integrity": kv_rows({"STOP_REASON": msg}), "precommit": [{"empty": True}]})
    print(msg, flush=True)
    return 2


def _eval_arm(
    tagged: list[dict[str, Any]],
    baseline: dict[str, Any],
    base_pack: dict[str, Any],
    arm_id: str,
    *,
    augment_rank: str,
    core_confirmed: bool,
) -> dict[str, Any]:
    overlay = overlay_replay(
        tagged,
        include_augment=True,
        augment_rank=augment_rank,
        core_confirmed=core_confirmed,
    )
    ov_trades = list(overlay.get("trades") or [])
    aug_trades = [t for t in ov_trades if str(t.get("arm") or "") == "AUGMENT"]
    ov_pack = economic_pack(ov_trades, list(ELIGIBLE_DAYS))
    aug_pack = economic_pack(aug_trades, list(ELIGIBLE_DAYS))
    paired = paired_delta(list(ov_pack.get("daily") or []), list(base_pack.get("daily") or []))
    pres = preservation_audit(baseline, overlay)
    pres["CURRENT_PRESERVATION_PASS"] = preservation_pass(pres)
    aug_adm = [a for a in (overlay.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    aug_fill = [f for f in (overlay.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    candidates = list(overlay.get("augment_candidates") or [])
    gate = overlay_gate(
        ov_pack,
        base_pack,
        paired,
        preservation_pass=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        integrity_ok=True,
    )
    conc = profit_concentration(list(aug_pack.get("daily") or []))
    rec = {
        "risk_arm_id": arm_id,
        "PRIMARY_RISK_MECHANISM": MECHANISM.get(arm_id),
        "augment_rank": augment_rank,
        "core_confirmed": bool(core_confirmed),
        "AUGMENT_CANDIDATE_N": len(candidates),
        "AUGMENT_ADMITTED_N": len(aug_adm),
        "AUGMENT_FILL_N": len(aug_fill),
        "AUGMENT_EXPIRED_N": max(len(aug_adm) - len(aug_fill), 0),
        "AUGMENT_FILL_RATE": _arm_fill_rate(len(aug_adm), len(aug_fill)),
        "AUGMENT_CORE_NOT_CONFIRMED_N": sum(
            1 for c in candidates if str(c.get("admit_status") or "") == "CORE_NOT_CONFIRMED"
        ),
        "AUGMENT_TRADE_N": aug_pack.get("trade_count"),
        "AUGMENT_NET_PNL": aug_pack.get("net_pnl_yen_100"),
        "AUGMENT_PF": aug_pack.get("profit_factor"),
        "AUGMENT_MAX_DD": aug_pack.get("max_drawdown_yen_100"),
        "AUGMENT_WIN_N": aug_pack.get("win_n"),
        "AUGMENT_LOSS_N": aug_pack.get("loss_n"),
        "AUGMENT_FLAT_N": aug_pack.get("flat_n"),
        "OVERLAY_TRADE_N": ov_pack.get("trade_count"),
        "OVERLAY_NET_PNL": ov_pack.get("net_pnl_yen_100"),
        "OVERLAY_PF": ov_pack.get("profit_factor"),
        "OVERLAY_MAX_DD": ov_pack.get("max_drawdown_yen_100"),
        "PAIRED_POS_DAYS": paired.get("PAIRED_POS_DAYS"),
        "PAIRED_NEG_DAYS": paired.get("PAIRED_NEG_DAYS"),
        "PAIRED_ZERO_DAYS": paired.get("PAIRED_ZERO_DAYS"),
        "PAIRED_MEDIAN_DAILY_DELTA": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "EX_BEST_DAY_PNL_DELTA": paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3_DAYS_PNL_DELTA": paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        "CURRENT_PRESERVATION_PASS": pres.get("CURRENT_PRESERVATION_PASS"),
        "CURRENT_ADMISSION_LOST_N": pres.get("CURRENT_ADMISSION_LOST_N"),
        "CURRENT_FILL_LOST_N": pres.get("CURRENT_FILL_LOST_N"),
        "CURRENT_EXIT_MISMATCH_N": pres.get("CURRENT_EXIT_MISMATCH_N"),
        "CURRENT_PNL_MISMATCH_N": pres.get("CURRENT_PNL_MISMATCH_N"),
        "CURRENT_CAP_INTERFERENCE_N": pres.get("CURRENT_CAP_INTERFERENCE_N"),
        "CURRENT_SAME_SYMBOL_INTERFERENCE_N": pres.get("CURRENT_SAME_SYMBOL_INTERFERENCE_N"),
        "RISK_ARM_PASS": gate.get("AUGMENT_OVERLAY_PASS"),
        "gates": gate.get("gates"),
        "preservation": pres,
        "overlay_daily": list(ov_pack.get("daily") or []),
        "augment_daily": list(aug_pack.get("daily") or []),
    }
    rec.update(conc)
    return rec


def _prior_joint_mismatch(proba_by_rep: dict[str, dict[str, Any]], specs: list[dict[str, Any]]) -> int:
    n = 0
    for s in specs:
        rid = str(s.get("representation_id") or "")
        sid = str(s.get("spec_id") or "").replace("|", "_")
        old_path = EXP_CACHE / f"{sid}_oof_scores.json"
        if not old_path.is_file():
            continue
        old = _load(old_path).get("scores") or {}
        new = proba_by_rep.get(rid) or {}
        keys = set(old) | set(new)
        for k in keys:
            ov = _f(old.get(k)) if not isinstance(old.get(k), dict) else _f((old.get(k) or {}).get("JOINT_SCORE"))
            nv_raw = new.get(k)
            if isinstance(nv_raw, dict):
                nv = _f(nv_raw.get("JOINT_SCORE"))
            else:
                nv = _f(nv_raw)
            if ov is None and nv is None:
                continue
            if ov is None or nv is None or abs(float(ov) - float(nv)) > 1e-12:
                n += 1
    return n


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        return _integrity("STOP. Runtime WAIT_SEC drifted from 1.0.")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        return _integrity("STOP. DEV_WAIT_SEC drifted from 5.0.")
    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        return _integrity("STOP. FEATURE_ORDER drift.")
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        return _integrity("STOP. rank_pass_gate drift.")
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        return _integrity("STOP. C14 identity mismatch.")
    if int(AUGMENT_MAX_PER_COHORT) != 1:
        return _integrity("STOP. AUGMENT_MAX_PER_COHORT drifted.")
    if CURRENT_PRIORITY is not True:
        return _integrity("STOP. CURRENT_PRIORITY drifted.")
    if FROZEN_ARM != ARM_X14_RF or FROZEN_ARM != "EXPANDED_X14_RF":
        return _integrity("STOP. Frozen arm drifted.")
    if TARGET != "PROFITABLE_FILL_CLASS":
        return _integrity("STOP. Target drifted.")
    if len(X14_BUNDLE) != 6:
        return _integrity("STOP. X14 bundle drifted.")
    if int(RISK_ARM_N) != 3 or list(RISK_ARM_IDS) != [R1, R2, R3]:
        return _integrity("STOP. Risk arm set drifted.")
    if TRUE_OOS is not False or int(NEW_FORWARD_N) != 0:
        return _integrity("STOP. TRUE_OOS / NEW_FORWARD_N drifted.")
    if int(SUBMIT_N) != 0 or int(CANCEL_N) != 0 or int(LIVE_ORDER_N) != 0:
        return _integrity("STOP. submit/cancel/live drifted.")
    if PAPER_OPERATED is not False:
        return _integrity("STOP. Paper operated flag drifted.")

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)
    print(
        "AM CURRENT-preserving EXPANDED_X14_RF risk integration. 3 frozen arms. 18-day OOF. No score search.",
        flush=True,
    )

    if LABELED_X14.is_file():
        rows = list(_load(LABELED_X14).get("rows") or [])
        rows_path = LABELED_X14
        future_n = 0
        x14_complete = sum(1 for r in rows if all(_f(r.get(k)) is not None for k in X14_BUNDLE))
    else:
        harvest, harvest_future = load_harvest(FEAT_DIR, list(ELIGIBLE_DAYS))
        if harvest_future < 0 or not harvest:
            return _integrity("STOP. Frozen X14 harvest cache missing.")
        if not LABELED.is_file():
            return _integrity("STOP. labeled_am.json missing.")
        labeled = json.loads(LABELED.read_text(encoding="utf-8"))
        raw_rows = list(labeled.get("rows") or [])
        joined = join_x14(raw_rows, harvest)
        if int(joined.get("JOIN_MISS_N") or 0) != 0:
            return _integrity("STOP. X14 harvest join miss.", extra={"JOIN_MISS_N": joined.get("JOIN_MISS_N")})
        rows = attach_target(list(joined.get("rows") or []))
        CACHE.mkdir(parents=True, exist_ok=True)
        rows_path = CACHE / "labeled_am_x14.json"
        _save_json(rows_path, {"session": "AM", "target": TARGET, "rows": rows})
        future_n = int(joined.get("FUTURE_FEATURE_USE_N") or 0)
        x14_complete = int(joined.get("X14_COMPLETE_N") or 0)

    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled AM.", extra={"PM_ROWS_USED_N": pm_n})

    class_n = {
        CLASS_WIN: sum(1 for r in rows if r.get(TARGET) == CLASS_WIN),
        CLASS_LOSS: sum(1 for r in rows if r.get(TARGET) == CLASS_LOSS),
        CLASS_NEUTRAL: sum(1 for r in rows if r.get(TARGET) == CLASS_NEUTRAL),
    }
    print(
        f"classes WIN={class_n[CLASS_WIN]} LOSS={class_n[CLASS_LOSS]} NEUTRAL={class_n[CLASS_NEUTRAL]} "
        f"x14_complete={x14_complete} n={len(rows)}",
        flush=True,
    )

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
    print("parity_pop", parity_pop.get("ok"), "replay_ok", replay_ok, flush=True)
    if not replay_ok:
        return _integrity("STOP. Frozen CURRENT parity did not reproduce.", extra={"parity_pop": parity_pop})
    print("BASE_PARITY true", flush=True)

    specs = arm_spec_grid(ARM_X14_RF)
    if len(specs) != int(REPRESENTATION_N):
        return _integrity("STOP. Representation count drifted.", extra={"n": len(specs)}, base_parity=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = []
    for s in specs:
        sid = str(s.get("spec_id"))
        jobs.append(
            {
                "spec_id": sid,
                "spec": s,
                "days": list(ELIGIBLE_DAYS),
                "rows_path": str(rows_path),
                "cache_path": str(CACHE / f"{sid.replace('|', '_')}_oof_proba.json"),
                "store_proba": True,
            }
        )
    print(f"oof jobs={len(jobs)} workers={min(MAX_WORKERS, len(jobs))} store_proba=true", flush=True)
    got = _pool(process_oof_scores, jobs, "OOF", "spec_id")
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(jobs):
        return _integrity(
            "STOP. OOF probability replay failed.",
            extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail]},
            base_parity=True,
        )

    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_FEATURE_USE_N": int(future_n or 0),
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "FEATURE_SUBSET_SEARCH_N": 0,
        "MODEL_SEARCH_N": 0,
        "HYPERPARAMETER_SEARCH_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "PROBABILITY_THRESHOLD_SEARCH_N": 0,
        "CONSENSUS_THRESHOLD_SEARCH_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "EXIT_CHANGE_N": 0,
        "CURRENT_POLICY_CHANGE_N": 0,
        "ORACLE_SELECTION_USE_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "REPRESENTATION_SELECTION_N": 0,
        "POSTHOC_MODEL_ADDITION_N": 0,
        "C14_CHANGED": C14_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "PRIOR_POLICY_REPLAY_OK": False,
        "PRIOR_JOINT_MISMATCH_N": 0,
    }
    proba_by_rep: dict[str, dict[str, Any]] = {}
    for b in got:
        aid = str(b.get("architecture_id") or "")
        if aid != ARM_X14_RF:
            leak["POSTHOC_MODEL_ADDITION_N"] += 1
        ig = b.get("integrity") or {}
        leak["OUTER_HELDOUT_FIT_LEAK_N"] += int(ig.get("HELDOUT_FIT_LEAK_N") or 0)
        leak["PM_ROWS_USED_N"] = max(int(leak["PM_ROWS_USED_N"] or 0), int(ig.get("PM_ROWS_USED_N") or 0))
        leak["TARGET_CONTAMINATION_N"] = max(
            int(leak["TARGET_CONTAMINATION_N"] or 0), int(ig.get("TARGET_CONTAMINATION_N") or 0)
        )
        leak["HYPERPARAMETER_SEARCH_N"] += int(ig.get("HYPERPARAMETER_SEARCH_N") or 0)
        if int(b.get("fold_n") or 0) != 18:
            leak["OUTER_HELDOUT_FIT_LEAK_N"] += 1
        scores = dict(b.get("scores") or {})
        sample = next(iter(scores.values()), None)
        if not (isinstance(sample, dict) and "P_WIN" in (sample or {})):
            return _integrity("STOP. OOF cache missing class probabilities.", extra={"spec_id": b.get("spec_id")}, base_parity=True)
        proba_by_rep[str(b.get("representation_id"))] = scores
    if leak["POSTHOC_MODEL_ADDITION_N"]:
        return _integrity("STOP. Post-hoc model addition.", extra={"integrity": leak}, base_parity=True)
    if len(proba_by_rep) != int(REPRESENTATION_N):
        return _integrity("STOP. Missing representation OOF probabilities.", extra={"n": len(proba_by_rep)}, base_parity=True)

    leak["PRIOR_JOINT_MISMATCH_N"] = _prior_joint_mismatch(proba_by_rep, specs)
    print(f"PRIOR_JOINT_MISMATCH_N={leak['PRIOR_JOINT_MISMATCH_N']}", flush=True)

    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak}, base_parity=True)

    baseline = overlay_replay(rows, include_augment=False)
    base_pack = economic_pack(list(baseline.get("trades") or []), list(ELIGIBLE_DAYS))
    if (
        int(base_pack.get("trade_count") or -1) != int(V2_CURRENT_TRADE_N)
        or not _close(base_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        or not _close(_pf_num(base_pack.get("profit_factor")), V2_CURRENT_PF, 1e-12)
        or not _close(base_pack.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
    ):
        return _integrity(
            "STOP. CURRENT-first replay drifted from frozen CURRENT.",
            extra={"base": _slim(base_pack)},
            base_parity=True,
        )

    rep_ids = [str(s.get("representation_id")) for s in specs]
    joint_by_rep = joint_map(proba_by_rep)
    prior_tagged = attach_ensemble(rows, joint_by_rep, rep_ids)
    win_tagged = attach_win_dominant(rows, proba_by_rep, rep_ids)

    prior_rec = _eval_arm(
        prior_tagged,
        baseline,
        base_pack,
        "PRIOR_EXPANDED_X14_RF",
        augment_rank="utility",
        core_confirmed=False,
    )
    prior_ok = (
        int(prior_rec.get("AUGMENT_TRADE_N") or -1) == int(PRIOR_AUGMENT_TRADE_N)
        and _close(prior_rec.get("AUGMENT_NET_PNL"), PRIOR_AUGMENT_NET, PARITY_ABS_TOL)
        and _close(prior_rec.get("OVERLAY_NET_PNL"), PRIOR_OVERLAY_NET, PARITY_ABS_TOL)
        and _close(_pf_num(prior_rec.get("OVERLAY_PF")), PRIOR_OVERLAY_PF, 1e-12)
    )
    leak["PRIOR_POLICY_REPLAY_OK"] = bool(prior_ok)
    print(
        f"prior replay ok={prior_ok} aug_n={prior_rec.get('AUGMENT_TRADE_N')} "
        f"aug_net={prior_rec.get('AUGMENT_NET_PNL')} ov_net={prior_rec.get('OVERLAY_NET_PNL')} "
        f"ov_pf={prior_rec.get('OVERLAY_PF')}",
        flush=True,
    )
    if not prior_ok:
        return _integrity(
            "STOP. Prior EXPANDED_X14_RF policy did not reproduce from regenerated probabilities.",
            extra={"integrity": leak, "prior": {k: v for k, v in prior_rec.items() if k not in ("overlay_daily", "augment_daily", "preservation", "gates")}},
            base_parity=True,
        )

    arm_cfgs = (
        (R1, "win_margin", False, win_tagged),
        (R2, "utility", True, prior_tagged),
        (R3, "win_margin", True, win_tagged),
    )
    arm_rows: list[dict[str, Any]] = []
    for arm_id, rank, core, tagged in arm_cfgs:
        rec = _eval_arm(tagged, baseline, base_pack, arm_id, augment_rank=rank, core_confirmed=core)
        arm_rows.append(rec)
        print(
            f"arm {arm_id} overlay_net={rec.get('OVERLAY_NET_PNL')} pf={rec.get('OVERLAY_PF')} "
            f"dd={rec.get('OVERLAY_MAX_DD')} paired_med={rec.get('PAIRED_MEDIAN_DAILY_DELTA')} "
            f"pos={rec.get('PAIRED_POS_DAYS')} neg={rec.get('PAIRED_NEG_DAYS')} "
            f"ex_best={rec.get('EX_BEST_DAY_PNL_DELTA')} ex_top3={rec.get('EX_TOP3_DAYS_PNL_DELTA')} "
            f"pres={rec.get('CURRENT_PRESERVATION_PASS')} pass={rec.get('RISK_ARM_PASS')}",
            flush=True,
        )

    preservation_all = all(bool(a.get("CURRENT_PRESERVATION_PASS")) for a in arm_rows)
    pass_arms = [a for a in arm_rows if bool(a.get("RISK_ARM_PASS"))]
    best_pass = pick_best(pass_arms) if pass_arms else None
    current_net = float(base_pack.get("net_pnl_yen_100") or 0.0)
    current_pf = _pf_num(base_pack.get("profit_factor"))
    current_dd = float(base_pack.get("max_drawdown_yen_100") or 0.0)
    decision = decide_case(
        integrity_ok=True,
        arms=arm_rows,
        current_net=current_net,
        current_pf=current_pf,
        current_dd=current_dd,
    )
    by = {str(a.get("risk_arm_id")): a for a in arm_rows}
    required = {
        "BASE_PARITY": True,
        "RISK_ARM_N": RISK_ARM_N,
        **_req_arm(by.get(R1), "R1"),
        **_req_arm(by.get(R2), "R2"),
        **_req_arm(by.get(R3), "R3"),
        "PASS_ARM_N": len(pass_arms),
        "BEST_PASS_ARM": (best_pass or {}).get("risk_arm_id") if pass_arms else None,
        "BEST_PASS_NET": (best_pass or {}).get("OVERLAY_NET_PNL") if pass_arms else None,
        "BEST_PASS_PF": (best_pass or {}).get("OVERLAY_PF") if pass_arms else None,
        "BEST_PASS_MAX_DD": (best_pass or {}).get("OVERLAY_MAX_DD") if pass_arms else None,
        "BEST_PASS_PAIRED_MEDIAN": (best_pass or {}).get("PAIRED_MEDIAN_DAILY_DELTA") if pass_arms else None,
        "BEST_PASS_EX_TOP3": (best_pass or {}).get("EX_TOP3_DAYS_PNL_DELTA") if pass_arms else None,
        "CURRENT_PRESERVATION_PASS_ALL": bool(preservation_all),
        "PRIMARY_RISK_MECHANISM": decision.get("PRIMARY_RISK_MECHANISM"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "PRECOMMIT_SPEC_SHA256": sha,
        "DEVELOPMENT_CANDIDATE": (best_pass or {}).get("risk_arm_id") if pass_arms else None,
        "CASE": decision.get("CASE"),
    }

    daily_rows = []
    outer_rows = []
    for d in ELIGIBLE_DAYS:
        cday = next((x for x in (base_pack.get("daily") or []) if x.get("date") == d), {})
        rec = {"date": d, "CURRENT": cday.get("pnl_yen_100")}
        orec = {"outer_day": d, "CURRENT_PNL": cday.get("pnl_yen_100")}
        for a in arm_rows:
            ad = next((x for x in (a.get("overlay_daily") or []) if x.get("date") == d), {})
            gd = next((x for x in (a.get("augment_daily") or []) if x.get("date") == d), {})
            rec[str(a.get("risk_arm_id"))] = ad.get("pnl_yen_100")
            rec[f"{a.get('risk_arm_id')}_AUG"] = gd.get("pnl_yen_100")
            orec[f"{a.get('risk_arm_id')}_OVERLAY"] = ad.get("pnl_yen_100")
            orec[f"{a.get('risk_arm_id')}_AUGMENT"] = gd.get("pnl_yen_100")
        daily_rows.append(rec)
        outer_rows.append(orec)

    arm_sheet = []
    conc_sheet = []
    for a in arm_rows:
        arm_sheet.append({k: v for k, v in a.items() if k not in ("overlay_daily", "augment_daily", "preservation", "gates")})
        conc_sheet.append(
            {
                "risk_arm_id": a.get("risk_arm_id"),
                "AUGMENT_NET_PNL": a.get("AUGMENT_NET_PNL"),
                "BEST_AUGMENT_DAY_PNL": a.get("BEST_AUGMENT_DAY_PNL"),
                "BEST_DAY_SHARE_OF_TOTAL_PROFIT": a.get("BEST_DAY_SHARE_OF_TOTAL_PROFIT"),
                "TOP2_DAY_SHARE_OF_TOTAL_PROFIT": a.get("TOP2_DAY_SHARE_OF_TOTAL_PROFIT"),
                "TOTAL_PNL_EX_BEST_AUGMENT_DAY": a.get("TOTAL_PNL_EX_BEST_AUGMENT_DAY"),
                "TOTAL_PNL_EX_TOP3_AUGMENT_DAYS": a.get("TOTAL_PNL_EX_TOP3_AUGMENT_DAYS"),
            }
        )
    extra = {
        "precommit": spec,
        "decision": decision,
        "classes": class_n,
        "x14_complete_n": x14_complete,
        "current": _slim(base_pack),
        "prior_policy": {k: v for k, v in prior_rec.items() if k not in ("overlay_daily", "augment_daily", "preservation", "gates")},
        "arms": arm_sheet,
        "gates": {a.get("risk_arm_id"): a.get("gates") for a in arm_rows},
        "integrity": leak,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "TARGET": TARGET,
        "FROZEN_ARM": FROZEN_ARM,
        "POSITIVE_REP_MIN": POS_REP_MIN,
        "AVAILABLE_REP_MIN": AVAILABLE_REP_MIN,
        "X14_BUNDLE": list(X14_BUNDLE),
        "RISK_ARM_IDS": list(RISK_ARM_IDS),
    }
    sheets = {
        "precommit": kv_rows({**spec, "PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "risk_arms": arm_sheet,
        "concentration": conc_sheet,
        "outer_folds": outer_rows,
        "daily_pnl": daily_rows,
        "integrity": kv_rows(leak),
    }
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    print(f"NEXT {required.get('NEXT')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
