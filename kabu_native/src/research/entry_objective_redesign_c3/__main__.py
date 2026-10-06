"""Offline C3 ENTRY objective redesign. No Runtime write. No Paper. Nested OOF then Exact iff gate."""
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
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.entry_objective_redesign_c3 import (
    ANALYSIS_ID,
    B2_FORMAL,
    C14_CHANGED,
    C14_ID,
    C2_STATUS_MAINTAINED,
    ELIGIBLE_DAYS,
    FEATURE_SETS,
    MAX_WORKERS,
    NEW_FORWARD_N,
    NORMS,
    OPVAL_OPERATED,
    PAPER_OPERATED,
    REENTRY_V2_FORMAL,
    RIDGE_ALPHAS,
    RUNTIME_CHANGED,
    TRUE_OOS,
    WAIT_SEC,
)
from research.entry_objective_redesign_c3.analyze import (
    a0_parity,
    a2_parity,
    abc_line,
    exact_success,
    paired_daily,
    ranking_gate,
    slim_pack,
    verdict,
)
from research.entry_objective_redesign_c3.extract import process_day as process_panel
from research.entry_objective_redesign_c3.oof import (
    eligibility_counts,
    fit_on_universe,
    inner_select,
    nested_oof,
    score_lookup_rows,
    slim_oof_rows,
    spec_grid,
    spec_label,
)
from research.entry_objective_redesign_c3.publish import (
    OUT,
    build_markdown,
    json_sanitize,
    kv_rows,
    write_artifacts,
)
from research.entry_objective_redesign_c3.replay import process_day as process_exact
from research.executable_target_v2_b_threshold.analyze import pack_metrics
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import CLOCK_GRID

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
CACHE = OUT / "_work_cache"
A_CACHE = NATIVE / "results" / "research" / "current_entry_nonexec_mechanism" / "_work_cache"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _cache_fp(day: str, stage: str) -> Path:
    return CACHE / f"{day}_{stage}.json"


def _load_cache(day: str, stage: str) -> dict | None:
    fp = _cache_fp(day, stage)
    if not fp.is_file():
        return None
    try:
        body = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None
    if body.get("ok") and body.get("date") == day and body.get("stage") == stage:
        return body
    return None


