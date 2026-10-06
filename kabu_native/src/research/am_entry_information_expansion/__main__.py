"""Offline CURRENT-preserving profitable-fill information expansion. No Runtime write. No Paper."""
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
    ANALYSIS_ID,
    ARM_BASE_LOGIT,
    ARM_BASE_RF,
    ARM_IDS,
    ARM_N,
    ARM_X14_LOGIT,
    ARM_X14_RF,
    AUGMENT_MAX_PER_COHORT,
    AVAILABLE_REP_MIN,
    CLASS_LOSS,
    CLASS_NEUTRAL,
    CLASS_WIN,
    MAX_WORKERS,
    TARGET,
    X14_BUNDLE,
)
from research.am_entry_information_expansion.analyze import decide_case, expansion_delta, pick_best, preservation_pass
from research.am_entry_information_expansion.features import join_x14, load_harvest
from research.am_entry_information_expansion.labels import attach_target
from research.am_entry_information_expansion.oof import arm_spec_grid, process_oof_scores
from research.am_entry_information_expansion.precommit import precommit_spec, print_precommit, spec_sha256
from research.am_entry_information_expansion.publish import OUT, build_markdown, write_artifacts
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
from research.canonical_entry_performance_rebase.analyze import session_of
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
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_information_expansion"
LABELED_X14 = CACHE / "labeled_am_x14.json"


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


def _d(a: Any, b: Any) -> Any:
    try:
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


