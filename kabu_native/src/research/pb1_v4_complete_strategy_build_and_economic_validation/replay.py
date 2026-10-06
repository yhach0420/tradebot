"""Development-only Complete Strategy replay. Frozen V4 ENTRY reused. No Confirmation/FV PnL."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pandas as pd

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add, in_lunch
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.pb1_v4_complete_strategy_build_and_economic_validation import CAP, DEV_FIRST, DEV_LAST, SHARES
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min
from research.pb1_v4_complete_strategy_build_and_economic_validation.exits import resolve_exit
from research.pb1_v4_complete_strategy_build_and_economic_validation.fill import apply_x1_tax, fill_stamp, signed_gross
from research.pb1_v4_complete_strategy_build_and_economic_validation.isolation import V4_CACHE
from research.pb1_v4_complete_strategy_build_and_economic_validation.portfolio import replay_occupancy

WALKED_PATH = V4_CACHE / "walked.json"
EXPECTED_E0 = 201
EXPECTED_E1 = 468


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f > 0


def _slim(sg: pd.DataFrame) -> dict[str, Any]:
    sg = sg.sort_values("time_label")
    times = [str(t)[:5] for t in sg["time_label"].tolist()]
    o = [float(x) if _finite(x) else float("nan") for x in sg["open"].tolist()]
    return {"t": times, "o": o, "n": len(times)}


def load_walked() -> dict[str, Any]:
    if not WALKED_PATH.is_file():
        return {"ok": False, "reason": "walked_json_missing"}
    raw = json.loads(WALKED_PATH.read_text(encoding="utf-8"))
    e0 = list(raw.get("e0_events") or [])
    e1 = list(raw.get("e1_events") or [])
    dates = sorted({str(r.get("date") or "") for r in e0 + e1})
    leak = [d for d in dates if d < DEV_FIRST or d > DEV_LAST]
    ok = bool(raw.get("ok")) and len(e0) == EXPECTED_E0 and len(e1) == EXPECTED_E1 and not leak
    return {
        "ok": ok,
        "reason": None if ok else "walked_not_discovery_frozen_stream",
        "e0_events": e0,
        "e1_events": e1,
        "funnel_days": list(raw.get("funnel_days") or []),
        "e0_n": len(e0),
        "e1_n": len(e1),
        "same_bar_entry_n": int(raw.get("same_bar_entry_n") or 0),
        "n_days": int(raw.get("n_days") or 0),
        "walked_path": str(WALKED_PATH).replace("\\", "/"),
    }


def _funnel_idx(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        out[(str(r.get("symbol") or ""), str(r.get("date") or ""))] = r
    return out


def _signal_t(entry_t: str) -> str:
    prev = hhmm_add(str(entry_t)[:5], -1)
    return str(prev or entry_t)[:5]


def build_candidates(*, walked: dict[str, Any], recs: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    funnel = _funnel_idx(list(walked.get("funnel_days") or []))
    rows: list[dict[str, Any]] = []
    skip: dict[str, int] = defaultdict(int)
    fill_px_mismatch = 0
    for kind, events in (("E0", walked.get("e0_events") or []), ("E1", walked.get("e1_events") or [])):
        for ev in events:
            symbol = str(ev.get("symbol") or "")
            date = str(ev.get("date") or "")
            entry_t = str(ev.get("entry_t") or "")[:5]
            entry_px = ev.get("entry_px")
            if date < DEV_FIRST or date > DEV_LAST:
                skip["outside_development"] += 1
                continue
            if not entry_t or not _finite(entry_px):
                skip["no_entry_fill"] += 1
                continue
            if ev.get("same_bar_entry"):
                skip["same_bar_entry"] += 1
                continue
            rec = recs.get((date, symbol))
            if rec is None:
                skip["missing_bars"] += 1
                continue
            if in_lunch(entry_t) or entry_t >= "15:20":
                skip["illegal_fill_clock"] += 1
                continue
            funnel_row = funnel.get((symbol, date)) or {}
            lost = bool(funnel_row.get("THESIS_LOST"))
            lost_at = str(funnel_row.get("THESIS_LOST_AT") or "")[:5] or None
            if lost and lost_at and lost_at < entry_t:
                skip["lost_before_fill"] += 1
                continue
            try:
                loc = list(rec["t"]).index(entry_t)
                rec_px = float(rec["o"][loc])
            except (ValueError, TypeError, IndexError):
                rec_px = float("nan")
            if not _finite(rec_px):
                skip["entry_bar_missing"] += 1
                continue
            if abs(rec_px - float(entry_px)) > 1e-9:
                fill_px_mismatch += 1
                skip["entry_px_mismatch"] += 1
                continue
            side = str(ev.get("direction") or ("bull" if int(ev.get("DIR") or 0) > 0 else "bear"))
            signal_t = _signal_t(entry_t)
            if signal_t >= entry_t:
                skip["signal_not_before_fill"] += 1
                continue
            resolved = resolve_exit(rec, fill_t=entry_t, thesis_lost=lost, thesis_lost_at=lost_at)
            if not resolved.get("ok"):
                skip[str(resolved.get("reason") or "exit_fail")] += 1
                continue
            if str(resolved.get("exit_t") or "")[:5] <= entry_t:
                skip["exit_not_after_fill"] += 1
                continue
            entry_stamp = fill_stamp(px=float(entry_px), t=entry_t, side=side, kind="ENTRY")
            exit_stamp = fill_stamp(px=float(resolved["exit_px"]), t=str(resolved["exit_t"]), side=side, kind="EXIT")
            gross = signed_gross(side=side, entry_px=float(entry_px), exit_px=float(resolved["exit_px"]))
            tax = apply_x1_tax(gross_yen=gross, entry_px=float(entry_px))
            hold = (hhmm_to_min(str(resolved["exit_t"])) or 0) - (hhmm_to_min(entry_t) or 0)
            rows.append(
                {
                    "symbol": symbol,
                    "date": date,
                    "entry_type": kind,
                    "exec_variant": ev.get("exec_variant"),
                    "execution_id": ev.get("execution_id"),
                    "thesis_id": ev.get("thesis_id"),
                    "candidate_day_id": ev.get("candidate_day_id"),
                    "side": side,
                    "DIR": ev.get("DIR"),
                    "signal_t": signal_t,
                    "event_completed_at": signal_t,
                    "entry_allowed_at": entry_t,
                    "entry_t": entry_t,
                    "entry_px": float(entry_px),
                    "exit_t": resolved["exit_t"],
                    "exit_px": float(resolved["exit_px"]),
                    "exit_reason": resolved["exit_reason"],
                    "thesis_death": bool(resolved.get("thesis_death")),
                    "ops_flatten": bool(resolved.get("ops_flatten")),
                    "THESIS_LOST": lost,
                    "THESIS_LOST_AT": lost_at,
                    "THESIS_LOST_REASON": funnel_row.get("THESIS_LOST_REASON"),
                    "shares": int(SHARES),
                    "gross_pnl_yen": float(gross),
                    "execution_cost_yen": float(tax["execution_cost_yen"]),
                    "net_pnl_yen": float(tax["net_pnl_yen"]),
                    "holding_min": int(hold),
                    "same_bar_entry": False,
                    "used_mid": False,
                    "RESEARCH_EXECUTION_APPROXIMATION": True,
                    "entry_fill": entry_stamp,
                    "exit_fill": exit_stamp,
                }
            )
    rows.sort(key=lambda r: (str(r["date"]), str(r["signal_t"]), str(r["symbol"]), str(r["entry_type"])))
    return {
        "candidates": rows,
        "candidate_n": len(rows),
        "skip": dict(skip),
        "fill_px_mismatch_n": int(fill_px_mismatch),
    }


def replay_development(*, bind: dict[str, Any], walked: dict[str, Any], forbidden_dates: set[str]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = [str(d) for d in list(split.get("discovery_dates") or [])]
    events = list(walked.get("e0_events") or []) + list(walked.get("e1_events") or [])
    symbols = sorted({str(r.get("symbol") or "") for r in events})
    event_dates = sorted({str(r.get("date") or "") for r in events})
    allowed = set(disc) & set(event_dates)
    if allowed & set(forbidden_dates):
        return {"ok": False, "reason": "forbidden_dates_in_allowed_load"}
    print(f"LOAD_MINUTES symbols={len(symbols)} event_days={len(allowed)} pb1_v4_complete_strategy", flush=True)
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=set(forbidden_dates))
    if minutes.empty:
        return {"ok": False, "reason": "empty_minutes"}
    if minutes["date"].isin(list(forbidden_dates)).any():
        raise RuntimeError("forbidden_partition_loaded")
    leak = [d for d in sorted({str(x) for x in minutes["date"].tolist()}) if d < DEV_FIRST or d > DEV_LAST]
    if leak:
        return {"ok": False, "reason": "non_development_bars_loaded", "leak": leak[:8]}
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    for (date, symbol), g in minutes.groupby(["date", "symbol"], sort=False):
        recs[(str(date), str(symbol))] = _slim(g)
    built = build_candidates(walked=walked, recs=recs)
    occ = replay_occupancy(list(built.get("candidates") or []), cap=CAP)
    trades = list(occ.get("trades") or [])
    trades.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("entry_t") or ""), str(r.get("symbol") or "")))
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for t in trades:
        key = (str(t.get("date") or ""), str(t.get("symbol") or ""))
        t["reentry_n"] = int(seen[key])
        seen[key] += 1
    return {
        "ok": True,
        "symbols_loaded": len(symbols),
        "dates_loaded": len(allowed),
        "bar_n": int(len(minutes)),
        "skip": built.get("skip"),
        "fill_px_mismatch_n": int(built.get("fill_px_mismatch_n") or 0),
        "candidate_n": int(built.get("candidate_n") or 0),
        "occupancy": {k: v for k, v in occ.items() if k not in {"trades", "rows"}},
        "trades": trades,
        "blocked_rows": [r for r in list(occ.get("rows") or []) if not r.get("admitted")],
        "max_concurrent": int(occ.get("max_concurrent") or 0),
        "cap_blocked_n": int(occ.get("cap_blocked_n") or 0),
        "same_symbol_blocked_n": int(occ.get("same_symbol_blocked_n") or 0),
        "same_symbol_overlap_violation_n": int(occ.get("same_symbol_overlap_violation_n") or 0),
        "cap_violation_n": int(occ.get("cap_violation_n") or 0),
    }
