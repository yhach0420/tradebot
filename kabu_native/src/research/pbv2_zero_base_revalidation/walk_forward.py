"""Strict chronological walk-forward across generator series."""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from research.pbv2_zero_base_revalidation.generators import (
    RuleSpec,
    combined_rule_candidates,
    dense_rule_candidates,
    dynamic_gate,
    dynamic_rule_candidates,
    dynamic_status,
    fit_quantile,
    fit_rule_thresholds,
    h_board_ts_keep_factory,
    i_price_board_keep_factory,
    metrics_for,
    pbv2_baseline_keep,
    static_rule_candidates,
    winner_filter_specs,
)
from research.pbv2_zero_base_revalidation.leakage import audit_fold
from research.pbv2_zero_base_revalidation.metrics import aggregate_oos_daily
from research.pbv2_zero_base_revalidation.panel import CandidateRow


def _by_day(panel: Sequence[CandidateRow]) -> dict[str, list[CandidateRow]]:
    out: dict[str, list[CandidateRow]] = {}
    for r in panel:
        out.setdefault(r.day, []).append(r)
    return out


def _pnl_rows(rows: Sequence[CandidateRow]) -> list[CandidateRow]:
    return [r for r in rows if getattr(r, "pnl_evaluable", False) or r.cf_pnl_5bps is not None or r.cf_pnl is not None]


def chronological_oos(
    panel: Sequence[CandidateRow],
    *,
    min_train_days: int = 3,
) -> list[dict[str, Any]]:
    days = sorted({r.day for r in panel})
    by = _by_day(panel)
    folds: list[dict[str, Any]] = []
    for i, test_day in enumerate(days):
        train_days = days[:i]
        if len(train_days) < min_train_days:
            continue
        train = _pnl_rows([r for d in train_days for r in by[d]])
        test = _pnl_rows(by[test_day])
        meta = audit_fold(train_days, test_day)
        if not meta["max_train_date_lt_test"]:
            return [{"leakage_blocked": True, **meta}]
        folds.append(
            {
                **meta,
                "train_n": len(train),
                "test_n": len(test),
                "train_rows": train,
                "test_rows": test,
            }
        )
    return folds


def _evaluate_series_on_folds(
    folds: Sequence[dict[str, Any]],
    specs: Sequence[RuleSpec],
    *,
    universe_fn: Optional[Callable[[Sequence[CandidateRow]], list[CandidateRow]]] = None,
) -> list[dict[str, Any]]:
    results = []
    for spec in specs:
        oos_kept_metrics = []
        thr_hist = []
        for fold in folds:
            train = fold["train_rows"]
            test = fold["test_rows"]
            if universe_fn:
                train = universe_fn(train)
                test = universe_fn(test)
            fitted = fit_rule_thresholds(train, spec)
            thr_hist.append(
                {
                    "test_date": fold["test_date"],
                    "thresholds": list(fitted.thresholds),
                    "features": list(fitted.feature_keys),
                }
            )
            m = metrics_for(test, fitted.keep)
            m["test_date"] = fold["test_date"]
            oos_kept_metrics.append(m)
        # aggregate (pooled PF, not average of daily PF)
        agg = aggregate_oos_daily(oos_kept_metrics)
        results.append(
            {
                "rule_id": spec.rule_id,
                "series": spec.series,
                "description": spec.description,
                "features": list(spec.feature_keys),
                "ops": list(spec.ops),
                "threshold_history": thr_hist,
                "oos": agg,
                "last_thresholds": thr_hist[-1]["thresholds"] if thr_hist else [],
            }
        )
    results.sort(key=lambda x: (-(x["oos"].get("pnl_5bps") or -1e18), -(x["oos"].get("pf") or 0)))
    return results


