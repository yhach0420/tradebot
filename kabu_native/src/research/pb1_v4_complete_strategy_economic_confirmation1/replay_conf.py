"""Confirmation-1 replay using frozen Complete Strategy fill/exit/occupancy. OC dates only."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.pb1_v4_complete_strategy_build_and_economic_validation import CAP, SHARES
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min
from research.pb1_v4_complete_strategy_build_and_economic_validation.exits import resolve_exit
from research.pb1_v4_complete_strategy_build_and_economic_validation.fill import apply_x1_tax, fill_stamp, signed_gross
from research.pb1_v4_complete_strategy_build_and_economic_validation.portfolio import replay_occupancy
from research.pb1_v4_complete_strategy_build_and_economic_validation.replay import _finite, _funnel_idx, _signal_t, _slim
from research.pb1_v4_complete_strategy_economic_confirmation1 import EVAL_FIRST, EVAL_LAST, FV_FIRST, FV_LAST, PROSPECTIVE_FROM
from research.pb1_v4_frozen_old_confirmation_blind_validation.walk_conf import (
    filter_confirmation,
    open_confirmation_minutes,
    walk_frozen,
)


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
            if date < EVAL_FIRST or date > EVAL_LAST:
                skip["outside_confirmation1"] += 1
                continue
            if FV_FIRST <= date <= FV_LAST or date >= PROSPECTIVE_FROM:
                skip["sealed_holdout_event"] += 1
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
            if str(resolved.get("exit_t") or "")[:5] > "15:20":
                skip["exit_after_session_close"] += 1
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
                    "used_future_quote": False,
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


def walk_and_replay(*, bind: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
    symbols = list(bind.get("symbols") or [])
    conf_dates = list(part.get("confirmation_dates") or [])
    lookback = list(part.get("lookback_dates") or [])
    fv = list(part.get("frozen_validation_dates") or [])
    loaded = open_confirmation_minutes(
        symbols=symbols,
        lookback_dates=lookback,
        confirmation_dates=conf_dates,
        frozen_validation_dates=fv,
    )
    if not loaded.get("ok"):
        return {
            "ok": False,
            "reason": loaded.get("reason") or "load_failed",
            "FROZEN_VALIDATION_ECONOMIC_OPENED": bool(loaded.get("fv_hit")),
            "PROSPECTIVE_DATA_OPENED": bool(loaded.get("prospective_hit")),
        }
    minutes = loaded["minutes"]
    leak_fv = [d for d in sorted({str(x) for x in minutes["date"].tolist()}) if FV_FIRST <= d <= FV_LAST]
    leak_pros = [d for d in sorted({str(x) for x in minutes["date"].tolist()}) if d >= PROSPECTIVE_FROM]
    if leak_fv or leak_pros:
        return {"ok": False, "reason": "sealed_holdout_loaded", "leak_fv": leak_fv[:8], "leak_prospective": leak_pros[:8]}
    walked_all = walk_frozen(
        bind=bind,
        minutes=minutes,
        walk_dates=list(part.get("walk_dates") or []),
        symbols=symbols,
    )
    if not walked_all.get("ok"):
        return {"ok": False, "reason": walked_all.get("reason") or "walk_failed"}
    walked = filter_confirmation(walked_all, confirmation_dates=conf_dates)
    e0 = list(walked.get("e0_events") or [])
    e1 = list(walked.get("e1_events") or [])
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    conf_set = set(conf_dates)
    minutes = minutes.sort_values(["date", "symbol", "time_label"])
    need_sym = {str(r.get("symbol") or "") for r in e0 + e1}
    for (date, symbol), g in minutes.groupby(["date", "symbol"], sort=False):
        d = str(date)
        s = str(symbol)
        if d in conf_set and s in need_sym:
            recs[(d, s)] = _slim(g)
    built = build_candidates(walked=walked, recs=recs)
    occ = replay_occupancy(list(built.get("candidates") or []), cap=CAP)
    trades = list(occ.get("trades") or [])
    trades.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("entry_t") or ""), str(r.get("symbol") or "")))
    seen: dict[tuple[str, str], int] = defaultdict(int)
    for t in trades:
        key = (str(t.get("date") or ""), str(t.get("symbol") or ""))
        t["reentry_n"] = int(seen[key])
        seen[key] += 1
    blocked = [r for r in list(occ.get("rows") or []) if not r.get("admitted")]
    return {
        "ok": True,
        "reason": None,
        "e0_n": len(e0),
        "e1_n": len(e1),
        "signal_n": len(e0) + len(e1),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "walked_ok": bool(walked.get("ok")),
        "skip": built.get("skip"),
        "fill_px_mismatch_n": int(built.get("fill_px_mismatch_n") or 0),
        "candidate_n": int(built.get("candidate_n") or 0),
        "occupancy": {k: v for k, v in occ.items() if k not in {"trades", "rows"}},
        "trades": trades,
        "blocked_rows": blocked,
        "fill_n": int(occ.get("fill_n") or 0),
        "trade_n": len(trades),
        "max_concurrent": int(occ.get("max_concurrent") or 0),
        "cap_blocked_n": int(occ.get("cap_blocked_n") or 0),
        "same_symbol_blocked_n": int(occ.get("same_symbol_blocked_n") or 0),
        "same_symbol_overlap_violation_n": int(occ.get("same_symbol_overlap_violation_n") or 0),
        "cap_violation_n": int(occ.get("cap_violation_n") or 0),
        "lookback_funnel_dropped_n": int(walked.get("lookback_funnel_dropped_n") or 0),
        "confirmation_dates_loaded": list(loaded.get("confirmation_dates_loaded") or []),
        "lookback_dates_loaded_n": len(list(loaded.get("lookback_dates_loaded") or [])),
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "OLD_CONFIRMATION_ECONOMIC_OPENED": True,
    }
