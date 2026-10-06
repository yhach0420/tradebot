"""Identity sets, replacement chains, board snapshots. Reuse Pre-CAP occupancy. No 20260903. No restream."""
from __future__ import annotations

from typing import Any, Optional

from research.anchor_timing_robustness.grid import hm_epoch
from research.simple_tech_redesign.branch_u_causal_analyze import _tid_map, attribution
from research.simple_tech_redesign.branch_u_causal_harvest import _sym
from research.simple_tech_redesign.pre_cap_candidate_harvest import load_cohort
from research.simple_tech_redesign.pre_cap_candidate_spec import COMPONENT_KEYS, COMPONENT_NAMES
from research.simple_tech_redesign.pre_cap_hard_reject_rca_spec import AM_OPEN_HOUR, AM_OPEN_MINUTE, DENSITY_LOOKBACK_SEC

GOOD = "GOOD_CONTINUATION"
DIP = "DIP_THEN_RECOVERY"
EARLY = "EARLY_FAILURE"
PTF = "PROFIT_THEN_FAILURE"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _tid(date: Any, symbol: Any, t0: Any) -> Optional[tuple[str, str, float]]:
    t = _f(t0)
    if t is None:
        return None
    return (str(date or ""), str(symbol or "").replace(".T", ""), float(t))


def execution_route(row: dict[str, Any]) -> str:
    role = str(row.get("fill_role") or "")
    if role == "CORE":
        return "E4_CORE"
    if role == "ADDED":
        return "ASK_CROSS_ADDED"
    e4 = row.get("e4_filled")
    if e4 is True:
        return "E4_CORE"
    if e4 is False:
        return "ASK_CROSS_ADDED"
    return "UNKNOWN"


def remove_diagnostic(row: dict[str, Any]) -> str:
    path = str(row.get("path_type") or "")
    never = bool(row.get("never_break_even"))
    if path in {GOOD, DIP}:
        return "LOOKED_WRONG_TO_REMOVE"
    if path == EARLY or never:
        return "LOOKED_RIGHT_TO_REMOVE"
    return "AMBIGUOUS"


def board_bits(row: dict[str, Any]) -> dict[str, Any]:
    comps = list(row.get("components") or [])
    by_name = {str(c.get("name") or ""): c for c in comps}
    pre = dict(row.get("pre_cap") or {})
    out = {
        "adverse_component_count": int(row.get("adverse_component_count") or 0),
        "pre_cap_reject": bool(row.get("pre_cap_reject")),
        "spread_bps": pre.get("spread_bps"),
        "bid1_qty": pre.get("bid1_qty"),
        "ask1_qty": pre.get("ask1_qty"),
        "bid_depth_diagnostic": row.get("bid_depth_diagnostic"),
        "snapshot_ok": bool(row.get("board_snapshot_ok")),
    }
    for name, key in zip(COMPONENT_NAMES, COMPONENT_KEYS):
        c = dict(by_name.get(name) or {})
        out[name] = bool(c.get("active"))
        out[key] = c.get("event_n") if c.get("event_n") is not None else pre.get(key)
    return out


def enrich_from_row(trade: dict[str, Any], row: dict[str, Any], *, am_start: float) -> dict[str, Any]:
    rec = dict(trade)
    rec["symbol"] = _sym(rec)
    t0 = _f(rec.get("t0"))
    rec["minutes_from_session_open"] = None if t0 is None else (float(t0) - float(am_start)) / 60.0
    rec["never_break_even"] = bool(row.get("never_break_even"))
    rec["break_even_reached"] = bool(row.get("break_even_reached"))
    rec["actual_filled"] = bool(row.get("actual_filled"))
    rec["e4_filled"] = row.get("e4_filled")
    rec["execution_route"] = execution_route(row if row.get("fill_role") else rec)
    rec["pre_cap_reject"] = bool(row.get("pre_cap_reject"))
    rec["reject_reason"] = row.get("reject_reason") or ""
    rec["control_pnl"] = _f(row.get("control_pnl"))
    rec["session_close_bps"] = row.get("session_close_bps")
    rec.update(board_bits(row))
    rec["remove_diagnostic"] = remove_diagnostic({**row, **rec})
    rec["row_present"] = bool(row)
    return rec


def occupants_at(trades: list[dict[str, Any]], t: float) -> list[dict[str, Any]]:
    out = []
    for tr in trades:
        ft = _f(tr.get("fill_time"))
        et = _f(tr.get("exit_time"))
        if ft is None or et is None:
            continue
        if float(ft) <= float(t) + 1e-12 and float(t) < float(et) - 1e-12:
            out.append(tr)
    return out


def slot_count_at(trades: list[dict[str, Any]], t: float) -> int:
    return len(occupants_at(trades, t))


