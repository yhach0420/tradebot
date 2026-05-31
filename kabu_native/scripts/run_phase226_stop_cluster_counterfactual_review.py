#!/usr/bin/env python3
"""
Phase226: Stop cluster counterfactual review (review only).

Test whether excluding Phase225 high-stop clusters improves PF/PnL.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase226_stop_cluster_counterfactual_review.json"

FOCUS_SYMBOLS = ("6203.T", "6659.T", "9348.T", "4888.T")

FEATURES: tuple[tuple[str, str], ...] = (
    ("TV", "trading_value"),
    ("VWAP", "entry_vwap_dev_pct"),
    ("Board", "entry_order_book_imbalance"),
    ("TickRatio", "tick_ratio_pct"),
)


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_hit_count": 0,
            "stop_hit_rate": None,
        }
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    n = len(rows)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
        "stop_hit_count": stops,
        "stop_hit_rate": round(stops / n, 4),
    }


def _gate_impact(kept: list[dict[str, Any]], excluded: list[dict[str, Any]]) -> dict[str, Any]:
    win_ex = [r for r in excluded if float(r.get("pnl_pct") or 0) > 0]
    lose_ex = [r for r in excluded if float(r.get("pnl_pct") or 0) < 0]
    stop_ex = [r for r in excluded if r.get("stop_hit")]
    return {
        "winner_missed_count": len(win_ex),
        "winner_missed_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in win_ex), 4),
        "loser_avoided_count": len(lose_ex),
        "loser_avoided_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in lose_ex), 4),
        "stop_avoided_count": len(stop_ex),
        "net_excluded_pnl_pct": round(sum(float(r.get("pnl_pct") or 0) for r in excluded), 4),
    }


def _evaluate(rows: list[dict[str, Any]], keep_fn: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    kept = [r for r in rows if keep_fn(r)]
    excluded = [r for r in rows if not keep_fn(r)]
    return {
        "kept": _metrics(kept),
        "excluded_trade_count": len(excluded),
        "kept_share_pct": round(100.0 * len(kept) / max(1, len(rows)), 2),
        "gate_impact": _gate_impact(kept, excluded),
    }


def _slice_eval(rows: list[dict[str, Any]], keep_fn: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    kept = [r for r in rows if keep_fn(r)]
    excluded = [r for r in rows if not keep_fn(r)]
    return {
        "kept": _metrics(kept),
        "excluded": len(excluded),
        "gate_impact": _gate_impact(kept, excluded),
    }


def _build_tertiles(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cuts: dict[str, dict[str, Any]] = {}
    for label, field in FEATURES:
        vals = [_float(r.get(field)) for r in rows]
        vals = [v for v in vals if v is not None]
        if len(vals) < 9:
            cuts[label] = {"field": field, "coverage_n": len(vals), "usable": False}
            continue
        cuts[label] = {
            "field": field,
            "coverage_n": len(vals),
            "coverage_pct": round(100.0 * len(vals) / len(rows), 2),
            "usable": True,
            "p33": round(_quantile(vals, 1.0 / 3.0), 6),
            "p66": round(_quantile(vals, 2.0 / 3.0), 6),
        }
    return cuts


def _bin_label(val: float, q33: float, q66: float) -> str:
    if val <= q33:
        return "low"
    if val <= q66:
        return "mid"
    return "high"


def _assign_bins(rows: list[dict[str, Any]], cuts: dict[str, dict[str, Any]]) -> None:
    for r in rows:
        bins: dict[str, str] = {}
        for label, info in cuts.items():
            if not info.get("usable"):
                continue
            v = _float(r.get(info["field"]))
            if v is None:
                continue
            bins[label] = _bin_label(v, info["p33"], info["p66"])
        r["_bins"] = bins


def _bin(r: dict[str, Any], label: str) -> Optional[str]:
    return (r.get("_bins") or {}).get(label)


def _cluster_b(r: dict[str, Any]) -> bool:
    return _bin(r, "Board") == "low" and _bin(r, "TickRatio") == "high"


def _cluster_c(r: dict[str, Any]) -> bool:
    return _bin(r, "VWAP") == "high" and _bin(r, "TickRatio") == "high"


def _cluster_d(r: dict[str, Any]) -> bool:
    return (
        _bin(r, "VWAP") == "high"
        and _bin(r, "Board") == "low"
        and _bin(r, "TickRatio") == "high"
    )


def _cluster_e_exclude(r: dict[str, Any]) -> bool:
    return _cluster_d(r) or _bin(r, "TV") == "low"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_module("phase217_loader_p226", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p226", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("assigning tertile bins...", flush=True)
    p221._augment_early_features(mod, rows)
    cuts = _build_tertiles(rows)
    _assign_bins(rows, cuts)

    scenarios: dict[str, tuple[str, Callable[[dict[str, Any]], bool]]] = {
        "A": ("current_all_trades", lambda r: True),
        "B": ("exclude_board_low_and_tickratio_high", lambda r: not _cluster_b(r)),
        "C": ("exclude_vwap_high_and_tickratio_high", lambda r: not _cluster_c(r)),
        "D": ("exclude_vwap_high_board_low_tickratio_high", lambda r: not _cluster_d(r)),
        "E": ("exclude_D_cluster_or_tv_low", lambda r: not _cluster_e_exclude(r)),
    }

    excluded_counts = {
        "B": sum(1 for r in rows if _cluster_b(r)),
        "C": sum(1 for r in rows if _cluster_c(r)),
        "D": sum(1 for r in rows if _cluster_d(r)),
        "E": sum(1 for r in rows if _cluster_e_exclude(r)),
    }

    scenario_results: dict[str, Any] = {}
    keep_fns: dict[str, Callable[[dict[str, Any]], bool]] = {}
    for key, (label, kfn) in scenarios.items():
        keep_fns[key] = kfn
        scenario_results[key] = {"label": label, **_evaluate(rows, kfn)}

    base = scenario_results["A"]["kept"]
    deltas = {
        key: {
            "trade_count_delta": scenario_results[key]["kept"]["trade_count"] - base["trade_count"],
            "total_pnl_delta": round(
                scenario_results[key]["kept"]["total_pnl_pct"] - base["total_pnl_pct"], 4
            ),
            "profit_factor_delta": round(
                (scenario_results[key]["kept"]["profit_factor"] or 0) - (base["profit_factor"] or 0),
                4,
            )
            if scenario_results[key]["kept"]["profit_factor"] is not None
            and base["profit_factor"] is not None
            else None,
            "stop_hit_rate_delta": round(
                (scenario_results[key]["kept"]["stop_hit_rate"] or 0) - (base["stop_hit_rate"] or 0),
                4,
            ),
        }
        for key in scenarios
        if key != "A"
    }

    am_pm = {
        key: {
            "AM": _slice_eval([r for r in rows if r.get("session_period") == "AM"], keep_fns[key]),
            "PM": _slice_eval([r for r in rows if r.get("session_period") == "PM"], keep_fns[key]),
        }
        for key in scenarios
    }

    focus = {
        sym: {key: _slice_eval([r for r in rows if r.get("symbol") == sym], keep_fns[key]) for key in scenarios}
        for sym in FOCUS_SYMBOLS
    }

    report = {
        "phase": 226,
        "mode": "stop_cluster_counterfactual_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "context_from_phase225": (
            "Phase225 top stop-risk cluster: VWAP:high & Board:low & TickRatio:high "
            "(59% stop rate, n=22)."
        ),
        "tertile_cutoffs_phase225_aligned": cuts,
        "excluded_cluster_counts": excluded_counts,
        "population": {"total_trades": len(rows)},
        "scenarios": scenario_results,
        "delta_vs_A_current": deltas,
        "summary_table": {
            key: {
                "trade_count": scenario_results[key]["kept"]["trade_count"],
                "kept_share_pct": scenario_results[key]["kept_share_pct"],
                "profit_factor": scenario_results[key]["kept"]["profit_factor"],
                "total_pnl_pct": scenario_results[key]["kept"]["total_pnl_pct"],
                "avg_pnl_pct": scenario_results[key]["kept"]["avg_pnl_pct"],
                "stop_hit_rate": scenario_results[key]["kept"]["stop_hit_rate"],
                "winner_missed": scenario_results[key]["gate_impact"]["winner_missed_count"],
                "loser_avoided": scenario_results[key]["gate_impact"]["loser_avoided_count"],
                "stop_avoided": scenario_results[key]["gate_impact"]["stop_avoided_count"],
                "net_excluded_pnl": scenario_results[key]["gate_impact"]["net_excluded_pnl_pct"],
            }
            for key in scenarios
        },
        "am_pm_by_scenario": am_pm,
        "focus_symbols_by_scenario": focus,
        "notes": [
            "Counterfactual exclusion only — no live entry policy change.",
            "Tertile bins match Phase225 (low/mid/high on non-null values).",
            "Trades missing TickRatio cannot match TickRatio-high clusters (not excluded).",
            "Scenario E excludes Phase225 D cluster OR any TV:low tertile trade.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    d_pf = scenario_results["D"]["kept"]["profit_factor"]
    print(f"wrote {OUT} n={len(rows)} D_pf={d_pf}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