def _save_cache(day: str, stage: str, body: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    _cache_fp(day, stage).write_text(
        json.dumps(json_sanitize(body), ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _pool(fn, jobs: list[dict]) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job["date"] for job in jobs}
        for fut in as_completed(futs):
            day = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, "date": day, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {day} stage={body.get('stage')} ok={body.get('ok')} "
                f"n={len(body.get('rows') or body.get('trades') or [])} "
                f"sec={body.get('elapsed_sec')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _load_a_trades(stage: str) -> tuple[list[dict], list[str]]:
    trades: list[dict] = []
    missing: list[str] = []
    for day in ELIGIBLE_DAYS:
        fp = A_CACHE / f"{day}_{stage}.json"
        if not fp.is_file():
            missing.append(day)
            continue
        body = json.loads(fp.read_text(encoding="utf-8"))
        if not body.get("ok"):
            missing.append(day)
            continue
        trades.extend(body.get("trades") or [])
    return trades, missing


def _drop_daily(metrics: dict, n: int, field: str = "TOP3_UPLIFT") -> float | None:
    days = list(metrics.get("daily_top3") or [])
    ordered = sorted(days, key=lambda r: -float(r.get(field) or -9e9))
    drop = {d["date"] for d in ordered[:n]}
    keep = [r for r in days if r.get("date") not in drop and r.get(field) is not None]
    if not keep:
        return None
    import numpy as np

    return float(np.mean([float(r[field]) for r in keep]))


def main() -> int:
    os.environ["PYTHONPATH"] = (
        f"{SRC};{NATIVE / 'scripts'};{NATIVE.parent}" if os.name == "nt" else f"{SRC}:{NATIVE / 'scripts'}:{NATIVE.parent}"
    )
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE C3 ENTRY OBJECTIVE REDESIGN", flush=True)
    print("C14/Runtime/CLOCK/EXIT/CAP/re-entry/Fill frozen. Ridge only. No Paper/OPVAL.", flush=True)

    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        print("STOP FEATURE_ORDER drift", flush=True)
        return 2
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        print("STOP rank_pass_gate drift", flush=True)
        return 2
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        print("STOP: C14 identity mismatch", flush=True)
        return 2
    if len(spec_grid()) != 27:
        print("STOP spec grid is not 27", flush=True)
        return 2

    inv = build_inventory()
    want = set(ELIGIBLE_DAYS)
    elig = [
        r
        for r in inv
        if r.get("date") in want
        and r.get("replay_eligible")
        and r.get("universe_symbols")
        and r.get("capture_path")
    ]
    if len(elig) != len(ELIGIBLE_DAYS):
        print("STOP eligible day mismatch", len(elig), flush=True)
        return 2
    print(f"eligible days={len(elig)} CLOCK_GRID_N={len(CLOCK_GRID)} ANALYSIS_ID={ANALYSIS_ID}", flush=True)

    jobs = []
    extracted = []
    for r in elig:
        cached = _load_cache(r["date"], "C3_PANEL")
        if cached:
            extracted.append(cached)
            print(f"extract {r['date']} cache-hit rows={len(cached.get('rows') or [])}", flush=True)
        else:
            jobs.append({"date": r["date"], "capture_path": r["capture_path"], "universe": r["universe_symbols"]})
    for body in _pool(process_panel, jobs):
        extracted.append(body)
        if body.get("ok"):
            _save_cache(str(body.get("date")), "C3_PANEL", body)
    extracted.sort(key=lambda x: str(x.get("date") or ""))
    failed = [r for r in extracted if not r.get("ok")]
    if failed:
        print("STOP extract failed", [(r.get("date"), r.get("blocker")) for r in failed], flush=True)
        return 2

    rows: list[dict] = []
    for body in extracted:
        rows.extend(body.get("rows") or [])
    elig_n = eligibility_counts(rows)
    print(
        f"TARGET_V4={elig_n.get('TOTAL_TARGET_V4_ROWS')} EXEC_T0={elig_n.get('EXECUTABLE_T0_ROWS')} "
        f"NONEXEC_REMOVED={elig_n.get('NONEXEC_REMOVED_ROWS')} unexpected={elig_n.get('UNEXPECTED_TARGET_MISSING')}",
        flush=True,
    )
    integrity_ok = True
    if elig_n.get("STOP_UNEXPECTED_MISSING"):
        print("STOP unexpected TARGET missing", flush=True)
        integrity_ok = False

    oof_fp = CACHE / "nested_oof.json"
    if oof_fp.is_file():
        oof = json.loads(oof_fp.read_text(encoding="utf-8"))
        print(
            f"OOF cache-hit folds={len(oof.get('folds') or [])} "
            f"c3_top3={((oof.get('c3') or {}).get('TOP3_UPLIFT'))}",
            flush=True,
        )
    else:
        print("nested OOF start 18 outer x 27 specs x inner LODO", flush=True)
        oof = nested_oof(rows, fold_cache_dir=CACHE)
        dump = {
            "folds": oof.get("folds"),
            "selected_specs": oof.get("selected_specs"),
            "current": oof.get("current"),
            "c3": oof.get("c3"),
            "SPEC_COUNTS": oof.get("SPEC_COUNTS"),
            "MOST_COMMON_SPEC": oof.get("MOST_COMMON_SPEC"),
            "MOST_COMMON_SPEC_SHARE": oof.get("MOST_COMMON_SPEC_SHARE"),
            "oof_rows": slim_oof_rows(oof.get("oof_rows") or []),
        }
        CACHE.mkdir(parents=True, exist_ok=True)
        oof_fp.write_text(json.dumps(json_sanitize(dump), ensure_ascii=False, default=str), encoding="utf-8")
        print(
            f"OOF done CURRENT_TOP3={((oof.get('current') or {}).get('TOP3_UPLIFT'))} "
            f"C3_TOP3={((oof.get('c3') or {}).get('TOP3_UPLIFT'))}",
            flush=True,
        )

    current = oof.get("current") or {}
    c3m = oof.get("c3") or {}
    paired = paired_daily(current, c3m)
    gate = ranking_gate(current, c3m, paired)
    print(f"OOF_RANKING_GATE_PASS={gate.get('OOF_RANKING_GATE_PASS')} fail={gate.get('fail')}", flush=True)

    a0_trades, a0_miss = _load_a_trades("A0")
    a2_trades, a2_miss = _load_a_trades("A2")
    if a0_miss or a2_miss:
        print("STOP A0/A2 cache missing", a0_miss, a2_miss, flush=True)
        integrity_ok = False
        a0_pack = {}
        a2_pack = {}
        p0 = {"ok": False, "missing": a0_miss}
        p2 = {"ok": False, "missing": a2_miss}
    else:
        days = list(ELIGIBLE_DAYS)
        a0_pack = pack_metrics(a0_trades, days)
        a2_pack = pack_metrics(a2_trades, days)
        p0 = a0_parity(a0_pack)
        p2 = a2_parity(a2_pack)
        print(f"A0_PARITY={p0.get('ok')} {abc_line(a0_pack)}", flush=True)
        print(f"A2_PARITY={p2.get('ok')} {abc_line(a2_pack)}", flush=True)
        if not p0.get("ok") or not p2.get("ok"):
            integrity_ok = False

    exact_ran = False
    final_spec = None
    c3_pack = None
    success = None
    lookup_n = 0
    if integrity_ok and gate.get("OOF_RANKING_GATE_PASS"):
        print("final spec select on all development days", flush=True)
        inner = inner_select(rows)
        final_spec = inner.get("selected") or {}
        fit = fit_on_universe(rows, final_spec)
        lookup = score_lookup_rows(rows, fit)
        lookup_n = len(lookup)
        print(f"FINAL_SPEC={spec_label(final_spec)} lookup_n={lookup_n}", flush=True)
        by_date = {r["date"]: r for r in elig}
        exact_jobs = []
        exact_got = []
        for day in ELIGIBLE_DAYS:
            cached = _load_cache(day, "C3_EXACT")
            if cached:
                exact_got.append(cached)
                print(f"exact {day} cache-hit trades={len(cached.get('trades') or [])}", flush=True)
            else:
                r = by_date[day]
                day_lookup = {k: v for k, v in lookup.items() if k.startswith(f"{day}|")}
                exact_jobs.append(
                    {
                        "date": day,
                        "capture_path": r["capture_path"],
                        "universe": r["universe_symbols"],
                        "score_lookup": day_lookup,
                        "fit": {
                            k: fit.get(k)
                            for k in (
                                "feature_set",
                                "features",
                                "normalization",
                                "alpha",
                                "kind",
                                "scaler_mean",
                                "scaler_scale",
                                "coef",
                                "intercept",
                                "train_n",
                            )
                        },
                    }
                )
        for body in _pool(process_exact, exact_jobs):
            exact_got.append(body)
            if body.get("ok"):
                slim = {k: body[k] for k in ("ok", "date", "stage", "trades", "candidate_n", "elapsed_sec") if k in body}
                _save_cache(str(body.get("date")), "C3_EXACT", slim)
        exact_got.sort(key=lambda x: str(x.get("date") or ""))
        exact_fail = [r for r in exact_got if not r.get("ok")]
        if exact_fail:
            print("STOP C3 exact failed", [(r.get("date"), r.get("blocker")) for r in exact_fail], flush=True)
            integrity_ok = False
        else:
            c3_trades = []
            for body in exact_got:
                c3_trades.extend(body.get("trades") or [])
            c3_pack = pack_metrics(c3_trades, list(ELIGIBLE_DAYS))
            exact_ran = True
            success = exact_success(c3_pack, a0_pack, a2_pack, gate_pass=True)
            print(f"C3_EXACT {abc_line(c3_pack)} vs A2 {abc_line(a2_pack)} vs A0 {abc_line(a0_pack)}", flush=True)
    else:
        print("Exact Dual-Lane skipped (gate or integrity)", flush=True)

    ver, reason = verdict(
        integrity_ok=integrity_ok,
        gate=gate,
        exact_ran=exact_ran,
        success=success,
    )
    print(f"VERDICT={ver}", flush=True)

    required = {
        "A0_PARITY": bool(p0.get("ok")),
        "A2_PARITY": bool(p2.get("ok")),
        "TOTAL_TARGET_V4_ROWS": elig_n.get("TOTAL_TARGET_V4_ROWS"),
        "EXECUTABLE_T0_ROWS": elig_n.get("EXECUTABLE_T0_ROWS"),
        "NONEXEC_REMOVED_ROWS": elig_n.get("NONEXEC_REMOVED_ROWS"),
        "COHORTS_EXCLUDED_LT10": current.get("n_cohorts_excluded_lt10"),
        "CURRENT_ALL_TARGET": current.get("ALL_TARGET"),
        "CURRENT_TOP10_TARGET": current.get("TOP10_TARGET"),
        "CURRENT_TOP5_TARGET": current.get("TOP5_TARGET"),
        "CURRENT_TOP3_TARGET": current.get("TOP3_TARGET"),
        "CURRENT_TOP1_TARGET": current.get("TOP1_TARGET"),
        "CURRENT_TOP10_UPLIFT": current.get("TOP10_UPLIFT"),
        "CURRENT_TOP5_UPLIFT": current.get("TOP5_UPLIFT"),
        "CURRENT_TOP3_UPLIFT": current.get("TOP3_UPLIFT"),
        "CURRENT_TOP1_UPLIFT": current.get("TOP1_UPLIFT"),
        "CURRENT_MEAN_DAILY_SPEARMAN": current.get("MEAN_DAILY_SPEARMAN"),
        "CURRENT_TOP3_POSITIVE_DAY_COUNT": current.get("TOP3_POSITIVE_DAY_COUNT"),
        "C3_TOP1_UPLIFT": c3m.get("TOP1_UPLIFT"),
        "C3_TOP3_UPLIFT": c3m.get("TOP3_UPLIFT"),
        "C3_TOP5_UPLIFT": c3m.get("TOP5_UPLIFT"),
        "C3_TOP10_UPLIFT": c3m.get("TOP10_UPLIFT"),
        "C3_MEAN_DAILY_SPEARMAN": c3m.get("MEAN_DAILY_SPEARMAN"),
        "TOP3_DELTA_MEAN": paired.get("TOP3_DELTA_MEAN"),
        "TOP3_DELTA_MEDIAN": paired.get("TOP3_DELTA_MEDIAN"),
        "TOP3_DELTA_POSITIVE_DAYS": paired.get("TOP3_DELTA_POSITIVE_DAYS"),
        "TOP3_DELTA_NEGATIVE_DAYS": paired.get("TOP3_DELTA_NEGATIVE_DAYS"),
        "TOP3_DELTA_EX_BEST_DAY": paired.get("TOP3_DELTA_EX_BEST_DAY"),
        "TOP3_DELTA_EX_TOP3_DAYS": paired.get("TOP3_DELTA_EX_TOP3_DAYS"),
        "MOST_COMMON_SPEC": oof.get("MOST_COMMON_SPEC"),
        "MOST_COMMON_SPEC_SHARE": oof.get("MOST_COMMON_SPEC_SHARE"),
        "OOF_RANKING_GATE_PASS": bool(gate.get("OOF_RANKING_GATE_PASS")),
        "OOF_GATE_FAIL": gate.get("fail"),
        "FINAL_SPEC": spec_label(final_spec) if final_spec else None,
        "C3_EXACT_RAN": exact_ran,
        "A0": abc_line(a0_pack) if a0_pack else None,
        "A2": abc_line(a2_pack) if a2_pack else None,
        "C3": abc_line(c3_pack) if c3_pack else None,
        "C3_VS_A2": (success or {}).get("C3_VS_A2_PNL"),
        "C3_VS_A0": (success or {}).get("C3_VS_A0_PNL"),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": ver,
        "VERDICT_REASON": reason,
    }

    report = {
        "ANALYSIS_ID": ANALYSIS_ID,
        "required": required,
        "eligibility": elig_n,
        "gate": gate,
        "paired": {k: v for k, v in paired.items() if k != "days"},
        "SPEC_COUNTS": oof.get("SPEC_COUNTS"),
        "final_spec": {k: v for k, v in (final_spec or {}).items() if k != "features"} | (
            {"features": (final_spec or {}).get("features")} if final_spec else {}
        ),
        "lookup_n": lookup_n,
        "A0": slim_pack(a0_pack) if a0_pack else None,
        "A2": slim_pack(a2_pack) if a2_pack else None,
        "C3": slim_pack(c3_pack) if c3_pack else None,
        "exact_success": success,
        "frozen": {
            "C2": C2_STATUS_MAINTAINED,
            "B2": B2_FORMAL,
            "REENTRY_V2": REENTRY_V2_FORMAL,
            "C14_CHANGED": C14_CHANGED,
            "RUNTIME_CHANGED": RUNTIME_CHANGED,
            "PAPER_OPERATED": PAPER_OPERATED,
            "OPVAL_OPERATED": OPVAL_OPERATED,
            "B_USED_AS_GATE": False,
            "UNIFORM10_USED_AS_GATE": False,
        },
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
    }
    report["_markdown"] = build_markdown(report)

    robustness_rows = [
        {
            "TOP3_DELTA_EX_BEST_DAY": paired.get("TOP3_DELTA_EX_BEST_DAY"),
            "TOP3_DELTA_EX_TOP3_DAYS": paired.get("TOP3_DELTA_EX_TOP3_DAYS"),
            "C3_TOP3_EX_BEST_DAY": _drop_daily(c3m, 1),
            "C3_TOP3_EX_TOP3_DAYS": _drop_daily(c3m, 3),
            "CURRENT_TOP3_EX_BEST_DAY": _drop_daily(current, 1),
            "CURRENT_TOP3_EX_TOP3_DAYS": _drop_daily(current, 3),
            "C3_TOP3_POSITIVE_DAYS": c3m.get("TOP3_POSITIVE_DAY_COUNT"),
            "CURRENT_TOP3_POSITIVE_DAYS": current.get("TOP3_POSITIVE_DAY_COUNT"),
        }
    ]
    spec_rows = [{"spec_id": k, "outer_folds": v} for k, v in sorted((oof.get("SPEC_COUNTS") or {}).items())]
    topk_rows = [
        {
            "side": "CURRENT",
            "ALL_TARGET": current.get("ALL_TARGET"),
            "TOP1_TARGET": current.get("TOP1_TARGET"),
            "TOP3_TARGET": current.get("TOP3_TARGET"),
            "TOP5_TARGET": current.get("TOP5_TARGET"),
            "TOP10_TARGET": current.get("TOP10_TARGET"),
            "TOP1_UPLIFT": current.get("TOP1_UPLIFT"),
            "TOP3_UPLIFT": current.get("TOP3_UPLIFT"),
            "TOP5_UPLIFT": current.get("TOP5_UPLIFT"),
            "TOP10_UPLIFT": current.get("TOP10_UPLIFT"),
            "MEAN_DAILY_SPEARMAN": current.get("MEAN_DAILY_SPEARMAN"),
            "n_cohorts_used": current.get("n_cohorts_used"),
            "n_cohorts_excluded_lt10": current.get("n_cohorts_excluded_lt10"),
        },
        {
            "side": "C3_OOF",
            "ALL_TARGET": c3m.get("ALL_TARGET"),
            "TOP1_TARGET": c3m.get("TOP1_TARGET"),
            "TOP3_TARGET": c3m.get("TOP3_TARGET"),
            "TOP5_TARGET": c3m.get("TOP5_TARGET"),
            "TOP10_TARGET": c3m.get("TOP10_TARGET"),
            "TOP1_UPLIFT": c3m.get("TOP1_UPLIFT"),
            "TOP3_UPLIFT": c3m.get("TOP3_UPLIFT"),
            "TOP5_UPLIFT": c3m.get("TOP5_UPLIFT"),
            "TOP10_UPLIFT": c3m.get("TOP10_UPLIFT"),
            "MEAN_DAILY_SPEARMAN": c3m.get("MEAN_DAILY_SPEARMAN"),
            "n_cohorts_used": c3m.get("n_cohorts_used"),
            "n_cohorts_excluded_lt10": c3m.get("n_cohorts_excluded_lt10"),
        },
    ]
    exact_rows = [
        {"lane": "A0", **(slim_pack(a0_pack) if a0_pack else {"missing": True})},
        {"lane": "A2", **(slim_pack(a2_pack) if a2_pack else {"missing": True})},
        {"lane": "C3", **(slim_pack(c3_pack) if c3_pack else {"C3_EXACT_RAN": False})},
    ]
    sheets = {
        "Summary": kv_rows(required),
        "Manifest": kv_rows(
            {
                "ANALYSIS_ID": ANALYSIS_ID,
                "TARGET": "TARGET V4 M4_PERSISTENT forward_mid_return_600s",
                "CLOCK": "CURRENT IRREGULAR CLOCK_GRID",
                "CLOCK_GRID": [f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID],
                "Fill": "Corrected Fill SoT is_executable_continuous_board WAIT_SEC=1 fill_price=limit_price",
                "EXIT": "CURRENT",
                "CAP": 5,
                "reentry": "CURRENT",
                "freshness_sec": 5,
                "model": "sklearn.linear_model.Ridge",
                "alphas": list(RIDGE_ALPHAS),
                "normalizations": list(NORMS),
                "feature_sets": {k: list(v) for k, v in FEATURE_SETS.items()},
                "n_specs": 27,
                "primary_metric": "MEAN_DAILY_TOP3_UPLIFT",
                "aggregation": "date×anchor uplift -> daily mean -> equal-weight days",
                "min_cohort_n": 10,
                "outer": "leave-one-day-out",
                "inner": "day-blocked CV on remaining days",
                "B_gate": False,
                "UNIFORM10_gate": False,
            }
        ),
        "Eligibility": kv_rows(elig_n),
        "Current_Baseline": kv_rows(
            {k: current.get(k) for k in current if k not in {"daily_top3", "daily_top1", "daily_top5", "daily_top10", "daily_spearman", "daily_all"}}
        ),
        "Outer_Folds": [
            {k: v for k, v in r.items() if k != "inner_leaderboard_head"} for r in (oof.get("folds") or [])
        ],
        "Spec_Selection": spec_rows or [{"empty": True}],
        "TopK_OOF": topk_rows,
        "Daily_Paired": paired.get("days") or [{"empty": True}],
        "Robustness": robustness_rows,
        "A0_A2_Parity": kv_rows({"A0": p0, "A2": p2}),
        "Exact_Portfolio": exact_rows + kv_rows(success or {"C3_EXACT_RAN": False}),
        "Safety": kv_rows(
            {
                "submit_cancel_live": "0/0/0",
                "Paper": 0,
                "OPVAL": 0,
                "C14_CHANGED": C14_CHANGED,
                "RUNTIME_CHANGED": RUNTIME_CHANGED,
                "C2_STATUS_MAINTAINED": C2_STATUS_MAINTAINED,
                "B2_FORMAL": B2_FORMAL,
                "REENTRY_V2_FORMAL": REENTRY_V2_FORMAL,
                "TRUE_OOS": TRUE_OOS,
                "NEW_FORWARD_N": NEW_FORWARD_N,
                "WAIT_SEC": WAIT_SEC,
                "occupancy_approximation": False,
            }
        ),
    }
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print("STOP. C3 not implemented into Runtime. C14 unchanged.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
