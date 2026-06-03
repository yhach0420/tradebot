#!/usr/bin/env python3
"""
Phase244: Expand Phase243 fast validation coverage (review only).

Purpose:
- Investigate why many sessions showed closed_trades=0
- Expand pairing logic to include sessions where accept rows already carry pnl/exit fields
  (no observer_exit emitted).

Outputs:
- kabu_native/results/reports/phase244_fast_validation_coverage_expansion.json

Metrics (same as Phase243) for:
no_score_gate, v1_score_ge5, v1_score_ge6, v2_score_ge5, v2_score_ge6
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native" / "results" / "reports" / "phase244_fast_validation_coverage_expansion.json"
P243 = REPO / "kabu_native" / "scripts" / "run_phase243_fast_validation_framework.py"


def _load_p243() -> Any:
    spec = importlib.util.spec_from_file_location("phase243_loader_p244", P243)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase243_loader_p244"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    p243 = _load_p243()
    # Run Phase243 main which now includes expanded coverage + debug.
    # We call its functions directly to keep output separate.
    p243._bootstrap()
    sessions = p243._discover_sessions(p243.SMALL_PAPER)
    all_trades: list[dict[str, Any]] = []
    coverage = []
    for sess in sessions:
        sdir = Path(sess["session_dir"])
        events = p243._load_events(sdir)
        trades = p243._extract_closed_trades(events)
        all_trades.extend(trades)
        # event_type coverage snapshot
        et_counts: dict[str, int] = {}
        for ev in events[:5000]:
            et = str(ev.get("event_type") or "")
            if not et:
                continue
            et_counts[et] = et_counts.get(et, 0) + 1
        coverage.append(
            {
                "session_id": sess["session_id"],
                "stream": sess["stream"],
                "events_loaded": len(events),
                "event_type_counts_head_5k": et_counts,
                "has_observer_exit": any(str(e.get("event_type") or "") == "observer_exit" for e in events[:20000]),
                "closed_trades": len(trades),
            }
        )

    gates: dict[str, list[dict[str, Any]]] = {
        "no_score_gate": list(all_trades),
        "v1_score_ge5": [t for t in all_trades if t.get("v1_ge5")],
        "v1_score_ge6": [t for t in all_trades if t.get("v1_ge6")],
        "v2_score_ge5": [t for t in all_trades if t.get("v2_ge5")],
        "v2_score_ge6": [t for t in all_trades if t.get("v2_ge6")],
    }

    report = {
        "phase": 244,
        "mode": "fast_validation_coverage_expansion",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "entry_change_forbidden": True,
        },
        "investigation": {
            "finding": "Many push_replay sessions have no observer_exit events; realized pnl is stored on accepted rows (exit_reason=live_virtual_hold) and/or in small_paper_trades_review.csv.",
            "action": "Expanded Phase243 pairing to use accept row pnl when observer_exit is absent.",
        },
        "population": {
            "sessions_scanned": len(sessions),
            "closed_trades_total": len(all_trades),
        },
        "coverage": coverage,
        "gates": {k: p243._metrics(v) for k, v in gates.items()},
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} sessions={len(sessions)} trades={len(all_trades)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

