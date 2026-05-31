#!/usr/bin/env python3
"""
Phase230: Entry expectancy score shadow observation (review only).

Aggregates score5/score6 metrics from NEW sessions only (phase230 marker).
No re-analysis of pre-Phase230 historical sessions.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase230_entry_expectancy_shadow_observation.json"
SMALL_PAPER = REPO / "kabu_native/results/small_paper"
MIN_SESSIONS = 30


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


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _discover_phase230_sessions(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for summary_path in sorted(base.rglob("small_paper_summary.json")):
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not summary.get("phase230_entry_expectancy_shadow") and not summary.get(
            "entry_expectancy_score_shadow_enabled"
        ):
            continue
        session_dir = summary_path.parent
        rel = session_dir.relative_to(base).as_posix()
        mode = str(summary.get("mode") or "")
        source = str(summary.get("source") or "")
        is_live = "live" in mode or source == "live"
        is_replay = "push_replay" in mode or source == "push-replay"
        out.append(
            {
                "session_id": rel,
                "session_dir": str(session_dir),
                "mode": mode,
                "source": source,
                "is_live": is_live,
                "is_push_replay": is_replay,
                "score5_count": int(summary.get("score5_count") or 0),
                "score5_pf": _float(summary.get("score5_pf")),
                "score5_pnl": _float(summary.get("score5_pnl")),
                "score6_count": int(summary.get("score6_count") or 0),
                "score6_pf": _float(summary.get("score6_pf")),
                "score6_pnl": _float(summary.get("score6_pnl")),
                "accepted_count": int(summary.get("accepted_count") or 0),
            }
        )
    return out


def _split_label(session_id: str, mod: Any) -> str:
    if session_id in mod.IN_SAMPLE:
        return "in_sample"
    if session_id in mod.OOS:
        return "oos"
    return "unknown"


def _aggregate(sessions: list[dict[str, Any]], mod: Any) -> dict[str, Any]:
    def _pool(split: Optional[str], key_pf: str, key_pnl: str, key_cnt: str) -> dict[str, Any]:
        subset = sessions if split is None else [s for s in sessions if _split_label(s["session_id"], mod) == split]
        pnls: list[float] = []
        for s in subset:
            pnl = _float(s.get(key_pnl))
            cnt = int(s.get(key_cnt) or 0)
            if pnl is not None and cnt > 0:
                pnls.append(float(pnl))
        return {
            "session_count": len(subset),
            "cohort_trade_count": sum(int(s.get(key_cnt) or 0) for s in subset),
            "total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
            "profit_factor": _pf(pnls) if len(pnls) >= 2 else None,
        }

    is_s5 = _pool("in_sample", "score5_pf", "score5_pnl", "score5_count")
    oos_s5 = _pool("oos", "score5_pf", "score5_pnl", "score5_count")
    all_s5 = _pool(None, "score5_pf", "score5_pnl", "score5_count")

    return {
        "score5_all_sessions": all_s5,
        "score5_in_sample": is_s5,
        "score5_oos": oos_s5,
        "score6_all_sessions": _pool(None, "score6_pf", "score6_pnl", "score6_count"),
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p213 = _load_module(
        "phase213c_loader_p230",
        "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py",
    )

    sessions = _discover_phase230_sessions(SMALL_PAPER)
    agg = _aggregate(sessions, p213)

    n = len(sessions)
    s5_is_pf = agg["score5_in_sample"].get("profit_factor")
    s5_oos_pf = agg["score5_oos"].get("profit_factor")
    s5_all_pf = agg["score5_all_sessions"].get("profit_factor")
    s5_all_pnl = agg["score5_all_sessions"].get("total_pnl_pct") or 0

    adoption_ready = (
        n >= MIN_SESSIONS
        and s5_all_pf is not None
        and s5_all_pf > 1
        and s5_all_pnl > 0
        and s5_is_pf is not None
        and s5_is_pf > 1
        and s5_oos_pf is not None
        and s5_oos_pf > 1
    )

    report = {
        "phase": 230,
        "mode": "entry_expectancy_score_shadow_observation",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "hard_reject_forbidden": True,
            "new_sessions_only": True,
            "no_historical_reanalysis": True,
        },
        "adoption_criteria": {
            "min_sessions": MIN_SESSIONS,
            "score_threshold": 5,
            "requires_pf_gt_1": True,
            "requires_total_pnl_gt_0": True,
            "requires_is_pf_gt_1": True,
            "requires_oos_pf_gt_1": True,
        },
        "observed_session_count": n,
        "sessions": sessions,
        "aggregates": agg,
        "adoption_candidate": adoption_ready,
        "adoption_status": (
            "candidate_ready"
            if adoption_ready
            else ("collecting_sessions" if n < MIN_SESSIONS else "criteria_not_met")
        ),
        "notes": [
            "Only sessions with phase230_entry_expectancy_shadow in summary are included.",
            "Pre-Phase230 sessions are excluded by design.",
            "Session-level score5_pf/score5_pnl come from small_paper_summary.json.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} sessions={n} adoption={adoption_ready}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
