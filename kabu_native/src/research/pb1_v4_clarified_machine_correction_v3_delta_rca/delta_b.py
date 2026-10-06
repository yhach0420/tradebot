"""Delta B: 7011/20250523 leftover-range vs V3 STALE event. Read-only V3 classifiers."""
from __future__ import annotations

from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.machine import _finite
from research.pb1_v4_clarified_machine_correction_v3.active import (
    classify_active_loss,
    classify_progress,
    classify_stale_range_resolution,
    opening_net,
)
from research.pb1_v4_clarified_machine_correction_v3.machine import progressed_extreme
from research.pb1_v4_clarified_machine_correction_v3.seed import classify_seed
from research.pb1_v4_clarified_machine_correction_v3_delta_rca.load import pick

FOCUS = (("7011", "20250523"), ("4063", "20251118"), ("3382", "20241004"))


def _bars(row: dict[str, Any]) -> list[dict[str, Any]]:
    snap = dict(row.get("snap") or {})
    bars = list(snap.get("bars") or [])
    return [b for b in bars if str(b.get("t1") or "") <= "11:19"]


def _overlap(a: dict[str, Any] | None, b: dict[str, Any] | None) -> float | None:
    if not a or not b:
        return None
    if not all(_finite(x.get(k)) for x in (a, b) for k in ("h", "l")):
        return None
    lo = max(float(a["l"]), float(b["l"]))
    hi = min(float(a["h"]), float(b["h"]))
    rng = float(b["h"]) - float(b["l"])
    if rng <= 0:
        return None
    return max(0.0, hi - lo) / rng


def _close_loc_in_range(bar: dict[str, Any], lo: Any, hi: Any) -> float | None:
    if not (_finite(bar.get("c")) and _finite(lo) and _finite(hi) and float(hi) > float(lo)):
        return None
    return (float(bar["c"]) - float(lo)) / (float(hi) - float(lo))


def _candidate_events(
    *,
    sign: int,
    bar: dict[str, Any],
    prev: dict[str, Any] | None,
    prog: dict[str, Any],
    stale: dict[str, Any],
    loss: dict[str, Any],
    overlap_prev: float | None,
    overlap_active: float | None,
    close_loc_active: float | None,
    disp_now: float | None,
    peak_disp: float | None,
    no_exp_n: int,
    resets: bool,
) -> list[str]:
    out: list[str] = []
    flags = list(loss.get("flags") or [])
    out.extend(flags)
    if stale.get("stale"):
        if "STALE_RANGE_RESOLUTION" not in out:
            out.append("STALE_RANGE_RESOLUTION")
    cls = str(prog.get("class") or "")
    heavy = overlap_prev is not None and float(overlap_prev) >= 0.70
    inside_active = close_loc_active is not None and 0.25 <= float(close_loc_active) <= 0.75
    if heavy and cls in ("NO_DIRECTIONAL_PROGRESS", "RANGE_DRIFT_EXTREME", "MARGINAL_EXTREME_ONLY"):
        out.append("RANGE_ACCEPTED_CANDIDATE")
    if overlap_active is not None and float(overlap_active) >= 0.70 and not resets:
        out.append("LEFTOVER_OVERLAP_ACCEPTED_CANDIDATE")
    if inside_active and cls == "NO_DIRECTIONAL_PROGRESS" and no_exp_n >= 2:
        out.append("BALANCE_REESTABLISHED_CANDIDATE")
    if _finite(peak_disp) and float(peak_disp) > 0 and disp_now is not None:
        given = float(peak_disp) - float(disp_now)
        if given >= 0.50 * float(peak_disp) and float(disp_now) < 0.50 * float(peak_disp):
            out.append("DISPLACEMENT_MATERIALLY_UNWOUND_CANDIDATE")
    if cls in ("MARGINAL_EXTREME_ONLY", "RANGE_DRIFT_EXTREME") and not resets:
        out.append("FAILED_BREAK_OR_GEOMETRIC_PROBE_CANDIDATE")
    if int(bar.get("direction") or 0) == -int(sign) and not stale.get("stale"):
        out.append("OPPOSITE_BAR_NOT_STALE_UNDER_V3_RULE")
    return list(dict.fromkeys(out))


