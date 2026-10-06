"""Offline AM ENTRY fixed-spec 18-day OOF. No inner selection. No Runtime write. No Paper."""
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

from research.am_entry_fixed_spec_oof import (
    ANALYSIS_ID,
    V2_ANALYSIS_ID,
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
    V2_FILL_ONLY_NET_PNL,
    V2_FILL_ONLY_TRADE_N,
)
from research.am_entry_fixed_spec_oof.analyze import (
    decide_case,
    gate_row,
    net_and_pf_edge,
    pass_rank_key,
    primary_failure_mechanism,
)
from research.am_entry_fixed_spec_oof.diagnose import (
    fill_row,
    load_v2_audit,
    nested_inner_outer,
    reconstruct_nested_fill,
    tail_loss,
    utility_scale,
)
from research.am_entry_fixed_spec_oof.oof import evaluate_policy, process_fixed_spec
from research.am_entry_fixed_spec_oof.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from research.am_entry_profit_improvement import (
    ARCH_PAIR,
    ARCH_RF,
    ARCH_RIDGE,
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
    LOGREG_PARAMS,
    MAX_WORKERS,
    NEW_FEATURE_N,
    NEW_FORWARD_N,
    OUTER_RESELECTION_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    POSTHOC_SPEC_ADDITION_N,
    REPRESENTATION_N,
    RIDGE_ALPHA,
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
from research.am_entry_profit_improvement.metrics import paired_delta
from research.am_entry_profit_improvement.models import spec_grid
from research.canonical_entry_performance_rebase.analyze import session_of
from research.direct_joint_objective.oof import representation_grid
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_profit_improvement"
LABELED = CACHE / "labeled_am.json"
V2_AUDIT = NATIVE / "results" / "research" / "am_entry_profit_improvement" / "audit.xlsx"
V2_REPORT = NATIVE / "results" / "research" / "am_entry_profit_improvement" / "report.json"


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


def _integrity(msg: str, extra: dict | None = None) -> int:
    required = {
        "BASE_PARITY": False,
        "SPEC_N": SPEC_N,
        "FIXED_SPEC_OOF_COMPLETE_N": 0,
        "PASS_SPEC_N": 0,
        "BEST_PASS_SPEC": None,
        "BEST_PASS_NET_PNL": None,
        "BEST_PASS_PF": None,
        "BEST_PASS_MAX_DD": None,
        "BEST_PASS_PAIRED_MEDIAN": None,
        "BEST_PASS_EX_TOP3": None,
        "INNER_OUTER_SPEARMAN": None,
        "INNER_OUTER_PEARSON": None,
        "SELECTED_OUTER_PNL_RF": None,
        "SELECTED_OUTER_PNL_RIDGE": None,
        "SELECTED_OUTER_PNL_PAIRWISE": None,
        "TOP2_LOSS_SHARE": None,
        "WORST_SYMBOL": None,
        "WORST_SYMBOL_PNL": None,
        "SPEARMAN_ABS_UTILITY_VS_FILL_PRICE": None,
        "PRIMARY_FAILURE_MECHANISM": "INTEGRITY_FAILURE",
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_ENTRY_FIXED_SPEC_INTEGRITY_FAILED",
        "NEXT": "STOP",
        "STOP_REASON": msg,
    }
    decision = decide_case(pass_n=0, partial_n=0, integrity_ok=False)
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(
        report,
        {
            "summary": kv_rows(required),
            "integrity": kv_rows({"STOP_REASON": msg, **(extra or {})}),
        },
    )
    print(msg, flush=True)
    return 2


def write_report(required: dict, *, decision: dict, extra: dict | None = None, sheets: dict | None = None) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": decision,
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets or {"summary": kv_rows(required)})
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    return 0


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
        return _integrity("STOP. labeled_am.json missing. V2 cache required.")
    if not V2_AUDIT.is_file() or not V2_REPORT.is_file():
        return _integrity("STOP. V2 nested artifacts missing.")
    v2 = _load(V2_REPORT)
    if str(v2.get("ANALYSIS_ID") or "") != V2_ANALYSIS_ID:
        return _integrity("STOP. V2 ANALYSIS_ID mismatch.")

    print("AM ENTRY fixed-spec OOF. 27 specs × 18 days. No inner selection. W5 research fill only.", flush=True)
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
    parity = freeze_parity(obs)
    print("parity", parity.get("ok"), parity.get("checks"), flush=True)
    if not parity.get("ok"):
        return _integrity("STOP. Frozen AM labeled population or CURRENT Top3 fill did not reproduce.", extra={"parity": parity})

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
    print(
        f"CURRENT trades={current_pack.get('trade_count')} pnl={current_pack.get('net_pnl_yen_100')} "
        f"FILL_ONLY trades={fill_only_pack.get('trade_count')} pnl={fill_only_pack.get('net_pnl_yen_100')} "
        f"replay_ok={replay_ok}",
        flush=True,
    )
    if not replay_ok:
        return _integrity(
            "STOP. CURRENT/FILL_ONLY W5 replay drifted from frozen V2 contract.",
            extra={
                "current": {k: v for k, v in current_pack.items() if k not in ("trades", "daily")},
                "fill_only": {k: v for k, v in fill_only_pack.items() if k not in ("trades", "daily")},
            },
        )
    print("BASE_PARITY true", flush=True)

    grid = representation_grid()
    specs = spec_grid(grid)
    if len(specs) != int(SPEC_N):
        return _integrity("STOP. SPEC_N drifted.", extra={"SPEC_N": len(specs)})

    jobs = []
    for spec in specs:
        jobs.append(
            {
                "spec_id": spec.get("spec_id"),
                "spec": spec,
                "days": list(ELIGIBLE_DAYS),
                "rows_path": str(LABELED),
            }
        )
    print(f"fixed-spec jobs={len(jobs)} workers={min(MAX_WORKERS, len(jobs))} evals={len(jobs) * 18}", flush=True)
    got = _pool(process_fixed_spec, jobs, "SPEC", "spec_id")
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != int(SPEC_N):
        return _integrity(
            "STOP. Fixed-spec OOF evaluation failed.",
            extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail], "complete_n": len([b for b in got if b.get("ok")])},
        )

    by_spec = {str(b.get("spec_id")): b for b in got}
    leak_sum = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "RUNTIME_CHANGE_N": 0,
        "PAPER_OPERATION_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "OUTER_RESELECTION_N": OUTER_RESELECTION_N,
        "POSTHOC_SPEC_ADDITION_N": POSTHOC_SPEC_ADDITION_N,
        "JOIN_MISS_N": 0,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "INNER_SELECTION_N": 0,
        "UTILITY_EXIT_MISS_N": 0,
    }
    spec_rows = []
    daily_rows = []
    spec_fold_lookup: dict[tuple[str, str], dict[str, Any]] = {}
    for spec in specs:
        sid = str(spec.get("spec_id"))
        b = by_spec[sid]
        ig = b.get("integrity") or {}
        leak_sum["OUTER_HELDOUT_FIT_LEAK_N"] += int(ig.get("OUTER_HELDOUT_FIT_LEAK_N") or 0)
        leak_sum["PM_ROWS_USED_N"] = max(int(leak_sum["PM_ROWS_USED_N"] or 0), int(ig.get("PM_ROWS_USED_N") or 0))
        leak_sum["TARGET_CONTAMINATION_N"] = max(
            int(leak_sum["TARGET_CONTAMINATION_N"] or 0),
            int(ig.get("TARGET_CONTAMINATION_N") or 0),
        )
        leak_sum["INNER_SELECTION_N"] += int(ig.get("INNER_SELECTION_N") or 0)
        leak_sum["POSTHOC_SPEC_ADDITION_N"] = max(
            int(leak_sum["POSTHOC_SPEC_ADDITION_N"] or 0),
            int(ig.get("POSTHOC_SPEC_ADDITION_N") or 0),
        )
        pack = dict(b.get("pack") or {})
        daily = list(b.get("daily") or [])
        paired = paired_delta(daily, list(current_pack.get("daily") or []))
        gates = gate_row(pack, current_pack, paired, integrity_ok=True)
        rec = {
            "spec_id": sid,
            "architecture_id": spec.get("architecture_id"),
            "representation_id": spec.get("representation_id"),
            "feature_set": spec.get("feature_set"),
            "normalization": spec.get("normalization"),
            "TRADE_N": pack.get("trade_count"),
            "NET_PNL": pack.get("net_pnl_yen_100"),
            "GROSS_PROFIT": pack.get("gross_profit"),
            "GROSS_LOSS": pack.get("gross_loss"),
            "PF": pack.get("profit_factor"),
            "MAX_DD": pack.get("max_drawdown_yen_100"),
            "WIN_N": pack.get("win_n"),
            "LOSS_N": pack.get("loss_n"),
            "FLAT_N": pack.get("flat_n"),
            "WIN_RATE": pack.get("win_rate"),
            "POSITIVE_DAY_N": pack.get("positive_day_n"),
            "NEGATIVE_DAY_N": pack.get("negative_day_n"),
            "ZERO_DAY_N": pack.get("zero_day_n"),
            "MEAN_DAILY_PNL": pack.get("mean_daily_pnl"),
            "MEDIAN_DAILY_PNL": pack.get("median_daily_pnl"),
            "ADMITTED_N": pack.get("admitted_n"),
            "FILL_N": pack.get("fill_n"),
            "EXPIRED_N": pack.get("expired_n"),
            "OOF_FILL_RATE": pack.get("fill_rate"),
            "DELTA_PNL_VS_CURRENT": (float(pack.get("net_pnl_yen_100") or 0.0) - float(current_pack.get("net_pnl_yen_100") or 0.0)),
            "DELTA_PF_VS_CURRENT": None,
            "DELTA_DD_VS_CURRENT": (float(pack.get("max_drawdown_yen_100") or 0.0) - float(current_pack.get("max_drawdown_yen_100") or 0.0)),
            "PAIRED_POS_DAYS": paired.get("PAIRED_POS_DAYS"),
            "PAIRED_NEG_DAYS": paired.get("PAIRED_NEG_DAYS"),
            "PAIRED_ZERO_DAYS": paired.get("PAIRED_ZERO_DAYS"),
            "PAIRED_MEDIAN_DAILY_DELTA": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
            "EX_BEST_DAY_PNL_DELTA": paired.get("EX_BEST_DAY_PNL_DELTA"),
            "EX_TOP3_DAYS_PNL_DELTA": paired.get("EX_TOP3_DAYS_PNL_DELTA"),
            "NET_AND_PF_EDGE": net_and_pf_edge(pack, current_pack),
            **gates,
        }
        try:
            rec["DELTA_PF_VS_CURRENT"] = float(pack.get("profit_factor")) - float(current_pack.get("profit_factor"))
        except (TypeError, ValueError):
            rec["DELTA_PF_VS_CURRENT"] = None
        spec_rows.append(rec)
        curr_by = {str(x.get("date")): float(x.get("pnl_yen_100") or 0.0) for x in (current_pack.get("daily") or [])}
        for drow in daily:
            d = str(drow.get("date"))
            spnl = float(drow.get("pnl_yen_100") or 0.0)
            cpnl = float(curr_by.get(d) or 0.0)
            daily_rows.append(
                {
                    "spec_id": sid,
                    "architecture_id": spec.get("architecture_id"),
                    "date": d,
                    "SPEC_PNL": spnl,
                    "CURRENT_PNL": cpnl,
                    "DELTA": spnl - cpnl,
                }
            )
        for fold in b.get("folds") or []:
            spec_fold_lookup[(sid, str(fold.get("date")))] = fold

    if int(leak_sum["OUTER_HELDOUT_FIT_LEAK_N"] or 0) != 0:
        return _integrity("STOP. OUTER_HELDOUT_FIT_LEAK_N != 0.", extra={"integrity": leak_sum})

    integrity_ok = all(
        int(leak_sum.get(k) or 0) == 0
        for k in (
            "OUTER_HELDOUT_FIT_LEAK_N",
            "FUTURE_FEATURE_USE_N",
            "TARGET_CONTAMINATION_N",
            "PM_ROWS_USED_N",
            "RUNTIME_CHANGE_N",
            "PAPER_OPERATION_N",
            "SUBMIT_N",
            "CANCEL_N",
            "LIVE_ORDER_N",
            "OUTER_RESELECTION_N",
            "POSTHOC_SPEC_ADDITION_N",
            "JOIN_MISS_N",
            "INNER_SELECTION_N",
            "UTILITY_EXIT_MISS_N",
        )
    )
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak_sum})

    # Re-apply gates with confirmed integrity.
    for rec, spec in zip(spec_rows, specs):
        b = by_spec[str(spec.get("spec_id"))]
        pack = dict(b.get("pack") or {})
        paired = paired_delta(list(b.get("daily") or []), list(current_pack.get("daily") or []))
        rec.update(gate_row(pack, current_pack, paired, integrity_ok=True))

    pass_rows = [r for r in spec_rows if r.get("FIXED_SPEC_PASS")]
    partial_rows = [r for r in spec_rows if r.get("NET_AND_PF_EDGE")]
    pass_n = len(pass_rows)
    partial_n = len(partial_rows)
    decision = decide_case(pass_n=pass_n, partial_n=partial_n, integrity_ok=True)
    best_pass = sorted(pass_rows, key=pass_rank_key)[0] if pass_rows else None

    v2_sheets = load_v2_audit(V2_AUDIT)
    nested = nested_inner_outer(list(v2_sheets.get("outer_folds") or []))
    cand_trades = [t for t in (v2_sheets.get("trades") or []) if str(t.get("arm") or "") == "CANDIDATE"]
    tail = tail_loss(cand_trades)
    util = utility_scale(rows)
    nested_fill = reconstruct_nested_fill(list(v2_sheets.get("outer_folds") or []), spec_fold_lookup)
    leak_sum["NESTED_FIXED_DAY_PNL_MISMATCH_N"] = int(nested_fill.get("NESTED_FIXED_DAY_PNL_MISMATCH_N") or 0)
    leak_sum["NESTED_FIXED_DAY_MISSING_N"] = int(nested_fill.get("NESTED_FIXED_DAY_MISSING_N") or 0)

    current_fill_rate = current_pack.get("fill_rate")
    mech = primary_failure_mechanism(
        case=str(decision.get("CASE") or ""),
        nested=nested,
        nested_fill=nested_fill,
        current_fill_rate=float(current_fill_rate) if current_fill_rate is not None else None,
        utility=util,
    )

    arch_rows = []
    for arch in (ARCH_RIDGE, ARCH_RF, ARCH_PAIR):
        xs = [r for r in spec_rows if r.get("architecture_id") == arch]
        nets = [float(r.get("NET_PNL") or 0.0) for r in xs]
        pfs = []
        for r in xs:
            try:
                pfs.append(float(r.get("PF")))
            except (TypeError, ValueError):
                pass
        fills = [float(r["OOF_FILL_RATE"]) for r in xs if r.get("OOF_FILL_RATE") is not None]
        med_net = float(sorted(nets)[(len(nets) - 1) // 2]) if nets else None
        if nets and len(nets) % 2 == 0:
            ordered = sorted(nets)
            med_net = 0.5 * (ordered[len(nets) // 2 - 1] + ordered[len(nets) // 2])
        arch_rows.append(
            {
                "architecture_id": arch,
                "SPEC_N": len(xs),
                "PASS_N": sum(1 for r in xs if r.get("FIXED_SPEC_PASS")),
                "NET_AND_PF_EDGE_N": sum(1 for r in xs if r.get("NET_AND_PF_EDGE")),
                "MEAN_NET_PNL": float(sum(nets) / len(nets)) if nets else None,
                "MEDIAN_NET_PNL": med_net,
                "MEAN_PF": float(sum(pfs) / len(pfs)) if pfs else None,
                "MEAN_OOF_FILL_RATE": float(sum(fills) / len(fills)) if fills else None,
                "BEST_NET_SPEC": (max(xs, key=lambda r: float(r.get("NET_PNL") or 0.0)).get("spec_id") if xs else None),
                "BEST_NET_PNL": (max(float(r.get("NET_PNL") or 0.0) for r in xs) if xs else None),
            }
        )

    fill_support_rows = [
        fill_row("CURRENT", current_pack),
        fill_row("FILL_ONLY", fill_only_pack),
        {
            "arm": "NESTED_CANDIDATE",
            "ADMITTED_N": nested_fill.get("ADMITTED_N"),
            "FILL_N": nested_fill.get("FILL_N"),
            "EXPIRED_N": nested_fill.get("EXPIRED_N"),
            "TRADE_N": nested_fill.get("TRADE_N"),
            "OOF_FILL_RATE": nested_fill.get("OOF_FILL_RATE"),
            "NET_PNL": nested_fill.get("NET_PNL"),
        },
    ]
    for r in spec_rows:
        fill_support_rows.append(
            {
                "arm": r.get("spec_id"),
                "ADMITTED_N": r.get("ADMITTED_N"),
                "FILL_N": r.get("FILL_N"),
                "EXPIRED_N": r.get("EXPIRED_N"),
                "TRADE_N": r.get("TRADE_N"),
                "OOF_FILL_RATE": r.get("OOF_FILL_RATE"),
                "NET_PNL": r.get("NET_PNL"),
            }
        )

    nested_fail_rows = [
        {
            "row_type": "SUMMARY",
            "INNER_OUTER_SPEARMAN": nested.get("INNER_OUTER_SPEARMAN"),
            "INNER_OUTER_PEARSON": nested.get("INNER_OUTER_PEARSON"),
            "SELECTED_OUTER_PNL_RF": nested.get("SELECTED_OUTER_PNL_RF"),
            "SELECTED_OUTER_PNL_RIDGE": nested.get("SELECTED_OUTER_PNL_RIDGE"),
            "SELECTED_OUTER_PNL_PAIRWISE": nested.get("SELECTED_OUTER_PNL_PAIRWISE"),
            "SELECTED_OUTER_TRADE_N_RF": nested.get("SELECTED_OUTER_TRADE_N_RF"),
            "SELECTED_OUTER_TRADE_N_RIDGE": nested.get("SELECTED_OUTER_TRADE_N_RIDGE"),
            "SELECTED_OUTER_TRADE_N_PAIRWISE": nested.get("SELECTED_OUTER_TRADE_N_PAIRWISE"),
            "SELECTED_FOLD_N_RF": nested.get("SELECTED_FOLD_N_RF"),
            "SELECTED_FOLD_N_RIDGE": nested.get("SELECTED_FOLD_N_RIDGE"),
            "SELECTED_FOLD_N_PAIRWISE": nested.get("SELECTED_FOLD_N_PAIRWISE"),
        }
    ]
    nested_fail_rows.extend({"row_type": "FOLD", **f} for f in (nested.get("folds") or []))

    tail_rows = kv_rows(
        {
            "TOP1_LOSS_SHARE": tail.get("TOP1_LOSS_SHARE"),
            "TOP2_LOSS_SHARE": tail.get("TOP2_LOSS_SHARE"),
            "TOP3_LOSS_SHARE": tail.get("TOP3_LOSS_SHARE"),
            "TOP5_LOSS_SHARE": tail.get("TOP5_LOSS_SHARE"),
            "WORST_SYMBOL": tail.get("WORST_SYMBOL"),
            "WORST_SYMBOL_PNL": tail.get("WORST_SYMBOL_PNL"),
            "WORST_SYMBOL_CONCENTRATION": tail.get("WORST_SYMBOL_CONCENTRATION"),
            "GROSS_LOSS": tail.get("GROSS_LOSS"),
            "TRADE_N": tail.get("TRADE_N"),
        }
    )
    tail_rows.extend(list(tail.get("worst_trades") or []))
    tail_rows.extend(list(tail.get("symbol_pnl") or []))

    util_rows = kv_rows(
        {
            "SPEARMAN_ABS_UTILITY_VS_FILL_PRICE": util.get("SPEARMAN_ABS_UTILITY_VS_FILL_PRICE"),
            "SPEARMAN_ABS_LOSS_VS_FILL_PRICE": util.get("SPEARMAN_ABS_LOSS_VS_FILL_PRICE"),
            "FILL_N": util.get("FILL_N"),
            "LOSS_N": util.get("LOSS_N"),
        }
    )
    util_rows.extend(list(util.get("price_terciles") or []))
    util_rows.extend(list(util.get("loss_terciles") or []))

    required = {
        "BASE_PARITY": True,
        "SPEC_N": SPEC_N,
        "FIXED_SPEC_OOF_COMPLETE_N": len(spec_rows),
        "PASS_SPEC_N": pass_n,
        "BEST_PASS_SPEC": None if best_pass is None else best_pass.get("spec_id"),
        "BEST_PASS_NET_PNL": None if best_pass is None else best_pass.get("NET_PNL"),
        "BEST_PASS_PF": None if best_pass is None else best_pass.get("PF"),
        "BEST_PASS_MAX_DD": None if best_pass is None else best_pass.get("MAX_DD"),
        "BEST_PASS_PAIRED_MEDIAN": None if best_pass is None else best_pass.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "BEST_PASS_EX_TOP3": None if best_pass is None else best_pass.get("EX_TOP3_DAYS_PNL_DELTA"),
        "INNER_OUTER_SPEARMAN": nested.get("INNER_OUTER_SPEARMAN"),
        "INNER_OUTER_PEARSON": nested.get("INNER_OUTER_PEARSON"),
        "SELECTED_OUTER_PNL_RF": nested.get("SELECTED_OUTER_PNL_RF"),
        "SELECTED_OUTER_PNL_RIDGE": nested.get("SELECTED_OUTER_PNL_RIDGE"),
        "SELECTED_OUTER_PNL_PAIRWISE": nested.get("SELECTED_OUTER_PNL_PAIRWISE"),
        "TOP2_LOSS_SHARE": tail.get("TOP2_LOSS_SHARE"),
        "WORST_SYMBOL": tail.get("WORST_SYMBOL"),
        "WORST_SYMBOL_PNL": tail.get("WORST_SYMBOL_PNL"),
        "SPEARMAN_ABS_UTILITY_VS_FILL_PRICE": util.get("SPEARMAN_ABS_UTILITY_VS_FILL_PRICE"),
        "PRIMARY_FAILURE_MECHANISM": mech,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "CASE": decision.get("CASE"),
        "SELECTED_FIXED_SPEC": None if best_pass is None else best_pass.get("spec_id"),
        "SELECTED_OUTER_TRADE_N_RF": nested.get("SELECTED_OUTER_TRADE_N_RF"),
        "SELECTED_OUTER_TRADE_N_RIDGE": nested.get("SELECTED_OUTER_TRADE_N_RIDGE"),
        "SELECTED_OUTER_TRADE_N_PAIRWISE": nested.get("SELECTED_OUTER_TRADE_N_PAIRWISE"),
        "NESTED_CANDIDATE_FILL_RATE": nested_fill.get("OOF_FILL_RATE"),
        "CURRENT_FILL_RATE": current_pack.get("fill_rate"),
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    extra = {
        "CASE": decision.get("CASE"),
        "SELECTED_FIXED_SPEC": None if best_pass is None else best_pass.get("spec_id"),
        "SELECTED_OUTER_TRADE_N_RF": nested.get("SELECTED_OUTER_TRADE_N_RF"),
        "SELECTED_OUTER_TRADE_N_RIDGE": nested.get("SELECTED_OUTER_TRADE_N_RIDGE"),
        "SELECTED_OUTER_TRADE_N_PAIRWISE": nested.get("SELECTED_OUTER_TRADE_N_PAIRWISE"),
        "NESTED_CANDIDATE_FILL_RATE": nested_fill.get("OOF_FILL_RATE"),
        "CURRENT_FILL_RATE": current_pack.get("fill_rate"),
        "FILL_ONLY_FILL_RATE": fill_only_pack.get("fill_rate"),
        "PARTIAL_SPEC_N": partial_n,
        "parity": parity,
        "integrity": leak_sum,
        "current": {k: v for k, v in current_pack.items() if k not in ("trades", "daily")},
        "fill_only": {k: v for k, v in fill_only_pack.items() if k not in ("trades", "daily")},
        "nested": {k: v for k, v in nested.items() if k != "folds"},
        "tail_loss": {k: v for k, v in tail.items() if k not in ("worst_trades", "symbol_pnl")},
        "utility_scale": {k: v for k, v in util.items() if k not in ("price_terciles", "loss_terciles")},
        "nested_fill": {k: v for k, v in nested_fill.items() if k != "days"},
        "independent_top3": {
            "CURRENT_FILL_RATE": (top3.get("CURRENT") or {}).get("FILL_RATE"),
            "FILL_ONLY_FILL_RATE": (top3.get("FILL_ONLY_INDEPENDENT") or {}).get("SELECTED_FILL_RATE"),
        },
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "RIDGE_ALPHA": RIDGE_ALPHA,
        "LOGREG_PARAMS": dict(LOGREG_PARAMS),
        "FILL_ONLY_REPRESENTATION_ID": FILL_ONLY_REPRESENTATION_ID,
        "ARCHITECTURE_N": ARCHITECTURE_N,
        "REPRESENTATION_N": REPRESENTATION_N,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "C14_ID": C14_ID,
        "C14_CHANGED": C14_CHANGED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "UTILITY_KEY": UTILITY_KEY,
    }
    sheets = {
        "summary": kv_rows(required),
        "fixed_spec_oof": spec_rows,
        "daily_pnl_by_spec": daily_rows,
        "architecture_summary": arch_rows,
        "nested_failure": nested_fail_rows,
        "tail_loss": tail_rows,
        "utility_scale": util_rows,
        "fill_support": fill_support_rows,
        "integrity": kv_rows(
            {
                **leak_sum,
                "OUTER_HELDOUT_FIT_LEAK_N": leak_sum["OUTER_HELDOUT_FIT_LEAK_N"],
                "SUBMIT_N": SUBMIT_N,
                "CANCEL_N": CANCEL_N,
                "LIVE_ORDER_N": LIVE_ORDER_N,
                "PAPER_OPERATED": PAPER_OPERATED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
                "WAIT_SEARCH_N": WAIT_SEARCH_N,
            }
        ),
    }
    return write_report(required, decision=decision, extra=extra, sheets=sheets)


if __name__ == "__main__":
    raise SystemExit(main())
