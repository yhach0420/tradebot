#!/usr/bin/env python3
"""
Phase224: Early momentum entry cohort review (review only).

Validate whether Phase221 early-momentum profile (low rise_5min, short duration,
low VWAP dev) improves expectancy vs current all-trades baseline.
"""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase224_early_momentum_entry_cohort_review.json"

FOCUS_SYMBOLS = ("6203.T", "6659.T", "9348.T", "4888.T")
BOTTOM_PCT = 0.20
VWAP_MAX = 2.5
TV_MIN = 1e8


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
    win_pnl = round(sum(float(r.get("pnl_pct") or 0) for r in win_ex), 4)
    lose_pnl = round(sum(float(r.get("pnl_pct") or 0) for r in lose_ex), 4)
    return {
        "winner_missed_count": len(win_ex),
        "winner_missed_pnl_pct": win_pnl,
        "loser_avoided_count": len(lose_ex),
        "loser_avoided_pnl_pct": lose_pnl,
        "net_excluded_pnl_pct": round(win_pnl + lose_pnl, 4),
    }


def _evaluate(rows: list[dict[str, Any]], pass_fn: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    kept = [r for r in rows if pass_fn(r)]
    excluded = [r for r in rows if not pass_fn(r)]
    return {
        "kept": _metrics(kept),
        "excluded_trade_count": len(excluded),
        "kept_share_pct": round(100.0 * len(kept) / max(1, len(rows)), 2),
        "gate_impact": _gate_impact(kept, excluded),
    }


def _slice_eval(rows: list[dict[str, Any]], pass_fn: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    kept = [r for r in rows if pass_fn(r)]
    excluded = [r for r in rows if not pass_fn(r)]
    return {
        "kept": _metrics(kept),
        "excluded": len(excluded),
        "gate_impact": _gate_impact(kept, excluded),
    }


def _trade_key(r: dict[str, Any]) -> tuple[str, str]:
    return (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))


def _bottom_pct_keys(rows: list[dict[str, Any]], field: str, pct: float = BOTTOM_PCT) -> set[tuple[str, str]]:
    pairs = [(r, v) for r in rows if (v := _float(r.get(field))) is not None]
    pairs.sort(key=lambda x: x[1])
    n = len(pairs)
    if n == 0:
        return set()
    k = max(1, int(math.ceil(n * pct)))
    return {_trade_key(r) for r, _ in pairs[:k]}


def _quantile(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return round(ys[lo], 6)
    return round(ys[lo] + (ys[hi] - ys[lo]) * (pos - lo), 6)


def _vwap_pass(row: dict[str, Any]) -> bool:
    if row.get("vwap_shadow_reject_candidate"):
        return False
    dev = _float(row.get("entry_vwap_dev_pct"))
    if dev is None:
        return False
    return dev < VWAP_MAX


def _tv_pass(row: dict[str, Any]) -> bool:
    tv = _float(row.get("trading_value"))
    return tv is not None and tv >= TV_MIN


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_module("phase217_loader_p224", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    p221 = _load_module("phase221_loader_p224", "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("augmenting early momentum features...", flush=True)
    p221._augment_early_features(mod, rows)

    rise_keys = _bottom_pct_keys(rows, "entry_rise_5min_pct", BOTTOM_PCT)
    dur_keys = _bottom_pct_keys(rows, "max_continuation_duration", BOTTOM_PCT)

    rise_vals = [_float(r.get("entry_rise_5min_pct")) for r in rows]
    rise_vals = [v for v in rise_vals if v is not None]
    dur_vals = [_float(r.get("max_continuation_duration")) for r in rows]
    dur_vals = [v for v in dur_vals if v is not None]

    def in_rise_bottom(r: dict[str, Any]) -> bool:
        return _trade_key(r) in rise_keys

    def in_dur_bottom(r: dict[str, Any]) -> bool:
        return _trade_key(r) in dur_keys

    scenarios: dict[str, tuple[str, Callable[[dict[str, Any]], bool]]] = {
        "A": ("current_all_trades", lambda r: True),
        "B": ("rise_5min_bottom_20pct", in_rise_bottom),
        "C": ("continuation_duration_bottom_20pct", in_dur_bottom),
        "D": ("rise_bottom_and_duration_bottom", lambda r: in_rise_bottom(r) and in_dur_bottom(r)),
        "E": (
            "D_plus_vwap_dev_lt_2p5",
            lambda r: in_rise_bottom(r) and in_dur_bottom(r) and _vwap_pass(r),
        ),
        "F": (
            "E_plus_tv_ge_1e8",
            lambda r: in_rise_bottom(r) and in_dur_bottom(r) and _vwap_pass(r) and _tv_pass(r),
        ),
    }

    scenario_results: dict[str, Any] = {}
    pass_fns: dict[str, Callable[[dict[str, Any]], bool]] = {}
    for key, (label, pfn) in scenarios.items():
        pass_fns[key] = pfn
        scenario_results[key] = {"label": label, **_evaluate(rows, pfn)}

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
            "AM": _slice_eval([r for r in rows if r.get("session_period") == "AM"], pass_fns[key]),
            "PM": _slice_eval([r for r in rows if r.get("session_period") == "PM"], pass_fns[key]),
        }
        for key in scenarios
    }

    focus = {
        sym: {key: _slice_eval([r for r in rows if r.get("symbol") == sym], pass_fns[key]) for key in scenarios}
        for sym in FOCUS_SYMBOLS
    }

    report = {
        "phase": 224,
        "mode": "early_momentum_entry_cohort_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "context_from_phase221": (
            "Top pnl cohort had lower rise_5min, shorter continuation_duration, lower VWAP deviation."
        ),
        "thresholds": {
            "rise_5min_bottom_20pct": {
                "field": "entry_rise_5min_pct",
                "method": f"lowest {int(BOTTOM_PCT * 100)}% by value among trades with data",
                "coverage_n": len(rise_vals),
                "coverage_pct": round(100.0 * len(rise_vals) / len(rows), 2),
                "p20_cutoff": _quantile(rise_vals, BOTTOM_PCT),
                "cohort_n": len(rise_keys),
            },
            "continuation_duration_bottom_20pct": {
                "field": "max_continuation_duration",
                "method": f"lowest {int(BOTTOM_PCT * 100)}% by value among trades with data",
                "coverage_n": len(dur_vals),
                "coverage_pct": round(100.0 * len(dur_vals) / len(rows), 2),
                "p20_cutoff": _quantile(dur_vals, BOTTOM_PCT),
                "cohort_n": len(dur_keys),
            },
            "vwap_pass": f"entry_vwap_dev_pct < {VWAP_MAX}% and not vwap_shadow_reject_candidate",
            "tv_pass": f"trading_value >= {TV_MIN:.0e}",
        },
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
                "net_excluded_pnl": scenario_results[key]["gate_impact"]["net_excluded_pnl_pct"],
            }
            for key in scenarios
        },
        "am_pm_by_scenario": am_pm,
        "focus_symbols_by_scenario": focus,
        "notes": [
            "Counterfactual review only — no live entry policy change.",
            "Bottom-20% cohorts computed on trades with non-null feature values; missing features fail B/C/D gates.",
            "rise_5min from push ring (Phase217/221); duration from accepted max_continuation_duration.",
            "Phase221 ring offline loader may under-cover early-session rise_5min (see Phase223).",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    pf_f = scenario_results["F"]["kept"]["profit_factor"]
    print(f"wrote {OUT} n={len(rows)} F_pf={pf_f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