def replay_path(row: dict[str, Any]) -> dict[str, Any]:
    bars = _bars(row)
    sign = int(row.get("machine_DIR") or 0)
    if sign not in (1, -1):
        sign = 1
    or_high = row.get("or_high")
    or_low = row.get("or_low")
    open_bars = [b for b in bars if str(b.get("t1") or "") <= "09:14"]
    later = [b for b in bars if str(b.get("t1") or "") > "09:14"]
    snap = dict(row.get("snap") or {})
    clock = dict(snap.get("clock_snap") or {})
    atr = snap.get("atr20")
    origin = open_bars[0].get("o") if open_bars else None
    if clock and atr is not None:
        seed = classify_seed(open_bars[:3], clock_snap=clock, atr20=atr)
        fail = dict(seed.get("failed_open") or {})
        if _finite(fail.get("failed_extreme")):
            origin = fail.get("failed_extreme")
        if int(seed.get("DIR") or 0) in (1, -1) and sign not in (1, -1):
            sign = int(seed["DIR"])
    last_open = open_bars[-1] if open_bars else None
    prev_ext = None
    prev_bar = last_open
    if last_open:
        prev_ext = last_open.get("h") if sign > 0 else last_open.get("l")
    peak = None
    no_exp = 0
    had_renewed = False
    live = True
    death_at = None
    death_reason = None
    active_hi = max((float(b["h"]) for b in open_bars if _finite(b.get("h"))), default=None)
    active_lo = min((float(b["l"]) for b in open_bars if _finite(b.get("l"))), default=None)
    rows = []
    for bar in later:
        t1 = str(bar.get("t1") or "")[:5]
        prog = classify_progress(sign=sign, bar=bar, prev_bar=prev_bar, prev_ext=prev_ext)
        stale = classify_stale_range_resolution(sign=sign, bar=bar, prev_bar=prev_bar)
        close = bar.get("c")
        disp_now = opening_net(sign=sign, open_0900=origin, close_now=close)
        if disp_now is not None and (peak is None or float(disp_now) > float(peak)):
            peak = float(disp_now)
        overlap_prev = _overlap(prev_bar, bar)
        overlap_active = None
        if _finite(active_hi) and _finite(active_lo) and _finite(bar.get("h")) and _finite(bar.get("l")):
            dummy_prev = {"h": active_hi, "l": active_lo}
            overlap_active = _overlap(dummy_prev, bar)
        close_loc_active = _close_loc_in_range(bar, active_lo, active_hi)
        close_beyond_prior_active = None
        if _finite(close) and sign > 0 and _finite(active_hi):
            close_beyond_prior_active = float(close) > float(active_hi)
        elif _finite(close) and sign < 0 and _finite(active_lo):
            close_beyond_prior_active = float(close) < float(active_lo)
        rng = bar.get("range")
        body = bar.get("body_over_range")
        ext = bar.get("h") if sign > 0 else bar.get("l")
        delta = None
        if _finite(ext) and _finite(prev_ext):
            delta = abs(float(ext) - float(prev_ext))
        resets = bool(prog.get("resets_stall"))
        stall_n = 0 if resets else no_exp + 1
        had_renewed_now = True if resets else had_renewed
        loss = classify_active_loss(
            sign=sign,
            close=float(close) if _finite(close) else 0.0,
            or_high=float(or_high) if _finite(or_high) else 0.0,
            or_low=float(or_low) if _finite(or_low) else 0.0,
            open_0900=origin,
            peak_disp=peak,
            wick_only_n=0,
            micro_break_n=0,
            recross_closes=0,
            left=True,
            five_m_no_expansion_n=stall_n,
            both_or_extremes_revisited=False,
            location_identified=bool(row.get("machine_LOCATION")),
            had_renewed_auction=had_renewed_now,
            bar=bar,
            prev_bar=prev_bar,
        )
        events = _candidate_events(
            sign=sign,
            bar=bar,
            prev=prev_bar,
            prog=prog,
            stale=stale,
            loss=loss,
            overlap_prev=overlap_prev,
            overlap_active=overlap_active,
            close_loc_active=close_loc_active,
            disp_now=disp_now,
            peak_disp=peak,
            no_exp_n=stall_n,
            resets=resets,
        )
        v3_kills = bool(loss.get("lost"))
        if live and v3_kills and death_at is None:
            death_at = t1
            death_reason = loss.get("reason")
            live = False
        geometric = str(prog.get("class") or "") in ("MARGINAL_EXTREME_ONLY", "RANGE_DRIFT_EXTREME") or (
            str(prog.get("class") or "") == "REAL_BREAKOUT_EXTENSION"
            and close_beyond_prior_active is False
        )
        real_renewed = (
            str(prog.get("class") or "") == "REAL_BREAKOUT_EXTENSION"
            and bool(prog.get("renewed_directional_auction"))
            and not geometric
        )
        rows.append(
            {
                "time": t1,
                "o": bar.get("o"),
                "h": bar.get("h"),
                "l": bar.get("l"),
                "c": bar.get("c"),
                "direction": int(bar.get("direction") or 0),
                "range": rng,
                "body_over_range": body,
                "current_extreme": ext,
                "prev_extreme": prev_ext,
                "distance_from_active_extreme": delta,
                "overlap_prev_bar": overlap_prev,
                "overlap_recent_active_range": overlap_active,
                "close_loc_recent_range": close_loc_active,
                "close_beyond_prior_active": close_beyond_prior_active,
                "disp_now": disp_now,
                "peak_disp": peak,
                "disp_given_back": (float(peak) - float(disp_now)) if _finite(peak) and disp_now is not None else None,
                "progress_class": prog.get("class"),
                "resets_stall": resets,
                "renewed_auction_flag": bool(prog.get("renewed_directional_auction")),
                "tiny_extreme": prog.get("tiny_extreme"),
                "close_prog": prog.get("close_prog"),
                "range_vs_prior": prog.get("range_vs_prior"),
                "v3_stale": bool(stale.get("stale")),
                "v3_loss_reason": loss.get("reason"),
                "v3_loss_flags": loss.get("flags"),
                "v3_ACTIVE_LIVE_after": live if death_at != t1 else False,
                "candidate_death_events": events,
                "GEOMETRIC_EXTENSION": geometric,
                "REAL_RENEWED_AUCTION": real_renewed,
            }
        )
        if resets:
            no_exp = 0
            had_renewed = True
            prev_ext = float(ext) if _finite(ext) else prev_ext
            prev_bar = dict(bar)
            if _finite(bar.get("h")) and _finite(bar.get("l")):
                if sign > 0:
                    active_hi = float(bar["h"])
                else:
                    active_lo = float(bar["l"])
        else:
            no_exp = stall_n
            cls = str(prog.get("class") or "")
            if cls in ("RANGE_DRIFT_EXTREME", "MARGINAL_EXTREME_ONLY") and progressed_extreme(sign=sign, ext=ext, prev=prev_ext):
                prev_ext = float(ext) if _finite(ext) else prev_ext
                prev_bar = dict(bar)
    funnel_lost_at = row.get("machine_THESIS_LOST_AT")
    funnel_reason = row.get("machine_THESIS_LOST_REASON")
    return {
        "symbol": row.get("symbol"),
        "date": row.get("date"),
        "sign": sign,
        "SEED": row.get("machine_SEED"),
        "opening_state": row.get("machine_opening_state"),
        "funnel_ACTIVE_LIVE": row.get("machine_ACTIVE_LIVE"),
        "funnel_THESIS_LIVE": row.get("machine_THESIS_LIVE"),
        "funnel_THESIS_REACHED": row.get("machine_THESIS_REACHED"),
        "funnel_lost_at": funnel_lost_at,
        "funnel_lost_reason": funnel_reason,
        "replay_first_v3_death_at": death_at,
        "replay_first_v3_death_reason": death_reason,
        "progress_log_funnel": row.get("progress_log"),
        "path": rows,
    }


