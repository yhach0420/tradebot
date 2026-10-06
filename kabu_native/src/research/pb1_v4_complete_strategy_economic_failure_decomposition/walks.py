"""ASF and OR-recross deep walks using already-defined V4 structure. No new price thresholds."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of
from research.pb1_v4_clarified_machine_correction_v4.location import classify_interaction, five_m_left
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min
from research.pb1_v4_complete_strategy_economic_failure_decomposition.classify import classify_asf
from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import dir_bps
from research.pb1_v4_machine_implementation.bars5 import build_five_m


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _level_from_setup(setup: dict[str, Any] | None) -> float | None:
    if not setup:
        return None
    loc = setup.get("location") or {}
    if isinstance(loc, dict) and _finite(loc.get("level")):
        return float(loc["level"])
    for k in ("location_level", "level"):
        if _finite(setup.get(k)):
            return float(setup[k])
    return None


def _delay(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    am, bm = hhmm_to_min(str(a)[:5]), hhmm_to_min(str(b)[:5])
    if am is None or bm is None:
        return None
    return int(bm) - int(am)


def asf_case(row: dict[str, Any], rec: dict[str, Any] | None, setup: dict[str, Any] | None) -> dict[str, Any]:
    sign = int(row.get("DIR") or (1 if str(row.get("side") or "").lower() in {"bull", "long"} else -1))
    entry_t = str(row.get("entry_t") or "")[:5]
    lost_t = str(row.get("THESIS_LOST_AT") or "")[:5]
    first_struct = None
    through = 0
    prev = "NO_INTERACTION_YET"
    level = _level_from_setup(setup)
    am_path = []
    if rec is not None:
        session_idx = session_idx_of(list(rec["t"]))
        bars = build_five_m(rec, session_idx, through="11:19")
        for b in bars:
            t1 = str(b.get("t1") or b.get("t") or "")[:5]
            if t1 <= entry_t:
                continue
            if lost_t and t1 > lost_t:
                break
            am_path.append({"t": t1, "o": b.get("o"), "h": b.get("h"), "l": b.get("l"), "c": b.get("c")})
            if level is None:
                continue
            inter = classify_interaction(
                sign=sign,
                high=float(b.get("h") or 0),
                low=float(b.get("l") or 0),
                close=float(b.get("c") or 0),
                level=float(level),
                prev=prev,
            )
            prev = str(inter.get("state") or prev)
            if inter.get("kills_thesis") and (through >= 1 or str(inter.get("state")) == "ACCEPTED_FAILURE"):
                first_struct = t1
                break
            if str(inter.get("state")) == "TEMPORARY_PENETRATION":
                through += 1
                if through >= 2:
                    first_struct = t1
                    break
            elif inter.get("kills_thesis"):
                through += 1
    delay = _delay(first_struct or row.get("MFE_t"), lost_t)
    label = classify_asf(row, first_struct_t=first_struct, delay_min=_delay(row.get("MFE_t"), lost_t))
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "side": row.get("side"),
        "entry_type": row.get("entry_type"),
        "entry_t": entry_t,
        "entry_px": row.get("entry_px"),
        "seed_family": row.get("seed_family"),
        "location_family": row.get("location_family"),
        "location_subtype": row.get("location_subtype"),
        "location_level": level,
        "first_favorable_t": row.get("first_favorable_t"),
        "first_adverse_t": row.get("first_adverse_t"),
        "MFE_bps": row.get("MFE_bps"),
        "MAE_bps": row.get("MAE_bps"),
        "MFE_t": row.get("MFE_t"),
        "structural_failure_first_observable_t": first_struct,
        "THESIS_LOST_AT": lost_t,
        "exit_t": row.get("exit_t"),
        "minutes_first_struct_to_THESIS_LOST": _delay(first_struct, lost_t),
        "minutes_MFE_to_THESIS_LOST": row.get("minutes_MFE_to_THESIS_LOST"),
        "gross_pnl_yen": row.get("gross_pnl_yen"),
        "net_pnl_yen": row.get("net_pnl_yen"),
        "asf_class": label,
        "am_path_n": len(am_path),
        "delay_used": delay,
    }


def recross_case(row: dict[str, Any], rec: dict[str, Any] | None) -> dict[str, Any]:
    sign = int(row.get("DIR") or -1)
    entry_t = str(row.get("entry_t") or "")[:5]
    lost_t = str(row.get("THESIS_LOST_AT") or "")[:5]
    first_left = None
    recross_ts: list[str] = []
    if rec is not None:
        session_idx = session_idx_of(list(rec["t"]))
        or15 = freeze_or15(list(rec["t"]), rec["h"], rec["l"], session_idx)
        oh, ol = or15.get("or_high"), or15.get("or_low")
        bars = build_five_m(rec, session_idx, through="11:19")
        left = False
        if _finite(oh) and _finite(ol):
            for b in bars:
                t1 = str(b.get("t1") or "")[:5]
                if not left and five_m_left(sign=sign, bar=b, or_high=float(oh), or_low=float(ol)):
                    left = True
                    first_left = t1
                    continue
                if left:
                    c = b.get("c")
                    if _finite(c) and float(ol) <= float(c) <= float(oh):
                        recross_ts.append(t1)
    first_r = recross_ts[0] if recross_ts else None
    second_r = recross_ts[1] if len(recross_ts) > 1 else None
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "side": row.get("side"),
        "entry_t": entry_t,
        "entry_px": row.get("entry_px"),
        "seed_family": row.get("seed_family"),
        "location_family": row.get("location_family"),
        "first_left_or_t": first_left,
        "first_recross_t": first_r,
        "second_recross_t": second_r,
        "THESIS_LOST_AT": lost_t,
        "minutes_first_recross_to_THESIS_LOST": _delay(first_r, lost_t),
        "minutes_second_recross_to_THESIS_LOST": _delay(second_r, lost_t),
        "MFE_bps": row.get("MFE_bps"),
        "MAE_bps": row.get("MAE_bps"),
        "MFE_t": row.get("MFE_t"),
        "minutes_MFE_to_THESIS_LOST": row.get("minutes_MFE_to_THESIS_LOST"),
        "gross_pnl_yen": row.get("gross_pnl_yen"),
        "net_pnl_yen": row.get("net_pnl_yen"),
        "could_existing_or_recross_rule_have_fired_at_second_close": bool(second_r and lost_t and str(second_r)[:5] < str(lost_t)[:5]),
        "new_price_threshold_not_created": True,
    }


def session_flat_case(row: dict[str, Any], rec: dict[str, Any] | None, occupied_block_n: int) -> dict[str, Any]:
    state_1120 = None
    pm_mfe = None
    if rec is not None:
        times = list(rec["t"])
        if "11:20" in times:
            i = times.index("11:20")
            state_1120 = {
                "t": "11:20",
                "o": rec["o"][i],
                "c": rec["c"][i],
                "dir_bps_vs_entry": dir_bps(side=str(row.get("side") or ""), entry=float(row.get("entry_px") or 0), px=float(rec["c"][i])),
            }
        # PM path already in MFE of full hold
        pm_mfe = row.get("MFE_bps")
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "entry_t": row.get("entry_t"),
        "entry_px": row.get("entry_px"),
        "MFE_bps": row.get("MFE_bps"),
        "MAE_bps": row.get("MAE_bps"),
        "MFE_t": row.get("MFE_t"),
        "state_1120": state_1120,
        "pm_included_in_MFE": pm_mfe,
        "exit_t": row.get("exit_t"),
        "gross_pnl_yen": row.get("gross_pnl_yen"),
        "net_pnl_yen": row.get("net_pnl_yen"),
        "occupancy_minutes": row.get("holding_min"),
        "blocked_signals_while_occupied": int(occupied_block_n),
        "dead_position_left_open_without_THESIS_LOST": True,
    }
