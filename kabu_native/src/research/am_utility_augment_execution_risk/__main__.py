"""Offline CURRENT-preserving utility-eligible / P_FILL-first augment. No Runtime write. No Paper."""
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

from research.am_current_utility_augment import (
    ARCHITECTURE,
    AUGMENT_MAX_PER_COHORT,
    AVAILABLE_REP_MIN,
    CURRENT_PRIORITY,
    ENSEMBLE,
    TARGET,
)
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
from research.am_entry_profit_failure_decomposition_v2.scores import process_oof_scores
from research.am_entry_profit_improvement import (
    ARCH_RIDGE,
    C14_CHANGED,
    C14_ID,
    CANCEL_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    FEATURE_SEARCH_N,
    FILL_ONLY_REPRESENTATION_ID,
    LIVE_ORDER_N,
    MAX_WORKERS,
    NEW_FEATURE_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    REPRESENTATION_N,
    RIDGE_ALPHA,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
    WAIT_SEARCH_N,
)
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.metrics import _pf_num, economic_pack, paired_delta
from research.am_entry_profit_improvement.models import spec_grid
from research.am_entry_profit_improvement.publish import kv_rows
from research.am_utility_augment_execution_risk import (
    ANALYSIS_ID,
    AUGMENT_RANK,
    PRIOR_CANDIDATE_N,
    PRIOR_FILL_N,
    PRIOR_FILL_RATE,
    PRIOR_MAX_DD,
    PRIOR_NEG_DAYS,
    PRIOR_NET_PNL,
    PRIOR_OVERLAY_MAX_DD,
    PRIOR_OVERLAY_NET,
    PRIOR_OVERLAY_PF,
    PRIOR_PF,
    PRIOR_POS_DAYS,
    PRIOR_ZERO_DAYS,
)
from research.am_utility_augment_execution_risk.analyze import (
    coverage_improved,
    decide_case,
    preservation_pass,
    rank_change_table,
)
from research.am_utility_augment_execution_risk.precommit import precommit_spec, print_precommit, spec_sha256
from research.am_utility_augment_execution_risk.publish import OUT, build_markdown, write_artifacts
from research.canonical_entry_performance_rebase.analyze import _f, row_key, session_of
from research.direct_joint_objective.oof import representation_grid
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
SCORE_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_profit_failure_decomposition_v2"
FILL_ONLY_SCORED = (
    NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment" / "AM_F0_CURRENT6_none_scored.json"
)
AM_ROWS_FILL = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_two_stage_development" / "am_rows.json"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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


def _pfill_ok(body: dict[str, Any]) -> bool:
    ig = body.get("integrity") or {}
    return (
        bool(body.get("ok"))
        and str(body.get("representation_id") or "") == FILL_ONLY_REPRESENTATION_ID
        and str(body.get("session") or "") == "AM"
        and int(body.get("outer_folds") or 0) == 18
        and int(ig.get("HELDOUT_FIT_LEAK_N") or 0) == 0
        and bool(body.get("rows"))
        and bool(body.get("FROZEN_OOF_REPLAY"))
        and body.get("NEW_MODEL_CREATED") is False
    )


def _join_pfill(rows: list[dict[str, Any]], scored_rows: list[dict[str, Any]]) -> int:
    by = {row_key(r): _f(r.get("fill_score")) for r in scored_rows}
    miss = 0
    for r in rows:
        k = row_key(r)
        if k not in by:
            miss += 1
            r["fill_score"] = None
            r["_p_fill"] = None
        else:
            r["fill_score"] = by[k]
            r["_p_fill"] = by[k]
    return miss