def _bar_at(path: dict[str, Any], t: str) -> dict[str, Any]:
    for r in list(path.get("path") or []):
        if str(r.get("time")) == t:
            return r
    return {}


def classify_7011_renewals(p7011: dict[str, Any]) -> dict[str, Any]:
    """10:09/10:19 vs leftover range accepted after the 09:29 failed probe.

    V3 REAL_BREAKOUT_EXTENSION is local vs the previous 5m only. After leftover
    RANGE_DRIFT ratchets drive_ext, a larger crawl-break looks like renewal —
    the ACTIVE analogue of GEOMETRIC_EXTENSION != REAL_AUCTION.
    """
    b0929 = _bar_at(p7011, "09:29")
    b1004 = _bar_at(p7011, "10:04")
    b1009 = _bar_at(p7011, "10:09")
    b1019 = _bar_at(p7011, "10:19")
    leftover_high = b0929.get("h")
    crawl_high = b1004.get("h")
    out = {}
    for lab, b, vs_prior_note in (
        ("10:09", b1009, "vs 10:04 range 11 leftover crawl"),
        ("10:19", b1019, "vs 10:14 contracted 19 after 10:09 leftover break"),
    ):
        cls = str(b.get("progress_class") or "")
        rng = b.get("range")
        # 09:19 drive bar range was 35. 10:09 range 23 is leftover-box expansion, not a new opening auction.
        beyond_failed_probe = _finite(b.get("c")) and _finite(leftover_high) and float(b["c"]) > float(leftover_high)
        vs_crawl = _finite(b.get("c")) and _finite(crawl_high) and float(b["c"]) > float(crawl_high)
        geometric = True
        real = False
        reason = (
            f"V3 class {cls}. Local vs previous 5m: overlap={b.get('overlap_prev_bar')} "
            f"range_vs_prior={b.get('range_vs_prior')} close_prog={b.get('close_prog')} body={b.get('body_over_range')}. "
            f"09:29 failed probe high={leftover_high} close=3108 body=0.04. 09:34-10:04 accepted leftover "
            f"3085-3147 (RANGE_DRIFT/MARGINAL nicks). {lab} close={b.get('c')} range={rng} {vs_prior_note}. "
            "Close does print beyond the leftover box, but that is leftover-range geometric break / failed-probe "
            "resolution, not a renewed opening-drive auction. Same collapse as ONE_BAR: new extreme != renewed auction."
        )
        out[lab] = {
            "v3_class": cls,
            "GEOMETRIC_EXTENSION": geometric,
            "REAL_RENEWED_AUCTION": real,
            "close_beyond_failed_09_29_probe": beyond_failed_probe,
            "close_beyond_10_04_crawl_high": vs_crawl,
            "overlap_prev_bar": b.get("overlap_prev_bar"),
            "close_prog": b.get("close_prog"),
            "range_vs_prior": b.get("range_vs_prior"),
            "tiny_extreme": b.get("tiny_extreme"),
            "body_over_range": b.get("body_over_range"),
            "range": rng,
            "o": b.get("o"),
            "h": b.get("h"),
            "l": b.get("l"),
            "c": b.get("c"),
            "direction": b.get("direction"),
            "reason": reason,
        }
    return out


