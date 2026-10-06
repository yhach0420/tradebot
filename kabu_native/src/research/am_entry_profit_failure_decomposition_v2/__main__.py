"""Offline AM ENTRY profit failure decomposition V2. Diagnostic only. No Runtime write."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_entry_fixed_spec_oof.diagnose import load_v2_audit
from research.am_entry_fixed_spec_oof.oof import evaluate_policy
from research.am_entry_profit_failure_decomposition_v2 import (
    ANALYSIS_ID,
    BEST_FIXED_NET_EXPECTED,
    CURRENT_FILL_RATE_EXPECTED,
    FIXED_SPEC_ANALYSIS_ID,
    INNER_OUTER_PEARSON_EXPECTED,
    INNER_OUTER_SPEARMAN_EXPECTED,
    NESTED_FILL_RATE_EXPECTED,
    PARTIAL_SPEC_N_EXPECTED,
    PASS_SPEC_N_EXPECTED,
    SPEARMAN_ABS_UTILITY_VS_FILL_PRICE_EXPECTED,
    TOP2_LOSS_SHARE_EXPECTED,
)
from research.am_entry_profit_failure_decomposition_v2.classify import (
    decide_case,
    price_scale_supported,
    zero_as_safe_supported,
)
from research.am_entry_profit_failure_decomposition_v2.diagnose import (
    ARCHITECTURES,
    daily_spearman,
    filled_utility_counts,
    mark_current_top3,
    outcome_class,
    paired_spearman,
    positive_utility_top_rank_enrichment,
    pred_outcome_medians,
    price_scale_label,
    price_tercile_cuts,
    replacement_fill_relations,
    robust_architecture,
    spec_abs_errors,
    spec_independent_top3_keys,
    top3_outcome_shares,
    utility_tercile_cuts,
    win_loss_scores,
)
from research.am_entry_profit_failure_decomposition_v2.oracle import attach_oracle_score, evaluate_eligible
from research.am_entry_profit_failure_decomposition_v2.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.am_entry_profit_failure_decomposition_v2.scores import process_oof_scores
from research.am_entry_profit_improvement import (
    ARCH_PAIR,
    ARCHITECTURE_N,
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
    POSTHOC_SPEC_ADDITION_N,
    REPRESENTATION_N,
    RUNTIME_CHANGED,
    SESSION,
    SPEC_N,
    SUBMIT_N,
    TRUE_OOS,
    UTILITY_KEY,
    W5_RUNTIME_ADOPTED,
    WAIT_SEARCH_N,
)
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.metrics import paired_delta, success_gate
from research.am_entry_profit_improvement.models import spec_grid
from research.am_entry_fixed_spec_oof import (
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
    V2_FILL_ONLY_NET_PNL,
    V2_FILL_ONLY_TRADE_N,
)
from research.canonical_entry_performance_rebase.analyze import _f, row_key, session_of
from research.direct_joint_objective.oof import representation_grid
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close, _median
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
FIXED_REPORT = NATIVE / "results" / "research" / "am_entry_fixed_spec_oof" / "report.json"
FIXED_AUDIT = NATIVE / "results" / "research" / "am_entry_fixed_spec_oof" / "audit.xlsx"
SCORE_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_profit_failure_decomposition_v2"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


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


def _integrity(msg: str, extra: dict | None = None) -> int:
    required = {
        "BASE_PARITY": False,
        "POSITIVE_UTILITY_N": None,
        "NEGATIVE_UTILITY_N": None,
        "SPEARMAN_FILL_VS_NET": None,
        "SPEARMAN_TRADE_N_VS_NET": None,
        "ZERO_AS_SAFE_OUTCOME_SUPPORTED": False,
        "PRICE_SCALE_TAIL_DISTORTION_SUPPORTED": False,
        "CURRENT_VETO_SIGNAL_SUPPORTED": False,
        "OUTSIDE_CURRENT_AUGMENT_SIGNAL_SUPPORTED": False,
        "ORACLE_NET_PNL": None,
        "ORACLE_PF": None,
        "ORACLE_MAX_DD": None,
        "ORACLE_CURRENT_GATE_PASS": False,
        "CURRENT_INTERNAL_ORACLE_NET_PNL": None,
        "CURRENT_INTERNAL_ORACLE_IMPROVEMENT": None,
        "BEST_VETO_ARCHITECTURE_DIAGNOSTIC": None,
        "BEST_AUGMENT_ARCHITECTURE_DIAGNOSTIC": None,
        "PRIMARY_FAILURE_MECHANISM": "INTEGRITY_FAILURE",
        "NEXT": "STOP",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_ENTRY_PROFIT_FAILURE_DECOMPOSITION_INTEGRITY_FAILED",
        "STOP_REASON": msg,
    }
    decision = decide_case(
        integrity_ok=False,
        veto=False,
        augment=False,
        internal_improvement=False,
        oracle_gate_pass=False,
        oracle_addition=False,
        zero_safe=False,
        price_scale=False,
    )
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, {"summary": kv_rows(required), "integrity": kv_rows({"STOP_REASON": msg})})
    print(msg, flush=True)
    return 2


def _slim_pack(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


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
    if not FIXED_REPORT.is_file() or not FIXED_AUDIT.is_file():
        return _integrity("STOP. Fixed-spec OOF artifacts missing.")

    print("AM ENTRY profit failure decomposition V2. Diagnostic only. Frozen 27 specs.", flush=True)
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
    current_pack = evaluate_policy(rows, list(ELIGIBLE_DAYS), score_key="current_score")
    fill_only_pack = evaluate_policy(rows, list(ELIGIBLE_DAYS), score_key="fill_score")
    replay_ok = (
        int(current_pack.get("trade_count") or -1) == int(V2_CURRENT_TRADE_N)
        and _close(current_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        and _close(current_pack.get("profit_factor"), V2_CURRENT_PF, 1e-12)
        and _close(current_pack.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
        and int(fill_only_pack.get("trade_count") or -1) == int(V2_FILL_ONLY_TRADE_N)
        and _close(fill_only_pack.get("net_pnl_yen_100"), V2_FILL_ONLY_NET_PNL, PARITY_ABS_TOL)
    )
    fixed_rep = _load(FIXED_REPORT)
    req = dict(fixed_rep.get("required") or {})
    sheets_fixed = load_v2_audit(FIXED_AUDIT)
    spec_oof = list(sheets_fixed.get("fixed_spec_oof") or [])
    best_fixed = None
    if spec_oof:
        best_fixed = max(float(r.get("NET_PNL") or -1e18) for r in spec_oof)
    pass_n = sum(1 for r in spec_oof if r.get("FIXED_SPEC_PASS"))
    partial_n = sum(1 for r in spec_oof if r.get("NET_AND_PF_EDGE"))
    prior_ok = (
        str(fixed_rep.get("ANALYSIS_ID") or "") == FIXED_SPEC_ANALYSIS_ID
        and int(pass_n) == int(PASS_SPEC_N_EXPECTED)
        and int(partial_n) == int(PARTIAL_SPEC_N_EXPECTED)
        and best_fixed is not None
        and _close(best_fixed, BEST_FIXED_NET_EXPECTED, PARITY_ABS_TOL)
        and _close(req.get("INNER_OUTER_SPEARMAN"), INNER_OUTER_SPEARMAN_EXPECTED, 1e-12)
        and _close(req.get("INNER_OUTER_PEARSON"), INNER_OUTER_PEARSON_EXPECTED, 1e-12)
        and _close(req.get("NESTED_CANDIDATE_FILL_RATE"), NESTED_FILL_RATE_EXPECTED, 1e-12)
        and _close(req.get("CURRENT_FILL_RATE"), CURRENT_FILL_RATE_EXPECTED, 1e-12)
        and _close(req.get("TOP2_LOSS_SHARE"), TOP2_LOSS_SHARE_EXPECTED, 1e-12)
        and _close(req.get("SPEARMAN_ABS_UTILITY_VS_FILL_PRICE"), SPEARMAN_ABS_UTILITY_VS_FILL_PRICE_EXPECTED, 1e-12)
        and _close(current_pack.get("fill_rate"), CURRENT_FILL_RATE_EXPECTED, 1e-12)
    )
    print("parity_pop", parity_pop.get("ok"), "replay_ok", replay_ok, "prior_ok", prior_ok, flush=True)
    if not (parity_pop.get("ok") and replay_ok and prior_ok):
        return _integrity(
            "STOP. Frozen CURRENT/FILL_ONLY/fixed-spec OOF parity did not reproduce.",
            extra={"parity_pop": parity_pop, "replay_ok": replay_ok, "prior_ok": prior_ok, "best_fixed": best_fixed},
        )
    print("BASE_PARITY true", flush=True)

    util_counts = filled_utility_counts(rows)
    current_keys = mark_current_top3(rows)
    current_top3_shares = top3_outcome_shares(rows, current_keys)
    trade_pnl: dict[str, float] = {}
    for t in current_pack.get("trades") or []:
        trade_pnl[row_key(t)] = float(_f(t.get("pnl_yen_100")) or 0.0)
    if len(trade_pnl) != int(V2_CURRENT_TRADE_N):
        return _integrity("STOP. CURRENT trade key join mismatch.", extra={"TRADE_KEY_N": len(trade_pnl)})

    grid = representation_grid()
    specs = spec_grid(grid)
    if len(specs) != int(SPEC_N) or len(spec_oof) != int(SPEC_N):
        return _integrity("STOP. SPEC_N drifted.")

    SCORE_CACHE.mkdir(parents=True, exist_ok=True)
    jobs = []
    for spec in specs:
        sid = str(spec.get("spec_id"))
        jobs.append(
            {
                "spec_id": sid,
                "spec": spec,
                "days": list(ELIGIBLE_DAYS),
                "rows_path": str(LABELED),
                "cache_path": str(SCORE_CACHE / f"{sid.replace('|', '_')}_oof_scores.json"),
            }
        )
    print(f"oof-score jobs={len(jobs)} workers={min(MAX_WORKERS, len(jobs))}", flush=True)
    got = _pool(process_oof_scores, jobs, "SCORE", "spec_id")
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != int(SPEC_N):
        return _integrity(
            "STOP. Frozen-spec OOF score replay failed.",
            extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail]},
        )
    by_spec = {str(b.get("spec_id")): b for b in got}
    for spec in specs:
        sid = str(spec.get("spec_id"))
        b = by_spec[sid]
        _save_json(SCORE_CACHE / f"{sid.replace('|', '_')}_oof_scores.json", {**b, "scores": b.get("scores")})

    leak = {
        "NEW_MODEL_N": 0,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "TARGET_CHANGE_N": 0,
        "PM_ROWS_USED_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "HELDOUT_FIT_LEAK_N": 0,
        "POLICY_FROM_ORACLE_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "CURRENT_POLICY_CHANGE_N": 0,
        "POSTHOC_SPEC_ADDITION_N": POSTHOC_SPEC_ADDITION_N,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "C14_CHANGED": C14_CHANGED,
    }
    for b in got:
        ig = b.get("integrity") or {}
        leak["HELDOUT_FIT_LEAK_N"] += int(ig.get("HELDOUT_FIT_LEAK_N") or 0)
        leak["PM_ROWS_USED_N"] = max(int(leak["PM_ROWS_USED_N"] or 0), int(ig.get("PM_ROWS_USED_N") or 0))
        leak["TARGET_CONTAMINATION_N"] = max(
            int(leak["TARGET_CONTAMINATION_N"] or 0), int(ig.get("TARGET_CONTAMINATION_N") or 0)
        )
        leak["NEW_MODEL_N"] += int(ig.get("NEW_MODEL_N") or 0)
    if int(leak["HELDOUT_FIT_LEAK_N"] or 0) != 0:
        return _integrity("STOP. HELDOUT_FIT_LEAK_N != 0.", extra={"integrity": leak})

    oof_by_id = {str(r.get("spec_id")): r for r in spec_oof}
    rel = replacement_fill_relations(spec_oof)
    cuts = price_tercile_cuts(rows)
    label_price = price_scale_label(rows, cuts)

    def _is_current(r: dict[str, Any]) -> bool:
        return bool(r.get("CURRENT_TOP3"))

    def _is_outside(r: dict[str, Any]) -> bool:
        return not bool(r.get("CURRENT_TOP3"))

    def _utility(r: dict[str, Any]) -> float | None:
        return _f(r.get(UTILITY_KEY))

    def _trade_y(r: dict[str, Any]) -> float | None:
        k = row_key(r)
        return trade_pnl.get(k)

    spec_diag = []
    yen_err_sp = []
    high_err_shares = []
    nonfill_minus_neg = []
    top3_nonfill_shares = []
    veto_daily: dict[str, dict[str, list]] = {a: defaultdict(list) for a in ARCHITECTURES}
    aug_daily: dict[str, dict[str, list]] = {a: defaultdict(list) for a in ARCHITECTURES}
    for spec in specs:
        sid = str(spec.get("spec_id"))
        arch = str(spec.get("architecture_id"))
        scores = dict(by_spec[sid].get("scores") or {})
        yen = arch != ARCH_PAIR
        pred_med = pred_outcome_medians(rows, scores)
        selected = spec_independent_top3_keys(rows, scores)
        shares = top3_outcome_shares(rows, selected)
        err = spec_abs_errors(rows, scores, cuts, yen_scale=yen)
        if err.get("HIGH_PRICE_ABS_ERROR_SHARE") is not None:
            high_err_shares.append(float(err["HIGH_PRICE_ABS_ERROR_SHARE"]))
        if yen and err.get("SPEARMAN_ABS_ERROR_VS_FILL_PRICE") is not None:
            yen_err_sp.append(float(err["SPEARMAN_ABS_ERROR_VS_FILL_PRICE"]))
        nf = pred_med.get("PRED_SCORE_NONFILL_MEDIAN")
        ng = pred_med.get("PRED_SCORE_NEG_FILL_MEDIAN")
        if nf is not None and ng is not None:
            nonfill_minus_neg.append(float(nf) - float(ng))
        if shares.get("NONFILL_SHARE") is not None:
            top3_nonfill_shares.append(float(shares["NONFILL_SHARE"]))
        veto_sp = paired_spearman(rows, scores, mask=_is_current, y_fn=_utility)
        fill_sp = paired_spearman(rows, scores, mask=lambda r: row_key(r) in trade_pnl, y_fn=_trade_y)
        out_sp = paired_spearman(rows, scores, mask=_is_outside, y_fn=_utility)
        enrich = positive_utility_top_rank_enrichment(rows, scores, current_keys)
        wl = win_loss_scores(rows, scores, trade_pnl)
        vdays = daily_spearman(rows, scores, mask=_is_current, y_fn=_utility, days=list(ELIGIBLE_DAYS))
        adays = daily_spearman(rows, scores, mask=_is_outside, y_fn=_utility, days=list(ELIGIBLE_DAYS))
        for drow in vdays:
            if drow.get("spearman") is not None:
                veto_daily[arch][str(drow["date"])].append(float(drow["spearman"]))
        for drow in adays:
            if drow.get("spearman") is not None:
                aug_daily[arch][str(drow["date"])].append(float(drow["spearman"]))
        oof = oof_by_id.get(sid) or {}
        spec_diag.append(
            {
                "spec_id": sid,
                "architecture_id": arch,
                "representation_id": spec.get("representation_id"),
                "OOF_FILL_RATE": oof.get("OOF_FILL_RATE"),
                "TRADE_N": oof.get("TRADE_N"),
                "NET_PNL": oof.get("NET_PNL"),
                "PF": oof.get("PF"),
                "MAX_DD": oof.get("MAX_DD"),
                **pred_med,
                **shares,
                "CURRENT_UTILITY_SPEARMAN": veto_sp,
                "CURRENT_FILL_PNL_SPEARMAN": fill_sp,
                "OUTSIDE_CURRENT_UTILITY_SPEARMAN": out_sp,
                "POSITIVE_UTILITY_TOP_RANK_ENRICHMENT": enrich,
                **wl,
                "HIGH_PRICE_ABS_ERROR_SHARE": err.get("HIGH_PRICE_ABS_ERROR_SHARE"),
                "SPEARMAN_ABS_ERROR_VS_FILL_PRICE": err.get("SPEARMAN_ABS_ERROR_VS_FILL_PRICE"),
                "ABS_ERROR_285A": (err.get("symbol_abs_error_median") or {}).get("285A"),
            }
        )

    arch_rows = []
    veto_supported_any = False
    aug_supported_any = False
    best_veto = None
    best_aug = None
    for arch in ARCHITECTURES:
        xs = [r for r in spec_diag if r.get("architecture_id") == arch]
        veto_fx = [r.get("CURRENT_UTILITY_SPEARMAN") for r in xs]
        aug_fx = [r.get("OUTSIDE_CURRENT_UTILITY_SPEARMAN") for r in xs]
        enrich_fx = [r.get("POSITIVE_UTILITY_TOP_RANK_ENRICHMENT") for r in xs]
        vday_med = []
        aday_med = []
        for d in ELIGIBLE_DAYS:
            vday_med.append(_median(veto_daily[arch].get(d) or []))
            aday_med.append(_median(aug_daily[arch].get(d) or []))
        vrob = robust_architecture(veto_fx, vday_med)
        arob = robust_architecture(aug_fx, aday_med)
        loss_pos = sum(1 for r in xs if r.get("WIN_MINUS_LOSS_SCORE") is not None and float(r["WIN_MINUS_LOSS_SCORE"]) > 0)
        worst_pos = sum(
            1 for r in xs if r.get("WORST_LOSS_DISCRIMINATION") is not None and float(r["WORST_LOSS_DISCRIMINATION"]) > 0
        )
        top_shares = {
            "NONFILL_SHARE": _median([float(r["NONFILL_SHARE"]) for r in xs if r.get("NONFILL_SHARE") is not None]),
            "POSITIVE_FILL_SHARE": _median([float(r["POSITIVE_FILL_SHARE"]) for r in xs if r.get("POSITIVE_FILL_SHARE") is not None]),
            "NEGATIVE_FILL_SHARE": _median([float(r["NEGATIVE_FILL_SHARE"]) for r in xs if r.get("NEGATIVE_FILL_SHARE") is not None]),
        }
        enrich_med = _median([float(v) for v in enrich_fx if v is not None])
        veto_ok = bool(vrob.get("SUPPORTED"))
        aug_ok = bool(arob.get("SUPPORTED") and enrich_med is not None and float(enrich_med) > 1.0)
        if veto_ok:
            veto_supported_any = True
        if aug_ok:
            aug_supported_any = True
        rec = {
            "architecture_id": arch,
            "LOSS_DISCRIMINATION_POS_REP_N": loss_pos,
            "WORST_LOSS_DISCRIMINATION_POS_REP_N": worst_pos,
            "CURRENT_UTILITY_SPEARMAN_MEDIAN": vrob.get("median_effect"),
            "OUTSIDE_CURRENT_UTILITY_SPEARMAN_MEDIAN": arob.get("median_effect"),
            "ENRICHMENT_MEDIAN": enrich_med,
            "VETO_ROBUST": vrob.get("SUPPORTED"),
            "AUGMENT_ROBUST": arob.get("SUPPORTED"),
            "AUGMENT_SIGNAL": aug_ok,
            **{f"VETO_{k}": v for k, v in (vrob.get("gates") or {}).items()},
            **{f"AUG_{k}": v for k, v in (arob.get("gates") or {}).items()},
            "VETO_POS_REP_N": vrob.get("positive_rep_n"),
            "AUG_POS_REP_N": arob.get("positive_rep_n"),
            "VETO_POS_DAYS": vrob.get("positive_days"),
            "VETO_NEG_DAYS": vrob.get("negative_days"),
            "VETO_EX_BEST": vrob.get("ex_best_day"),
            "VETO_EX_TOP3": vrob.get("ex_top3_days"),
            "AUG_POS_DAYS": arob.get("positive_days"),
            "AUG_NEG_DAYS": arob.get("negative_days"),
            "AUG_EX_BEST": arob.get("ex_best_day"),
            "AUG_EX_TOP3": arob.get("ex_top3_days"),
            **top_shares,
        }
        arch_rows.append(rec)
        if veto_ok and (
            best_veto is None
            or best_veto.get("median") is None
            or (vrob.get("median_effect") is not None and float(vrob["median_effect"]) > float(best_veto["median"]))
        ):
            best_veto = {"architecture_id": arch, "median": vrob.get("median_effect"), "supported": True}
        if aug_ok and (
            best_aug is None
            or best_aug.get("median") is None
            or (arob.get("median_effect") is not None and float(arob["median_effect"]) > float(best_aug["median"]))
        ):
            best_aug = {"architecture_id": arch, "median": arob.get("median_effect"), "supported": True}

    if best_veto is None:
        ranked_v = [r for r in arch_rows if r.get("CURRENT_UTILITY_SPEARMAN_MEDIAN") is not None]
        if ranked_v:
            pick = max(ranked_v, key=lambda r: float(r["CURRENT_UTILITY_SPEARMAN_MEDIAN"]))
            best_veto = {"architecture_id": pick.get("architecture_id"), "median": pick.get("CURRENT_UTILITY_SPEARMAN_MEDIAN")}
    if best_aug is None:
        ranked_a = [r for r in arch_rows if r.get("OUTSIDE_CURRENT_UTILITY_SPEARMAN_MEDIAN") is not None]
        if ranked_a:
            pick = max(ranked_a, key=lambda r: float(r["OUTSIDE_CURRENT_UTILITY_SPEARMAN_MEDIAN"]))
            best_aug = {"architecture_id": pick.get("architecture_id"), "median": pick.get("OUTSIDE_CURRENT_UTILITY_SPEARMAN_MEDIAN")}

    zero_safe = zero_as_safe_supported(
        nonfill_minus_neg=_median(nonfill_minus_neg),
        top3_nonfill=_median(top3_nonfill_shares),
        current_nonfill=current_top3_shares.get("NONFILL_SHARE"),
    )
    spear_err = _median(yen_err_sp)
    high_err = _median(high_err_shares)
    price_ok = price_scale_supported(
        spearman_abs_error=spear_err,
        high_abs_error_share=high_err,
        high_gross_loss_share=label_price.get("HIGH_PRICE_GROSS_LOSS_SHARE"),
    )

    oracle_rows = attach_oracle_score(rows, UTILITY_KEY)
    oracle_pack = evaluate_policy(oracle_rows, list(ELIGIBLE_DAYS), score_key="oracle_score")
    oracle_paired = paired_delta(list(oracle_pack.get("daily") or []), list(current_pack.get("daily") or []))
    oracle_gate = success_gate(oracle_pack, current_pack, oracle_paired, integrity_ok=True)

    cur_utils = []
    for r in rows:
        if not r.get("CURRENT_TOP3"):
            continue
        u = _f(r.get(UTILITY_KEY))
        cur_utils.append(0.0 if u is None else float(u))
    ucuts = utility_tercile_cuts(cur_utils)
    internal_keys: set[str] = set()
    for r in rows:
        if not r.get("CURRENT_TOP3"):
            continue
        u = _f(r.get(UTILITY_KEY))
        uu = 0.0 if u is None else float(u)
        if ucuts is None or uu > float(ucuts[0]):
            internal_keys.add(row_key(r))
    internal_pack = evaluate_eligible(
        oracle_rows, list(ELIGIBLE_DAYS), score_key="oracle_score", eligible_keys=internal_keys
    )
    internal_imp = float(internal_pack.get("net_pnl_yen_100") or 0.0) - float(current_pack.get("net_pnl_yen_100") or 0.0)

    ora_lookup = {row_key(r): r.get("oracle_score") for r in oracle_rows}
    ora_top = spec_independent_top3_keys(rows, ora_lookup)
    add_pos = 0
    for r in rows:
        k = row_key(r)
        if k not in ora_top or k in current_keys:
            continue
        if outcome_class(r) == "POSITIVE_FILL":
            add_pos += 1
    oracle_addition = bool(add_pos > 0 or float(oracle_pack.get("net_pnl_yen_100") or 0.0) > float(current_pack.get("net_pnl_yen_100") or 0.0))

    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in (
        "NEW_MODEL_N",
        "FEATURE_SEARCH_N",
        "NEW_FEATURE_N",
        "TARGET_CHANGE_N",
        "PM_ROWS_USED_N",
        "FUTURE_FEATURE_USE_N",
        "TARGET_CONTAMINATION_N",
        "HELDOUT_FIT_LEAK_N",
        "POLICY_FROM_ORACLE_N",
        "SCORE_THRESHOLD_SEARCH_N",
        "WAIT_SEARCH_N",
        "CURRENT_POLICY_CHANGE_N",
    ))
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak})

    decision = decide_case(
        integrity_ok=True,
        veto=veto_supported_any,
        augment=aug_supported_any,
        internal_improvement=bool(internal_imp > 0),
        oracle_gate_pass=bool(oracle_gate.get("AM_ENTRY_PROFIT_IMPROVEMENT_PASS")),
        oracle_addition=oracle_addition,
        zero_safe=zero_safe,
        price_scale=price_ok,
    )

    # 285A
    sym_rows = []
    for r in rows:
        if str(r.get("symbol") or "").replace(".T", "") != "285A":
            continue
        k = row_key(r)
        rec = {
            "date": r.get("date"),
            "anchor": r.get("anchor"),
            "CURRENT_TOP3": bool(r.get("CURRENT_TOP3")),
            "Y_FILL5": r.get("Y_FILL5"),
            "fill_price": r.get("fill_price"),
            "utility": r.get(UTILITY_KEY),
            "CURRENT_TRADE_PNL": trade_pnl.get(k),
        }
        sym_rows.append(rec)

    required = {
        "BASE_PARITY": True,
        "POSITIVE_UTILITY_N": util_counts.get("POSITIVE_UTILITY_N"),
        "NEGATIVE_UTILITY_N": util_counts.get("NEGATIVE_UTILITY_N"),
        "SPEARMAN_FILL_VS_NET": rel.get("SPEARMAN_FILL_VS_NET"),
        "SPEARMAN_TRADE_N_VS_NET": rel.get("SPEARMAN_TRADE_N_VS_NET"),
        "ZERO_AS_SAFE_OUTCOME_SUPPORTED": zero_safe,
        "PRICE_SCALE_TAIL_DISTORTION_SUPPORTED": price_ok,
        "CURRENT_VETO_SIGNAL_SUPPORTED": veto_supported_any,
        "OUTSIDE_CURRENT_AUGMENT_SIGNAL_SUPPORTED": aug_supported_any,
        "ORACLE_NET_PNL": oracle_pack.get("net_pnl_yen_100"),
        "ORACLE_PF": oracle_pack.get("profit_factor"),
        "ORACLE_MAX_DD": oracle_pack.get("max_drawdown_yen_100"),
        "ORACLE_CURRENT_GATE_PASS": bool(oracle_gate.get("AM_ENTRY_PROFIT_IMPROVEMENT_PASS")),
        "CURRENT_INTERNAL_ORACLE_NET_PNL": internal_pack.get("net_pnl_yen_100"),
        "CURRENT_INTERNAL_ORACLE_IMPROVEMENT": internal_imp,
        "BEST_VETO_ARCHITECTURE_DIAGNOSTIC": None if best_veto is None else best_veto.get("architecture_id"),
        "BEST_AUGMENT_ARCHITECTURE_DIAGNOSTIC": None if best_aug is None else best_aug.get("architecture_id"),
        "PRIMARY_FAILURE_MECHANISM": decision.get("PRIMARY_FAILURE_MECHANISM"),
        "NEXT": decision.get("NEXT"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "CASE": decision.get("CASE"),
        "SPEARMAN_FILL_VS_PF": rel.get("SPEARMAN_FILL_VS_PF"),
        "FLAT_UTILITY_N": util_counts.get("FLAT_UTILITY_N"),
        "ORACLE_TRADE_N": oracle_pack.get("trade_count"),
        "CURRENT_INTERNAL_ORACLE_TRADE_N": internal_pack.get("trade_count"),
        "ORACLE_POSITIVE_OUTSIDE_CURRENT_N": add_pos,
    }
    extra = {
        "decision": decision,
        "parity": {"population": parity_pop, "replay_ok": replay_ok, "prior_ok": prior_ok},
        "integrity": leak,
        "utility_counts": util_counts,
        "current": _slim_pack(current_pack),
        "fill_only": _slim_pack(fill_only_pack),
        "oracle": _slim_pack(oracle_pack),
        "oracle_gates": oracle_gate.get("gates"),
        "current_internal_oracle": _slim_pack(internal_pack),
        "current_top3_shares": current_top3_shares,
        "replacement": {k: v for k, v in rel.items() if k not in ("NET_POS_SPECS", "PF_GT1_SPECS")},
        "price_label": label_price,
        "SPEARMAN_ABS_ERROR_VS_FILL_PRICE": spear_err,
        "HIGH_PRICE_ABS_ERROR_SHARE": high_err,
        "HIGH_PRICE_GROSS_LOSS_SHARE": label_price.get("HIGH_PRICE_GROSS_LOSS_SHARE"),
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "FILL_ONLY_REPRESENTATION_ID": FILL_ONLY_REPRESENTATION_ID,
        "ARCHITECTURE_N": ARCHITECTURE_N,
        "REPRESENTATION_N": REPRESENTATION_N,
        "SPEC_N": SPEC_N,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "C14_ID": C14_ID,
    }
    replacement_sheet = list(spec_oof)
    replacement_sheet.extend(rel.get("NET_POS_SPECS") or [])
    sheets = {
        "summary": kv_rows(required),
        "replacement_fill": spec_diag,
        "zero_as_safe": [
            {
                "CURRENT_TOP3_NONFILL_SHARE": current_top3_shares.get("NONFILL_SHARE"),
                "SPEC_TOP3_NONFILL_SHARE_MEDIAN": _median(top3_nonfill_shares),
                "NONFILL_MINUS_NEG_MEDIAN": _median(nonfill_minus_neg),
                "ZERO_AS_SAFE_OUTCOME_SUPPORTED": zero_safe,
            }
        ]
        + spec_diag,
        "price_scale": list(label_price.get("terciles") or [])
        + [
            {
                "HIGH_PRICE_GROSS_LOSS_SHARE": label_price.get("HIGH_PRICE_GROSS_LOSS_SHARE"),
                "HIGH_PRICE_ABS_ERROR_SHARE": high_err,
                "SPEARMAN_ABS_ERROR_VS_FILL_PRICE": spear_err,
                "PRICE_SCALE_TAIL_DISTORTION_SUPPORTED": price_ok,
            }
        ]
        + spec_diag
        + (sym_rows or [{"symbol": "285A", "empty": True}]),
        "current_veto": spec_diag,
        "loss_discrimination": spec_diag,
        "outside_augment": spec_diag,
        "oracle": [
            {
                "arm": "ORACLE_REPLACEMENT",
                **_slim_pack(oracle_pack),
                "GATE_PASS": bool(oracle_gate.get("AM_ENTRY_PROFIT_IMPROVEMENT_PASS")),
                "POSITIVE_OUTSIDE_CURRENT_N": add_pos,
            },
            {
                "arm": "CURRENT_INTERNAL_ORACLE",
                **_slim_pack(internal_pack),
                "IMPROVEMENT": internal_imp,
                "ELIGIBLE_N": len(internal_keys),
            },
            {"arm": "CURRENT", **_slim_pack(current_pack)},
        ],
        "robustness": arch_rows,
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