def density_n(rows: list[dict[str, Any]], t: float, lookback: float = DENSITY_LOOKBACK_SEC) -> int:
    n = 0
    for r in rows:
        t0 = _f(r.get("t0"))
        if t0 is None:
            continue
        if float(t) - float(lookback) - 1e-12 <= float(t0) <= float(t) + 1e-12:
            n += 1
    return n


def day_identities(body: dict[str, Any]) -> dict[str, Any]:
    day = str(body.get("date") or "")
    am_start = float(hm_epoch(day, AM_OPEN_HOUR, AM_OPEN_MINUTE))
    rows = list(body.get("rows") or [])
    by_row = {}
    for r in rows:
        k = _tid(r.get("date") or day, r.get("symbol"), r.get("t0"))
        if k:
            by_row[k] = r
    ctrl_tr = [enrich_from_row(t, by_row.get(_tid(t.get("date") or day, t.get("symbol"), t.get("t0")) ) or {}, am_start=am_start) for t in list((body.get("control") or {}).get("trades") or [])]
    treat_tr = [enrich_from_row(t, by_row.get(_tid(t.get("date") or day, t.get("symbol"), t.get("t0")) ) or {}, am_start=am_start) for t in list((body.get("treatment") or {}).get("trades") or [])]
    attr = attribution(ctrl_tr, treat_tr)
    cm = _tid_map(ctrl_tr)
    tm = _tid_map(treat_tr)
    common_ids = sorted(set(cm) & set(tm))
    ctrl_only_ids = sorted(set(cm) - set(tm))
    incr_ids = sorted(set(tm) - set(cm))
    common = [cm[k] for k in common_ids]
    control_only = [cm[k] for k in ctrl_only_ids]
    incremental = [tm[k] for k in incr_ids]
    flagged = [r for r in rows if r.get("pre_cap_reject")]
    flagged.sort(key=lambda r: (_f(r.get("t0")) or 0.0, _sym(r)))
    reject_index = {}
    for i, r in enumerate(flagged, start=1):
        k = _tid(r.get("date") or day, r.get("symbol"), r.get("t0"))
        if k:
            reject_index[k] = i

    used_removed = set()
    pairs = []
    incremental_sorted = sorted(incremental, key=lambda t: (_f(t.get("t0")) or 0.0, str(t.get("symbol") or "")))
    for inc in incremental_sorted:
        t0 = _f(inc.get("t0"))
        if t0 is None:
            continue
        occ = [t for t in occupants_at(control_only, float(t0)) if _tid(t.get("date"), t.get("symbol"), t.get("t0")) not in used_removed]
        occ.sort(key=lambda t: float(_f(t.get("fill_time")) or 0.0), reverse=True)
        removed = occ[0] if occ else None
        if removed is None:
            prior = [
                t
                for t in control_only
                if _tid(t.get("date"), t.get("symbol"), t.get("t0")) not in used_removed
                and _f(t.get("fill_time")) is not None
                and float(_f(t.get("fill_time"))) <= float(t0) + 1e-12
            ]
            prior.sort(key=lambda t: float(_f(t.get("fill_time")) or 0.0), reverse=True)
            removed = prior[0] if prior else None
        if removed is None:
            continue
        rk = _tid(removed.get("date"), removed.get("symbol"), removed.get("t0"))
        if rk:
            used_removed.add(rk)
        rem_pnl = float(_f(removed.get("pnl_yen_100")) or 0.0)
        inc_pnl = float(_f(inc.get("pnl_yen_100")) or 0.0)
        rem_t0 = _f(removed.get("t0"))
        elapsed = None if rem_t0 is None else float(t0) - float(rem_t0)
        enabling_reject_n = reject_index.get(rk)
        pairs.append(
            {
                "date": day,
                "removed": removed,
                "replacement": inc,
                "removed_trade_id": removed.get("trade_id"),
                "replacement_trade_id": inc.get("trade_id"),
                "removed_trade_pnl": rem_pnl,
                "replacement_trade_pnl": inc_pnl,
                "replacement_minus_removed": inc_pnl - rem_pnl,
                "removed_adverse_component_count": int(removed.get("adverse_component_count") or 0),
                "replacement_adverse_component_count": int(inc.get("adverse_component_count") or 0),
                "elapsed_sec_from_removed": elapsed,
                "replacement_minutes_from_open": inc.get("minutes_from_session_open"),
                "removed_minutes_from_open": removed.get("minutes_from_session_open"),
                "control_slot_count_at_replacement_t0": slot_count_at(ctrl_tr, float(t0)),
                "treatment_slot_count_at_replacement_t0": slot_count_at(treat_tr, float(t0)),
                "candidate_density_60s": density_n(rows, float(t0)),
                "preceding_precap_reject_n": sum(1 for r in flagged if (_f(r.get("t0")) or 0.0) <= float(t0) + 1e-12),
                "enabling_reject_seq": enabling_reject_n,
                "match_mode": "occupying_control_only" if occ else "preceding_control_only_fill",
            }
        )

    unpaired_removed = [t for t in control_only if _tid(t.get("date"), t.get("symbol"), t.get("t0")) not in used_removed]
    unpaired_incr = [t for t in incremental if all(p.get("replacement_trade_id") != t.get("trade_id") for p in pairs)]
    for t in control_only:
        t["removal_class"] = "DIRECT_PRECAP" if t.get("pre_cap_reject") else "OCCUPANCY_CASCADE"
        t["minutes_from_session_open"] = t.get("minutes_from_session_open")
        t["signal_order_in_day"] = None
    rows_sorted = sorted(rows, key=lambda r: (_f(r.get("t0")) or 0.0, _sym(r)))
    order_map = {}
    for i, r in enumerate(rows_sorted, start=1):
        k = _tid(r.get("date") or day, r.get("symbol"), r.get("t0"))
        if k:
            order_map[k] = i
    for bucket in (common, control_only, incremental):
        for t in bucket:
            k = _tid(t.get("date"), t.get("symbol"), t.get("t0"))
            t["signal_order_in_day"] = order_map.get(k)
            t["candidate_density_60s"] = density_n(rows, float(_f(t.get("t0")) or 0.0)) if _f(t.get("t0")) is not None else None

    return {
        "date": day,
        "common": common,
        "control_only": control_only,
        "incremental": incremental,
        "pairs": pairs,
        "unpaired_removed": unpaired_removed,
        "unpaired_incremental": unpaired_incr,
        "flagged_n": len(flagged),
        "attribution": {
            "COMMON_N": len(common),
            "CONTROL_ONLY_N": len(control_only),
            "INCREMENTAL_TREATMENT_N": len(incremental),
            "DIRECT_REMOVAL_EFFECT": attr.get("DISPLACED_TRADE_DELTA"),
            "DOWNSTREAM_OCCUPANCY_EFFECT": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
        },
        "occupancy_sot_ok": bool(body.get("control_sot_ok")) and bool(body.get("treatment_sot_ok")),
        "occupancy_leftover_n": int(body.get("occupancy_leftover_n") or 0),
        "rows": rows,
        "control_trades": ctrl_tr,
        "treatment_trades": treat_tr,
    }


