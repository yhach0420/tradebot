"""Offline CURRENT-preserving Ridge utility augment. No Runtime write. No Paper."""
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
    ANALYSIS_ID,
    ARCHITECTURE,
    AUGMENT_MAX_PER_COHORT,
    AVAILABLE_REP_MIN,
    CURRENT_PRIORITY,
    ENSEMBLE,
    TARGET,
)
from research.am_current_utility_augment.analyze import decide_case, overlay_gate, preservation_audit
from research.am_current_utility_augment.ensemble import attach_ensemble
from research.am_current_utility_augment.overlay import overlay_replay
from research.am_current_utility_augment.precommit import precommit_spec, print_precommit, spec_sha256
from research.am_current_utility_augment.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.am_entry_fixed_spec_oof import (
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
)
from research.am_entry_fixed_spec_oof.oof import evaluate_policy
from research.am_entry_profit_failure_decomposition_v2.diagnose import (
    outcome_class,
    price_bucket,
    price_tercile_cuts,
)
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
    UTILITY_KEY,
    W5_RUNTIME_ADOPTED,
    WAIT_SEARCH_N,
)
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.metrics import _pf_num, economic_pack, paired_delta
from research.am_entry_profit_improvement.models import spec_grid
from research.canonical_entry_performance_rebase.analyze import _f, row_key, session_of
from research.direct_joint_objective.oof import representation_grid
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3.oof import spearman
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


def _integrity(msg: str, extra: dict | None = None) -> int:
    required = {
        "BASE_PARITY": False,
        "PRECOMMIT_SPEC_SHA256": None,
        "OUTER_FOLD_N": 18,
        "AUGMENT_OVERLAY_PASS": False,
        "CURRENT_PRESERVATION_PASS": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_UTILITY_AUGMENT_INTEGRITY_FAILED",
        "NEXT": "STOP",
        "STOP_REASON": msg,
    }
    decision = decide_case(
        integrity_ok=False,
        preservation_pass=False,
        overlay_pass=False,
        augment_net=0.0,
        overlay_net=0.0,
        current_net=0.0,
    )
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, {"integrity": kv_rows({"STOP_REASON": msg}), "precommit": [{"empty": True}]})
    print(msg, flush=True)
    return 2


