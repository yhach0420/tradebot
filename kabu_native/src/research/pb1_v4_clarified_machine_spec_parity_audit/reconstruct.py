"""Reconstruct completed 5m paths for the 88 charts. Discovery only. No future outcome."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol
from research.pb1_opening_range_continuation_face_valid_v1.or15 import freeze_or15, session_idx_of
from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v3_2_face_failure_rca.second_pass import HUMAN_LABELS
from research.pb1_v4_clarified_machine_implementation import REPEATED_NO_EXPANSION_N, UNWIND_FRAC
from research.pb1_v4_clarified_machine_implementation.active import classify_active_loss, opening_net
from research.pb1_v4_clarified_machine_implementation.location import five_m_left
from research.pb1_v4_clarified_machine_spec_parity_audit.isolation import FACE_RCA_CACHE, MACHINE_CACHE
from research.pb1_v4_machine_implementation.bars5 import build_five_m


def load_rca_rows() -> list[dict[str, Any]]:
    path = FACE_RCA_CACHE / "descriptor_slim.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_walked() -> dict[str, Any]:
    path = MACHINE_CACHE / "walked.json"
    if not path.is_file():
        return {"ok": False}
    return json.loads(path.read_text(encoding="utf-8"))


def funnel_idx(funnel: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for r in funnel:
        out[(str(r.get("symbol")), str(r.get("date")))] = r
    return out


def loc_time(location_id: Any) -> str | None:
    if not location_id:
        return None
    parts = str(location_id).split("|")
    for p in reversed(parts):
        if len(p) == 5 and p[2] == ":":
            return p
    return None


def join_88(*, walked: dict[str, Any]) -> list[dict[str, Any]]:
    rca = load_rca_rows()
    fidx = funnel_idx(list(walked.get("funnel_days") or []))
    e0idx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("e0_events") or [])}
    e1idx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("e1_events") or [])}
    sidx = {(str(r.get("symbol")), str(r.get("date"))): r for r in list(walked.get("setups") or [])}
    rows = []
    for r in rca:
        rid = int(r.get("rca_id") or 0)
        lab = dict(HUMAN_LABELS.get(rid) or {})
        key = (str(r.get("symbol")), str(r.get("date")))
        f = dict(fidx.get(key) or {})
        setup = sidx.get(key) or {}
        rows.append(
            {
                "rca_id": rid,
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "direction_v32": r.get("direction"),
                "human_pattern": lab.get("sp_pattern"),
                "human_opening_state": lab.get("sp_opening_state"),
                "human_location": lab.get("sp_location"),
                "human_retest": lab.get("sp_retest"),
                "human_trigger": lab.get("sp_trigger"),
                "human_late": bool(lab.get("sp_late")),
                "human_note": lab.get("sp_note"),
                "machine_WHY": bool(f.get("WHY_THIS_STOCK")),
                "machine_SEED": f.get("OPENING_DRIVE_SEED"),
                "machine_ACTIVE": bool(f.get("OPENING_DRIVE_ACTIVE")),
                "machine_LOCATION": bool(f.get("LOCATION_IDENTIFIED")),
                "machine_THESIS_READY": bool(f.get("THESIS_READY")),
                "machine_E0": bool(f.get("E0")),
                "machine_E1": bool(f.get("E1")),
                "machine_opening_state": f.get("opening_state"),
                "machine_DIR": f.get("DIR"),
                "machine_death": f.get("death"),
                "location_family": f.get("location_family") or setup.get("location_family"),
                "location_reason": f.get("location_reason") or setup.get("location_reason"),
                "interaction": f.get("interaction") or setup.get("interaction"),
                "candidate_day_id": f.get("candidate_day_id") or setup.get("candidate_day_id"),
                "opening_seed_id": f.get("opening_seed_id") or setup.get("opening_seed_id"),
                "opening_drive_id": f.get("opening_drive_id") or setup.get("opening_drive_id"),
                "location_id": f.get("location_id") or setup.get("location_id"),
                "thesis_id": f.get("thesis_id") or setup.get("thesis_id"),
                "execution_id": f.get("execution_id") or setup.get("execution_id"),
                "location_t": loc_time(f.get("location_id") or setup.get("location_id")),
                "hidden_1m_snapshot": setup.get("hidden_1m_snapshot") or f.get("hidden_1m_snapshot"),
                "e0_entry_t": (e0idx.get(key) or {}).get("entry_t"),
                "e1_entry_t": (e1idx.get(key) or {}).get("entry_t"),
                "or_high": f.get("or_high") or setup.get("or_high"),
                "or_low": f.get("or_low") or setup.get("or_low"),
            }
        )
    return rows


def _bar_slim(b: dict[str, Any]) -> dict[str, Any]:
    return {
        "t0": b.get("t0"),
        "t1": b.get("t1"),
        "o": b.get("o"),
        "h": b.get("h"),
        "l": b.get("l"),
        "c": b.get("c"),
        "range": b.get("range"),
        "body": b.get("body"),
        "body_over_range": b.get("body_over_range"),
        "direction": b.get("direction"),
        "net": b.get("net"),
    }


def replay_active(bars: list[dict[str, Any]], *, sign: int, or_high: Any, or_low: Any, until: str | None) -> dict[str, Any]:
    """Replay frozen ACTIVE invalidation. Diagnosis only. Does not change the machine."""
    if sign not in (1, -1) or len(bars) < 3:
        return {"ok": False, "reason": "no_sign_or_bars"}
    open_0900 = bars[0].get("o")
    peak = None
    drive_ext = None
    no_exp = 0
    left = False
    recross = 0
    both = False
    last_new_ext_t = None
    last_dir_close_t = None
    tiny_resets: list[dict[str, Any]] = []
    would_stale_without_tiny = False
    bars_no_meaningful = 0
    max_retrace = 0.0
    gross_since = 0.0
    tiny_streak = 0
    timeline = []
    death = None
    death_t = None
    loc_left_t = None
    for b in bars:
        t1 = str(b.get("t1") or "")[:5]
        if t1 < "09:14":
            continue
        if until and t1 > str(until)[:5]:
            break
        c, h, l = b.get("c"), b.get("h"), b.get("l")
        if not (_finite(c) and _finite(h) and _finite(l)):
            continue
        disp = opening_net(sign=sign, open_0900=open_0900, close_now=c)
        if disp is not None and (peak is None or float(disp) > float(peak)):
            peak = float(disp)
        if peak and disp is not None:
            retrace = float(peak) - float(disp)
            if retrace > max_retrace:
                max_retrace = retrace
        if _finite(b.get("range")):
            gross_since += float(b["range"]) if t1 > "09:14" else 0.0
        if _finite(or_high) and _finite(or_low) and five_m_left(sign=sign, bar=b, or_high=float(or_high), or_low=float(or_low)):
            left = True
            if loc_left_t is None:
                loc_left_t = t1
        if left and _finite(or_high) and _finite(or_low) and float(or_low) <= float(c) <= float(or_high):
            recross += 1
        if _finite(or_high) and _finite(or_low) and float(h) >= float(or_high) and float(l) <= float(or_low):
            both = True
        ext = float(h) if sign > 0 else float(l)
        rng = float(h) - float(l) if float(h) > float(l) else None
        tiny = False
        progressed = False
        if drive_ext is None:
            drive_ext = ext
            no_exp = 0
            last_new_ext_t = t1
        else:
            progressed = (sign > 0 and ext > float(drive_ext)) or (sign < 0 and ext < float(drive_ext))
            if progressed:
                delta = abs(ext - float(drive_ext))
                tiny = bool(rng and rng > 0 and delta <= 0.15 * rng) or (peak and peak > 0 and delta <= 0.05 * abs(float(peak)))
                if tiny:
                    tiny_resets.append({"t1": t1, "delta": delta, "bar_range": rng, "reset_stall": True})
                    if no_exp + 1 >= int(REPEATED_NO_EXPANSION_N):
                        would_stale_without_tiny = True
                drive_ext = ext
                no_exp = 0
                last_new_ext_t = t1
                if not tiny:
                    bars_no_meaningful = 0
                    tiny_streak = 0
                else:
                    bars_no_meaningful += 1
                    tiny_streak += 1
                    if tiny_streak >= int(REPEATED_NO_EXPANSION_N):
                        would_stale_without_tiny = True
            else:
                no_exp += 1
                bars_no_meaningful += 1
                tiny_streak = 0
        if int(b.get("direction") or 0) == int(sign):
            last_dir_close_t = t1
        loss = classify_active_loss(
            sign=sign,
            close=float(c),
            or_high=float(or_high) if _finite(or_high) else 0.0,
            or_low=float(or_low) if _finite(or_low) else 0.0,
            open_0900=open_0900,
            peak_disp=peak,
            wick_only_n=0,
            micro_break_n=0,
            recross_closes=recross,
            left=left,
            five_m_no_expansion_n=no_exp,
            both_or_extremes_revisited=both,
        )
        timeline.append(
            {
                "t1": t1,
                "progressed": progressed,
                "tiny_new_extreme": tiny,
                "no_expansion_n": no_exp,
                "disp": disp,
                "peak": peak,
                "left": left,
                "loss": loss.get("reason"),
            }
        )
        if loss.get("lost"):
            death = loss.get("reason")
            death_t = t1
            break
    last_c = None
    for b in reversed(bars):
        t1 = str(b.get("t1") or "")[:5]
        if until and t1 > str(until)[:5]:
            continue
        last_c = b.get("c")
        break
    seed_c = bars[2].get("c") if len(bars) >= 3 else None
    net_since = None
    if _finite(last_c) and _finite(seed_c):
        net_since = (float(last_c) - float(seed_c)) * float(sign)
    return {
        "ok": True,
        "sign": sign,
        "UNWIND_FRAC_applied": UNWIND_FRAC,
        "REPEATED_NO_EXPANSION_N_applied": REPEATED_NO_EXPANSION_N,
        "how_unwind_applied": "lost only if given>=0.75*peak AND disp_now<0.25*peak",
        "how_no_expansion_applied": "any new directional extreme, including a tiny one, resets the stall counter to 0",
        "last_meaningful_new_extreme_t": last_new_ext_t,
        "last_directional_close_progression_t": last_dir_close_t,
        "gross_path_since_seed": gross_since,
        "net_progress_since_seed": net_since,
        "max_retrace_from_directional_extreme": max_retrace,
        "completed_5m_without_meaningful_directional_progress": bars_no_meaningful,
        "or_or_local_recross_closes": recross,
        "two_sided_balance_reformed": both,
        "tiny_new_extreme_resets": tiny_resets,
        "tiny_reset_n": len(tiny_resets),
        "would_stale_without_tiny_resets": would_stale_without_tiny,
        "left_t": loc_left_t,
        "replay_death": death,
        "replay_death_t": death_t,
        "until": until,
        "clock_cutoff": False,
        "open_bars": [_bar_slim(b) for b in bars[:3]],
        "post_seed_n": sum(1 for b in bars if str(b.get("t1") or "") > "09:14"),
    }


def reconstruct_paths(*, bind: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    keys = [(str(r["symbol"]), str(r["date"])) for r in rows]
    dates = {d for _s, d in keys}
    if dates - disc:
        raise RuntimeError("non_discovery_date_in_88")
    by_sym: dict[str, set[str]] = defaultdict(set)
    for s, d in keys:
        by_sym[s].add(d)
    print(f"LOAD_MINUTES_88 symbols={len(by_sym)} dates={len(dates)}", flush=True)
    grouped: dict[tuple[str, str], Any] = {}
    for i, (sym, dset) in enumerate(sorted(by_sym.items()), start=1):
        minutes = load_minutes(symbols=[sym], allowed_dates=dset, forbidden_dates=conf | val)
        if minutes is None or minutes.empty:
            continue
        minutes["date"] = minutes["date"].astype(str)
        minutes["symbol"] = minutes["symbol"].astype(str)
        for date, g in minutes.groupby("date", sort=False):
            grouped[(sym, str(date))] = g
        if i % 10 == 0:
            print(f"LOAD_88 {i}/{len(by_sym)}", flush=True)
    out_rows = []
    for r in rows:
        symbol, date = str(r["symbol"]), str(r["date"])
        sg = grouped.get((symbol, date))
        path = {"ok": False, "reason": "minutes_missing"}
        if sg is not None and not sg.empty:
            rec = prep_symbol(sg)
            rec["session_idx"] = session_idx_of(rec["t"])
            or15 = freeze_or15(rec["t"], rec["h"], rec["l"], rec["session_idx"])
            bars = build_five_m(rec, rec["session_idx"], through="11:19")
            sign = int(r.get("machine_DIR") or 0)
            until = r.get("location_t")
            path = replay_active(
                bars,
                sign=sign if sign in (1, -1) else 0,
                or_high=or15.get("or_high") if or15.get("ok") else r.get("or_high"),
                or_low=or15.get("or_low") if or15.get("ok") else r.get("or_low"),
                until=until,
            )
            path["or_high"] = or15.get("or_high") if or15.get("ok") else None
            path["or_low"] = or15.get("or_low") if or15.get("ok") else None
            path["open_bars"] = [_bar_slim(b) for b in bars[:3]]
        out_rows.append({**r, "path": path})
    return {"ok": True, "rows": out_rows, "n": len(out_rows), "future_outcome_n": 0}


def earliest_human_known_t(human_open: str, *, late: bool) -> str:
    if human_open == "LATE_RANGE_RESOLUTION" or late:
        return "AFTER_0915"
    return "09:15"