def harvest_cohort(days: list[str], *, cohort: str) -> dict[str, Any]:
    raw = load_cohort(days, cohort=cohort)
    if not raw.get("ok"):
        return raw
    day_packs = []
    for body in list(raw.get("day_bodies") or []):
        print(f"{cohort} {body.get('date')} identity/replacement", flush=True)
        day_packs.append(day_identities(body))
    common, control_only, incremental, pairs = [], [], [], []
    for p in day_packs:
        common.extend(list(p.get("common") or []))
        control_only.extend(list(p.get("control_only") or []))
        incremental.extend(list(p.get("incremental") or []))
        pairs.extend(list(p.get("pairs") or []))
    attr = attribution(sum((list(p.get("control_trades") or []) for p in day_packs), []), sum((list(p.get("treatment_trades") or []) for p in day_packs), []))
    return {
        "ok": True,
        "cohort": cohort,
        "days": list(days),
        "day_packs": day_packs,
        "common": common,
        "control_only": control_only,
        "incremental": incremental,
        "pairs": pairs,
        "rows": list(raw.get("rows") or []),
        "occupancy_sot_ok": bool(raw.get("occupancy_sot_ok")),
        "occupancy_leftover_n": int(raw.get("occupancy_leftover_n") or 0),
        "identity": {
            "COMMON_N": len(common),
            "CONTROL_ONLY_N": len(control_only),
            "INCREMENTAL_TREATMENT_N": len(incremental),
        },
        "attribution": {
            "DIRECT_REMOVAL_EFFECT": attr.get("DISPLACED_TRADE_DELTA"),
            "DOWNSTREAM_OCCUPANCY_EFFECT": attr.get("SLOT_RELEASE_DOWNSTREAM_DELTA"),
            "COMMON_PNL_DELTA": attr.get("DIRECT_EXIT_DELTA"),
            "TOTAL_CAUSAL_DELTA": attr.get("TOTAL_CAUSAL_DELTA"),
            "COMMON_N": attr.get("COMMON_N"),
            "CONTROL_ONLY_N": attr.get("CONTROL_ONLY_N"),
            "INCREMENTAL_TREATMENT_N": attr.get("INCREMENTAL_TREATMENT_N"),
        },
    }
