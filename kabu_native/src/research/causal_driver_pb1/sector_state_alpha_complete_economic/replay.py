"""Chronological complete-strategy replay. Occupancy is the frozen engine."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from research.causal_driver_pb1 import PROSPECTIVE_FROM
from research.causal_driver_pb1.sector_state_alpha_complete_economic.alpha_state import build_alpha, calendar
from research.causal_driver_pb1.sector_state_alpha_complete_economic.economics import TARGETS, economics, fold_of, stamp_primary
from research.causal_driver_pb1.sector_state_alpha_complete_economic.join import attach_exit, classify_event
from research.causal_driver_pb1.sector_state_alpha_shadow import M3_TARGETS
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.pb1_v4_clarified_machine_correction_v4.walk import emit_v4
from research.pb1_v4_complete_strategy_build_and_economic_validation.portfolio import replay_occupancy


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f > 0


def _recs(minutes: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    if minutes.empty:
        return out
    for (date, symbol), sg in minutes.groupby(["date", "symbol"], sort=False):
        sg = sg.sort_values("time_label")
        out[(str(date), str(symbol))] = {
            "t": [str(t)[:5] for t in sg["time_label"].tolist()],
            "o": [float(x) if _finite(x) else float("nan") for x in sg["open"].tolist()],
        }
    return out


def _walk_pb1(dates: list[str]) -> dict[str, Any]:
    bind = {
        "symbols": list(M3_TARGETS),
        "split": {
            "discovery_dates": list(dates),
            "confirmation_dates": [],
            "frozen_validation_dates": [],
        },
        "blocks": {"date_to_block": {}},
    }
    walked = emit_v4(bind)
    if not walked.get("ok"):
        return walked
    leaked = [str(r.get("date") or "") for r in list(walked.get("e0_events") or []) + list(walked.get("e1_events") or []) if str(r.get("date") or "") >= PROSPECTIVE_FROM]
    if leaked:
        raise RuntimeError("prospective_pb1_event")
    return walked


def _events(walked: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for kind, key in (("E0", "e0_events"), ("E1", "e1_events")):
        for ev in list(walked.get(key) or []):
            if str(ev.get("symbol") or "") not in set(M3_TARGETS):
                continue
            rows.append({
                "symbol": str(ev.get("symbol") or ""),
                "date": str(ev.get("date") or ""),
                "entry_t": str(ev.get("entry_t") or "")[:5],
                "entry_px": ev.get("entry_px"),
                "direction": ev.get("direction"),
                "DIR": ev.get("DIR"),
                "same_bar_entry": bool(ev.get("same_bar_entry")),
                "entry_type": kind,
            })
    rows.sort(key=lambda r: (r["date"], r["entry_t"], r["symbol"], r["entry_type"]))
    return rows


def _lost(walked: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    out = {}
    for row in list(walked.get("funnel_days") or []):
        out[(str(row.get("symbol") or ""), str(row.get("date") or ""))] = row
    return out


def _class_rows(trades: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    from research.causal_driver_pb1.sector_state_alpha_complete_economic.economics import _compact

    chosen = []
    for row in trades:
        reasons = list(row.get("exit_reasons") or [])
        if reason == "SESSION_FLAT_1520":
            if row.get("session_flat_fill"):
                chosen.append(row)
            continue
        if reason in reasons or (reason == "PB1_THESIS_LOST" and "PB1_THESIS_LOST" in reasons):
            chosen.append(row)
    item = _compact(chosen)
    item["exit_class"] = reason
    return item


def run_replay(symbols: list[str]) -> dict[str, Any]:
    dates = calendar()
    print(f"ELIGIBLE_DAYS {len(dates)} {dates[0]} {dates[-1]}", flush=True)
    alpha = build_alpha(symbols)
    if int(alpha["prospective_rows_read"]) != 0:
        raise RuntimeError("prospective_rows_read")
    print("PB1_WALK_START", flush=True)
    walked = _walk_pb1(dates)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason") or "pb1_walk_failed"}
    minutes = load_minutes(symbols=list(M3_TARGETS), allowed_dates=set(dates), forbidden_dates={d for d in dates if d >= PROSPECTIVE_FROM})
    if not minutes.empty and minutes["date"].astype(str).ge(PROSPECTIVE_FROM).any():
        raise RuntimeError("prospective_minutes")
    recs = _recs(minutes)
    lost = _lost(walked)
    classified = []
    for ev in _events(walked):
        funnel = lost.get((ev["symbol"], ev["date"])) or {}
        row = classify_event(
            event=ev,
            clocks=alpha["clocks"].get(ev["date"]) or {},
            lost=bool(funnel.get("THESIS_LOST")),
            lost_at=str(funnel.get("THESIS_LOST_AT") or "")[:5] or None,
        )
        if row["status"] != "ADMIT_ATTEMPT":
            classified.append(row)
            continue
        rec = recs.get((row["date"], row["symbol"]))
        if rec is None or row["entry_t"] not in rec["t"]:
            classified.append({**row, "status": "ENTRY_BAR_MISSING"})
            continue
        loc = rec["t"].index(row["entry_t"])
        px = float(rec["o"][loc])
        if not _finite(px) or abs(px - float(row["entry_px"])) > 1e-6:
            classified.append({**row, "status": "ENTRY_PX_MISMATCH"})
            continue
        classified.append(attach_exit(row, clocks=alpha["clocks"].get(row["date"]) or {}, rec=rec))
    candidates = [r for r in classified if r.get("status") == "CANDIDATE"]
    candidates.sort(key=lambda r: (r["date"], r["signal_t"], r["symbol"], r["entry_type"]))
    occupied = replay_occupancy(candidates)
    trades = stamp_primary(list(occupied["trades"]))
    fold_days: dict[str, int] = defaultdict(int)
    for day in dates:
        fold_days[fold_of(day)] += 1
    econ = economics(trades, eligible_by_fold=dict(fold_days))
    opportunity_n = len(alpha["episodes"]) * len(TARGETS)
    confirmed = mismatch = 0
    by_opp: dict[tuple[str, str], set[str]] = defaultdict(set)
    for ep in alpha["episodes"]:
        for symbol in TARGETS:
            key = (ep["episode_id"], symbol)
            hits = [
                r for r in classified
                if r.get("episode_id") == ep["episode_id"] and r.get("symbol") == symbol and r.get("date") == ep["date"]
                and r.get("status") not in {"STALE_OR_NO_ALPHA", "PB1_NOT_CONSUMED"}
            ]
            if any(r.get("status") == "ALPHA_DIRECTION_MISMATCH" for r in hits):
                by_opp[key].add("MISMATCH")
            if any(r.get("entry_type") in {"E0", "E1"} and r.get("side") == "long" and r.get("status") != "ALPHA_DIRECTION_MISMATCH" and r.get("status") != "STALE_OR_NO_ALPHA" for r in hits):
                by_opp[key].add("CONFIRMED")
    for key, flags in by_opp.items():
        if "CONFIRMED" in flags:
            confirmed += 1
        elif "MISMATCH" in flags:
            mismatch += 1
    rejected = opportunity_n - confirmed - mismatch
    qualified = [r for r in classified if r.get("side") == "long" and r.get("status") not in {"STALE_OR_NO_ALPHA", "PB1_NOT_CONSUMED", "ALPHA_DIRECTION_MISMATCH"}]
    both = 0
    seen: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in qualified:
        seen[(str(row.get("date")), str(row.get("symbol")), str(row.get("episode_id")))].add(str(row.get("entry_type")))
    both = sum(1 for kinds in seen.values() if "E0" in kinds and "E1" in kinds)
    exit_classes = [
        "Q60_STATE_LOSS",
        "PB1_THESIS_LOST",
        "ALPHA_OBSERVABILITY_WINDOW_END",
        "SESSION_FLAT_1520",
    ]
    return {
        "ok": True,
        "eligible_day_n": len(dates),
        "eligible_first": dates[0],
        "eligible_last": dates[-1],
        "prospective_rows_read": 0,
        "alpha_episode_n": len(alpha["episodes"]),
        "target_alpha_opportunity_n": opportunity_n,
        "PB1_confirmed_n": confirmed,
        "PB1_rejected_n": rejected,
        "ALPHA_DIRECTION_MISMATCH_n": mismatch,
        "E0_qualified_n": sum(1 for r in qualified if r.get("entry_type") == "E0"),
        "E1_qualified_n": sum(1 for r in qualified if r.get("entry_type") == "E1"),
        "e0_e1_overlap_n": both,
        "admit_attempt_n": len(qualified),
        "filled_trade_n": len(trades),
        "CAP_blocked_n": int(occupied["cap_blocked_n"]),
        "same_symbol_blocked_n": int(occupied["same_symbol_blocked_n"]),
        "observability_end_before_fill_reject_n": sum(1 for r in classified if r.get("status") == "REJECT_ALPHA_OBSERVABILITY_END_BEFORE_FILL"),
        "other_reject_n": sum(1 for r in classified if r.get("status") in {"ILLEGAL_FILL", "LOST_BEFORE_FILL", "ENTRY_BAR_MISSING", "ENTRY_PX_MISMATCH", "NO_EXIT_BAR", "NO_EXIT_DECISION"}),
        "occupancy": {
            "cap_violation_n": int(occupied["cap_violation_n"]),
            "same_symbol_overlap_violation_n": int(occupied["same_symbol_overlap_violation_n"]),
            "max_concurrent": int(occupied["max_concurrent"]),
            "reentry_n": int(occupied["reentry_n"]),
        },
        "exit_breakdown": {name: _class_rows(trades, name) for name in exit_classes},
        "same_clock_dual_n": sum(1 for r in trades if r.get("same_clock_dual")),
        "other_fail_close_n": sum(1 for r in trades if "FAIL_CLOSE_INVALID_DATA" in list(r.get("exit_reasons") or [])),
        "e0_fills": [r for r in trades if r.get("entry_type") == "E0"],
        "e1_fills": [r for r in trades if r.get("entry_type") == "E1"],
        "trades": trades,
        "economics": econ,
        "pb1_e0_emitted_n": len(walked.get("e0_events") or []),
        "pb1_e1_emitted_n": len(walked.get("e1_events") or []),
    }