def _arm_fill_rate(admitted: int, fill_n: int) -> float | None:
    return (float(fill_n) / float(admitted)) if admitted else None


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

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)
    print("AM CURRENT-preserving Ridge utility augment. 9-rep median. 1 per cohort. W5 research fill only.", flush=True)

    labeled = json.loads(LABELED.read_text(encoding="utf-8"))
    rows = list(labeled.get("rows") or [])
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled_am.json.", extra={"PM_ROWS_USED_N": pm_n})

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
        return _integrity("STOP. Ridge OOF score replay failed.", extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail]})

    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "REPRESENTATION_SELECTION_N": 0,
        "POSTHOC_ARCHITECTURE_CHANGE_N": 0,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "NEW_TARGET_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "EXIT_CHANGE_N": 0,
        "RUNTIME_CHANGE_N": 0,
        "PAPER_OPERATION_N": 0,
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

    overlay = overlay_replay(tagged, include_augment=True)
    ov_trades = list(overlay.get("trades") or [])
    aug_trades = [t for t in ov_trades if str(t.get("arm") or "") == "AUGMENT"]
    ov_pack = economic_pack(ov_trades, list(ELIGIBLE_DAYS))
    aug_pack = economic_pack(aug_trades, list(ELIGIBLE_DAYS))
    paired = paired_delta(list(ov_pack.get("daily") or []), list(base_pack.get("daily") or []))
    pres = preservation_audit(baseline, overlay)

    aug_adm = [a for a in (overlay.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    aug_fill = [f for f in (overlay.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    candidates = list(overlay.get("augment_candidates") or [])
    aug_admitted_n = len(aug_adm)
    aug_fill_n = len(aug_fill)
    aug_expired_n = max(aug_admitted_n - aug_fill_n, 0)

    integrity_ok = all(
        int(leak.get(k) or 0) == 0
        for k in (
            "OUTER_HELDOUT_FIT_LEAK_N",
            "FUTURE_FEATURE_USE_N",
            "TARGET_CONTAMINATION_N",
            "PM_ROWS_USED_N",
            "SCORE_THRESHOLD_SEARCH_N",
            "REPRESENTATION_SELECTION_N",
            "POSTHOC_ARCHITECTURE_CHANGE_N",
            "NEW_FEATURE_N",
            "NEW_TARGET_N",
            "WAIT_SEARCH_N",
            "EXIT_CHANGE_N",
            "RUNTIME_CHANGE_N",
            "PAPER_OPERATION_N",
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
    decision = decide_case(
        integrity_ok=True,
        preservation_pass=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        overlay_pass=bool(gate.get("AUGMENT_OVERLAY_PASS")),
        augment_net=float(aug_pack.get("net_pnl_yen_100") or 0.0),
        overlay_net=float(ov_pack.get("net_pnl_yen_100") or 0.0),
        current_net=float(base_pack.get("net_pnl_yen_100") or 0.0),
    )

    # Secondary: chosen candidates + eligible outside rows
    cand_scores = []
    cand_utils = []
    n_pos = n_neg = n_non = n_flat = 0
    for c in candidates:
        sc = _f(c.get("AUG_SCORE"))
        # reconstruct class from tagged row
        rec = next((r for r in tagged if row_key(r) == c.get("row_key")), None)
        if rec is None:
            continue
        oc = outcome_class(rec)
        u = _f(rec.get(UTILITY_KEY))
        if sc is not None and u is not None:
            cand_scores.append(float(sc))
            cand_utils.append(float(u))
        if oc == "POSITIVE_FILL":
            n_pos += 1
        elif oc == "NEGATIVE_FILL":
            n_neg += 1
        elif oc == "NONFILL":
            n_non += 1
        else:
            n_flat += 1
    n_cand = len(candidates)
    outside_pos = outside_n = 0
    elig_scores = []
    elig_utils = []
    for r in tagged:
        if not r.get("_aug_eligible"):
            continue
        outside_n += 1
        oc = outcome_class(r)
        if oc == "POSITIVE_FILL":
            outside_pos += 1
        sc = _f(r.get("AUG_SCORE"))
        u = _f(r.get(UTILITY_KEY))
        if sc is not None and u is not None:
            elig_scores.append(float(sc))
            elig_utils.append(float(u))
    base_rate = (float(outside_pos) / float(outside_n)) if outside_n else None
    top_rate = (float(n_pos) / float(n_cand)) if n_cand else None
    enrich = (top_rate / base_rate) if base_rate and top_rate is not None and base_rate > 1e-12 else None
    cand_sp = spearman(cand_scores, cand_utils) if len(cand_scores) >= 10 else None
    elig_sp = spearman(elig_scores, elig_utils) if len(elig_scores) >= 10 else None

    rep_diag = []
    for rid in rep_ids:
        xs = []
        ys = []
        for r in tagged:
            if not r.get("_aug_eligible"):
                continue
            sc = _f((r.get("rep_preds") or {}).get(rid))
            u = _f(r.get(UTILITY_KEY))
            if sc is None or u is None:
                continue
            xs.append(float(sc))
            ys.append(float(u))
        rep_diag.append(
            {
                "representation_id": rid,
                "SPEARMAN_PRED_VS_UTILITY": spearman(xs, ys) if len(xs) >= 10 else None,
                "n": len(xs),
            }
        )

    cuts = price_tercile_cuts(rows)
    tail_rows = []
    for name in ("LOW", "MID", "HIGH"):
        xs = [t for t in aug_trades if price_bucket(_f(t.get("fill_price")), cuts) == name]
        pack = economic_pack(xs, list(ELIGIBLE_DAYS))
        tail_rows.append(
            {
                "tercile": name,
                "trade_n": pack.get("trade_count"),
                "net_pnl": pack.get("net_pnl_yen_100"),
                "gross_loss": pack.get("gross_loss"),
                "win_rate": pack.get("win_rate"),
            }
        )
    n_285a = sum(1 for t in aug_trades if str(t.get("symbol") or "") == "285A")
    pnl_285a = sum(float(t.get("pnl_yen_100") or 0.0) for t in aug_trades if str(t.get("symbol") or "") == "285A")

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

    def _d(a: Any, b: Any) -> Any:
        try:
            return float(a) - float(b)
        except (TypeError, ValueError):
            return None

    required = {
        "BASE_PARITY": True,
        "PRECOMMIT_SPEC_SHA256": sha,
        "OUTER_FOLD_N": 18,
        "AUGMENT_CANDIDATE_N": n_cand,
        "AUGMENT_ADMITTED_N": aug_admitted_n,
        "AUGMENT_FILL_N": aug_fill_n,
        "AUGMENT_TRADE_N": aug_pack.get("trade_count"),
        "AUGMENT_FILL_RATE": _arm_fill_rate(aug_admitted_n, aug_fill_n),
        "AUGMENT_NET_PNL": aug_pack.get("net_pnl_yen_100"),
        "AUGMENT_PF": aug_pack.get("profit_factor"),
        "AUGMENT_MAX_DD": aug_pack.get("max_drawdown_yen_100"),
        "CURRENT_NET_PNL": base_pack.get("net_pnl_yen_100"),
        "CURRENT_PF": base_pack.get("profit_factor"),
        "CURRENT_MAX_DD": base_pack.get("max_drawdown_yen_100"),
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
        "CURRENT_ADMISSION_LOST_N": pres.get("CURRENT_ADMISSION_LOST_N"),
        "CURRENT_FILL_LOST_N": pres.get("CURRENT_FILL_LOST_N"),
        "CURRENT_EXIT_MISMATCH_N": pres.get("CURRENT_EXIT_MISMATCH_N"),
        "CURRENT_PNL_MISMATCH_N": pres.get("CURRENT_PNL_MISMATCH_N"),
        "CURRENT_CAP_INTERFERENCE_N": pres.get("CURRENT_CAP_INTERFERENCE_N"),
        "CURRENT_SAME_SYMBOL_INTERFERENCE_N": pres.get("CURRENT_SAME_SYMBOL_INTERFERENCE_N"),
        "CURRENT_PRESERVATION_PASS": pres.get("CURRENT_PRESERVATION_PASS"),
        "AUGMENT_OVERLAY_PASS": gate.get("AUGMENT_OVERLAY_PASS"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "CASE": decision.get("CASE"),
        "AUGMENT_EXPIRED_N": aug_expired_n,
        "AUGMENT_ELIGIBLE_N": outside_n,
        "OVERLAY_TRADE_N": ov_pack.get("trade_count"),
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
            "AUGMENT_CANDIDATE_N": n_cand,
            "AUGMENT_ADMITTED_N": aug_admitted_n,
            "AUGMENT_FILL_N": aug_fill_n,
            "AUGMENT_EXPIRED_N": aug_expired_n,
            "AUGMENT_FILL_RATE": required.get("AUGMENT_FILL_RATE"),
            "AUGMENT_WIN_N": aug_pack.get("win_n"),
            "AUGMENT_LOSS_N": aug_pack.get("loss_n"),
            "AUGMENT_FLAT_N": aug_pack.get("flat_n"),
            "AUGMENT_POSITIVE_DAY_N": aug_pack.get("positive_day_n"),
            "AUGMENT_NEGATIVE_DAY_N": aug_pack.get("negative_day_n"),
        },
        "secondary": {
            "AUG_SCORE_VS_UTILITY_SPEARMAN_CANDIDATES": cand_sp,
            "AUG_SCORE_VS_UTILITY_SPEARMAN_ELIGIBLE": elig_sp,
            "POSITIVE_UTILITY_ENRICHMENT": enrich,
            "CANDIDATE_POSITIVE_UTILITY_RATE": top_rate,
            "CANDIDATE_NEGATIVE_UTILITY_RATE": (float(n_neg) / float(n_cand)) if n_cand else None,
            "CANDIDATE_NONFILL_RATE": (float(n_non) / float(n_cand)) if n_cand else None,
            "ELIGIBLE_POSITIVE_UTILITY_RATE": base_rate,
        },
        "representation_diagnostic": rep_diag,
        "price_tail_285A_trade_n": n_285a,
        "price_tail_285A_pnl": pnl_285a,
        "integrity": leak,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "RIDGE_ALPHA": RIDGE_ALPHA,
        "ENSEMBLE": ENSEMBLE,
        "POSITIVE_REP_MIN": POS_REP_MIN,
        "AVAILABLE_REP_MIN": AVAILABLE_REP_MIN,
        "TARGET": TARGET,
    }
    sheets = {
        "precommit": kv_rows({**spec, "PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "outer_folds": outer_rows,
        "augment_candidates": candidates or [{"empty": True}],
        "augment_trades": aug_trades or [{"empty": True}],
        "daily_pnl": daily_rows,
        "price_tail": tail_rows
        + [{"symbol": "285A", "trade_n": n_285a, "net_pnl": pnl_285a}]
        + rep_diag,
        "current_preservation": kv_rows(pres),
        "integrity": kv_rows(leak),
    }
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
