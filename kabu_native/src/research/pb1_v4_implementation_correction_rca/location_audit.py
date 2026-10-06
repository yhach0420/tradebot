"""3382 location, FAILED_OPEN timelines, CLEAR19 deaths. No rule change."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_v4_implementation_correction_rca import FAILED_OPEN_FOCUS
from research.pb1_v4_opening_drive_location_reaccel_spec import VALID_OPENING_STATES


def _pick(rows: list[dict[str, Any]], symbol: str, date: str) -> dict[str, Any]:
    for r in rows:
        if str(r.get("symbol")) == symbol and str(r.get("date")) == date:
            return r
    return {}


def _tl(timelines: dict[str, Any], symbol: str, date: str) -> dict[str, Any]:
    return dict(timelines.get(f"{symbol}|{date}") or {})


def failed_open_audit(rows: list[dict[str, Any]], timelines: dict[str, Any]) -> dict[str, Any]:
    human = [r for r in rows if r.get("human_opening_state") == "FAILED_OPEN_THEN_REAL_DRIVE"]
    out = []
    for r in human:
        tl = _tl(timelines, str(r.get("symbol")), str(r.get("date")))
        bars = list(tl.get("five_m") or [])
        first_opp = None
        real_drive_bar = None
        leave_bar = None
        sign = int(tl.get("sign") or 0)
        saw_counter = False
        for i, b in enumerate(bars):
            d = int(b.get("direction") or 0)
            if d == -int(sign) and d != 0:
                saw_counter = True
                if first_opp is None:
                    first_opp = {"i": i, "t1": b.get("t1")}
            if i >= 3 and b.get("fully_beyond_or") and leave_bar is None:
                leave_bar = {"i": i, "t1": b.get("t1")}
            if i >= 3 and saw_counter and d == int(sign) and real_drive_bar is None and (b.get("body_over_range") or 0) >= 0.35:
                real_drive_bar = {"i": i, "t1": b.get("t1")}
        if real_drive_bar is None:
            real_drive_bar = leave_bar
        extra_after_0914 = None
        if real_drive_bar is not None:
            extra_after_0914 = max(0, int(real_drive_bar["i"]) - 2)
        out.append(
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "human_note": r.get("human_note"),
                "machine_opening_state": r.get("machine_opening_state"),
                "machine_S1": r.get("machine_S1"),
                "machine_death": r.get("machine_death"),
                "first_counter_5m": first_opp,
                "first_same_dir_after": real_drive_bar,
                "extra_5m_bars_after_0914_until_same_dir": extra_after_0914 if real_drive_bar else None,
                "within_fail_extend_6": extra_after_0914 <= 6 if real_drive_bar else None,
                "opening_horizon": "still_opening_auction" if extra_after_0914 is not None and extra_after_0914 <= 3 else ("late_if_after_0930" if extra_after_0914 and extra_after_0914 > 3 else None),
                "timeline": bars[:12],
            }
        )
    focus = {}
    for sym, date in FAILED_OPEN_FOCUS:
        rec = next((x for x in out if x.get("symbol") == sym and x.get("date") == date), None)
        focus[f"{sym}_{date}"] = rec or {"symbol": sym, "date": date, "missing": True}
    return {
        "human_failed_open_n": len(human),
        "rows": out,
        "focus": focus,
        "FAIL_EXTEND_MAX_BARS_is": "arbitrary implementation cutoff, not semantic necessity",
        "spec_unsupported": True,
    }


def interpret_7011_20250523(fo: dict[str, Any]) -> dict[str, Any]:
    rec = dict((fo.get("focus") or {}).get("7011_20250523") or {})
    extra = rec.get("extra_5m_bars_after_0914_until_same_dir")
    t1 = ((rec.get("first_same_dir_after") or {}) or {}).get("t1")
    bars = list(rec.get("timeline") or [])
    first = bars[0] if bars else {}
    first_body = first.get("body_over_range")
    leave = next((b for b in bars if b.get("fully_beyond_or")), None)
    leave_t1 = (leave or {}).get("t1")
    # FAIL_EXTEND is not the killer: machine never locked FAILED_OPEN (wide doji first bar).
    # The later leave (if any) still sits in the opening auction.
    if str(rec.get("machine_opening_state") or "") == "LATE_RANGE_RESOLUTION" and _finite_body(first_body) and float(first_body) < 0.20:
        why = (
            "FAILED_OPEN seed never formed: first 5m is a wide doji/range wick, not a large counter-body. "
            "The later directional leave remains inside the opening auction, so FAIL_EXTEND_MAX_BARS=6 is not what killed the chart. "
            "Do not extend the cutoff. Spec vs human disagreement is whether a two-sided opening range counts as a failed-open seed."
        )
        kind = "opening_auction_still;_seed_encoding_not_FAIL_EXTEND"
    elif extra is None:
        why = "Opposite 5m auction never locks under current FAILED_OPEN scale; LATE_RANGE_RESOLUTION is the absorbing reject after extend timeout."
        kind = "implementation_cutoff_and_or_not_a_visible_failed_open_on_opening_three"
    elif int(extra) <= 3:
        why = "Real opposite drive still sits in the opening auction; FAIL_EXTEND_MAX_BARS is the wrong kind of gate if the seed was present."
        kind = "implementation_cutoff_wrong_if_seed_valid"
    else:
        why = "Opposite drive, if any, is late enough that it may no longer be an opening setup. Human FAILED_OPEN may need reinterpretation vs LATE_RANGE_RESOLUTION."
        kind = "human_horizon_may_need_reinterpretation"
    return {
        "machine_opening_state": rec.get("machine_opening_state"),
        "extra_5m_after_0914": extra,
        "first_same_dir_t1": t1,
        "first_bar_body_over_range": first_body,
        "first_completed_5m_leave_t1": leave_t1,
        "interpretation": kind,
        "why": why,
        "do_not_extend_FAIL_EXTEND_MAX_BARS_in_this_rca": True,
        "fail_extend_is_the_killer": False,
    }


def _finite_body(x) -> bool:
    try:
        return x is not None and float(x) == float(x)
    except (TypeError, ValueError):
        return False


def decompose_4063(rows: list[dict[str, Any]], timelines: dict[str, Any]) -> dict[str, Any]:
    r = _pick(rows, "4063", "20251118")
    tl = _tl(timelines, "4063", "20251118")
    return {
        "opening_state_mismatch": {
            "human": r.get("human_opening_state"),
            "machine": r.get("machine_opening_state"),
            "note": "Human FAILED_OPEN; machine TRUE_OPENING_DRIVE on opening-three numeric clauses.",
        },
        "location_timeframe_mismatch": {
            "human_location": r.get("human_location"),
            "human_location_kind": r.get("human_location_kind"),
            "machine_death": r.get("machine_death"),
            "any_completed_5m_leave": tl.get("any_completed_5m_leave"),
            "note": "Death is NO_COMPLETED_5M_LEAVE — a completed-5m leave gate, not a 1R/room kill.",
        },
        "execution_mismatch": {
            "human_trigger": r.get("human_trigger"),
            "machine_E1": r.get("machine_E1"),
            "note": "Human weak 1m cue. Machine never reached S4 so E1 correctly cannot create the setup.",
        },
    }


def location_3382(rows: list[dict[str, Any]], timelines: dict[str, Any]) -> dict[str, Any]:
    r = _pick(rows, "3382", "20241004")
    tl = _tl(timelines, "3382", "20241004")
    five = list(tl.get("five_m") or [])
    hold_bars = [b for b in five if b.get("completed_5m_hold_or")]
    leave_bars = [b for b in five if b.get("fully_beyond_or")]
    hold_after_leave = [b for b in five if b.get("left_so_far") and b.get("completed_5m_hold_or")]
    cause = str(r.get("machine_death") or "OR_TOUCH_ONLY")
    if leave_bars and hold_after_leave and cause == "OR_TOUCH_ONLY":
        detail = (
            "Completed 5m leave exists (first fully-beyond 09:49) and a later completed 5m OR hold exists (10:04). "
            "Machine still dies OR_TOUCH_ONLY because location is committed on the first 5m close after the 1m retest "
            "observation, which can be the leave bar itself (does not test/hold OR). The later hold never runs because the state is already dead. "
            "Frozen exemplar treated the 5m OR_LOW hold as valid location; 1m only times it."
        )
        vis = "completed_5m_hold_exists_but_commit_bar_is_not_the_hold"
    elif not leave_bars:
        detail = "No completed 5m bar is fully beyond OR_LOW. Leave/hold that the human saw is not a fully-beyond completed 5m candle."
        vis = "hold_may_be_1m_or_intrabar_5m"
    elif not hold_after_leave:
        detail = "At least one completed 5m leave exists, but no later completed 5m tests-and-holds OR from the continuation side under loc>=0.50."
        vis = "completed_5m_hold_absent_or_stricter_than_spec"
    else:
        detail = "Completed 5m leave and hold exist; death OR_TOUCH_ONLY then comes from classifier extra purity/context, not missing 5m evidence."
        vis = "classifier_stricter_than_visible_5m"
    return {
        "s1": r.get("machine_opening_state"),
        "s1_ok": r.get("machine_S1"),
        "death": r.get("machine_death"),
        "location_family": r.get("location_family"),
        "any_completed_5m_leave": bool(leave_bars),
        "any_completed_5m_or_hold": bool(hold_bars),
        "hold_after_leave": bool(hold_after_leave),
        "leave_t1": [b.get("t1") for b in leave_bars[:6]],
        "hold_t1": [b.get("t1") for b in hold_bars[:6]],
        "cause": cause,
        "detail": detail,
        "visibility": vis,
        "new_classifier_requires_more_than_frozen_spec": vis in (
            "hold_may_be_1m_or_intrabar_5m",
            "completed_5m_hold_absent_or_stricter_than_spec",
            "completed_5m_hold_exists_but_commit_bar_is_not_the_hold",
            "classifier_stricter_than_visible_5m",
        ),
        "human_label_may_use_1m_execution_detail": vis == "hold_may_be_1m_or_intrabar_5m",
        "do_not_special_case": True,
        "timeline": five[:14],
    }


def _clear_miss_class(r: dict[str, Any], tl: dict[str, Any]) -> str:
    if not r.get("machine_S1"):
        if str(r.get("machine_opening_state") or "") in VALID_OPENING_STATES:
            return "S1_REPRESENTATION"
        if str(r.get("human_opening_state") or "") in VALID_OPENING_STATES:
            return "S1_REPRESENTATION"
        return "ORIGINAL_HUMAN_STACK_WAS_NOT_ACTUALLY_5M_VALID"
    death = str(r.get("machine_death") or "")
    if death == "NO_COMPLETED_5M_LEAVE" or (not tl.get("any_completed_5m_leave") and not r.get("machine_S2")):
        return "COMPLETED_5M_LEAVE"
    if death in ("OR_TOUCH_ONLY", "NO_DEFENSIBLE_LOCATION", "OR_RETEST_NOT_PURE_AND_NO_A_OR_C", "NO_COMPLETED_5M_HOLD"):
        return "LOCATION_SEMANTICS"
    if death in ("failed_retest", "RETEST_EXTREME_BREACH", "PERSISTENT_ACCEPTANCE_FAILURE"):
        return "RETEST_TIMEFRAME"
    if death in ("LACK_OF_DIRECTIONAL_EXPANSION",) or (r.get("machine_S3") and not r.get("machine_S4")):
        return "S4_TOO_STRICT"
    if death == "LATE_RANGE_RESOLUTION":
        return "S1_REPRESENTATION"
    if r.get("human_location") != "CLEAR_DEFENDED_LOCATION":
        return "ORIGINAL_HUMAN_STACK_WAS_NOT_ACTUALLY_5M_VALID"
    return "OTHER"


def clear19(rows: list[dict[str, Any]], timelines: dict[str, Any]) -> dict[str, Any]:
    clear = [r for r in rows if r.get("human_pattern") == "CLEAR_CONTINUATION"]
    misses = [r for r in clear if not r.get("machine_S4")]
    deaths = dict(Counter(str(r.get("machine_death") or "NONE") for r in misses))
    classified = []
    layer_n: Counter[str] = Counter()
    for r in clear:
        tl = _tl(timelines, str(r.get("symbol")), str(r.get("date")))
        klass = "S4_PASS" if r.get("machine_S4") else _clear_miss_class(r, tl)
        if not r.get("machine_S4"):
            layer_n[klass] += 1
        classified.append(
            {
                "rca_id": r.get("rca_id"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "human_opening_state": r.get("human_opening_state"),
                "human_location_kind": r.get("human_location_kind"),
                "human_location": r.get("human_location"),
                "machine_S1": r.get("machine_S1"),
                "machine_S2": r.get("machine_S2"),
                "machine_S3": r.get("machine_S3"),
                "machine_S4": r.get("machine_S4"),
                "machine_opening_state": r.get("machine_opening_state"),
                "death": r.get("machine_death"),
                "location_family": r.get("location_family"),
                "class": klass,
                "any_completed_5m_leave": tl.get("any_completed_5m_leave"),
            }
        )
    return {
        "clear_n": len(clear),
        "s4_pass_n": sum(1 for r in clear if r.get("machine_S4")),
        "miss_n": len(misses),
        "death_distribution": deaths,
        "miss_class_n": dict(layer_n),
        "rows": classified,
    }


def conditioned_parity(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def acc(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
        tp = sum(1 for h, m in pairs if h and m)
        tn = sum(1 for h, m in pairs if (not h) and (not m))
        fp = sum(1 for h, m in pairs if (not h) and m)
        fn = sum(1 for h, m in pairs if h and (not m))
        n = len(pairs)
        return {"n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn, "accuracy": (tp + tn) / n if n else None}

    s0 = [r for r in rows if r.get("machine_S0") or str(r.get("human_opening_state") or "") != "FLAT_OR_CRAWL"]
    s1_pairs = [(bool(r.get("human_s1_valid")), bool(r.get("machine_S1"))) for r in s0]
    agree_s1 = [r for r in rows if bool(r.get("human_s1_valid")) and bool(r.get("machine_S1"))]
    s2_pairs = [(str(r.get("human_location") or "") == "CLEAR_DEFENDED_LOCATION", bool(r.get("machine_S2"))) for r in agree_s1]
    agree_s2 = [r for r in agree_s1 if str(r.get("human_location") or "") == "CLEAR_DEFENDED_LOCATION" and r.get("machine_S2")]
    s3_h = []
    for r in agree_s2:
        h = str(r.get("human_retest") or "") in ("VALID_FIRST_RETEST", "FIRST_RETEST", "")
        if str(r.get("human_retest") or "") == "STALE_OR_EXHAUSTED":
            h = False
        s3_h.append((h, bool(r.get("machine_S3"))))
    agree_s3 = [r for r, (h, m) in zip(agree_s2, s3_h) if h and m]
    s4_pairs = [
        (
            bool(r.get("human_s1_valid") and r.get("human_location") == "CLEAR_DEFENDED_LOCATION" and not (str(r.get("human_retest") or "") == "STALE_OR_EXHAUSTED")),
            bool(r.get("machine_S4")),
        )
        for r in agree_s3
    ]
    agree_s4 = [r for r in agree_s3 if r.get("machine_S4")]
    e1_pairs = [(str(r.get("human_trigger") or "") == "VALID_REACCELERATION", bool(r.get("machine_E1"))) for r in agree_s4]
    return {
        "label": "STAGE_CONDITIONED_DEVELOPMENT_PARITY_ONLY",
        "S1_given_S0_semantic_candidates": acc(s1_pairs),
        "S2_given_human_and_machine_S1_valid": acc(s2_pairs),
        "S3_given_comparable_valid_S2": acc(s3_h),
        "S4_given_comparable_valid_S3": acc(s4_pairs),
        "E1_given_comparable_valid_S4": acc(e1_pairs),
        "not_an_optimization_target": True,
    }
