#!/usr/bin/env python3
"""
Phase248 (review only): v2 adoption decision.

Target:
- Phase243 report population (13 sessions; replay/push_replay; accept-realized)
- Phase244 report population (13 sessions; replay/push_replay; accept-realized)
- Phase247 context population (Phase246 session set; 38 sessions scanned, but in report list may be fewer)

Check:
1) accept_realized only: compare v1>=5 vs v2>=5
2) evaluate PF and PnL only (trade_count is reference)

Decision:
If market-wide (Phase247/Phase246 session set) v2>=5 has PF>1 and PnL>0
then adoption_candidate=true.

Output:
kabu_native/results/reports/phase248_v2_adoption_decision.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
REPORTS = REPO / "kabu_native" / "results" / "reports"
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"

PH243 = REPORTS / "phase243_fast_validation_framework.json"
PH244 = REPORTS / "phase244_fast_validation_coverage_expansion.json"
PH246 = REPORTS / "phase246_v2_priority_simulation.json"
OUT = REPORTS / "phase248_v2_adoption_decision.json"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_accept_realized_rows(session_ids: list[str]) -> list[dict[str, Any]]:
    """
    Use Phase243 extraction logic (accept+observer_exit; fallback accept-embedded pnl),
    which recomputes v1/v2 scores at accept.
    """
    from kabu_native.scripts import run_phase243_fast_validation_framework as p243

    rows: list[dict[str, Any]] = []
    for sid in session_ids:
        sdir = SMALL_PAPER / Path(sid)
        events = p243._load_events(sdir)  # type: ignore[attr-defined]
        rows.extend(p243._extract_closed_trades(events))  # type: ignore[attr-defined]
    return rows


def _pf_pnl_only(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(r["pnl_pct"]) for r in rows]
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    pf = None
    if pnls:
        gl = abs(loss)
        if gl == 0:
            pf = None if wins == 0 else float("inf")
        else:
            pf = round(wins / gl, 4)
    return {
        "trade_count": len(pnls),
        "profit_factor": pf,
        "total_pnl_pct": round(sum(pnls), 4),
    }


def _compare_v1_v2(rows: list[dict[str, Any]]) -> dict[str, Any]:
    v1 = [r for r in rows if r.get("v1_ge5")]
    v2 = [r for r in rows if r.get("v2_ge5")]
    return {
        "v1_score_ge5": _pf_pnl_only(v1),
        "v2_score_ge5": _pf_pnl_only(v2),
        "note": "accept_realized only; PF/PnL evaluated; trade_count is reference",
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    ph243 = _read_json(PH243)
    ph244 = _read_json(PH244)
    ph246 = _read_json(PH246)

    phase243_sessions = list(ph243.get("sessions", {}).get("session_ids") or [])
    phase244_sessions = [str(c.get("session_id")) for c in (ph244.get("coverage") or []) if c.get("session_id")]
    phase246_sessions = [str(s.get("session_id")) for s in (ph246.get("by_session") or []) if s.get("session_id")]

    rows_243 = _extract_accept_realized_rows(phase243_sessions)
    rows_244 = _extract_accept_realized_rows(phase244_sessions)
    rows_247_market = _extract_accept_realized_rows(phase246_sessions)

    report = {
        "phase": 248,
        "mode": "v2_adoption_decision",
        "constraints": {
            "review_only": True,
            "entry_change_forbidden": True,
            "score_change_forbidden": True,
            "yaml_change_forbidden": True,
            "production_change_forbidden": True,
        },
        "inputs": {
            "phase243_report": str(PH243),
            "phase244_report": str(PH244),
            "phase246_report_for_phase247_market_population": str(PH246),
        },
        "populations": {
            "phase243_accept_realized": {
                "sessions": len(phase243_sessions),
                "comparison": _compare_v1_v2(rows_243),
            },
            "phase244_accept_realized": {
                "sessions": len(phase244_sessions),
                "comparison": _compare_v1_v2(rows_244),
            },
            "phase247_market_accept_realized": {
                "sessions": len(phase246_sessions),
                "comparison": _compare_v1_v2(rows_247_market),
                "notes": [
                    "Market population approximated as Phase246 session set (Phase247 context).",
                    "accept_realized only (no max_concurrent counterfactual injections).",
                ],
            },
        },
    }

    v2_market = report["populations"]["phase247_market_accept_realized"]["comparison"]["v2_score_ge5"]
    adoption_candidate = bool((v2_market["profit_factor"] is not None) and (v2_market["profit_factor"] > 1) and (v2_market["total_pnl_pct"] > 0))
    report["decision"] = {
        "adoption_candidate": adoption_candidate,
        "rule": "PF>1 and PnL>0 on market-wide accept_realized v2>=5",
        "evaluated_population": "phase247_market_accept_realized",
        "evaluated_gate": "v2_score_ge5",
        "evaluated_metrics": v2_market,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} adoption_candidate={adoption_candidate}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