def run_walk_forward(panel: Sequence[CandidateRow]) -> dict[str, Any]:
    print("[pbv2_zb] walk_forward start", flush=True)
    folds = chronological_oos(panel)
    if folds and folds[0].get("leakage_blocked"):
        return {"leakage_blocked": True, "folds": folds}

    # Series 1: PBv2 baseline (fixed rule, no threshold fit on score)
    pbv2_daily = []
    for fold in folds:
        m = metrics_for(fold["test_rows"], pbv2_baseline_keep)
        m["test_date"] = fold["test_date"]
        pbv2_daily.append(m)
    pbv2_oos = aggregate_oos_daily(pbv2_daily)

    # Baselines H/I + WinnerFilters on PBv2 decision universe
    baseline_rows = []
    h_daily = []
    i_daily = []
    for fold in folds:
        train, test = fold["train_rows"], fold["test_rows"]
        thr_imb = fit_quantile(train, "f_np_imb_chg_60", 0.2, "low")
        thr_chase = fit_quantile(train, "f_chase", 0.8, "high")
        thr_near = fit_quantile(train, "f_near_high", 0.8, "high")
        h_fn = h_board_ts_keep_factory(thr_imb)
        i_fn = i_price_board_keep_factory(thr_imb, thr_chase, thr_near)
        mh = metrics_for(test, h_fn)
        mi = metrics_for(test, i_fn)
        mh["test_date"] = fold["test_date"]
        mi["test_date"] = fold["test_date"]
        h_daily.append(mh)
        i_daily.append(mi)
    baseline_rows.append({"rule_id": "H_board_ts", "series": "baseline", "oos": aggregate_oos_daily(h_daily)})
    baseline_rows.append({"rule_id": "I_price_board", "series": "baseline", "oos": aggregate_oos_daily(i_daily)})

    wf_specs = winner_filter_specs()
    # parallel series evaluation (max 4)
    tasks = {
        "dense": dense_rule_candidates(),
        "static": static_rule_candidates(),
        "dynamic": dynamic_rule_candidates(),
        "winner": wf_specs,
    }
    series_out: dict[str, Any] = {}
    # Run sequentially in-process for Windows pickle safety; still respect max 4 conceptual series
    for name, specs in tasks.items():
        if name == "static":
            series_out[name] = _evaluate_series_on_folds(
                folds,
                specs,
                universe_fn=lambda rows: [
                    r
                    for r in rows
                    if r.day not in {"20260615", "20260616", "20260617", "20260618", "20260619"}
                    and r.board_quality == "TOP_ONLY"
                    and r.features.get("f_imb") is not None
                    and r.features.get("f_near_high") is not None
                    and r.features.get("f_tv") is not None
                ],
            )
        elif name == "dynamic":
            complete_days = sorted({r.day for r in panel if r.lane_c_complete})
            dyn_folds = []
            for fold in folds:
                st = dynamic_status(complete_days, fold["test_date"])
                fold2 = dict(fold)
                fold2["dynamic_status"] = st
                if st == "OOS_EVALUABLE":
                    fold2["train_rows"] = [r for r in fold["train_rows"] if r.lane_c_complete and r.day in complete_days and r.day < fold["test_date"]]
                    fold2["test_rows"] = [r for r in fold["test_rows"] if r.lane_c_complete]
                    dyn_folds.append(fold2)
            series_out[name] = _evaluate_series_on_folds(dyn_folds, specs) if dyn_folds else []
            series_out["dynamic_meta"] = {
                "lane_c_any_feature_days": sorted({r.day for r in panel if r.lane_c_any}),
                "lane_c_complete_required_days": complete_days,
                "lane_c_full_window_days": complete_days,
                "lane_c_oos_evaluable_days": [f["test_date"] for f in dyn_folds],
                "warmup_day": min(complete_days) if complete_days else None,
            }
        else:
            series_out[name] = _evaluate_series_on_folds(folds, specs)

    # Combined second stage only after 4 series
    series_out["combined"] = _evaluate_series_on_folds(
        folds,
        combined_rule_candidates(),
        universe_fn=lambda rows: [r for r in rows if r.lane_c_complete or r.features.get("f_mom") is not None],
    )

    best_dense = series_out["dense"][0] if series_out["dense"] else None
    best_static = series_out["static"][0] if series_out["static"] else None
    best_dynamic = series_out["dynamic"][0] if series_out["dynamic"] else None
    best_combined = series_out["combined"][0] if series_out["combined"] else None

    dyn_oos_days = len(series_out.get("dynamic_meta", {}).get("lane_c_oos_evaluable_days") or [])
    dyn_verdict = "DYNAMIC_BOARD_INSUFFICIENT_OOS"
    if best_dynamic:
        dyn_verdict = dynamic_gate(dyn_oos_days, best_dynamic["oos"], pbv2_oos)

    fold_summaries = [
        {
            "train_start": f["train_start"],
            "train_end": f["train_end"],
            "test_date": f["test_date"],
            "max_train_date": f["max_train_date"],
            "max_train_date_lt_test": f["max_train_date_lt_test"],
            "train_n": f["train_n"],
            "test_n": f["test_n"],
        }
        for f in folds
    ]

    return {
        "leakage_blocked": False,
        "folds": fold_summaries,
        "pbv2_baseline": {"rule_id": "PBv2_runtime", "oos": pbv2_oos},
        "baselines": baseline_rows + series_out.get("winner", []),
        "dense": series_out["dense"],
        "static": series_out["static"],
        "dynamic": series_out["dynamic"],
        "combined": series_out["combined"],
        "dynamic_meta": series_out.get("dynamic_meta") or {},
        "best": {
            "dense": best_dense,
            "static": best_static,
            "dynamic": best_dynamic,
            "combined": best_combined,
        },
        "dynamic_verdict": dyn_verdict,
    }
