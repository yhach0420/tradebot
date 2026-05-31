#!/usr/bin/env python3
"""
Phase219: Board entry gate counterfactual review (review only).

Scenarios: A=current; B/C/D = low_liq + vwap pass + board top 30/20/10%.
No hard reject, no production entry/YAML changes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase219_board_entry_gate_counterfactual_review.json"

FOCUS_SYMBOLS = ("6203.T", "6659.T", "9348.T", "4888.T")
SCENARIOS: tuple[tuple[str, str, str | None], ...] = (
    ("A", "current_all_trades", None),
    ("B", "low_liq_plus_vwap_plus_board_top30pct", "30%"),
    ("C", "low_liq_plus_vwap_plus_board_top20pct", "20%"),
    ("D", "low_liq_plus_vwap_plus_board_top10pct", "10%"),
)


def _load_phase217() -> Any:
    path = REPO / "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py"
    name = "phase217_loader_p219"
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


def _passes_base(p217: Any, row: dict[str, Any]) -> bool:
    return not p217._low_liq_reject(row) and not p217._vwap_reject(row)


def _passes_board(p217: Any, row: dict[str, Any], tier: str) -> bool:
    from small_paper.board_imbalance_shadow import IMBALANCE_TIER_CUTOFFS

    imb = p217._float(row.get("entry_order_book_imbalance"))
    if imb is None:
        return False
    return float(imb) >= IMBALANCE_TIER_CUTOFFS[tier]


def _make_pass_fn(p217: Any, tier: str | None) -> Callable[[dict[str, Any]], bool]:
    if tier is None:

        def pass_a(row: dict[str, Any]) -> bool:
            return True

        return pass_a

    def pass_gated(row: dict[str, Any]) -> bool:
        return _passes_base(p217, row) and _passes_board(p217, row, tier)

    return pass_gated


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
            "trailing_mfe_count": 0,
            "trailing_mfe_rate": None,
        }
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    n = len(rows)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    trails = sum(1 for r in rows if r.get("trailing_mfe_exit"))
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
        "stop_hit_count": stops,
        "stop_hit_rate": round(stops / n, 4),
        "trailing_mfe_count": trails,
        "trailing_mfe_rate": round(trails / n, 4),
    }


def _evaluate(
    p217: Any,
    all_rows: list[dict[str, Any]],
    pass_fn: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    kept = [r for r in all_rows if pass_fn(r)]
    excluded = [r for r in all_rows if not pass_fn(r)]
    share = round(100.0 * len(kept) / max(1, len(all_rows)), 2)
    return {
        "kept": _metrics(kept),
        "excluded_trade_count": len(excluded),
        "kept_share_pct": share,
        "gate_impact": _gate_impact(kept, excluded),
    }


def _slice_metrics(
    p217: Any,
    rows: list[dict[str, Any]],
    pass_fn: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    kept = [r for r in rows if pass_fn(r)]
    excluded = [r for r in rows if not pass_fn(r)]
    return {
        "kept": _metrics(kept),
        "excluded": len(excluded),
        "gate_impact": _gate_impact(kept, excluded),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_phase217()
    from small_paper.board_imbalance_shadow import IMBALANCE_TIER_CUTOFFS

    mod = p217._load_phase213c_module()
    print("loading trades...", flush=True)
    all_rows = p217._build_all(mod)

    scenario_results: dict[str, Any] = {}
    pass_fns: dict[str, Callable[[dict[str, Any]], bool]] = {}

    for key, label, tier in SCENARIOS:
        pfn = _make_pass_fn(p217, tier)
        pass_fns[key] = pfn
        scenario_results[key] = {
            "label": label,
            "board_tier": tier,
            "imbalance_cutoff": IMBALANCE_TIER_CUTOFFS.get(tier) if tier else None,
            "requires": (
                "all trades"
                if tier is None
                else f"pass low_liq (tv>={p217.TV_MIN}, turnover>={p217.TURNOVER_MIN}) "
                f"+ pass vwap (dev<{p217.VWAP_DEV_REJECT}) + imbalance>={IMBALANCE_TIER_CUTOFFS[tier]}"
            ),
            **_evaluate(p217, all_rows, pfn),
        }

    # AM / PM per scenario
    am_pm: dict[str, Any] = {}
    for key, _, _ in SCENARIOS:
        pfn = pass_fns[key]
        am_pm[key] = {
            "AM": _slice_metrics(p217, [r for r in all_rows if r.get("session_period") == "AM"], pfn),
            "PM": _slice_metrics(p217, [r for r in all_rows if r.get("session_period") == "PM"], pfn),
        }

    # Focus symbols per scenario
    focus: dict[str, Any] = {}
    for sym in FOCUS_SYMBOLS:
        sym_rows = [r for r in all_rows if r.get("symbol") == sym]
        focus[sym] = {}
        for key, _, _ in SCENARIOS:
            focus[sym][key] = _slice_metrics(p217, sym_rows, pass_fns[key])

    # Delta vs A
    base = scenario_results["A"]["kept"]
    deltas: dict[str, Any] = {}
    for key in ("B", "C", "D"):
        k = scenario_results[key]["kept"]
        deltas[key] = {
            "trade_count_delta": k["trade_count"] - base["trade_count"],
            "total_pnl_delta": round(k["total_pnl_pct"] - base["total_pnl_pct"], 4),
            "stop_hit_rate_delta": round((k["stop_hit_rate"] or 0) - (base["stop_hit_rate"] or 0), 4)
            if k["stop_hit_rate"] is not None and base["stop_hit_rate"] is not None
            else None,
            "pf_delta": (
                round(float(k["profit_factor"]) - float(base["profit_factor"]), 4)
                if k.get("profit_factor") is not None
                and base.get("profit_factor") not in (None, float("inf"))
                and k["profit_factor"] not in (float("inf"),)
                else None
            ),
        }

    report = {
        "phase": 219,
        "mode": "board_entry_gate_counterfactual_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
            "fixed_thresholds": True,
        },
        "population": {
            "session_scope": "IS 11 + OOS 9",
            "total_trades": len(all_rows),
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
            for key, _, _ in SCENARIOS
        },
        "notes": [
            "Counterfactual only — no live entry policy change.",
            "Board tier = fixed Phase213b/214 imbalance cutoffs (top 30/20/10%).",
            "winner_missed / loser_avoided count excluded trades by realized pnl sign.",
            "net_excluded_pnl negative => gate removes net losing volume on average.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} n={len(all_rows)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