def auction_end_7011(p7011: dict[str, Any]) -> dict[str, Any]:
    b0919 = _bar_at(p7011, "09:19")
    b0924 = _bar_at(p7011, "09:24")
    b0929 = _bar_at(p7011, "09:29")
    b0934 = _bar_at(p7011, "09:34")
    b0944 = _bar_at(p7011, "09:44")
    return {
        "real_opposite_drive": "09:19 REAL_BREAKOUT_EXTENSION O/H/L/C 3074/3096/3061/3092 range 35 body 0.51",
        "geometric_probes": "09:24 MARGINAL high 3119 contracted range 28; 09:29 MARGINAL high 3130 body 0.04 close 3108",
        "failed_break_reaccepted": "09:34 opposite 3112-3085 close 3101 body 0.26 (below STALE 0.50); 09:39-09:44 overlap 3100-3125 under 3130",
        "observable_event": "FAILED_BREAK_REACCEPTED / RANGE_ACCEPTED after 09:29 probe — leftover two-sided box, not N bars",
        "confirmed_by": "09:44",
        "not_at": "10:09 REAL_BREAKOUT vs prior crawl bar",
        "v3_misses_because": "STALE requires committed opposite body>=0.50 and range>=prev; leftover same-direction overlap never qualifies",
        "bars": {
            "09:19": {k: b0919.get(k) for k in ("o", "h", "l", "c", "range", "body_over_range", "progress_class")},
            "09:24": {k: b0924.get(k) for k in ("o", "h", "l", "c", "range", "body_over_range", "progress_class")},
            "09:29": {k: b0929.get(k) for k in ("o", "h", "l", "c", "range", "body_over_range", "progress_class")},
            "09:34": {k: b0934.get(k) for k in ("o", "h", "l", "c", "range", "body_over_range", "progress_class", "direction")},
            "09:44": {k: b0944.get(k) for k in ("o", "h", "l", "c", "range", "body_over_range", "progress_class", "overlap_prev_bar")},
        },
    }


