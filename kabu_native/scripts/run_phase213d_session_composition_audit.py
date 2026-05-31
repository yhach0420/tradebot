#!/usr/bin/env python3
"""
Phase213d: AM/PM composition audit for Phase213c 20260529 cohort trades.

Review only — no hard reject, no YAML changes.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase213d_session_composition_audit.json"
PHASE213C = REPO / "kabu_native/results/reports/phase213c_board_imbalance_cohort_stability_review.json"

TARGET_DAY = "20260529"
PHASE213C_DAY_N = 94

AM_LIVE_SESSION = "20260529/live_session_075135"
PM_LIVE_SESSION = "20260529/live_session_122541"
AM_SESSIONS = frozenset(
    {
        AM_LIVE_SESSION,
        "20260529/push_replay_002526",
    }
)
PM_SESSIONS = frozenset(
    {
        PM_LIVE_SESSION,
        "20260529/push_replay_003645",
    }
)

JST = ZoneInfo("Asia/Tokyo")
AM_ENTRY_START = time(9, 3)
AM_ENTRY_END = time(11, 20)
PM_ENTRY_START = time(12, 33)
PM_ENTRY_END = time(15, 18)


def _load_phase213c_module() -> Any:
    path = REPO / "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py"
    name = "phase213c_loader_p213d"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _entry_time_parts(entry_time: str) -> Optional[time]:
    try:
        dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00")).astimezone(JST)
        return dt.time()
    except (TypeError, ValueError):
        return None


def _am_pm_by_entry_time(entry_time: str) -> str:
    t = _entry_time_parts(entry_time)
    if t is None:
        return "unknown"
    if AM_ENTRY_START <= t <= AM_ENTRY_END:
        return "am"
    if PM_ENTRY_START <= t <= PM_ENTRY_END:
        return "pm"
    return "outside_policy_window"


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
        }
    pnls = [float(r["pnl_pct"]) for r in rows]
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        pf: Optional[float] = None if wins <= 0 else float("inf")
    else:
        pf = round(wins / gl, 4)
    total = round(sum(pnls), 4)
    return {
        "trade_count": len(rows),
        "profit_factor": pf,
        "total_pnl_pct": total,
        "avg_pnl_pct": round(total / len(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
    }


def _share(part: int, whole: int) -> Optional[float]:
    if whole <= 0:
        return None
    return round(100.0 * part / whole, 2)


def _build_cohort(mod: Any) -> list[dict[str, Any]]:
    p71 = mod._load_phase71()
    book_cache: dict[tuple[str, str], list[Any]] = {}
    cohort: list[dict[str, Any]] = []
    for session_rel in mod.ALL_SESSIONS:
        trades, _source = mod._load_session_trades(session_rel, p71)
        if not trades:
            continue
        enriched = mod._enrich_trades(session_rel, trades, book_cache)
        seen: set[tuple[str, str]] = set()
        for r in enriched:
            if not r.get("in_phase213b_D_cohort"):
                continue
            key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            cohort.append(r)
    return cohort


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mod = _load_phase213c_module()
    cohort = _build_cohort(mod)
    day_rows = [r for r in cohort if str(r.get("day_stamp")) == TARGET_DAY]

    phase213c_ref: dict[str, Any] = {}
    if PHASE213C.is_file():
        phase213c_ref = json.loads(PHASE213C.read_text(encoding="utf-8"))

    live_am = [r for r in day_rows if r.get("session_id") == AM_LIVE_SESSION]
    pm_live = [r for r in day_rows if r.get("session_id") == PM_LIVE_SESSION]
    am_by_session = [r for r in day_rows if str(r.get("session_id")) in AM_SESSIONS]
    pm_by_session = [r for r in day_rows if str(r.get("session_id")) in PM_SESSIONS]
    am_by_time = [r for r in day_rows if _am_pm_by_entry_time(str(r.get("entry_time") or "")) == "am"]
    pm_by_time = [r for r in day_rows if _am_pm_by_entry_time(str(r.get("entry_time") or "")) == "pm"]
    unclassified = [
        r
        for r in day_rows
        if str(r.get("session_id")) not in AM_SESSIONS | PM_SESSIONS
        and _am_pm_by_entry_time(str(r.get("entry_time") or "")) == "outside_policy_window"
    ]

    day_n = len(day_rows)
    am_m = _metrics(am_by_session)
    pm_m = _metrics(pm_by_session)
    live_am_m = _metrics(live_am)
    pm_live_m = _metrics(pm_live)

    session_split: dict[str, Any] = {}
    for sid in sorted(AM_SESSIONS | PM_SESSIONS):
        rows = [r for r in day_rows if r.get("session_id") == sid]
        session_split[sid] = {
            **_metrics(rows),
            "am_pm_bucket": "am" if sid in AM_SESSIONS else "pm",
            "trade_share_of_day_pct": _share(len(rows), day_n),
            "pnl_share_of_day_pct": _share(
                sum(float(r["pnl_pct"]) for r in rows),
                am_m["total_pnl_pct"] + pm_m["total_pnl_pct"],
            ),
        }

    report = {
        "phase": "213d",
        "mode": "session_composition_audit",
        "target_day": TARGET_DAY,
        "reference": "Phase213c day_stamp cohort (Phase213b D top-20% imbalance)",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "production_yaml_changes_forbidden": True,
        },
        "phase213c_reference": {
            "day_trade_count": (
                (phase213c_ref.get("daily_breakdown") or {}).get(TARGET_DAY) or {}
            ).get("trade_count", PHASE213C_DAY_N),
            "day_total_pnl_pct": (
                (phase213c_ref.get("daily_breakdown") or {}).get(TARGET_DAY) or {}
            ).get("total_pnl_pct"),
            "day_profit_factor": (
                (phase213c_ref.get("daily_breakdown") or {}).get(TARGET_DAY) or {}
            ).get("profit_factor"),
        },
        "rebuilt_day_cohort": {
            "trade_count": day_n,
            "matches_phase213c_n": day_n == PHASE213C_DAY_N,
            "metrics": _metrics(day_rows),
        },
        "live_session_075135": {
            **live_am_m,
            "trade_share_of_20260529_day_pct": _share(live_am_m["trade_count"], day_n),
            "pnl_share_of_20260529_day_pct": _share(
                live_am_m["total_pnl_pct"],
                _metrics(day_rows)["total_pnl_pct"],
            ),
        },
        "pm_session_live_122541": {
            **pm_live_m,
            "trade_share_of_20260529_day_pct": _share(pm_live_m["trade_count"], day_n),
            "pnl_share_of_20260529_day_pct": _share(
                pm_live_m["total_pnl_pct"],
                _metrics(day_rows)["total_pnl_pct"],
            ),
        },
        "am_pm_by_session_folder": {
            "am_sessions": sorted(AM_SESSIONS),
            "pm_sessions": sorted(PM_SESSIONS),
            "am": {
                **am_m,
                "trade_share_of_20260529_day_pct": _share(am_m["trade_count"], day_n),
                "pnl_share_of_20260529_day_pct": _share(
                    am_m["total_pnl_pct"],
                    _metrics(day_rows)["total_pnl_pct"],
                ),
            },
            "pm": {
                **pm_m,
                "trade_share_of_20260529_day_pct": _share(pm_m["trade_count"], day_n),
                "pnl_share_of_20260529_day_pct": _share(
                    pm_m["total_pnl_pct"],
                    _metrics(day_rows)["total_pnl_pct"],
                ),
            },
            "pm_session_trade_count": pm_m["trade_count"],
            "am_pf": am_m["profit_factor"],
            "pm_pf": pm_m["profit_factor"],
            "am_total_pnl_pct": am_m["total_pnl_pct"],
            "pm_total_pnl_pct": pm_m["total_pnl_pct"],
            "session_breakdown": session_split,
        },
        "am_pm_by_entry_time_policy": {
            "am_window": "09:03-11:20 JST",
            "pm_window": "12:33-15:18 JST",
            "am": _metrics(am_by_time),
            "pm": _metrics(pm_by_time),
            "outside_policy_window_count": len(unclassified),
        },
        "composition_vs_phase213c_94": {
            "phase213c_day_n": PHASE213C_DAY_N,
            "rebuilt_day_n": day_n,
            "live_session_075135_share_pct": _share(live_am_m["trade_count"], PHASE213C_DAY_N),
            "pm_session_share_pct": _share(pm_m["trade_count"], PHASE213C_DAY_N),
            "am_session_share_pct": _share(am_m["trade_count"], PHASE213C_DAY_N),
            "live_075135_plus_pm_live_share_pct": _share(
                live_am_m["trade_count"] + pm_live_m["trade_count"],
                PHASE213C_DAY_N,
            ),
            "push_replay_am_share_pct": _share(
                len([r for r in day_rows if r.get("session_id") == "20260529/push_replay_002526"]),
                PHASE213C_DAY_N,
            ),
            "push_replay_pm_share_pct": _share(
                len([r for r in day_rows if r.get("session_id") == "20260529/push_replay_003645"]),
                PHASE213C_DAY_N,
            ),
        },
        "verdict_notes": [
            "AM/PM bucket uses Phase116 session folders: AM=075135+push_replay_002526, PM=122541+push_replay_003645.",
            "Day cohort keyed by entry_time JST day_stamp (Phase213c 94-trade definition).",
            "Entry-time policy cross-check uses AmPmSessionPolicy allowed entry windows.",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {OUT} day_n={day_n} live_075135={live_am_m['trade_count']} "
        f"pm={pm_m['trade_count']} am_pf={am_m['profit_factor']} pm_pf={pm_m['profit_factor']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
