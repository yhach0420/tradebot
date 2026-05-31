#!/usr/bin/env python3
"""
Phase233: Phase230 entry expectancy shadow validation (review only).

Aggregate ge5/ge6 shadow cohorts from new Phase230 sessions at trade level.
Splits: live / push_replay / combined. Requires 30+ sessions for evaluation.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Literal, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase233_entry_expectancy_shadow_validation.json"
SMALL_PAPER = REPO / "kabu_native/results/small_paper"
MIN_SESSIONS = 30

Stream = Literal["live", "push_replay", "combined"]
Cohort = Literal["score_ge5", "score_ge6"]

FLAG_KEY = {
    "score_ge5": "entry_expectancy_score_ge5_flag",
    "score_ge6": "entry_expectancy_score_ge6_flag",
}


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


def _boolish(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    return []


def _session_stream(summary: dict[str, Any]) -> Optional[Stream]:
    mode = str(summary.get("mode") or "").lower()
    source = str(summary.get("source") or "").lower()
    if "push_replay" in mode or source in ("push-replay", "push_replay"):
        return "push_replay"
    if "live" in mode or source == "live":
        return "live"
    return None


def _is_phase230_session(summary: dict[str, Any], events: list[dict[str, Any]]) -> bool:
    if summary.get("phase230_entry_expectancy_shadow") or summary.get(
        "entry_expectancy_score_shadow_enabled"
    ):
        return True
    for ev in events:
        if ev.get("entry_expectancy_score") is not None:
            return True
        if FLAG_KEY["score_ge5"] in ev or FLAG_KEY["score_ge6"] in ev:
            return True
    return False


def _extract_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if ev.get("event_type") != "accepted":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if not key[1]:
            continue
        accepts[key] = ev

    trades: list[dict[str, Any]] = []
    for key, acc in accepts.items():
        sym, entry_time = key
        exit_ev: Optional[dict[str, Any]] = None
        for ev in events:
            if ev.get("event_type") != "observer_exit":
                continue
            if (str(ev.get("symbol") or ""), str(ev.get("entry_time") or "")) == key:
                exit_ev = ev
                break
        pnl = _float(exit_ev.get("pnl_pct")) if exit_ev else None
        reason = str(exit_ev.get("exit_reason") or "") if exit_ev else ""
        stop_hit = bool(exit_ev.get("stop_hit")) if exit_ev else False
        if exit_ev and not stop_hit and reason == "stop_hit":
            stop_hit = True

        ge5 = _boolish(acc.get(FLAG_KEY["score_ge5"]))
        if exit_ev and FLAG_KEY["score_ge5"] in exit_ev:
            ge5 = _boolish(exit_ev.get(FLAG_KEY["score_ge5"]))
        ge6 = _boolish(acc.get(FLAG_KEY["score_ge6"]))
        if exit_ev and FLAG_KEY["score_ge6"] in exit_ev:
            ge6 = _boolish(exit_ev.get(FLAG_KEY["score_ge6"]))

        trades.append(
            {
                "symbol": sym,
                "entry_time": entry_time,
                "entry_expectancy_score": acc.get("entry_expectancy_score"),
                "entry_expectancy_score_ge5_flag": ge5,
                "entry_expectancy_score_ge6_flag": ge6,
                "pnl_pct": pnl,
                "exit_reason": reason,
                "stop_hit": stop_hit,
                "has_exit": exit_ev is not None,
            }
        )
    return trades


def _discover_sessions(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for summary_path in sorted(base.rglob("small_paper_summary.json")):
        session_dir = summary_path.parent
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        events = _load_events(session_dir)
        if not _is_phase230_session(summary, events):
            continue
        stream = _session_stream(summary)
        if stream is None:
            continue
        rel = session_dir.relative_to(base).as_posix()
        trades = _extract_trades(events)
        out.append(
            {
                "session_id": rel,
                "session_dir": str(session_dir),
                "stream": stream,
                "mode": summary.get("mode"),
                "source": summary.get("source"),
                "accepted_count": int(summary.get("accepted_count") or 0),
                "summary_score5_count": int(summary.get("score5_count") or 0),
                "summary_score6_count": int(summary.get("score6_count") or 0),
                "trades": trades,
            }
        )
    return out


def _split_label(session_id: str, mod: Any) -> str:
    if session_id in mod.IN_SAMPLE:
        return "in_sample"
    if session_id in mod.OOS:
        return "oos"
    return "unknown"


def _trade_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [t for t in trades if _float(t.get("pnl_pct")) is not None]
    pnls = [float(t["pnl_pct"]) for t in closed]
    n = len(closed)
    if n == 0:
        return {
            "trade_count": 0,
            "closed_trade_count": 0,
            "open_without_exit": len(trades) - len(closed),
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in closed if t.get("stop_hit"))
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "closed_trade_count": n,
        "open_without_exit": len(trades) - len(closed),
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def _cohort_block(
    sessions: list[dict[str, Any]],
    cohort: Cohort,
    mod: Any,
) -> dict[str, Any]:
    flag = FLAG_KEY[cohort]
    all_trades: list[dict[str, Any]] = []
    for sess in sessions:
        split = _split_label(sess["session_id"], mod)
        for t in sess["trades"]:
            if not t.get(flag):
                continue
            all_trades.append({**t, "session_id": sess["session_id"], "split": split})

    is_trades = [t for t in all_trades if t.get("split") == "in_sample"]
    oos_trades = [t for t in all_trades if t.get("split") == "oos"]
    unknown_trades = [t for t in all_trades if t.get("split") == "unknown"]

    return {
        "all": _trade_metrics(all_trades),
        "in_sample": _trade_metrics(is_trades),
        "oos": _trade_metrics(oos_trades),
        "unknown_split": _trade_metrics(unknown_trades),
    }


def _passes_validation(block: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    all_m = block["all"]
    is_m = block["in_sample"]
    oos_m = block["oos"]

    pf = all_m.get("profit_factor")
    if pf is None or pf <= 1:
        reasons.append("combined_pf_not_gt_1")
    if (all_m.get("total_pnl_pct") or 0) <= 0:
        reasons.append("combined_pnl_not_gt_0")

    is_pf = is_m.get("profit_factor")
    if is_pf is None or is_pf <= 1:
        reasons.append("is_pf_not_gt_1")
    oos_pf = oos_m.get("profit_factor")
    if oos_pf is None or oos_pf <= 1:
        reasons.append("oos_pf_not_gt_1")

    if all_m.get("trade_count", 0) == 0:
        reasons.append("no_closed_trades")

    return not reasons, reasons


def _stream_sessions(sessions: list[dict[str, Any]], stream: Stream) -> list[dict[str, Any]]:
    if stream == "combined":
        return sessions
    return [s for s in sessions if s["stream"] == stream]


def _build_stream_report(
    sessions: list[dict[str, Any]],
    stream: Stream,
    mod: Any,
) -> dict[str, Any]:
    subset = _stream_sessions(sessions, stream)
    session_count = len(subset)
    ge5 = _cohort_block(subset, "score_ge5", mod)
    ge6 = _cohort_block(subset, "score_ge6", mod)

    ge5_ok, ge5_fail = _passes_validation(ge5)
    ge6_ok, ge6_fail = _passes_validation(ge6)
    eval_ready = session_count >= MIN_SESSIONS

    return {
        "session_count": session_count,
        "evaluation_ready": eval_ready,
        "session_ids": [s["session_id"] for s in subset],
        "score_ge5": {
            "flag": FLAG_KEY["score_ge5"],
            "metrics": ge5,
            "validation_pass": eval_ready and ge5_ok,
            "validation_status": (
                "pass"
                if eval_ready and ge5_ok
                else ("collecting_sessions" if not eval_ready else "criteria_not_met")
            ),
            "validation_fail_reasons": ge5_fail if eval_ready else ["session_count_lt_30"],
        },
        "score_ge6": {
            "flag": FLAG_KEY["score_ge6"],
            "metrics": ge6,
            "validation_pass": eval_ready and ge6_ok,
            "validation_status": (
                "pass"
                if eval_ready and ge6_ok
                else ("collecting_sessions" if not eval_ready else "criteria_not_met")
            ),
            "validation_fail_reasons": ge6_fail if eval_ready else ["session_count_lt_30"],
        },
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p213 = _load_module(
        "phase213c_loader_p233",
        "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py",
    )

    sessions = _discover_sessions(SMALL_PAPER)
    streams: dict[str, Any] = {}
    for stream in ("live", "push_replay", "combined"):
        streams[stream] = _build_stream_report(sessions, stream, p213)

    combined = streams["combined"]
    report = {
        "phase": 233,
        "mode": "entry_expectancy_shadow_validation",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "new_feature_exploration_forbidden": True,
            "loser_analysis_forbidden": True,
            "time_of_day_analysis_forbidden": True,
            "symbol_analysis_forbidden": True,
            "new_sessions_only": True,
        },
        "method": {
            "source": "Phase230 shadow sessions (phase230_entry_expectancy_shadow marker or score fields in events)",
            "aggregation": "trade-level from accept+observer_exit pairs",
            "streams": ["live", "push_replay", "combined"],
            "cohorts": ["entry_expectancy_score_ge5_flag", "entry_expectancy_score_ge6_flag"],
            "min_sessions_for_evaluation": MIN_SESSIONS,
            "validation_requires": {
                "score_ge5": "PF>1, IS_PF>1, OOS_PF>1, total_pnl>0",
                "score_ge6": "PF>1, IS_PF>1, OOS_PF>1, total_pnl>0",
            },
        },
        "observed_session_count": {
            "live": streams["live"]["session_count"],
            "push_replay": streams["push_replay"]["session_count"],
            "combined": combined["session_count"],
        },
        "streams": streams,
        "summary": {
            "combined_evaluation_ready": combined["evaluation_ready"],
            "score_ge5_combined_pass": combined["score_ge5"]["validation_pass"],
            "score_ge6_combined_pass": combined["score_ge6"]["validation_pass"],
            "score_ge5_combined_metrics": combined["score_ge5"]["metrics"]["all"],
            "score_ge6_combined_metrics": combined["score_ge6"]["metrics"]["all"],
        },
        "notes": [
            "Only sessions with Phase230 shadow marker or entry_expectancy score fields are included.",
            "Pre-Phase230 sessions are excluded by design.",
            "IS/OOS split uses phase213c session lists.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {OUT} sessions={combined['session_count']} "
        f"ge5_pass={combined['score_ge5']['validation_pass']} "
        f"ge6_pass={combined['score_ge6']['validation_pass']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
