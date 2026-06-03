#!/usr/bin/env python3
"""
Phase239: Counterfactual ENTRY gate — v1 Score>=5 vs v2 Score>=5 (system PnL).

Applies each score threshold as if it were the entry accept gate on the same
push_replay + replay trade population (Phase238 loader). Review only.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase239_entry_score_ge5_gate_system_comparison.json"
SMALL_PAPER = REPO / "kabu_native/results/small_paper"
P238_SCRIPT = REPO / "kabu_native/scripts/run_phase238_entry_score_v2_full_history_validation.py"


def _load_phase238() -> Any:
    spec = importlib.util.spec_from_file_location("phase238_loader_p239", P238_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase238_loader_p239"] = mod
    spec.loader.exec_module(mod)
    return mod


def _metrics(rows: list[dict[str, Any]], pf_fn: Any) -> dict[str, Any]:
    if not rows:
        return {"trade_count": 0, "profit_factor": None, "total_pnl_pct": 0.0}
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    pf = pf_fn(pnls)
    return {
        "trade_count": len(rows),
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
    }


def main() -> int:
    p238 = _load_phase238()
    p238._bootstrap()

    mod = p238._load_module(
        "phase213c_loader_p239",
        "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py",
    )
    p217 = p238._load_module(
        "phase217_loader_p239",
        "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py",
    )
    p221 = p238._load_module(
        "phase221_loader_p239",
        "kabu_native/scripts/run_phase221_early_momentum_discovery_review.py",
    )
    p71 = mod._load_phase71()

    print("discovering sessions...", flush=True)
    sessions = p238.discover_replay_sessions(SMALL_PAPER, mod, p71)
    print(f"  sessions={len(sessions)} loading trades...", flush=True)
    rows = p238._load_population(sessions, mod, p217, p221, p71)

    v1_gate = [r for r in rows if r.get("entry_expectancy_score_ge5_flag")]
    v2_gate = [r for r in rows if r.get("entry_expectancy_score_v2_ge5_flag")]

    report = {
        "phase": 239,
        "mode": "entry_score_ge5_gate_system_comparison",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "hard_reject_forbidden": True,
            "winner_missed_analysis_forbidden": True,
            "oos_analysis_forbidden": True,
            "time_of_day_analysis_forbidden": True,
            "symbol_analysis_forbidden": True,
        },
        "method": {
            "counterfactual": "Only trades that would pass Score>=5 at accept are kept",
            "v1_gate": "entry_expectancy_score >= 5 (Phase229 map)",
            "v2_gate": "entry_expectancy_score_v2 >= 5 (RollingMAE:mid +0)",
            "population": "Phase238 — all available push_replay + replay sessions",
        },
        "sessions_loaded": len(sessions),
        "baseline_trades_without_score_gate": len(rows),
        "entry_gates": {
            "v1_score_ge5_entry": _metrics(v1_gate, p238._pf),
            "v2_score_ge5_entry": _metrics(v2_gate, p238._pf),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    g1 = report["entry_gates"]["v1_score_ge5_entry"]
    g2 = report["entry_gates"]["v2_score_ge5_entry"]
    print(
        f"wrote {OUT} baseline_n={len(rows)} "
        f"v1_n={g1['trade_count']} v1_pf={g1['profit_factor']} v1_pnl={g1['total_pnl_pct']} "
        f"v2_n={g2['trade_count']} v2_pf={g2['profit_factor']} v2_pnl={g2['total_pnl_pct']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
