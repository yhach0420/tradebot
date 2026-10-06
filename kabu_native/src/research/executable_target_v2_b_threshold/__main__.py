"""Offline EXECUTABLE TARGET V2 + B1 score-percentile study. No Runtime write. No Paper/OPVAL."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_timing_robustness.inventory import build_inventory
from research.executable_target_v2_b_threshold import (
    ANALYSIS_ID,
    B1_PERCENTILES,
    C14_ID,
    MAX_HARD_GATES,
    MAX_WORKERS,
    NEW_FORWARD_N,
    PRIMARY_TARGET,
    STRATEGY_RETUNED,
    WAIT_SEC,
)
from research.executable_target_v2_b_threshold.analyze import (
    INNER_SELECT_RULE,
    aggregate_coverage,
    b0_parity,
    b1_success,
    compare_b0_b1,
    entry_freeze,
    mfe_audit,
    nested_b1,
    occupancy_parity,
    pack_metrics,
    score_distribution,
    target_verdict,
)
from research.executable_target_v2_b_threshold.extract import process_day
from research.executable_target_v2_b_threshold.occupancy import occupancy_day
from research.executable_target_v2_b_threshold.publish import (
    OUT,
    build_markdown,
    kv_rows,
    write_artifacts,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER

REBUILD = NATIVE / "results" / "research" / "uniform10_entry_rebuild"
CACHE = OUT / "_work_cache"
C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str) -> Path:
    return CACHE / f"{day}_EXTRACT.json"


def _load_cache(day: str) -> dict | None:
    fp = _cache_fp(day)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == "EXTRACT_KEEPALL":
        return body
    return None


def _save_cache(day: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_fp(day).write_text(json.dumps(body, ensure_ascii=False, default=str), encoding="utf-8")


def _pool(jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(process_day, job): job["date"] for job in jobs}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"extract {day} ok={body.get('ok')} cand={len(body.get('candidates') or [])} "
                f"sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _empty_b1() -> dict:
    return {
        "BEST_B1_THRESHOLD": None,
        "trades": None,
        "PnL": None,
        "PF": None,
        "maxDD": None,
        "positive_day_rate": None,
        "median_daily_pnl": None,
        "PnL_ex_top3_trades": None,
        "PnL_ex_top3_days": None,
        "PnL_ex_top_symbol": None,
    }


def main() -> int:
    os.environ["PYTHONPATH"] = f"{SRC};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE.parent}"
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE RESEARCH ONLY", flush=True)
    print("C14 mutation forbidden. Runtime ENTRY/EXIT/CLOCK forbidden. Paper/OPVAL forbidden.", flush=True)

    freeze = entry_freeze()
    print("ENTRY freeze rank_pass_gate=", freeze.get("rank_pass_gate"), "FEATURE_ORDER=", freeze.get("FEATURE_ORDER"), flush=True)
    if list(FEATURE_ORDER) != [
        "spread_bps", "imbalance", "mid_ret_60s", "mid_ret_180s", "event_rate_60s", "log_bid_qty"
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    if freeze.get("rank_pass_gate") is not None:
        print("STOP rank_pass_gate is not null", flush=True)
        return 2

    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2

    inv = build_inventory()
    elig = [r for r in inv if r.get("replay_eligible") and r.get("universe_symbols") and r.get("capture_path")]
    days = [r["date"] for r in elig]
    print(f"eligible days={len(elig)} WAIT_SEC={WAIT_SEC} PRIMARY_TARGET={PRIMARY_TARGET}", flush=True)

    jobs = []
    extracted = []
    for r in elig:
        cached = _load_cache(r["date"])
        if cached:
            extracted.append(cached)
            print(f"extract {r['date']} cache-hit cand={len(cached.get('candidates') or [])}", flush=True)
        else:
            jobs.append(
                {
                    "date": r["date"],
                    "capture_path": r["capture_path"],
                    "universe": r["universe_symbols"],
                }
            )
    for body in _pool(jobs):
        extracted.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), body)
    extracted.sort(key=lambda x: str(x.get("date") or ""))
    failed = [r for r in extracted if not r.get("ok")]
    if failed:
        print("STOP extract failed", [(r.get("date"), r.get("blocker")) for r in failed], flush=True)
        return 2

    coverage: list[dict] = []
    candidates: list[dict] = []
    cand_by_day: dict[str, list] = {}
    for body in extracted:
        coverage.extend(body.get("coverage") or [])
        xs = body.get("candidates") or []
        candidates.extend(xs)
        cand_by_day[str(body.get("date"))] = xs
    print(f"coverage_rows={len(coverage)} candidates={len(candidates)}", flush=True)

    cov = aggregate_coverage(coverage)
    oof_ready = True  # nested LODO infrastructure is implemented in this package
    tgt = target_verdict(cov, oof_ready=oof_ready)
    print("TARGET_CONTRACT_V2_VALID", tgt.get("TARGET_CONTRACT_V2_VALID"), "PRIMARY_ROWS", tgt.get("PRIMARY_ROWS"), flush=True)

    b0_trades: list[dict] = []
    for d in days:
        bp = REBUILD / "_work_cache" / f"{d}_B_PANEL.json"
        if not bp.is_file():
            print("STOP missing B0 cache", d, flush=True)
            return 2
        b0_trades.extend(json.loads(bp.read_text(encoding="utf-8")).get("trades") or [])
    b0_head = pack_metrics(b0_trades, days)
    b0_check = b0_parity(b0_trades)
    print("B0 cache", b0_check.get("observed"), "ok", b0_check.get("ok"), flush=True)

    scores = score_distribution(candidates)
    occ0 = occupancy_day(candidates, None)
    occ_par = occupancy_parity(b0_trades, occ0)
    b0_occ_m = pack_metrics(occ0, days)
    print("occupancy parity ok", occ_par.get("ok"), "jaccard", occ_par.get("jaccard"), "occ_n", occ_par.get("occ_n"), flush=True)

    b_started = bool(
        tgt.get("TARGET_CONTRACT_V2_VALID")
        and b0_check.get("ok")
        and not scores.get("B1_SCORE_SCALE_INVALID")
    )
    b1_pack = None
    nested = None
    success = False
    success_checks: dict = {}
    b_verdict = "B1_NOT_STARTED"
    if not tgt.get("TARGET_CONTRACT_V2_VALID"):
        b_verdict = "B1_NOT_STARTED_TARGET_FAIL"
    elif not b0_check.get("ok"):
        b_verdict = "B1_NOT_STARTED_B0_MISMATCH"
    elif scores.get("B1_SCORE_SCALE_INVALID"):
        b_verdict = "B1_SCORE_THRESHOLD_INVALID_SCALE"
    else:
        print("nested B1 start search_space", list(B1_PERCENTILES), "occ_parity", occ_par.get("ok"), flush=True)
        nested = nested_b1(cand_by_day, days)
        b1_pack = pack_metrics(nested.get("oof_trades") or [], days)
        b0_occ = b0_occ_m
        # Fair threshold test is occupancy B0 vs occupancy B1 OOF (same engine).
        success, success_checks = b1_success(b0_occ, b1_pack)
        success_checks["occupancy_dual_parity"] = occ_par.get("ok")
        success_checks["success_baseline"] = "occupancy_NO_THRESHOLD_not_dual_ledger"
        if nested.get("mode_p") is None:
            b_verdict = "B1_SCORE_THRESHOLD_NO_ROBUST_IMPROVEMENT"
        elif success:
            b_verdict = "B1_SCORE_THRESHOLD_ROBUST_CANDIDATE_FOUND"
        else:
            b_verdict = "B1_SCORE_THRESHOLD_NO_ROBUST_IMPROVEMENT"

    c_status = (
        "C_REBUILD_V2_READY_FOR_FUTURE_SEARCH"
        if tgt.get("TARGET_CONTRACT_V2_VALID") and tgt.get("LODO_OOF_CONTRACT_READY")
        else "C_REBUILD_V2_NOT_READY"
    )

    b1_out = _empty_b1()
    if nested and b1_pack:
        ex = b1_pack.get("exclude") or {}
        b1_out = {
            "BEST_B1_THRESHOLD": nested.get("BEST_B1_THRESHOLD"),
            "trades": b1_pack.get("trades"),
            "PnL": b1_pack.get("PnL"),
            "PF": b1_pack.get("PF"),
            "maxDD": b1_pack.get("maxDD"),
            "avg_trade": b1_pack.get("avg_trade"),
            "median_trade": b1_pack.get("median_trade"),
            "positive_day_rate": b1_pack.get("positive_day_rate"),
            "median_daily_pnl": b1_pack.get("median_daily_pnl"),
            "PnL_ex_top3_trades": (ex.get("ex_top3_trades") or {}).get("PnL"),
            "PnL_ex_top3_days": (ex.get("ex_top3_days") or {}).get("PnL"),
            "PnL_ex_top_symbol": (ex.get("ex_top_symbol") or {}).get("PnL"),
            "PnL_ex_285A": (ex.get("ex_285A") or {}).get("PnL"),
            "AM_PnL": (b1_pack.get("AM") or {}).get("pnl"),
            "PM_PnL": (b1_pack.get("PM") or {}).get("pnl"),
            "first_entry_PnL": (b1_pack.get("first_entry") or {}).get("pnl"),
            "re_entry_PnL": (b1_pack.get("re_entry") or {}).get("pnl"),
        }

    mfe_cache = mfe_audit(b0_trades, source="B0_cache_dual")
    mfe_occ = mfe_audit(occ0, source="occupancy_hypo_floor0")
    if b1_pack:
        cmp_dual = compare_b0_b1(b0_head, b1_pack)
        cmp_occ = compare_b0_b1(b0_occ_m, b1_pack)
        cmp_rows = [{"engine": "Dual_B0_vs_occupancy_B1", **r} for r in (cmp_dual.get("rows") or [])] + [
            {"engine": "occupancy_B0_vs_occupancy_B1", **r} for r in (cmp_occ.get("rows") or [])
        ]
        cmp = {"rows": cmp_rows}
    else:
        cmp = {"rows": [{"metric": "B1", "B0": "n/a", "B1_OOF": "not started"}]}

    oof_contract = {
        "publish_primary_performance": "OOF_ONLY",
        "full_development_refit_on_same_data_forbidden": True,
        "outer": "leave_one_day_out",
        "inner_select_rule": INNER_SELECT_RULE,
        "held_out_labels_not_used_for_architecture": True,
        "feature_formula_frozen": True,
        "FEATURE_ORDER_frozen": list(FEATURE_ORDER),
        "weights_frozen": True,
        "serialized_model_frozen": True,
        "MAX_HARD_GATES_this_run": MAX_HARD_GATES,
        "per_feature_threshold_auto_started": False,
        "LODO_OOF_CONTRACT_READY": True,
        "search_space_precommitted": ["NO_THRESHOLD" if p is None else f"p{p}" for p in B1_PERCENTILES],
        "search_expanded_mid_run": False,
    }

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "PRIMARY_TARGET": PRIMARY_TARGET,
        "WAIT_SEC": WAIT_SEC,
        "fill_price": "limit_price",
        "FILL_SOT": "is_executable_continuous_board",
        "C14_ID": C14_ID,
        "C14_SHA": c14.get("sha256"),
        "development_days": days,
        "entry_freeze": freeze,
        "target": tgt,
        "coverage_overall": cov.get("overall"),
        "B0": {
            "trades": b0_head.get("trades"),
            "PnL": b0_head.get("PnL"),
            "PF": b0_head.get("PF"),
            "maxDD": b0_head.get("maxDD"),
            "avg_trade": b0_head.get("avg_trade"),
            "median_trade": b0_head.get("median_trade"),
            "positive_day_rate": b0_head.get("positive_day_rate"),
            "median_daily_pnl": b0_head.get("median_daily_pnl"),
            "AM_PnL": (b0_head.get("AM") or {}).get("pnl"),
            "PM_PnL": (b0_head.get("PM") or {}).get("pnl"),
            "first_entry_PnL": (b0_head.get("first_entry") or {}).get("pnl"),
            "re_entry_PnL": (b0_head.get("re_entry") or {}).get("pnl"),
            "PnL_ex_285A": ((b0_head.get("exclude") or {}).get("ex_285A") or {}).get("PnL"),
            "parity": b0_check,
        },
        "occupancy_parity": occ_par,
        "occupancy_B0": {
            "trades": b0_occ_m.get("trades"),
            "PnL": b0_occ_m.get("PnL"),
            "PF": b0_occ_m.get("PF"),
            "maxDD": b0_occ_m.get("maxDD"),
            "positive_day_rate": b0_occ_m.get("positive_day_rate"),
            "median_daily_pnl": b0_occ_m.get("median_daily_pnl"),
        },
        "score_distribution": {k: v for k, v in scores.items() if k not in {"by_day", "by_anchor"}},
        "B1": b1_out,
        "B1_success_checks": success_checks,
        "nested": None
        if nested is None
        else {k: v for k, v in nested.items() if k != "oof_trades"},
        "B_VERDICT": b_verdict,
        "C_REBUILD_V1_STATUS": "C_REBUILD_V1_INVALID_TARGET_CONTAMINATED",
        "C_REBUILD_V2_STATUS": c_status,
        "STRATEGY_RETUNED": STRATEGY_RETUNED,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "SAFETY": "submit/cancel/live=0/0/0",
        "oof_contract": oof_contract,
        "mfe_cache": mfe_cache,
        "mfe_occupancy": mfe_occ,
    }
    report["_markdown"] = build_markdown(report)

    def daily_rows(pack: dict, tag: str) -> list[dict]:
        out = []
        for r in (pack.get("daily") or {}).get("days") or []:
            rec = dict(r)
            rec["slice"] = tag
            out.append(rec)
        return out

    sheets = {
        "Summary": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "PRIMARY_TARGET": PRIMARY_TARGET,
                **{k: tgt.get(k) for k in (
                    "TARGET_CONTRACT_V2_VALID",
                    "PRIMARY_ROWS",
                    "ITAYOSE_BASE_ROW_N",
                    "NON_EXECUTABLE_T0_ROW_N",
                    "15_20_PRIMARY_TARGET_STATUS",
                    "LODO_OOF_CONTRACT_READY",
                    "TARGET_VERDICT",
                )},
                "B0_trades": b0_head.get("trades"),
                "B0_PnL": b0_head.get("PnL"),
                "B0_PF": b0_head.get("PF"),
                "B0_maxDD": b0_head.get("maxDD"),
                **{f"B1.{k}": v for k, v in b1_out.items()},
                "B_VERDICT": b_verdict,
                "C_REBUILD_V2_STATUS": c_status,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            }
        ),
        "Target_Contract": kv_rows(tgt) + kv_rows(freeze),
        "Target_Coverage": [{"grain": "overall", **(cov.get("overall") or {})}]
        + (cov.get("by_date") or [])
        + (cov.get("by_anchor") or [])
        + (cov.get("by_session") or [])
        + (cov.get("by_date_session") or [])
        + (cov.get("null_reasons") or []),
        "Endpoint_Audit": cov.get("endpoint_calendar")
        or [{"anchor": "15:20", "PRIMARY_STATUS": tgt.get("15_20_PRIMARY_TARGET_STATUS")}],
        "MFE_Audit": kv_rows(mfe_cache) + kv_rows(mfe_occ),
        "OOF_Contract": kv_rows(oof_contract),
        "B0": kv_rows(
            {
                **{k: b0_head.get(k) for k in ("trades", "PnL", "PF", "maxDD", "avg_trade", "median_trade", "positive_day_rate", "median_daily_pnl")},
                **(b0_check.get("observed") or {}),
                "parity_ok": b0_check.get("ok"),
                "occupancy_parity_ok": occ_par.get("ok"),
            }
        ),
        "Score_Distribution": kv_rows({k: v for k, v in scores.items() if k not in {"by_day", "by_anchor"}})
        + (scores.get("by_day") or [])
        + (scores.get("by_anchor") or []),
        "Threshold_Search": (nested.get("folds") if nested else None) or [{"status": b_verdict, "search_space_precommitted": True}],
        "Threshold_OOF": kv_rows(b1_out) if b1_pack else [{"status": b_verdict}],
        "B0_vs_B1": cmp.get("rows") or [{"empty": True}],
        "Concentration": [
            *([{"slice": "B0", **((b0_head.get("exclude") or {}).get(k) or {})} for k in (
                "ex_top1_trade", "ex_top3_trades", "ex_top10_trades", "ex_best_day", "ex_top3_days", "ex_top_symbol", "ex_top3_symbols", "ex_285A"
            )]),
            *([{"slice": "B1_OOF", **((b1_pack.get("exclude") or {}).get(k) or {})} for k in (
                "ex_top1_trade", "ex_top3_trades", "ex_top10_trades", "ex_best_day", "ex_top3_days", "ex_top_symbol", "ex_top3_symbols", "ex_285A"
            )] if b1_pack else []),
            {"slice": "B0_shares", **(b0_head.get("concentration") or {})},
            {"slice": "B1_shares", **((b1_pack or {}).get("concentration") or {})},
        ],
        "Daily": daily_rows(b0_head, "B0") + (daily_rows(b1_pack, "B1_OOF") if b1_pack else []),
        "Session": [
            {"slice": "B0_AM", **(b0_head.get("AM") or {})},
            {"slice": "B0_PM", **(b0_head.get("PM") or {})},
            {"slice": "B0_first", **(b0_head.get("first_entry") or {})},
            {"slice": "B0_reentry", **(b0_head.get("re_entry") or {})},
            {"slice": "B1_AM", **((b1_pack or {}).get("AM") or {})},
            {"slice": "B1_PM", **((b1_pack or {}).get("PM") or {})},
            {"slice": "B1_first", **((b1_pack or {}).get("first_entry") or {})},
            {"slice": "B1_reentry", **((b1_pack or {}).get("re_entry") or {})},
        ],
        "Decision": kv_rows(
            {
                **tgt,
                "B_VERDICT": b_verdict,
                "C_REBUILD_V2_STATUS": c_status,
                "C_REBUILD_V1_STATUS": "C_REBUILD_V1_INVALID_TARGET_CONTAMINATED",
                "B1_started": b_started,
                "success": success,
                **success_checks,
                "BEST_B1_THRESHOLD": b1_out.get("BEST_B1_THRESHOLD"),
                "per_feature_threshold_this_run": False,
                "NEW_FORWARD_N": NEW_FORWARD_N,
            }
        ),
        "Safety": kv_rows(
            {
                "OFFLINE_RESEARCH_ONLY": True,
                "C14_changed": False,
                "C14_ID": C14_ID,
                "C14_SHA": c14.get("sha256"),
                "Runtime_changed": False,
                "ENTRY_changed": False,
                "EXIT_changed": False,
                "CLOCK_changed": False,
                "UNIFORM10_activated": False,
                "B1_written_to_runtime": False,
                "new_strategy_candidate": False,
                "Paper_started": False,
                "OPVAL_started": False,
                "submit": 0,
                "cancel": 0,
                "live": 0,
                "STRATEGY_RETUNED": STRATEGY_RETUNED,
                "FEATURE_ORDER_changed": False,
                "weights_changed": False,
                "score_refit": False,
            }
        ),
    }
    write_artifacts(report, sheets)
    print("OUT", OUT, flush=True)
    print("TARGET_CONTRACT_V2_VALID", tgt.get("TARGET_CONTRACT_V2_VALID"), flush=True)
    print("PRIMARY_ROWS", tgt.get("PRIMARY_ROWS"), flush=True)
    print("ITAYOSE_BASE_ROW_N", tgt.get("ITAYOSE_BASE_ROW_N"), flush=True)
    print("NON_EXECUTABLE_T0_ROW_N", tgt.get("NON_EXECUTABLE_T0_ROW_N"), flush=True)
    print("15_20_PRIMARY_TARGET_STATUS", tgt.get("15_20_PRIMARY_TARGET_STATUS"), flush=True)
    print("LODO_OOF_CONTRACT_READY", tgt.get("LODO_OOF_CONTRACT_READY"), flush=True)
    print("B0", b0_head.get("trades"), b0_head.get("PnL"), b0_head.get("PF"), b0_head.get("maxDD"), flush=True)
    print("BEST_B1_THRESHOLD", b1_out.get("BEST_B1_THRESHOLD"), flush=True)
    print("B1_OOF", b1_out.get("trades"), b1_out.get("PnL"), b1_out.get("PF"), b1_out.get("maxDD"), flush=True)
    print("B_VERDICT", b_verdict, flush=True)
    print("C_REBUILD_V2_STATUS", c_status, flush=True)
    print("NEW_FORWARD_N", NEW_FORWARD_N, flush=True)
    print("STOP. Runtime not changed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