def first_semantic_end(path: dict[str, Any]) -> dict[str, Any]:
    """Do not use the first overlap bar. Look for failed probe then leftover overlap."""
    probe = None
    for r in list(path.get("path") or []):
        cls = str(r.get("progress_class") or "")
        if cls in ("MARGINAL_EXTREME_ONLY", "RANGE_DRIFT_EXTREME") and r.get("body_over_range") is not None:
            if float(r.get("body_over_range") or 0) < 0.35 or (r.get("close_prog") is not None and float(r.get("close_prog")) <= 0):
                probe = r
        if probe and cls == "NO_DIRECTIONAL_PROGRESS":
            ov = r.get("overlap_prev_bar")
            if ov is not None and float(ov) >= 0.70:
                return {
                    "time": r.get("time"),
                    "events": ["FAILED_BREAK_REACCEPTED", "RANGE_ACCEPTED"],
                    "probe_time": probe.get("time"),
                    "probe_class": probe.get("progress_class"),
                    "v3_kills": bool(r.get("v3_loss_reason")),
                    "progress_class": cls,
                    "note": "Failed probe then overlapping leftover. Not first NO_PROGRESS and not N-count.",
                }
        if r.get("v3_stale"):
            return {
                "time": r.get("time"),
                "events": ["STALE_RANGE_RESOLUTION"],
                "v3_kills": True,
                "progress_class": cls,
            }
    return {"time": None, "events": [], "v3_kills": False}


def natural_controls(rows: list[dict[str, Any]]) -> dict[str, Any]:
    stale = []
    pause_then_real = []
    for r in rows:
        if not r.get("machine_THESIS_REACHED"):
            continue
        reason = str(r.get("machine_THESIS_LOST_REASON") or "")
        log = list(r.get("progress_log") or [])
        if reason == "STALE_RANGE_RESOLUTION":
            stale.append(
                {
                    "symbol": r.get("symbol"),
                    "date": r.get("date"),
                    "THESIS_LOST_AT": r.get("machine_THESIS_LOST_AT"),
                    "e1_entry_t": r.get("e1_entry_t"),
                    "SEED": r.get("machine_SEED"),
                    "last_progress_class": r.get("last_progress_class"),
                }
            )
        classes = [str(x.get("class") or "") for x in log]
        nprog = 0
        leftover_n = 0
        saw_pause_then_real = False
        saw_leftover_then_real = False
        for c in classes:
            if c == "NO_DIRECTIONAL_PROGRESS":
                nprog += 1
                leftover_n += 1
            elif c in ("MARGINAL_EXTREME_ONLY", "RANGE_DRIFT_EXTREME"):
                leftover_n += 1
            elif c == "REAL_BREAKOUT_EXTENSION" and nprog >= 3:
                saw_pause_then_real = True
                leftover_n = 0
                nprog = 0
            elif c == "REAL_BREAKOUT_EXTENSION" and leftover_n >= 3:
                saw_leftover_then_real = True
                leftover_n = 0
                nprog = 0
            elif c in ("REAL_BREAKOUT_EXTENSION", "MEANINGFUL_DIRECTIONAL_EXTENSION"):
                nprog = 0
                leftover_n = 0
            else:
                leftover_n = 0
                nprog = 0
        if saw_pause_then_real or saw_leftover_then_real:
            pause_then_real.append(
                {
                    "symbol": r.get("symbol"),
                    "date": r.get("date"),
                    "THESIS_LIVE": r.get("machine_THESIS_LIVE"),
                    "THESIS_LOST_AT": r.get("machine_THESIS_LOST_AT"),
                    "THESIS_LOST_REASON": r.get("machine_THESIS_LOST_REASON"),
                    "e1_entry_t": r.get("e1_entry_t"),
                    "pause_then_real": saw_pause_then_real,
                    "leftover_then_real": saw_leftover_then_real,
                    "progress_classes": classes,
                }
            )
    return {
        "stale_after_thesis_reached_n": len(stale),
        "stale_after_thesis_reached": stale,
        "long_no_progress_then_real_breakout_n": len(pause_then_real),
        "long_no_progress_then_real_breakout": pause_then_real,
    }