def _integrity(msg: str, extra: dict | None = None) -> int:
    required = {
        "BASE_PARITY": False,
        "ARM_N": ARM_N,
        "OUTER_FOLD_N": 18,
        "BASE_LOGIT_NET": None,
        "BASE_RF_NET": None,
        "X14_LOGIT_NET": None,
        "X14_RF_NET": None,
        "BASE_LOGIT_PF": None,
        "BASE_RF_PF": None,
        "X14_LOGIT_PF": None,
        "X14_RF_PF": None,
        "BEST_ARM": None,
        "BEST_ARM_NET": None,
        "BEST_ARM_PF": None,
        "BEST_ARM_MAX_DD": None,
        "BEST_ARM_PAIRED_MEDIAN": None,
        "BEST_ARM_EX_TOP3": None,
        "PASS_ARM_N": 0,
        "CURRENT_PRESERVATION_PASS_ALL": False,
        "X14_INFORMATION_INCREMENTAL": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_ENTRY_INFORMATION_EXPANSION_INTEGRITY_FAILED",
        "NEXT": "STOP",
        "STOP_REASON": msg,
    }
    decision = decide_case(
        integrity_ok=False,
        preservation_all=False,
        arms=[],
        current_net=0.0,
        current_pf=0.0,
    )
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
) -> dict[str, Any]:
    overlay = overlay_replay(tagged, include_augment=True, augment_rank="utility")
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
    return {
        "architecture_id": arm_id,
        "AUGMENT_CANDIDATE_N": len(candidates),
        "AUGMENT_ADMITTED_N": len(aug_adm),
        "AUGMENT_FILL_N": len(aug_fill),
        "AUGMENT_EXPIRED_N": max(len(aug_adm) - len(aug_fill), 0),
        "AUGMENT_FILL_RATE": _arm_fill_rate(len(aug_adm), len(aug_fill)),
        "AUGMENT_TRADE_N": aug_pack.get("trade_count"),
        "AUGMENT_NET_PNL": aug_pack.get("net_pnl_yen_100"),
        "AUGMENT_PF": aug_pack.get("profit_factor"),
        "AUGMENT_MAX_DD": aug_pack.get("max_drawdown_yen_100"),
        "OVERLAY_TRADE_N": ov_pack.get("trade_count"),
        "OVERLAY_NET_PNL": ov_pack.get("net_pnl_yen_100"),
        "OVERLAY_PF": ov_pack.get("profit_factor"),
        "OVERLAY_MAX_DD": ov_pack.get("max_drawdown_yen_100"),
        "DELTA_PNL_VS_CURRENT": _d(ov_pack.get("net_pnl_yen_100"), base_pack.get("net_pnl_yen_100")),
        "DELTA_PF_VS_CURRENT": _d(_pf_num(ov_pack.get("profit_factor")), _pf_num(base_pack.get("profit_factor"))),
        "DELTA_DD_VS_CURRENT": _d(ov_pack.get("max_drawdown_yen_100"), base_pack.get("max_drawdown_yen_100")),
        "PAIRED_POS_DAYS": paired.get("PAIRED_POS_DAYS"),
        "PAIRED_NEG_DAYS": paired.get("PAIRED_NEG_DAYS"),
        "PAIRED_ZERO_DAYS": paired.get("PAIRED_ZERO_DAYS"),
        "PAIRED_MEDIAN_DAILY_DELTA": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "EX_BEST_DAY_PNL_DELTA": paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3_DAYS_PNL_DELTA": paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        "CURRENT_PRESERVATION_PASS": pres.get("CURRENT_PRESERVATION_PASS"),
        "ARM_PASS": gate.get("AUGMENT_OVERLAY_PASS"),
        "gates": gate.get("gates"),
        "preservation": pres,
        "overlay_daily": list(ov_pack.get("daily") or []),
        "augment_daily": list(aug_pack.get("daily") or []),
    }


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
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
    if not LABELED.is_file():
        return _integrity("STOP. labeled_am.json missing.")
    if int(AUGMENT_MAX_PER_COHORT) != 1:
        return _integrity("STOP. AUGMENT_MAX_PER_COHORT drifted.")
    if len(X14_BUNDLE) != 6:
        return _integrity("STOP. X14 bundle drifted.")

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)
    print(
        "AM CURRENT-preserving profitable-fill classification. 4 arms. 18-day OOF. W5 research fill only.",
        flush=True,
    )

    labeled = json.loads(LABELED.read_text(encoding="utf-8"))
    rows = list(labeled.get("rows") or [])
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled_am.json.", extra={"PM_ROWS_USED_N": pm_n})

    harvest, _day_future = load_harvest(FEAT_DIR, list(ELIGIBLE_DAYS))
    if _day_future < 0 or not harvest:
        return _integrity("STOP. Frozen X14 harvest cache missing.")
    joined = join_x14(rows, harvest)
    if int(joined.get("JOIN_MISS_N") or 0) != 0:
        return _integrity("STOP. X14 harvest join miss.", extra={"JOIN_MISS_N": joined.get("JOIN_MISS_N")})
    rows = attach_target(list(joined.get("rows") or []))
    class_n = {
        CLASS_WIN: sum(1 for r in rows if r.get(TARGET) == CLASS_WIN),
        CLASS_LOSS: sum(1 for r in rows if r.get(TARGET) == CLASS_LOSS),
        CLASS_NEUTRAL: sum(1 for r in rows if r.get(TARGET) == CLASS_NEUTRAL),
    }
    print(
        f"classes WIN={class_n[CLASS_WIN]} LOSS={class_n[CLASS_LOSS]} NEUTRAL={class_n[CLASS_NEUTRAL]} "
        f"x14_complete={joined.get('X14_COMPLETE_N')}",
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

    CACHE.mkdir(parents=True, exist_ok=True)
    _save_json(LABELED_X14, {"session": "AM", "target": TARGET, "rows": rows})

    jobs = []
    for arm_id in ARM_IDS:
        specs = arm_spec_grid(arm_id)
        if len(specs) != int(REPRESENTATION_N):
            return _integrity("STOP. Representation count drifted.", extra={"arm": arm_id, "n": len(specs)})
        for s in specs:
            sid = str(s.get("spec_id"))
            jobs.append(
                {
                    "spec_id": sid,
                    "spec": s,
                    "days": list(ELIGIBLE_DAYS),
                    "rows_path": str(LABELED_X14),
                    "cache_path": str(CACHE / f"{sid.replace('|', '_')}_oof_scores.json"),
                }
            )
    print(f"oof jobs={len(jobs)} workers={min(MAX_WORKERS, len(jobs))}", flush=True)
    got = _pool(process_oof_scores, jobs, "OOF", "spec_id")
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(jobs):
        return _integrity(
            "STOP. OOF score replay failed.",
            extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail]},
        )

    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_FEATURE_USE_N": int(joined.get("FUTURE_FEATURE_USE_N") or 0),
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "FEATURE_SUBSET_SEARCH_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "PFILL_USE_N": 0,
        "REPRESENTATION_SELECTION_N": 0,
        "POSTHOC_MODEL_ADDITION_N": 0,
        "HYPERPARAMETER_SEARCH_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "EXIT_CHANGE_N": 0,
        "CURRENT_POLICY_CHANGE_N": 0,
        "ORACLE_SELECTION_USE_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "NEW_TARGET_N": 0,
        "C14_CHANGED": C14_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
    }
    by_arm: dict[str, dict[str, dict[str, Any]]] = {a: {} for a in ARM_IDS}
    allowed_arms = set(ARM_IDS)
    for b in got:
        aid = str(b.get("architecture_id") or "")
        if aid not in allowed_arms:
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
        by_arm.setdefault(aid, {})[str(b.get("representation_id"))] = dict(b.get("scores") or {})
    if leak["POSTHOC_MODEL_ADDITION_N"]:
        return _integrity("STOP. Post-hoc model addition.", extra={"integrity": leak})
    integrity_ok = all(
        int(leak.get(k) or 0) == 0
        for k in (
            "OUTER_HELDOUT_FIT_LEAK_N",
            "FUTURE_FEATURE_USE_N",
            "TARGET_CONTAMINATION_N",
            "PM_ROWS_USED_N",
            "FEATURE_SEARCH_N",
            "FEATURE_SUBSET_SEARCH_N",
            "SCORE_THRESHOLD_SEARCH_N",
            "PFILL_USE_N",
            "REPRESENTATION_SELECTION_N",
            "POSTHOC_MODEL_ADDITION_N",
            "HYPERPARAMETER_SEARCH_N",
            "WAIT_SEARCH_N",
            "EXIT_CHANGE_N",
            "CURRENT_POLICY_CHANGE_N",
            "ORACLE_SELECTION_USE_N",
            "SUBMIT_N",
            "CANCEL_N",
            "LIVE_ORDER_N",
        )
    )
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak})

    baseline = overlay_replay(rows, include_augment=False)
    base_pack = economic_pack(list(baseline.get("trades") or []), list(ELIGIBLE_DAYS))
    if (
        int(base_pack.get("trade_count") or -1) != int(V2_CURRENT_TRADE_N)
        or not _close(base_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
    ):
        return _integrity("STOP. CURRENT-first replay drifted from frozen CURRENT.", extra={"base": _slim(base_pack)})

    arm_rows = []
    for arm_id in ARM_IDS:
        specs = arm_spec_grid(arm_id)
        rep_ids = [str(s.get("representation_id")) for s in specs]
        scores_by_rep = by_arm.get(arm_id) or {}
        if len(scores_by_rep) != int(REPRESENTATION_N):
            return _integrity("STOP. Missing representation OOF for arm.", extra={"arm": arm_id})
        tagged = attach_ensemble(rows, scores_by_rep, rep_ids)
        for r in tagged:
            r["ENSEMBLE_JOINT_SCORE"] = r.get("AUG_SCORE")
            r["POSITIVE_SCORE_REP_N"] = r.get("POSITIVE_REP_N")
        rec = _eval_arm(tagged, baseline, base_pack, arm_id)
        arm_rows.append(rec)
        print(
            f"arm {arm_id} overlay_net={rec.get('OVERLAY_NET_PNL')} pf={rec.get('OVERLAY_PF')} "
            f"fill_n={rec.get('AUGMENT_FILL_N')} pass={rec.get('ARM_PASS')}",
            flush=True,
        )

    preservation_all = all(bool(a.get("CURRENT_PRESERVATION_PASS")) for a in arm_rows)
    by = {str(a.get("architecture_id")): a for a in arm_rows}
    logit_exp = expansion_delta(by.get(ARM_X14_LOGIT), by.get(ARM_BASE_LOGIT))
    rf_exp = expansion_delta(by.get(ARM_X14_RF), by.get(ARM_BASE_RF))
    x14_inc = False
    for fam in (logit_exp, rf_exp):
        dnet = fam.get("DELTA_NET_X14_VS_BASE")
        if dnet is not None and float(dnet) > 0:
            x14_inc = True
    pass_arms = [a for a in arm_rows if bool(a.get("ARM_PASS"))]
    best = pick_best(pass_arms) if pass_arms else pick_best(arm_rows)
    decision = decide_case(
        integrity_ok=True,
        preservation_all=bool(preservation_all),
        arms=arm_rows,
        current_net=float(base_pack.get("net_pnl_yen_100") or 0.0),
        current_pf=_pf_num(base_pack.get("profit_factor")),
    )

    required = {
        "BASE_PARITY": True,
        "ARM_N": ARM_N,
        "OUTER_FOLD_N": 18,
        "BASE_LOGIT_NET": (by.get(ARM_BASE_LOGIT) or {}).get("OVERLAY_NET_PNL"),
        "BASE_RF_NET": (by.get(ARM_BASE_RF) or {}).get("OVERLAY_NET_PNL"),
        "X14_LOGIT_NET": (by.get(ARM_X14_LOGIT) or {}).get("OVERLAY_NET_PNL"),
        "X14_RF_NET": (by.get(ARM_X14_RF) or {}).get("OVERLAY_NET_PNL"),
        "BASE_LOGIT_PF": (by.get(ARM_BASE_LOGIT) or {}).get("OVERLAY_PF"),
        "BASE_RF_PF": (by.get(ARM_BASE_RF) or {}).get("OVERLAY_PF"),
        "X14_LOGIT_PF": (by.get(ARM_X14_LOGIT) or {}).get("OVERLAY_PF"),
        "X14_RF_PF": (by.get(ARM_X14_RF) or {}).get("OVERLAY_PF"),
        "BEST_ARM": (best or {}).get("architecture_id"),
        "BEST_ARM_NET": (best or {}).get("OVERLAY_NET_PNL"),
        "BEST_ARM_PF": (best or {}).get("OVERLAY_PF"),
        "BEST_ARM_MAX_DD": (best or {}).get("OVERLAY_MAX_DD"),
        "BEST_ARM_PAIRED_MEDIAN": (best or {}).get("PAIRED_MEDIAN_DAILY_DELTA"),
        "BEST_ARM_EX_TOP3": (best or {}).get("EX_TOP3_DAYS_PNL_DELTA"),
        "PASS_ARM_N": len(pass_arms),
        "CURRENT_PRESERVATION_PASS_ALL": bool(preservation_all),
        "X14_INFORMATION_INCREMENTAL": bool(x14_inc),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "PRECOMMIT_SPEC_SHA256": sha,
        "DEVELOPMENT_CANDIDATE": (best or {}).get("architecture_id") if pass_arms else None,
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
            rec[str(a.get("architecture_id"))] = ad.get("pnl_yen_100")
            rec[f"{a.get('architecture_id')}_AUG"] = gd.get("pnl_yen_100")
            orec[f"{a.get('architecture_id')}_OVERLAY"] = ad.get("pnl_yen_100")
            orec[f"{a.get('architecture_id')}_AUGMENT"] = gd.get("pnl_yen_100")
        daily_rows.append(rec)
        outer_rows.append(orec)

    arm_sheet = []
    for a in arm_rows:
        arm_sheet.append({k: v for k, v in a.items() if k not in ("overlay_daily", "augment_daily", "preservation", "gates")})
    extra = {
        "precommit": spec,
        "decision": decision,
        "classes": class_n,
        "x14_complete_n": joined.get("X14_COMPLETE_N"),
        "current": _slim(base_pack),
        "arms": arm_sheet,
        "gates": {a.get("architecture_id"): a.get("gates") for a in arm_rows},
        "expansion": {"LOGIT": logit_exp, "RF": rf_exp},
        "integrity": leak,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "TARGET": TARGET,
        "POSITIVE_REP_MIN": POS_REP_MIN,
        "AVAILABLE_REP_MIN": AVAILABLE_REP_MIN,
        "X14_BUNDLE": list(X14_BUNDLE),
    }
    sheets = {
        "precommit": kv_rows({**spec, "PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "arms": arm_sheet,
        "expansion": [
            {"family": "LOGIT", **logit_exp},
            {"family": "RF", **rf_exp},
        ],
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
