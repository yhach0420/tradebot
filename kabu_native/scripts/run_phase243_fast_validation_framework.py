#!/usr/bin/env python3
"""
Phase243: Fast validation framework for ENTRY gates (review only).

Goal:
Evaluate multiple entry gate candidates on historical push_replay + replay sessions
without waiting for paper trades. Uses existing small_paper_events + observer_exit pairs.

Gates (initial set):
1) no_score_gate
2) v1_score_ge5
3) v1_score_ge6
4) v2_score_ge5
5) v2_score_ge6

Metrics per gate:
- trade_count
- profit_factor
- total_pnl_pct
- win_rate
- stop_rate
- avg_pnl_pct

Constraints:
- review only
- no production/YAML changes
- no entry/score changes (uses existing score computation)
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase243_fast_validation_framework.json"


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


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
    """
    Prefer JSONL (richer types), fall back to CSV.
    """
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


def _read_summary(session_dir: Path) -> dict[str, Any]:
    path = session_dir / "small_paper_summary.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _classify_session(session_id: str, summary: dict[str, Any]) -> Optional[str]:
    """
    Return push_replay | replay | None.
    Live sessions are excluded (Phase243 target is replay/push_replay history).
    """
    sid = session_id.replace("\\", "/")
    base_name = sid.split("/")[-1].lower()
    mode = str((summary or {}).get("mode") or "").lower()
    source = str((summary or {}).get("source") or "").lower()

    if "live_session" in base_name or "live_full_session" in base_name:
        return None
    if "push_replay" in base_name or "push_replay_sim" in sid.lower():
        return "push_replay"
    if source in ("push-replay", "push_replay") or "push_replay" in mode:
        return "push_replay"
    if source == "replay" or ("replay" in mode and "push" not in mode and "live" not in mode):
        return "replay"
    if "/" not in sid and len(sid) == 8 and sid.isdigit():
        return "replay"
    return None


def _discover_sessions(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for summary_path in sorted(base.rglob("small_paper_summary.json")):
        sdir = summary_path.parent
        rel = sdir.relative_to(base).as_posix()
        summ = _read_summary(sdir)
        stream = _classify_session(rel, summ)
        if stream is None:
            continue
        # Must have events; framework relies on accept+observer_exit
        if not ((sdir / "small_paper_events.jsonl").is_file() or (sdir / "small_paper_events.csv").is_file()):
            continue
        out.append(
            {
                "session_id": rel,
                "session_dir": str(sdir),
                "stream": stream,
                "mode": summ.get("mode"),
                "source": summ.get("source"),
            }
        )
    return out


def _extract_closed_trades(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Build trade rows from accept + observer_exit pairs when available.
    Fallback: accept rows that already contain exit fields (e.g. live_virtual_hold push_replay runs)
    using accept.exit_time + accept.pnl_pct.
    """
    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("event_type") or "") != "accepted":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key[0] and key[1]:
            accepts[key] = ev

    exits: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if str(ev.get("event_type") or "") != "observer_exit":
            continue
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if key[0] and key[1]:
            exits[key] = ev

    rows: list[dict[str, Any]] = []
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    for key, acc in accepts.items():
        ex = exits.get(key)
        if ex:
            pnl = _float(ex.get("pnl_pct"))
            reason = str(ex.get("exit_reason") or "")
            stop_hit = bool(ex.get("stop_hit")) or reason == "stop_hit"
        else:
            # Fallback: accept row itself may contain realized pnl (push_replay live_virtual_hold style)
            pnl = _float(acc.get("pnl_pct"))
            reason = str(acc.get("exit_reason") or "")
            stop_hit = _boolish(acc.get("stop_hit")) or reason == "stop_hit"
        if pnl is None:
            continue

        # Recompute v1+v2 score at accept (does not alter production; for offline eval).
        score_fields = compute_entry_expectancy_score_fields(trade=acc)
        v1 = int(score_fields.get("entry_expectancy_score") or 0)
        v2 = int(score_fields.get("entry_expectancy_score_v2") or 0)

        rows.append(
            {
                "symbol": key[0],
                "entry_time": key[1],
                "pnl_pct": float(pnl),
                "stop_hit": stop_hit,
                "exit_reason": reason,
                "v1_score": v1,
                "v2_score": v2,
                "v1_ge5": v1 >= 5,
                "v1_ge6": v1 >= 6,
                "v2_ge5": v2 >= 5,
                "v2_ge6": v2 >= 6,
            }
        )
    return rows


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(r["pnl_pct"]) for r in rows]
    n = len(pnls)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "win_rate": None,
            "stop_rate": None,
            "avg_pnl_pct": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions = _discover_sessions(SMALL_PAPER)
    all_trades: list[dict[str, Any]] = []
    per_session: list[dict[str, Any]] = []

    coverage_debug: list[dict[str, Any]] = []
    for i, sess in enumerate(sessions, 1):
        sdir = Path(sess["session_dir"])
        events = _load_events(sdir)
        et_counts: dict[str, int] = {}
        for ev in events[:5000]:
            et = str(ev.get("event_type") or "")
            if not et:
                continue
            et_counts[et] = et_counts.get(et, 0) + 1
        trades = _extract_closed_trades(events)
        all_trades.extend(trades)
        per_session.append(
            {
                "session_id": sess["session_id"],
                "stream": sess["stream"],
                "closed_trades": len(trades),
            }
        )
        coverage_debug.append(
            {
                "session_id": sess["session_id"],
                "stream": sess["stream"],
                "event_type_counts_head_5k": et_counts,
                "events_loaded": len(events),
                "closed_trades": len(trades),
                "has_observer_exit": any(str(e.get("event_type") or "") == "observer_exit" for e in events[:20000]),
            }
        )
        if i % 20 == 0:
            print(f"  [{i}/{len(sessions)}] scanned", flush=True)

    # Gates
    gates: dict[str, list[dict[str, Any]]] = {
        "no_score_gate": list(all_trades),
        "v1_score_ge5": [t for t in all_trades if t.get("v1_ge5")],
        "v1_score_ge6": [t for t in all_trades if t.get("v1_ge6")],
        "v2_score_ge5": [t for t in all_trades if t.get("v2_ge5")],
        "v2_score_ge6": [t for t in all_trades if t.get("v2_ge6")],
    }

    report = {
        "phase": 243,
        "mode": "fast_validation_framework",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "entry_change_forbidden": True,
            "hard_reject_forbidden": True,
            "new_feature_exploration_forbidden": True,
        },
        "population": {
            "sessions_scanned": len(sessions),
            "closed_trades_total": len(all_trades),
            "data_source": "accepted + observer_exit pairs from small_paper_events",
        },
        "sessions": {
            "session_ids": [s["session_id"] for s in sessions],
            "by_session_closed_trade_counts": per_session,
            "coverage_debug": coverage_debug,
        },
        "gates": {
            name: {
                "definition": name,
                "metrics": _metrics(rows),
            }
            for name, rows in gates.items()
        },
        "notes": [
            "This framework evaluates gates offline using historical logs only.",
            "Score v1/v2 are recomputed at accept using entry_expectancy_score_shadow (no YAML/production changes).",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} sessions={len(sessions)} trades={len(all_trades)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

