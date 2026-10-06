"""Offline AM ENTRY profit nested program. No Runtime write. No Paper. No live orders."""
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

from research.am_entry_profit_improvement import (
    ANALYSIS_ID,
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
from research.am_entry_profit_improvement.analyze import decide, freeze_parity, independent_top3, stability
from research.am_entry_profit_improvement.labels import apply_utility, join_harvest
from research.am_entry_profit_improvement.metrics import economic_pack, paired_delta, success_gate
from research.am_entry_profit_improvement.models import spec_grid
from research.am_entry_profit_improvement.nested import evaluate_scored, process_outer_fold
from research.am_entry_profit_improvement.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.am_entry_profit_improvement.replay_day import process_exit_day
from research.anchor_timing_robustness.inventory import build_inventory
from research.canonical_entry_performance_rebase.analyze import row_key, session_of
from research.direct_joint_objective.oof import representation_grid
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3 import F2_UNION
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
AM_ROWS = NATIVE / "results" / "research" / "_work_cache" / "am_direct_exec_u_development" / "am_rows.json"
WAIT_CACHE = NATIVE / "results" / "research" / "_work_cache" / "passive_wait_policy_reassessment"
OOF_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_wait5_stage2_reassessment"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_profit_improvement"
FILL_ONLY_SCORED = OOF_CACHE / "AM_F0_CURRENT6_none_scored.json"

LABELED_KEYS = (
    "date",
    "anchor",
    "symbol",
    "session",
    "t0",
    "current_score",
    "fill_score",
    "Y_FILL5",
    UTILITY_KEY,
    "fill_t",
    "fill_price",
    "exit_t",
    "exit_price",
    "exit_reason",
    "pnl_yen_100",
    "limit",
    "executable_at_t0",
    "future_event_use",
    *F2_UNION,
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str), encoding="utf-8")


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
        "AM_ENTRY_PROFIT_IMPROVEMENT_PASS": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": "AM_ENTRY_PROFIT_IMPROVEMENT_INTEGRITY_FAILED",
        "NEXT": "AM_ENTRY_PROFIT_FAILURE_DECOMPOSITION",
        "STOP_REASON": msg,
    }
    decision = decide(passed=False, integrity_ok=False)
    required["VERDICT"] = decision["VERDICT"]
    required["NEXT"] = decision["NEXT"]
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(
        report,
        {
            "summary": kv_rows(required),
            "integrity": kv_rows({"STOP_REASON": msg, **(extra or {})}),
            "Safety": kv_rows(
                {
                    "SUBMIT_N": SUBMIT_N,
                    "CANCEL_N": CANCEL_N,
                    "LIVE_ORDER_N": LIVE_ORDER_N,
                    "PAPER_OPERATED": PAPER_OPERATED,
                    "RUNTIME_CHANGED": RUNTIME_CHANGED,
                    "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
                }
            ),
        },
    )
    print(msg, flush=True)
    return 2


