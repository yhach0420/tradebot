#!/usr/bin/env python3
"""
Phase222: High-break counterfactual review (review only).

Test whether Phase221 high_break_count filter improves expectancy when stacked with guards.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase222_high_break_counterfactual_review.json"

FOCUS_SYMBOLS = ("6203.T", "6659.T", "9348.T", "4888.T")
VWAP_MAX = 2.5


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _pf(pnls: list[float]) -> float | None:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


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


def _evaluate(rows: list[dict[str, Any]], pass_fn: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    kept = [r for r in rows if pass_fn(r)]
    excluded = [r for r in rows if not pass_fn(r)]
    return {
        "kept": _metrics(kept),
        "excluded_trade_count": len(excluded),
        "kept_share_pct": round(100.0 * len(kept) / max(1, len(rows)), 2),
        "gate_impact": _gate_impact(kept, excluded),
    }


def _slice_eval(
    rows: list[dict[str, Any]], pass_fn: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    kept = [r for r in rows if pass_fn(r)]
    excluded = [r for r in rows if not pass_fn(r)]
    return {
        "kept": _metrics(kept),
        "excluded": len(excluded),
        "gate_impact": _gate_impact(kept, excluded),
    }


def _hb(row: dict[str, Any]) -> int:
    try:
        return int(row.get("high_break_count") or 0)
    except (TypeError, ValueError):
        return 0


def _board_pass(p217: Any, row: dict[str, Any]) -> bool:
    imb = p217._float(row.get("entry_order_book_imbalance"))
    if imb is None:
        return False
    return float(imb) >= p217.IMBALANCE_30PCT


def _make_scenarios(p217: Any) -> dict[str, tuple[str, Callable[[dict[str, Any]], bool]]]:
    def vwap_pass(row: dict[str, Any]) -> bool:
        return not p217._vwap_reject(row)

    def liq_pass(row: dict[str, Any]) -> bool:
        return not p217._low_liq_reject(row)

    return {
        "A": (
            "current_all_trades",
            lambda row: True,
        ),
        "B": (
            "high_break_count_ge_1",
            lambda row: _hb(row) >= 1,
        ),
        "C": (
            "high_break_count_ge_2",
            lambda row: _hb(row) >= 2,
        ),
        "D": (
            "high_break_ge_1_and_vwap_lt_2p5",
            lambda row: _hb(row) >= 1 and vwap_pass(row),
        ),
        "E": (
            "high_break_ge_1_vwap_pass_low_liq_pass",
            lambda row: _hb(row) >= 1 and vwap_pass(row) and liq_pass(row),
        ),
        "F": (
            "high_break_ge_1_vwap_low_liq_board_pass",
            lambda row: _hb(row) >= 1 and vwap_pass(row) and liq_pass(row) and _board_pass(p217, row),
        ),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_module(
        "phase217_loader_p222",
        REPO / "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py",
    )
    p221 = _load_module(
        "phase221_loader_p222",
        REPO / "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py",
    )
    mod = p217._load_phase213c_module()
    print("loading trades...", flush=True)
    rows = p217._build_all(mod)
    print("computing high_break_count...", flush=True)
    p221._augment_early_features(mod, rows)

    hb_dist = Counter(_hb(r) for r in rows)

    scenarios_def = _make_scenarios(p217)
    scenario_results: dict[str, Any] = {}
    pass_fns: dict[str, Callable[[dict[str, Any]], bool]] = {}

    for key, (label, pfn) in scenarios_def.items():
        pass_fns[key] = pfn
        scenario_results[key] = {"label": label, **_evaluate(rows, pfn)}

    base = scenario_results["A"]["kept"]
    deltas = {
        key: {
            "trade_count_delta": scenario_results[key]["kept"]["trade_count"] - base["trade_count"],
            "total_pnl_delta": round(
                scenario_results[key]["kept"]["total_pnl_pct"] - base["total_pnl_pct"], 4
            ),
            "stop_hit_rate_delta": round(
                (scenario_results[key]["kept"]["stop_hit_rate"] or 0)
                - (base["stop_hit_rate"] or 0),
                4,
            ),
        }
        for key in scenarios_def
        if key != "A"
    }

    am_pm: dict[str, Any] = {}
    for key in scenarios_def:
        pfn = pass_fns[key]
        am_pm[key] = {
            "AM": _slice_eval([r for r in rows if r.get("session_period") == "AM"], pfn),
            "PM": _slice_eval([r for r in rows if r.get("session_period") == "PM"], pfn),
        }

    focus: dict[str, Any] = {}
    for sym in FOCUS_SYMBOLS:
        sym_rows = [r for r in rows if r.get("symbol") == sym]
        focus[sym] = {key: _slice_eval(sym_rows, pass_fns[key]) for key in scenarios_def}

    report = {
        "phase": 222,
        "mode": "high_break_counterfactual_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "context_from_phase221": (
            "high_break_count = new price highs in 10min pre-entry; top pnl cohort had higher counts."
        ),
        "thresholds": {
            "high_break_count": "new highs in 600s pre-entry window (Phase221)",
            "vwap_pass": f"entry_vwap_dev_pct < {VWAP_MAX}% (not vwap_shadow_reject)",
            "low_liq_pass": f"tv>={p217.TV_MIN}, turnover>={p217.TURNOVER_MIN}, not shadow_rejected",
            "board_pass": f"entry_order_book_imbalance>={p217.IMBALANCE_30PCT} (30pct tier)",
        },
        "population": {
            "total_trades": len(rows),
            "high_break_count_distribution": dict(sorted(hb_dist.items())),
            "trades_with_high_break_ge_1": sum(1 for r in rows if _hb(r) >= 1),
            "trades_with_high_break_ge_2": sum(1 for r in rows if _hb(r) >= 2),
        },
        "scenarios": scenario_results,
        "delta_vs_A_current": deltas,
        "am_pm_by_scenario": am_pm,
        "focus_symbols_by_scenario": focus,
        "summary_table": {
            key: {
                "trade_count": scenario_results[key]["kept"]["trade_count"],
                "kept_share_pct": scenario_results[key]["kept_share_pct"],
                "profit_factor": scenario_results[key]["kept"]["profit_factor"],
                "total_pnl_pct": scenario_results[key]["kept"]["total_pnl_pct"],
                "stop_hit_rate": scenario_results[key]["kept"]["stop_hit_rate"],
                "winner_missed": scenario_results[key]["gate_impact"]["winner_missed_count"],
                "loser_avoided": scenario_results[key]["gate_impact"]["loser_avoided_count"],
                "net_excluded_pnl": scenario_results[key]["gate_impact"]["net_excluded_pnl_pct"],
            }
            for key in scenarios_def
        },
        "notes": [
            "Counterfactual only — no live entry policy change.",
            "high_break_count=0 when push ring unavailable (included in A, excluded by B+).",
            "board_pass uses fixed 30pct tier cutoff (Phase217/214).",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} n={len(rows)} hb>=1={report['population']['trades_with_high_break_ge_1']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