def _integrity(msg: str, extra: dict | None = None) -> int:
    required = {
        "BASE_PARITY": False,
        "OUTER_FOLD_N": 18,
        "PRIOR_AUGMENT_FILL_RATE": PRIOR_FILL_RATE,
        "NEW_AUGMENT_FILL_RATE": None,
        "DELTA_FILL_RATE": None,
        "NEW_AUGMENT_TRADE_N": None,
        "NEW_AUGMENT_NET_PNL": None,
        "NEW_AUGMENT_PF": None,
        "NEW_AUGMENT_MAX_DD": None,
        "OVERLAY_NET_PNL": None,
        "OVERLAY_PF": None,
        "OVERLAY_MAX_DD": None,
        "DELTA_PNL_VS_CURRENT": None,
        "DELTA_PF_VS_CURRENT": None,
        "DELTA_DD_VS_CURRENT": None,
        "PAIRED_POS_DAYS": None,
        "PAIRED_NEG_DAYS": None,
        "PAIRED_ZERO_DAYS": None,
        "PAIRED_MEDIAN_DAILY_DELTA": None,
        "EX_BEST_DAY_PNL_DELTA": None,
        "EX_TOP3_DAYS_PNL_DELTA": None,
        "RANK_CHANGED_COHORT_N": None,
        "RANK_CHANGED_RATE": None,
        "CURRENT_PRESERVATION_PASS": False,
        "ECONOMIC_GATE_PASS": False,
        "PRIMARY_MECHANISM": "INTEGRITY_OR_CURRENT_PRESERVATION_FAILURE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_UTILITY_FILLABILITY_AUGMENT_INTEGRITY_FAILED",
        "NEXT": "STOP",
        "STOP_REASON": msg,
    }
    decision = decide_case(
        integrity_ok=False,
        preservation_ok=False,
        gate_pass=False,
        coverage_up=False,
        augment_net=0.0,
        overlay_net=0.0,
        current_net=0.0,
    )
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, {"integrity": kv_rows({"STOP_REASON": msg}), "precommit": [{"empty": True}]})
    print(msg, flush=True)
    return 2


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
    if ARCHITECTURE != ARCH_RIDGE:
        return _integrity("STOP. Architecture drifted from RIDGE_UTILITY.")
    if not CURRENT_PRIORITY:
        return _integrity("STOP. CURRENT_PRIORITY drifted.")
    if int(AUGMENT_MAX_PER_COHORT) != 1:
        return _integrity("STOP. AUGMENT_MAX_PER_COHORT drifted.")
    if AUGMENT_RANK != "P_FILL5":
        return _integrity("STOP. AUGMENT_RANK drifted.")

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)
    print(
        "AM CURRENT-preserving utility-eligible P_FILL-first augment. "
        "Eligibility frozen. No P_FILL cutoff. W5 research fill only.",
        flush=True,
    )

    labeled = json.loads(LABELED.read_text(encoding="utf-8"))
    rows = list(labeled.get("rows") or [])
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled_am.json.", extra={"PM_ROWS_USED_N": pm_n})

    fo_body = _load(FILL_ONLY_SCORED)
    pfill_regen_n = 0
    if not _pfill_ok(fo_body):
        from research.am_wait5_stage2_reassessment.replay import replay_frozen_oof

        fill_spec = next(
            (s for s in representation_grid() if str(s.get("representation_id")) == FILL_ONLY_REPRESENTATION_ID),
            None,
        )
        if fill_spec is None:
            return _integrity("STOP. FILL_ONLY representation missing from frozen grid.")
        rows_path = AM_ROWS_FILL if AM_ROWS_FILL.is_file() else LABELED
        print(
            f"P_FILL5 provenance mismatch. Regenerating frozen 17→1 LODO from {rows_path.name}.",
            flush=True,
        )
        fo_body = replay_frozen_oof(
            {
                "spec": fill_spec,
                "representation_id": FILL_ONLY_REPRESENTATION_ID,
                "rows_path": str(rows_path),
                "days": list(ELIGIBLE_DAYS),
            }
        )
        pfill_regen_n = 1
        if not _pfill_ok(fo_body):
            return _integrity(
                "STOP. FILL_ONLY P_FILL5 OOF provenance failed after frozen replay.",
                extra={"pfill": {k: fo_body.get(k) for k in ("ok", "representation_id", "outer_folds", "blocker")}},
            )
    join_miss = _join_pfill(rows, list(fo_body.get("rows") or []))
    if join_miss:
        return _integrity("STOP. FILL_ONLY P_FILL5 join miss.", extra={"JOIN_MISS_N": join_miss})

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

    grid = representation_grid()
    ridge_specs = [s for s in spec_grid(grid) if s.get("architecture_id") == ARCH_RIDGE]
    if len(ridge_specs) != int(REPRESENTATION_N):
        return _integrity("STOP. Ridge representation count drifted.", extra={"n": len(ridge_specs)})
    rep_ids = [str(s.get("representation_id")) for s in ridge_specs]

    jobs = []
    for s in ridge_specs:
        sid = str(s.get("spec_id"))
        jobs.append(
            {
                "spec_id": sid,
                "spec": s,
                "days": list(ELIGIBLE_DAYS),
                "rows_path": str(LABELED),
                "cache_path": str(SCORE_CACHE / f"{sid.replace('|', '_')}_oof_scores.json"),
            }
        )
    print(f"ridge oof jobs={len(jobs)} workers={min(MAX_WORKERS, len(jobs))}", flush=True)
    got = _pool(process_oof_scores, jobs, "RIDGE", "spec_id")
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != int(REPRESENTATION_N):
        return _integrity(
            "STOP. Ridge OOF score replay failed.",
            extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail]},
        )

    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "PFILL_THRESHOLD_SEARCH_N": 0,
        "REPRESENTATION_SELECTION_N": 0,
        "POSTHOC_ARCHITECTURE_CHANGE_N": 0,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "NEW_TARGET_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "EXIT_CHANGE_N": 0,
        "CURRENT_POLICY_CHANGE_N": 0,
        "ORACLE_SELECTION_USE_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "C14_CHANGED": C14_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "PFILL_REGENERATED_N": pfill_regen_n,
    }
    scores_by_rep: dict[str, dict[str, Any]] = {}
    for b in got:
        if str(b.get("architecture_id") or ARCH_RIDGE) != ARCH_RIDGE:
            leak["POSTHOC_ARCHITECTURE_CHANGE_N"] += 1
        ig = b.get("integrity") or {}
        leak["OUTER_HELDOUT_FIT_LEAK_N"] += int(ig.get("HELDOUT_FIT_LEAK_N") or 0)
        leak["PM_ROWS_USED_N"] = max(int(leak["PM_ROWS_USED_N"] or 0), int(ig.get("PM_ROWS_USED_N") or 0))
        leak["TARGET_CONTAMINATION_N"] = max(
            int(leak["TARGET_CONTAMINATION_N"] or 0), int(ig.get("TARGET_CONTAMINATION_N") or 0)
        )
        scores_by_rep[str(b.get("representation_id"))] = dict(b.get("scores") or {})
    if int(leak["OUTER_HELDOUT_FIT_LEAK_N"] or 0) != 0:
        return _integrity("STOP. OUTER_HELDOUT_FIT_LEAK_N != 0.", extra={"integrity": leak})
    if leak["POSTHOC_ARCHITECTURE_CHANGE_N"]:
        return _integrity("STOP. Non-Ridge architecture entered augment.", extra={"integrity": leak})

    tagged = attach_ensemble(rows, scores_by_rep, rep_ids)
    for r in tagged:
        r["_p_fill"] = _f(r.get("fill_score"))

    baseline = overlay_replay(tagged, include_augment=False)
    base_pack = economic_pack(list(baseline.get("trades") or []), list(ELIGIBLE_DAYS))
    if (
        int(base_pack.get("trade_count") or -1) != int(V2_CURRENT_TRADE_N)
        or not _close(base_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
    ):
        return _integrity(
            "STOP. CURRENT-first replay drifted from frozen CURRENT.",
            extra={"base": _slim(base_pack)},
        )

    prior = overlay_replay(tagged, include_augment=True, augment_rank="utility")
    prior_trades = list(prior.get("trades") or [])
    prior_aug_trades = [t for t in prior_trades if str(t.get("arm") or "") == "AUGMENT"]
    prior_ov_pack = economic_pack(prior_trades, list(ELIGIBLE_DAYS))
    prior_aug_pack = economic_pack(prior_aug_trades, list(ELIGIBLE_DAYS))
    prior_adm = [a for a in (prior.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    prior_fills = [f for f in (prior.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    prior_cands = list(prior.get("augment_candidates") or [])
    prior_fill_rate = _arm_fill_rate(len(prior_adm), len(prior_fills))
    prior_ok = (
        int(len(prior_cands)) == int(PRIOR_CANDIDATE_N)
        and int(len(prior_fills)) == int(PRIOR_FILL_N)
        and prior_fill_rate is not None
        and _close(prior_fill_rate, PRIOR_FILL_RATE, 1e-12)
        and _close(prior_aug_pack.get("net_pnl_yen_100"), PRIOR_NET_PNL, PARITY_ABS_TOL)
        and _close(_pf_num(prior_aug_pack.get("profit_factor")), PRIOR_PF, 1e-12)
        and _close(prior_aug_pack.get("max_drawdown_yen_100"), PRIOR_MAX_DD, PARITY_ABS_TOL)
        and int(prior_aug_pack.get("positive_day_n") or -1) == int(PRIOR_POS_DAYS)
        and int(prior_aug_pack.get("negative_day_n") or -1) == int(PRIOR_NEG_DAYS)
        and int(prior_aug_pack.get("zero_day_n") or -1) == int(PRIOR_ZERO_DAYS)
        and _close(prior_ov_pack.get("net_pnl_yen_100"), PRIOR_OVERLAY_NET, PARITY_ABS_TOL)
        and _close(_pf_num(prior_ov_pack.get("profit_factor")), PRIOR_OVERLAY_PF, 1e-12)
        and _close(prior_ov_pack.get("max_drawdown_yen_100"), PRIOR_OVERLAY_MAX_DD, PARITY_ABS_TOL)
    )
    print(
        f"prior_augment_parity {prior_ok} cand={len(prior_cands)} fill={len(prior_fills)} "
        f"rate={prior_fill_rate} net={prior_aug_pack.get('net_pnl_yen_100')} "
        f"overlay_net={prior_ov_pack.get('net_pnl_yen_100')}",
        flush=True,
    )
    if not prior_ok:
        return _integrity(
            "STOP. Prior utility-ranked augment did not reproduce.",
            extra={
                "prior_observed": {
                    "CANDIDATE_N": len(prior_cands),
                    "FILL_N": len(prior_fills),
                    "FILL_RATE": prior_fill_rate,
                    "NET_PNL": prior_aug_pack.get("net_pnl_yen_100"),
                    "PF": prior_aug_pack.get("profit_factor"),
                    "MAX_DD": prior_aug_pack.get("max_drawdown_yen_100"),
                    "POS_DAYS": prior_aug_pack.get("positive_day_n"),
                    "NEG_DAYS": prior_aug_pack.get("negative_day_n"),
                    "ZERO_DAYS": prior_aug_pack.get("zero_day_n"),
                    "OVERLAY_NET": prior_ov_pack.get("net_pnl_yen_100"),
                    "OVERLAY_PF": prior_ov_pack.get("profit_factor"),
                    "OVERLAY_MAX_DD": prior_ov_pack.get("max_drawdown_yen_100"),
                }
            },
        )

    overlay = overlay_replay(tagged, include_augment=True, augment_rank="p_fill")
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
    aug_admitted_n = len(aug_adm)
    aug_fill_n = len(aug_fill)
    aug_expired_n = max(aug_admitted_n - aug_fill_n, 0)
    new_fill_rate = _arm_fill_rate(aug_admitted_n, aug_fill_n)
    delta_fill_rate = _d(new_fill_rate, PRIOR_FILL_RATE)

    integrity_ok = all(
        int(leak.get(k) or 0) == 0
        for k in (
            "OUTER_HELDOUT_FIT_LEAK_N",
            "FUTURE_FEATURE_USE_N",
            "TARGET_CONTAMINATION_N",
            "PM_ROWS_USED_N",
            "SCORE_THRESHOLD_SEARCH_N",
            "PFILL_THRESHOLD_SEARCH_N",
            "REPRESENTATION_SELECTION_N",
            "POSTHOC_ARCHITECTURE_CHANGE_N",
            "NEW_FEATURE_N",
            "NEW_TARGET_N",
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

    gate = overlay_gate(
        ov_pack,
        base_pack,
        paired,
        preservation_pass=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        integrity_ok=True,
    )
    cov_up = coverage_improved(
        new_rate=new_fill_rate,
        prior_rate=PRIOR_FILL_RATE,
        new_fill_n=aug_fill_n,
        prior_fill_n=PRIOR_FILL_N,
        new_trade_n=int(aug_pack.get("trade_count") or 0),
        prior_trade_n=int(prior_aug_pack.get("trade_count") or PRIOR_FILL_N),
    )
    decision = decide_case(
        integrity_ok=True,
        preservation_ok=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        gate_pass=bool(gate.get("AUGMENT_OVERLAY_PASS")),
        coverage_up=bool(cov_up),
        augment_net=float(aug_pack.get("net_pnl_yen_100") or 0.0),
        overlay_net=float(ov_pack.get("net_pnl_yen_100") or 0.0),
        current_net=float(base_pack.get("net_pnl_yen_100") or 0.0),
    )
    if not pres.get("CURRENT_PRESERVATION_PASS"):
        return _integrity(
            "STOP. CURRENT preservation failed.",
            extra={"preservation": pres, "integrity": leak},
        )

    rank = rank_change_table(prior_cands, candidates)
    old_st = rank.get("OLD_ONLY_STATS") or {}
    new_st = rank.get("NEW_ONLY_STATS") or {}

    daily_rows = []
    outer_rows = []
    aug_daily = {r["date"]: r for r in (aug_pack.get("daily") or [])}
    for d in ELIGIBLE_DAYS:
        cday = next((x for x in (base_pack.get("daily") or []) if x.get("date") == d), {})
        oday = next((x for x in (ov_pack.get("daily") or []) if x.get("date") == d), {})
        aday = aug_daily.get(d) or {}
        daily_rows.append(
            {
                "date": d,
                "CURRENT": cday.get("pnl_yen_100"),
                "OVERLAY": oday.get("pnl_yen_100"),
                "AUGMENT": aday.get("pnl_yen_100"),
                "DELTA": float(oday.get("pnl_yen_100") or 0.0) - float(cday.get("pnl_yen_100") or 0.0),
            }
        )
        outer_rows.append(
            {
                "outer_day": d,
                "CURRENT_PNL": cday.get("pnl_yen_100"),
                "OVERLAY_PNL": oday.get("pnl_yen_100"),
                "AUGMENT_PNL": aday.get("pnl_yen_100"),
                "CURRENT_TRADES": cday.get("trade_count"),
                "OVERLAY_TRADES": oday.get("trade_count"),
                "AUGMENT_TRADES": aday.get("trade_count"),
                "AUGMENT_CANDIDATES": sum(1 for c in candidates if str(c.get("date")) == str(d)),
                "AUGMENT_ADMITTED": sum(1 for a in aug_adm if str(a.get("date")) == str(d)),
            }
        )

    required = {
        "BASE_PARITY": True,
        "OUTER_FOLD_N": 18,
        "PRIOR_AUGMENT_FILL_RATE": PRIOR_FILL_RATE,
        "NEW_AUGMENT_FILL_RATE": new_fill_rate,
        "DELTA_FILL_RATE": delta_fill_rate,
        "NEW_AUGMENT_TRADE_N": aug_pack.get("trade_count"),
        "NEW_AUGMENT_NET_PNL": aug_pack.get("net_pnl_yen_100"),
        "NEW_AUGMENT_PF": aug_pack.get("profit_factor"),
        "NEW_AUGMENT_MAX_DD": aug_pack.get("max_drawdown_yen_100"),
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
        "RANK_CHANGED_COHORT_N": rank.get("RANK_CHANGED_COHORT_N"),
        "RANK_CHANGED_RATE": rank.get("RANK_CHANGED_RATE"),
        "CURRENT_PRESERVATION_PASS": pres.get("CURRENT_PRESERVATION_PASS"),
        "ECONOMIC_GATE_PASS": gate.get("AUGMENT_OVERLAY_PASS"),
        "PRIMARY_MECHANISM": decision.get("PRIMARY_MECHANISM"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "PRECOMMIT_SPEC_SHA256": sha,
        "AUGMENT_CANDIDATE_N": len(candidates),
        "AUGMENT_ADMITTED_N": aug_admitted_n,
        "AUGMENT_FILL_N": aug_fill_n,
        "AUGMENT_EXPIRED_N": aug_expired_n,
        "AUGMENT_WIN_N": aug_pack.get("win_n"),
        "AUGMENT_LOSS_N": aug_pack.get("loss_n"),
        "AUGMENT_FLAT_N": aug_pack.get("flat_n"),
        "AUGMENT_POS_DAYS": aug_pack.get("positive_day_n"),
        "AUGMENT_NEG_DAYS": aug_pack.get("negative_day_n"),
        "AUGMENT_ZERO_DAYS": aug_pack.get("zero_day_n"),
        "DELTA_FILL_N_VS_PRIOR_AUGMENT": _d(aug_fill_n, PRIOR_FILL_N),
        "DELTA_FILL_RATE_VS_PRIOR_AUGMENT": delta_fill_rate,
        "DELTA_AUGMENT_PNL": _d(aug_pack.get("net_pnl_yen_100"), PRIOR_NET_PNL),
        "DELTA_AUGMENT_PF": _d(_pf_num(aug_pack.get("profit_factor")), PRIOR_PF),
        "OVERLAY_TRADE_N": ov_pack.get("trade_count"),
        "CASE": decision.get("CASE"),
    }
    extra = {
        "precommit": spec,
        "decision": decision,
        "gates": gate.get("gates"),
        "preservation": pres,
        "current": _slim(base_pack),
        "overlay": _slim(ov_pack),
        "augment": {
            **_slim(aug_pack),
            "AUGMENT_CANDIDATE_N": len(candidates),
            "AUGMENT_ADMITTED_N": aug_admitted_n,
            "AUGMENT_FILL_N": aug_fill_n,
            "AUGMENT_EXPIRED_N": aug_expired_n,
            "AUGMENT_FILL_RATE": new_fill_rate,
        },
        "prior_augment": {
            "CANDIDATE_N": len(prior_cands),
            "FILL_N": len(prior_fills),
            "FILL_RATE": prior_fill_rate,
            "NET_PNL": prior_aug_pack.get("net_pnl_yen_100"),
            "PF": prior_aug_pack.get("profit_factor"),
            "MAX_DD": prior_aug_pack.get("max_drawdown_yen_100"),
        },
        "rank_change": {
            "RANK_CHANGED_COHORT_N": rank.get("RANK_CHANGED_COHORT_N"),
            "RANK_CHANGED_RATE": rank.get("RANK_CHANGED_RATE"),
            "COHORT_N": rank.get("COHORT_N"),
            "OLD_ONLY": old_st,
            "NEW_ONLY": new_st,
        },
        "integrity": leak,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "RIDGE_ALPHA": RIDGE_ALPHA,
        "ENSEMBLE": ENSEMBLE,
        "AUGMENT_RANK": AUGMENT_RANK,
        "POSITIVE_REP_MIN": POS_REP_MIN,
        "AVAILABLE_REP_MIN": AVAILABLE_REP_MIN,
        "TARGET": TARGET,
        "FILL_ONLY_REPRESENTATION_ID": FILL_ONLY_REPRESENTATION_ID,
        "FILL_ONLY_OUTER_FOLDS": fo_body.get("outer_folds"),
    }
    mech_rows = [
        {"side": "OLD_ONLY", **old_st},
        {"side": "NEW_ONLY", **new_st},
        {
            "side": "COMPARE",
            "RANK_CHANGED_COHORT_N": rank.get("RANK_CHANGED_COHORT_N"),
            "RANK_CHANGED_RATE": rank.get("RANK_CHANGED_RATE"),
            "COHORT_N": rank.get("COHORT_N"),
            "COVERAGE_IMPROVED": cov_up,
        },
    ]
    sheets = {
        "precommit": kv_rows({**spec, "PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "outer_folds": outer_rows,
        "rank_change": rank.get("rows") or [{"empty": True}],
        "augment_candidates": candidates or [{"empty": True}],
        "augment_trades": aug_trades or [{"empty": True}],
        "daily_pnl": daily_rows,
        "mechanism": mech_rows,
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