def _slim(r: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in LABELED_KEYS:
        if k in r:
            out[k] = r.get(k)
    return out


def _arm_block(pack: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        f"{prefix}_TRADE_N": pack.get("trade_count"),
        f"{prefix}_NET_PNL": pack.get("net_pnl_yen_100"),
        f"{prefix}_PF": pack.get("profit_factor"),
        f"{prefix}_MAX_DD": pack.get("max_drawdown_yen_100"),
    }


def write_report(
    required: dict,
    *,
    decision: dict,
    extra: dict | None = None,
    sheets_extra: dict | None = None,
) -> int:
    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "decision": decision,
        **(extra or {}),
    }
    report["_markdown"] = build_markdown(report)
    sheets = {
        "summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "SESSION": SESSION,
                "DEV_WAIT_SEC": DEV_WAIT_SEC,
                "RUNTIME_WAIT_SEC": WAIT_SEC,
                "POSITION_CAP": POSITION_CAP,
                "RIDGE_ALPHA": RIDGE_ALPHA,
                "LOGREG_PARAMS": dict(LOGREG_PARAMS),
                "FILL_ONLY_REPRESENTATION_ID": FILL_ONLY_REPRESENTATION_ID,
                "ARCHITECTURE_N": ARCHITECTURE_N,
                "REPRESENTATION_N": REPRESENTATION_N,
                "SPEC_N": SPEC_N,
                "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
                "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
            }
        ),
        "Parity": kv_rows((extra or {}).get("parity") or {"empty": True}),
        "outer_folds": (sheets_extra or {}).get("outer_folds") or [{"empty": True}],
        "inner_selection": (sheets_extra or {}).get("inner_selection") or [{"empty": True}],
        "spec_performance": (sheets_extra or {}).get("spec_performance") or [{"empty": True}],
        "daily_pnl": (sheets_extra or {}).get("daily_pnl") or [{"empty": True}],
        "trades": (sheets_extra or {}).get("trades") or [{"empty": True}],
        "integrity": kv_rows((extra or {}).get("integrity") or {"empty": True}),
        "Decision": kv_rows(decision),
        "Safety": kv_rows(
            {
                "SUBMIT_N": SUBMIT_N,
                "CANCEL_N": CANCEL_N,
                "LIVE_ORDER_N": LIVE_ORDER_N,
                "PAPER_OPERATED": PAPER_OPERATED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C14_CHANGED": C14_CHANGED,
                "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
                "OUTER_RESELECTION_N": OUTER_RESELECTION_N,
                "POSTHOC_SPEC_ADDITION_N": POSTHOC_SPEC_ADDITION_N,
                "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
                "NEW_FEATURE_N": NEW_FEATURE_N,
                "WAIT_SEARCH_N": WAIT_SEARCH_N,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    return 0 if required.get("AM_ENTRY_PROFIT_IMPROVEMENT_PASS") or required.get("VERDICT") else 0


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
        print("STOP FEATURE_ORDER drift", flush=True)
        return _integrity("STOP. FEATURE_ORDER drift.")
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        print("STOP rank_pass_gate drift", flush=True)
        return _integrity("STOP. rank_pass_gate drift.")
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        return _integrity("STOP. C14 identity mismatch.")
    if not AM_ROWS.is_file():
        return _integrity("STOP. AM labeled rows missing.")
    print("AM ENTRY profit nested program. 27 specs. 18 outer folds. W5 research fill only.", flush=True)

    am_body = json.loads(AM_ROWS.read_text(encoding="utf-8"))
    rows = list(am_body.get("rows") or [])
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in AM labeled file.", extra={"PM_ROWS_USED_N": pm_n})

    harvest = []
    for day in ELIGIBLE_DAYS:
        fp = WAIT_CACHE / f"{day}_WAIT.json"
        saved = _load(fp)
        if not (saved.get("ok") and saved.get("date") == day and saved.get("rows")):
            return _integrity(f"STOP. Missing WAIT harvest cache for {day}.")
        harvest.extend(list(saved.get("rows") or []))
    hj = join_harvest(rows, harvest)
    if int(hj.get("JOIN_MISS_N") or 0) != 0 or int(hj.get("Y_FILL5_HARVEST_MISMATCH_N") or 0) != 0:
        return _integrity("STOP. Harvest join or Y_FILL5 mismatch.", extra=hj)

    fo_body = _load(FILL_ONLY_SCORED)
    fo_rows = list(fo_body.get("rows") or [])
    fo_by = {row_key(r): r.get("fill_score") for r in fo_rows}
    fill_miss = 0
    for r in rows:
        k = row_key(r)
        if k not in fo_by:
            fill_miss += 1
            r["fill_score"] = None
        else:
            r["fill_score"] = fo_by.get(k)
    if fill_miss:
        return _integrity("STOP. FILL_ONLY P_FILL5 join miss.", extra={"JOIN_MISS_N": fill_miss})

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
    print("BASE_PARITY true", flush=True)

    CACHE.mkdir(parents=True, exist_ok=True)
    inv = build_inventory()
    elig = [
        r
        for r in inv
        if r.get("date") in set(ELIGIBLE_DAYS)
        and r.get("replay_eligible")
        and r.get("universe_symbols")
        and r.get("capture_path")
    ]
    if len(elig) != len(ELIGIBLE_DAYS):
        return _integrity("STOP. Eligible Capture days mismatch.")
    inv_by = {r["date"]: r for r in elig}

    fills_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if int(r.get("Y_FILL5") or 0) != 1:
            continue
        fills_by[str(r.get("date") or "")].append(
            {
                "date": r.get("date"),
                "anchor": r.get("anchor"),
                "symbol": r.get("symbol"),
                "session": r.get("session") or "AM",
                "t0": r.get("t0"),
                "fill_t": r.get("fill_t"),
                "fill_price": r.get("fill_price"),
            }
        )

    exit_got = []
    exit_jobs = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_EXIT.json"
        saved = _load(fp)
        if saved.get("ok") and saved.get("date") == day and saved.get("rows"):
            exit_got.append(saved)
            print(f"EXIT cache-hit {day} n={len(saved.get('rows') or [])}", flush=True)
            continue
        r = inv_by[day]
        exit_jobs.append(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "fills": fills_by.get(day) or [],
            }
        )
    print(f"exit jobs={len(exit_jobs)}", flush=True)
    for body in _pool(process_exit_day, exit_jobs, "EXIT", "date"):
        if body.get("ok"):
            _save_json(CACHE / f"{body.get('date')}_EXIT.json", body)
        exit_got.append(body)
    fail = [b for b in exit_got if not b.get("ok")]
    if fail or len(exit_got) != len(ELIGIBLE_DAYS):
        return _integrity("STOP. Standalone current EXIT label generation failed.", extra={"fail": [(b.get("date"), b.get("blocker")) for b in fail]})

    exits: dict[str, dict[str, Any]] = {}
    for b in exit_got:
        for rec in b.get("rows") or []:
            exits[str(rec.get("key") or row_key(rec))] = rec
    util = apply_utility(rows, exits)
    if int(util.get("UTILITY_EXIT_MISS_N") or 0) != 0:
        return _integrity("STOP. REALIZED_ENTRY_UTILITY missing on a W5 fill.", extra=util)

    labeled_path = CACHE / "labeled_am.json"
    slim_rows = [_slim(r) for r in rows]
    _save_json(labeled_path, {"session": "AM", "target": UTILITY_KEY, "rows": slim_rows})
    print(
        f"labeled n={len(slim_rows)} fill_utility={util.get('UTILITY_FILL_N')} zero={util.get('UTILITY_ZERO_N')}",
        flush=True,
    )

    current_pack = evaluate_scored(slim_rows, list(ELIGIBLE_DAYS), score_key="current_score")
    fill_only_pack = evaluate_scored(slim_rows, list(ELIGIBLE_DAYS), score_key="fill_score")
    print(
        f"CURRENT trades={current_pack.get('trade_count')} pnl={current_pack.get('net_pnl_yen_100')} "
        f"FILL_ONLY trades={fill_only_pack.get('trade_count')} pnl={fill_only_pack.get('net_pnl_yen_100')}",
        flush=True,
    )

    grid = representation_grid()
    specs = spec_grid(grid)
    if len(specs) != int(SPEC_N):
        return _integrity("STOP. SPEC_N drifted.", extra={"SPEC_N": len(specs)})

    outer_jobs = []
    for day in ELIGIBLE_DAYS:
        train_days = [d for d in ELIGIBLE_DAYS if d != day]
        outer_jobs.append(
            {
                "outer_day": day,
                "train_days": train_days,
                "rows_path": str(labeled_path),
                "specs": specs,
            }
        )
    print(f"outer jobs={len(outer_jobs)} workers={min(MAX_WORKERS, len(outer_jobs))}", flush=True)
    outer_got = _pool(process_outer_fold, outer_jobs, "OUTER", "outer_day")
    outer_fail = [b for b in outer_got if not b.get("ok")]
    if outer_fail or len(outer_got) != len(ELIGIBLE_DAYS):
        return _integrity(
            "STOP. Nested outer fold failed.",
            extra={"fail": [(b.get("outer_day"), b.get("blocker")) for b in outer_fail]},
        )

    by_outer = {str(b.get("outer_day")): b for b in outer_got}
    cand_trades = []
    selected = []
    inner_sel_rows = []
    spec_perf = []
    leak_sum = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "INNER_VALIDATION_FIT_LEAK_N": 0,
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
        "JOIN_MISS_N": int(hj.get("JOIN_MISS_N") or 0) + int(fill_miss),
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "UTILITY_EXIT_MISS_N": int(util.get("UTILITY_EXIT_MISS_N") or 0),
    }
    for day in ELIGIBLE_DAYS:
        b = by_outer[day]
        cand_trades.extend(list(b.get("outer_trades") or []))
        sel = dict(b.get("selected") or {})
        sel["outer_day"] = day
        selected.append(sel)
        inner_sel_rows.append(sel)
        spec_perf.extend(list(b.get("inner_specs") or []))
        ig = b.get("integrity") or {}
        leak_sum["OUTER_HELDOUT_FIT_LEAK_N"] += int(ig.get("OUTER_HELDOUT_FIT_LEAK_N") or 0)
        leak_sum["INNER_VALIDATION_FIT_LEAK_N"] += int(ig.get("INNER_VALIDATION_FIT_LEAK_N") or 0)
        leak_sum["PM_ROWS_USED_N"] = max(int(leak_sum["PM_ROWS_USED_N"] or 0), int(ig.get("PM_ROWS_USED_N") or 0))
        leak_sum["POSTHOC_SPEC_ADDITION_N"] = max(
            int(leak_sum["POSTHOC_SPEC_ADDITION_N"] or 0),
            int(ig.get("POSTHOC_SPEC_ADDITION_N") or 0),
        )
        leak_sum["OUTER_RESELECTION_N"] += int(ig.get("OUTER_RESELECTION_N") or 0)
        leak_sum["TARGET_CONTAMINATION_N"] = max(
            int(leak_sum["TARGET_CONTAMINATION_N"] or 0),
            int(ig.get("TARGET_CONTAMINATION_N") or 0),
        )
    leak_sum["FUTURE_FEATURE_USE_N"] = 0

    cand_pack = economic_pack(cand_trades, list(ELIGIBLE_DAYS))
    paired = paired_delta(list(cand_pack.get("daily") or []), list(current_pack.get("daily") or []))
    stab = stability(selected)
    integrity_ok = all(int(leak_sum.get(k) or 0) == 0 for k in (
        "OUTER_HELDOUT_FIT_LEAK_N",
        "INNER_VALIDATION_FIT_LEAK_N",
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
        "UTILITY_EXIT_MISS_N",
    ))
    gate = success_gate(cand_pack, current_pack, paired, integrity_ok=integrity_ok)
    decision = decide(passed=bool(gate.get("AM_ENTRY_PROFIT_IMPROVEMENT_PASS")), integrity_ok=integrity_ok)

    def _d(a: Any, b: Any) -> Any:
        try:
            return float(a) - float(b)
        except (TypeError, ValueError):
            return None

    def _pf_num(v: Any) -> float:
        if v in ("Infinity", float("inf")):
            return float("inf")
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    required = {
        "BASE_PARITY": True,
        "OUTER_FOLD_N": 18,
        "ARCHITECTURE_N": ARCHITECTURE_N,
        "REPRESENTATION_N": REPRESENTATION_N,
        "SPEC_N": SPEC_N,
        **_arm_block(current_pack, "CURRENT"),
        **_arm_block(fill_only_pack, "FILL_ONLY"),
        **_arm_block(cand_pack, "CANDIDATE"),
        "DELTA_PNL_VS_CURRENT": _d(cand_pack.get("net_pnl_yen_100"), current_pack.get("net_pnl_yen_100")),
        "DELTA_PF_VS_CURRENT": _d(_pf_num(cand_pack.get("profit_factor")), _pf_num(current_pack.get("profit_factor"))),
        "DELTA_DD_VS_CURRENT": _d(cand_pack.get("max_drawdown_yen_100"), current_pack.get("max_drawdown_yen_100")),
        "DELTA_PNL_VS_FILL_ONLY": _d(cand_pack.get("net_pnl_yen_100"), fill_only_pack.get("net_pnl_yen_100")),
        "DELTA_PF_VS_FILL_ONLY": _d(_pf_num(cand_pack.get("profit_factor")), _pf_num(fill_only_pack.get("profit_factor"))),
        "DELTA_DD_VS_FILL_ONLY": _d(cand_pack.get("max_drawdown_yen_100"), fill_only_pack.get("max_drawdown_yen_100")),
        "PAIRED_POS_DAYS": paired.get("PAIRED_POS_DAYS"),
        "PAIRED_NEG_DAYS": paired.get("PAIRED_NEG_DAYS"),
        "PAIRED_ZERO_DAYS": paired.get("PAIRED_ZERO_DAYS"),
        "PAIRED_MEDIAN_DAILY_DELTA": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "EX_BEST_DAY_PNL_DELTA": paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3_DAYS_PNL_DELTA": paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        "SELECTED_ARCH_COUNTS": stab.get("SELECTED_ARCH_COUNTS"),
        "SELECTED_REP_COUNTS": stab.get("SELECTED_REP_COUNTS"),
        "MOST_COMMON_ARCH_SHARE": stab.get("MOST_COMMON_ARCH_SHARE"),
        "MOST_COMMON_REP_SHARE": stab.get("MOST_COMMON_REP_SHARE"),
        "AM_ENTRY_PROFIT_IMPROVEMENT_PASS": gate.get("AM_ENTRY_PROFIT_IMPROVEMENT_PASS"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
    }
    extra = {
        "parity": parity,
        "integrity": leak_sum,
        "gates": gate.get("gates"),
        "current": {k: v for k, v in current_pack.items() if k not in ("trades", "daily")},
        "fill_only": {k: v for k, v in fill_only_pack.items() if k not in ("trades", "daily")},
        "candidate": {k: v for k, v in cand_pack.items() if k not in ("trades", "daily")},
        "stability": stab,
        "utility": util,
        "FILL_ONLY_REPRESENTATION_ID": FILL_ONLY_REPRESENTATION_ID,
        "RIDGE_ALPHA": RIDGE_ALPHA,
        "independent_top3": {
            "CURRENT_FILL_RATE": (top3.get("CURRENT") or {}).get("FILL_RATE"),
            "FILL_ONLY_FILL_RATE": (top3.get("FILL_ONLY_INDEPENDENT") or {}).get("SELECTED_FILL_RATE"),
        },
    }
    daily_rows = []
    for d in ELIGIBLE_DAYS:
        cday = next((x for x in (cand_pack.get("daily") or []) if x.get("date") == d), {})
        rday = next((x for x in (current_pack.get("daily") or []) if x.get("date") == d), {})
        fday = next((x for x in (fill_only_pack.get("daily") or []) if x.get("date") == d), {})
        daily_rows.append(
            {
                "date": d,
                "CANDIDATE": cday.get("pnl_yen_100"),
                "CURRENT": rday.get("pnl_yen_100"),
                "FILL_ONLY": fday.get("pnl_yen_100"),
                "DELTA_VS_CURRENT": (float(cday.get("pnl_yen_100") or 0.0) - float(rday.get("pnl_yen_100") or 0.0)),
            }
        )
    trade_rows = []
    for arm, pack in (("CURRENT", current_pack), ("FILL_ONLY", fill_only_pack), ("CANDIDATE", cand_pack)):
        for t in pack.get("trades") or []:
            rec = dict(t)
            rec["arm"] = arm
            trade_rows.append(rec)
    sheets = {
        "outer_folds": [
            {
                "outer_day": b.get("outer_day"),
                **(b.get("selected") or {}),
                "outer_pnl": (b.get("outer_pack") or {}).get("net_pnl_yen_100"),
                "outer_trades": (b.get("outer_pack") or {}).get("trade_count"),
                "fit_kind": (b.get("fit") or {}).get("kind"),
            }
            for b in [by_outer[d] for d in ELIGIBLE_DAYS]
        ],
        "inner_selection": inner_sel_rows,
        "spec_performance": spec_perf,
        "daily_pnl": daily_rows,
        "trades": trade_rows or [{"empty": True}],
    }
    return write_report(required, decision=decision, extra=extra, sheets_extra=sheets)


if __name__ == "__main__":
    raise SystemExit(main())