def delta_b(rows: list[dict[str, Any]]) -> dict[str, Any]:
    paths = {f"{s}_{d}": replay_path(pick(rows, s, d)) for s, d in FOCUS}
    p7011 = paths["7011_20250523"]
    p4063 = paths["4063_20251118"]
    p3382 = paths["3382_20241004"]
    renew = classify_7011_renewals(p7011)
    end7011 = auction_end_7011(p7011)
    end4063_v3 = {
        "funnel_lost_at": p4063.get("funnel_lost_at"),
        "funnel_reason": p4063.get("funnel_lost_reason"),
        "replay_v3_death_at": p4063.get("replay_first_v3_death_at"),
        "replay_v3_death_reason": p4063.get("replay_first_v3_death_reason"),
        "failed_probe_then_overlap": first_semantic_end(p4063),
    }
    nat = natural_controls(rows)
    early_3382 = None
    leftover_before_0950 = []
    for r in list(p3382.get("path") or []):
        t = str(r.get("time") or "")
        if t < "09:50" and r.get("v3_loss_reason"):
            if early_3382 is None:
                early_3382 = {"time": t, "v3_loss_reason": r.get("v3_loss_reason"), "progress_class": r.get("progress_class"), "source": "simplified_replay_not_full_walk"}
        if t < "09:50":
            ev = list(r.get("candidate_death_events") or [])
            if any(
                x in ev
                for x in (
                    "RANGE_ACCEPTED_CANDIDATE",
                    "LEFTOVER_OVERLAP_ACCEPTED_CANDIDATE",
                    "BALANCE_REESTABLISHED_CANDIDATE",
                )
            ):
                leftover_before_0950.append({"time": t, "events": ev, "progress_class": r.get("progress_class")})
    naive_leftover_would_kill_3382 = bool(leftover_before_0950)
    same_mechanism = (
        "Three V2-named observables, one family AUCTION_ENDED_AS_RANGE: "
        "(1) FAILED_BREAK_REACCEPTED / leftover two-sided box after a failed probe (7011 09:29 then 09:34-09:44). "
        "(2) Pause bounce that contracts, then same-direction REAL continuation (3382 09:34 range 3.5<prev 5.0, then 09:44/09:54/09:59 REAL, E1 09:50). "
        "(3) Committed opposite non-contracting STALE (4063 10:19). "
        "V3 encodes only (3). Sequence shape 'leftover bars then REAL_BREAKOUT' is shared by 3382 and 7011, so N-count/pause-length cannot separate them. "
        "The separator is failed-probe close rejection resolved into overlap vs pause then continued dump. "
        f"Naive overlap-as-death would fire on 3382 before 09:50={naive_leftover_would_kill_3382}; that encoding is forbidden."
    )
    return {
        "7011_20250523": p7011,
        "4063_20251118": p4063,
        "3382_20241004": p3382,
        "renewal_event_audit": renew,
        "7011_first_semantic_end": end7011,
        "4063_first_semantic_end": end4063_v3,
        "3382_v3_death_before_0950": None if not (p3382.get("funnel_lost_at") and str(p3382.get("funnel_lost_at")) < "09:50") else p3382.get("funnel_lost_at"),
        "3382_funnel_lost_at": p3382.get("funnel_lost_at"),
        "3382_simplified_replay_loss_before_0950": early_3382,
        "3382_simplified_replay_is_not_full_walk": True,
        "3382_leftover_acceptance_candidates_before_0950": leftover_before_0950,
        "3382_naive_overlap_would_kill_before_E1": naive_leftover_would_kill_3382,
        "3382_funnel_e1": pick(rows, "3382", "20241004").get("e1_entry_t"),
        "natural_controls": nat,
        "GEOMETRIC_EXTENSION_ne_RENEWED_DIRECTIONAL_AUCTION": True,
        "N_bar_rule_forbidden": True,
        "same_semantic_mechanism_exists": True,
        "same_semantic_mechanism": same_mechanism,
        "v3_stale_encodes_committed_opposite_not_leftover_acceptance": True,
        "Q4_auction_end": end7011,
        "Q5_event_not_bar_count": (
            "FAILED_BREAK_REACCEPTED / RANGE_ACCEPTED after the 09:29 doji probe — leftover two-sided box under 3130. Not N bars."
        ),
        "Q6_1009_1019": renew,
        "Q7_same_mechanism": True,
        "Q8_without_new_numeric": True,
    }
